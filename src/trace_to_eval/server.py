"""Local-only web UI for the trace-to-eval case store.

The server is intentionally small and dependency-free. It binds to loopback by default,
serves static assets from the package, and exposes a JSON API backed by the same SQLite
store as the CLI. Mutating requests require a per-process token injected into the HTML so
another website cannot drive the local API through a browser.

Live providers are never started from the UI. The only executable action is a fake-provider
replay, which uses the configured fake script and the same deterministic runner as the CLI.
"""

from __future__ import annotations

import contextlib
import copy
import json
import logging
import os
import re
import secrets
import threading
import webbrowser
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .config import Config
from .errors import ConfigError, TraceToEvalError
from .gates import BaselineSnapshot, Thresholds, evaluate_gates
from .providers import FakeProvider, FakeScript
from .redact import ALL_CATEGORIES
from .runner import (
    RunConfig,
    RunSummary,
    reconstruct_summary,
    run_replay,
    select_cases,
)
from .schema import (
    FAILURE_CATEGORIES,
    normalize_trace,
    validate_trace,
)
from .store import CaseStore
from .util import truncate, utc_now

logger = logging.getLogger(__name__)

MAX_BODY_BYTES = 1024 * 1024
MAX_CASES = 500
MAX_NOTES = 2000
MAX_ASSERTIONS = 50
MAX_ASSERTION_CHARS = 1000
MAX_LIST_ITEMS = 200
MAX_NAME_CHARS = 64

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_ASSETS = {
    "tokens.css": "text/css; charset=utf-8",
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}
_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; font-src 'self'; base-uri 'none'; form-action 'self'; "
    "frame-ancestors 'none'"
)
_ASSERTION_TYPES = frozenset({"text_equals", "text_contains", "text_not_contains"})
_RUN_FIELDS = frozenset(
    {
        "provider",
        "case_ids",
        "tags",
        "max_cases",
        "baseline",
        "save_baseline",
        "code_revision",
        "thresholds",
    }
)
_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class _RequestError(Exception):
    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class _State:
    db_path: str
    config: Config
    token: str
    allowed_hosts: frozenset[str]
    web_root: Path
    allow_remote: bool = False
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    job_lock: threading.Lock = field(default_factory=threading.Lock)


class _UiServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        state: _State,
    ) -> None:
        self.state = state
        super().__init__(server_address, handler)


def _loopback_url_host(host: str) -> str:
    if host in {"0.0.0.0", "::"}:  # noqa: S104 - display-only loopback rewrite
        return "127.0.0.1"
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def create_server(
    config: Config,
    db_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    allow_remote: bool = False,
) -> _UiServer:
    """Build a server without starting it. Useful for tests and embedding."""
    if db_path == ":memory:":
        raise ConfigError("serve requires a file-backed store, not :memory:")
    if not 0 <= port <= 65535:
        raise ConfigError("port must be between 0 and 65535")
    if host not in _LOOPBACK_HOSTS and not allow_remote:
        raise ConfigError(
            "refusing to bind the local UI to a non-loopback address; pass --allow-remote "
            "only when you understand that the UI has no user accounts"
        )
    resolved = str(Path(db_path).expanduser().resolve())
    with CaseStore(resolved):
        pass
    web_root = Path(__file__).parent / "web"
    if not (web_root / "index.html").is_file():
        raise ConfigError(f"UI assets are missing from {web_root}")
    allowed = frozenset({host.lower(), *_LOOPBACK_HOSTS})
    state = _State(
        db_path=resolved,
        config=config,
        token=secrets.token_urlsafe(32),
        allowed_hosts=allowed,
        web_root=web_root,
        allow_remote=allow_remote,
    )
    return _UiServer((host, port), _Handler, state)


def serve(
    config: Config,
    db_path: str,
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    open_browser: bool = False,
    allow_remote: bool = False,
) -> int:
    """Run the local UI until interrupted."""
    server = create_server(
        config,
        db_path,
        host=host,
        port=port,
        allow_remote=allow_remote,
    )
    actual_host, actual_port = server.server_address[:2]
    url = f"http://{_loopback_url_host(str(actual_host))}:{actual_port}/"
    print(f"trace-to-eval UI: {url}")
    print("local only; press Ctrl+C to stop")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopping")
    finally:
        server.server_close()
    return 0


# ------------------------------------------------------------------- serializers
def _output_text(result: dict[str, Any]) -> str:
    output = result.get("output_json")
    if isinstance(output, dict) and isinstance(output.get("text"), str):
        return str(output["text"])
    value = result.get("output_text")
    return value if isinstance(value, str) else ""


def _case_summary(case: dict[str, Any]) -> dict[str, Any]:
    trace_value = case.get("trace")
    trace: dict[str, Any] = trace_value if isinstance(trace_value, dict) else {}
    input_value = trace.get("input")
    input_data: dict[str, Any] = input_value if isinstance(input_value, dict) else {}
    messages = input_data.get("messages") or []
    output_value = trace.get("output")
    output: dict[str, Any] = output_value if isinstance(output_value, dict) else {}
    label = case.get("label")
    preview = ""
    for message in reversed(messages):
        if not isinstance(message, dict):
            continue
        if message.get("role") == "user" and isinstance(message.get("content"), str):
            preview = str(message["content"])
            break
    if not preview and isinstance(output.get("text"), str):
        preview = str(output["text"])
    return {
        "case_id": case["case_id"],
        "trace_id": case.get("trace_id"),
        "schema_version": case.get("schema_version"),
        "imported_at": case.get("imported_at"),
        "content_hash": case.get("content_hash"),
        "source_path": case.get("source_path"),
        "tags": case.get("tags") or [],
        "redaction": case.get("redaction") or {"total": 0, "by_category": {}},
        "label": label,
        "preview": truncate(preview, max_length=180),
        "message_count": len(messages),
        "tool_call_count": len(output.get("tool_calls") or []),
        "outcome": trace.get("outcome") or {},
        "model": trace.get("model") or {},
        "source": trace.get("source") or {},
        "metrics": trace.get("metrics") or {},
        "created_at": trace.get("created_at"),
        "deleted": False,
    }


def _deleted_case(case_id: str) -> dict[str, Any]:
    return {"case_id": case_id, "deleted": True, "preview": "Case deleted", "tags": []}


def _run_payload(run: dict[str, Any]) -> dict[str, Any]:
    summary = run.get("summary")
    return {
        "run_id": run["run_id"],
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "provider": run.get("provider"),
        "model": run.get("model"),
        "prompt_version": run.get("prompt_version"),
        "code_revision": run.get("code_revision"),
        "baseline_name": run.get("baseline_name"),
        "status": run.get("status"),
        "config": run.get("config") or {},
        "summary": summary,
        "cases_evaluated": summary.get("total_cases") if isinstance(summary, dict) else None,
    }


def _baseline_info(store: CaseStore, stored: dict[str, Any]) -> dict[str, Any]:
    run = store.get_run(stored["run_id"])
    return {
        "name": stored.get("name"),
        "run_id": stored.get("run_id"),
        "created_at": stored.get("created_at"),
        "summary": stored.get("summary"),
        "run": _run_payload(run) if run is not None else None,
        "unavailable": False,
    }


def _resolve_baseline(
    store: CaseStore,
    run: dict[str, Any],
    requested: str | None,
) -> dict[str, Any] | None:
    if requested:
        stored = store.get_baseline(requested)
        if stored is not None:
            return _baseline_info(store, stored)
        other = store.get_run(requested)
        if other is not None:
            return {
                "name": None,
                "run_id": other["run_id"],
                "created_at": other.get("started_at"),
                "summary": other.get("summary"),
                "run": _run_payload(other),
                "unavailable": False,
            }
        raise _RequestError(HTTPStatus.NOT_FOUND, f"unknown baseline or run: {requested}")

    name = run.get("baseline_name")
    if not isinstance(name, str) or not name:
        return None
    stored = store.get_baseline(name)
    if stored is None:
        return {
            "name": name,
            "run_id": None,
            "created_at": None,
            "summary": None,
            "run": None,
            "unavailable": True,
        }
    return _baseline_info(store, stored)


def _baseline_summary(baseline: dict[str, Any] | None) -> dict[str, Any] | None:
    if baseline is None or baseline.get("unavailable"):
        return None
    summary = baseline.get("summary")
    if isinstance(summary, dict):
        return summary
    run = baseline.get("run")
    if isinstance(run, dict):
        summary = run.get("summary")
        if isinstance(summary, dict):
            return summary
    return None


def _percent_delta(current: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return (current - baseline) / baseline * 100.0


def _deltas(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> dict[str, Any]:
    if not isinstance(current, dict) or not isinstance(baseline, dict):
        return {
            "available": False,
            "pass_rate": None,
            "cost_pct": None,
            "latency_pct": None,
        }
    try:
        current_pass = float(current["pass_rate"])
        current_cost = float(current["avg_cost_usd"])
        current_latency = float(current["p95_latency_ms"])
        baseline_pass = float(baseline["pass_rate"])
        baseline_cost = float(baseline["avg_cost_usd"])
        baseline_latency = float(baseline["p95_latency_ms"])
    except (KeyError, TypeError, ValueError):
        return {
            "available": False,
            "pass_rate": None,
            "cost_pct": None,
            "latency_pct": None,
        }
    return {
        "available": True,
        "pass_rate": round(current_pass - baseline_pass, 4),
        "cost_pct": (
            None
            if (value := _percent_delta(current_cost, baseline_cost)) is None
            else round(value, 2)
        ),
        "latency_pct": (
            None
            if (value := _percent_delta(current_latency, baseline_latency)) is None
            else round(value, 2)
        ),
    }


def _first_failure(result: dict[str, Any]) -> dict[str, Any] | None:
    if result.get("status") == "infrastructure_error":
        return {
            "kind": "infrastructure",
            "name": result.get("error_class") or "provider",
            "message": truncate(result.get("error_message") or "provider failure", max_length=240),
        }
    for assertion in result.get("assertions") or []:
        if assertion.get("kind") == "deterministic" and assertion.get("status") == "fail":
            return {
                "kind": "assertion",
                "name": assertion.get("name") or "assertion",
                "message": truncate(assertion.get("message") or "assertion failed", max_length=240),
            }
    return None


def _assertion_statuses(result: dict[str, Any] | None) -> list[tuple[str, str]]:
    if not isinstance(result, dict):
        return []
    return [
        (str(item.get("name")), str(item.get("status")))
        for item in result.get("assertions") or []
        if isinstance(item, dict)
    ]


def _first_divergence(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return None
    if baseline.get("status") != candidate.get("status"):
        return {
            "field": "status",
            "baseline": baseline.get("status"),
            "candidate": candidate.get("status"),
        }
    baseline_text = _output_text(baseline)
    candidate_text = _output_text(candidate)
    if baseline_text != candidate_text:
        baseline_lines = baseline_text.splitlines()
        candidate_lines = candidate_text.splitlines()
        limit = max(len(baseline_lines), len(candidate_lines))
        for index in range(limit):
            old = baseline_lines[index] if index < len(baseline_lines) else ""
            new = candidate_lines[index] if index < len(candidate_lines) else ""
            if old != new:
                return {
                    "field": "output.text",
                    "line": index + 1,
                    "baseline": truncate(old, max_length=400),
                    "candidate": truncate(new, max_length=400),
                }
        return {
            "field": "output.text",
            "line": 1,
            "baseline": truncate(baseline_text, max_length=400),
            "candidate": truncate(candidate_text, max_length=400),
        }
    old_assertions = _assertion_statuses(baseline)
    new_assertions = _assertion_statuses(candidate)
    for index in range(max(len(old_assertions), len(new_assertions))):
        old_pair = old_assertions[index] if index < len(old_assertions) else ("missing", "missing")
        new_pair = new_assertions[index] if index < len(new_assertions) else ("missing", "missing")
        if old_pair != new_pair:
            return {
                "field": "assertions",
                "assertion": new_pair[0],
                "baseline": old_pair[1],
                "candidate": new_pair[1],
            }
    return None


def _results_differ(
    baseline: dict[str, Any] | None,
    candidate: dict[str, Any] | None,
) -> bool:
    if not isinstance(baseline, dict) or not isinstance(candidate, dict):
        return True
    return _first_divergence(baseline, candidate) is not None


def _failure_clusters(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for result in results:
        if result.get("status") == "pass":
            continue
        failure = _first_failure(result)
        if failure is None:
            key = "unclassified"
            title = "Unclassified failure"
            kind = "quality"
        elif failure["kind"] == "infrastructure":
            key = f"infrastructure:{failure['name']}"
            title = f"Infrastructure · {failure['name']}"
            kind = "infrastructure"
        else:
            key = f"assertion:{failure['name']}"
            title = f"Assertion · {failure['name']}"
            kind = "quality"
        group = groups.setdefault(
            key,
            {
                "key": key,
                "title": title,
                "kind": kind,
                "count": 0,
                "case_ids": [],
                "example": failure["message"] if failure else "",
            },
        )
        group["count"] += 1
        group["case_ids"].append(result["case_id"])
    return sorted(groups.values(), key=lambda item: (-int(item["count"]), str(item["title"])))


def _editable_assertions(trace: dict[str, Any]) -> list[dict[str, str]]:
    expectations = trace.get("expectations")
    if not isinstance(expectations, dict):
        return []
    items: list[dict[str, str]] = []
    if isinstance(expectations.get("text_equals"), str):
        items.append({"type": "text_equals", "value": str(expectations["text_equals"])})
    for kind in ("text_contains", "text_not_contains"):
        values = expectations.get(kind)
        if isinstance(values, list):
            for value in values:
                if isinstance(value, str):
                    items.append({"type": kind, "value": value})
    return items


def _apply_assertions(trace: dict[str, Any], assertions: Any) -> dict[str, Any]:
    if not isinstance(assertions, list):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "assertions must be an array")
    if len(assertions) > MAX_ASSERTIONS:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST, f"at most {MAX_ASSERTIONS} assertions may be stored"
        )
    updated = copy.deepcopy(trace)
    expectations = updated.get("expectations")
    if not isinstance(expectations, dict):
        expectations = {}
        updated["expectations"] = expectations
    expectations.pop("text_equals", None)
    expectations.pop("text_contains", None)
    expectations.pop("text_not_contains", None)

    equals: list[str] = []
    contains: list[str] = []
    not_contains: list[str] = []
    for index, item in enumerate(assertions):
        if not isinstance(item, dict):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"assertion {index} must be an object")
        unknown = sorted(set(item) - {"type", "value"})
        if unknown:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                f"assertion {index} has unknown fields: {', '.join(unknown)}",
            )
        kind = item.get("type")
        value = item.get("value")
        if kind not in _ASSERTION_TYPES:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                f"assertion {index} type must be one of {sorted(_ASSERTION_TYPES)}",
            )
        if not isinstance(value, str) or not value:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"assertion {index} value must be a non-empty string"
            )
        if len(value) > MAX_ASSERTION_CHARS:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                f"assertion {index} value exceeds {MAX_ASSERTION_CHARS} characters",
            )
        if kind == "text_equals":
            equals.append(value)
        elif kind == "text_contains":
            contains.append(value)
        else:
            not_contains.append(value)

    if len(equals) > 1:
        raise _RequestError(HTTPStatus.BAD_REQUEST, "text_equals may only be set once")
    if len(set(contains)) != len(contains):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "text_contains has duplicate values")
    if len(set(not_contains)) != len(not_contains):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "text_not_contains has duplicate values")

    if equals:
        expectations["text_equals"] = equals[0]
    if contains:
        expectations["text_contains"] = contains
    if not_contains:
        expectations["text_not_contains"] = not_contains
    if not expectations:
        updated.pop("expectations", None)

    errors = validate_trace(updated)
    if errors:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST,
            "assertion update rejected: " + "; ".join(errors[:4]),
        )
    return normalize_trace(updated)


def _optional_query(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values or values[0] == "":
        return None
    return values[0]


def _int_query(
    query: dict[str, list[str]],
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int,
) -> int:
    raw = (query.get(name) or [""])[0]
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"{name} must be an integer") from None
    if not minimum <= value <= maximum:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST, f"{name} must be between {minimum} and {maximum}"
        )
    return value


def _string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"{field_name} must be an array")
    if len(value) > MAX_LIST_ITEMS:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST, f"{field_name} may contain at most {MAX_LIST_ITEMS} items"
        )
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"{field_name} entries must be non-empty strings"
            )
        text = item.strip()
        if len(text) > 200:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"{field_name} entries may be at most 200 characters"
            )
        if text not in result:
            result.append(text)
    return result


def _optional_name(value: Any, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise _RequestError(
            HTTPStatus.BAD_REQUEST,
            f"{field_name} must use 1-{MAX_NAME_CHARS} letters, digits, dots, dashes, "
            "or underscores",
        )
    return value


def _merge_thresholds(base: Thresholds, value: Any) -> Thresholds:
    if value is None:
        return base
    if not isinstance(value, dict):
        raise _RequestError(HTTPStatus.BAD_REQUEST, "thresholds must be an object")
    known = set(Thresholds.__dataclass_fields__)
    unknown = sorted(set(value) - known)
    if unknown:
        raise _RequestError(
            HTTPStatus.BAD_REQUEST, f"unknown threshold fields: {', '.join(unknown)}"
        )
    int_fields = {"max_infrastructure_errors", "max_signal_failures"}
    for key, item in value.items():
        if item is None:
            continue
        if key in int_fields:
            if not isinstance(item, int) or isinstance(item, bool):
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST, f"threshold {key} must be an integer or null"
                )
        elif not isinstance(item, (int, float)) or isinstance(item, bool):
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"threshold {key} must be a number or null")
    merged = base.as_dict() | value
    try:
        return Thresholds.from_mapping(merged)
    except ConfigError as exc:
        raise _RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from None


def _settings_payload(store: CaseStore, state: _State) -> dict[str, Any]:
    config = state.config
    redaction = config.redaction
    return {
        "version": __version__,
        "db": state.db_path,
        "config_source": config.source,
        "provider": config.provider,
        "model": config.model,
        "prompt_version": config.prompt_version,
        "code_revision": config.code_revision,
        "fake_script": config.fake_script,
        "base_url": config.base_url,
        "rubric": config.rubric,
        "rubric_model": config.rubric_model,
        "thresholds": config.thresholds.as_dict(),
        "budget": config.budget.as_dict(),
        "redaction": {
            "enabled": sorted(redaction.categories),
            "disabled": sorted(ALL_CATEGORIES - redaction.categories),
            "custom": sorted(pattern.name for pattern in redaction.custom),
            "all_categories": sorted(ALL_CATEGORIES),
        },
        "retention": (
            "Cases, runs, labels, and baselines stay in this local SQLite file until you "
            "delete them. The UI never uploads traces or reports. Deleting a case also "
            "deletes its labels and stored run results."
        ),
        "live": {
            "ui_enabled": False,
            "env_consent": bool(os.environ.get("TRACE_TO_EVAL_ALLOW_LIVE", "").strip()),
            "note": (
                "Live providers are opt-in through the CLI with explicit consent and hard "
                "request, token, and cost caps. The UI only runs the deterministic fake provider."
            ),
        },
        "adapters": [
            {
                "name": "fake",
                "status": "enabled",
                "detail": "Deterministic, local, zero-cost replay. The UI uses this adapter.",
            },
            {
                "name": "openai-compatible",
                "status": (
                    "configured"
                    if config.provider in {"openai", "openai-compatible"}
                    else "available"
                ),
                "detail": "Requires CLI --allow-live, TRACE_TO_EVAL_ALLOW_LIVE=1, and caps.",
            },
            {
                "name": "anthropic",
                "status": (
                    "configured" if config.provider in {"anthropic", "claude"} else "available"
                ),
                "detail": "Requires CLI --allow-live, TRACE_TO_EVAL_ALLOW_LIVE=1, and caps.",
            },
        ],
        "failure_categories": sorted(FAILURE_CATEGORIES),
        "counts": {
            "cases": store.count_cases(),
            "runs": store.count_runs(),
        },
    }


_THRESHOLD_FLAGS: tuple[tuple[str, str], ...] = (
    ("min_pass_rate", "--min-pass-rate"),
    ("max_total_cost_usd", "--max-total-cost-usd"),
    ("max_avg_cost_usd", "--max-avg-cost-usd"),
    ("max_p95_latency_ms", "--max-p95-latency-ms"),
    ("max_infrastructure_errors", "--max-infrastructure-errors"),
    ("max_signal_failures", "--max-signal-failures"),
    ("max_pass_rate_drop", "--max-pass-rate-drop"),
    ("max_cost_increase_pct", "--max-cost-increase-pct"),
    ("max_latency_increase_pct", "--max-latency-increase-pct"),
)

_INT_THRESHOLDS = frozenset({"max_infrastructure_errors", "max_signal_failures"})


def _toml_value(value: float | int) -> str:
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    text = f"{float(value):g}"
    return text if any(marker in text for marker in (".", "e", "E")) else f"{text}.0"


def _ci_config_toml(config: Config, thresholds: dict[str, Any]) -> str:
    lines = [
        "# trace-to-eval.toml - generated from the resolved local configuration.",
        "# The same table works under [tool.trace_to_eval] in pyproject.toml.",
        "[trace_to_eval]",
        f'db = "{config.db}"',
        'provider = "fake"',
        f'model = "{config.model}"',
        f'prompt_version = "{config.prompt_version}"',
        "",
        "[trace_to_eval.thresholds]",
    ]
    present = [(key, thresholds.get(key)) for key, _ in _THRESHOLD_FLAGS]
    if not any(value is not None for _, value in present):
        lines.append("# No thresholds set; add gate values here to fail CI on regressions.")
    for key, value in present:
        if value is not None:
            lines.append(f"{key} = {_toml_value(value)}")
    return "\n".join(lines)


def _ci_run_command(thresholds: dict[str, Any], baseline: str | None) -> str:
    parts = ["trace-to-eval run --provider fake"]
    if baseline:
        parts.append(f"--baseline {baseline}")
    for key, flag in _THRESHOLD_FLAGS:
        value = thresholds.get(key)
        if value is not None:
            parts.append(f"{flag} {_toml_value(value)}")
    parts.append("--format junit --out trace-to-eval-report.xml")
    return " ".join(parts)


def _ci_workflow(command: str, baseline: str | None) -> str:
    baseline_note = (
        f"      # Baseline `{baseline}` must already exist in the database used by CI.\n"
        if baseline
        else ""
    )
    return (
        "name: trace-to-eval\n"
        "on: [pull_request]\n"
        "jobs:\n"
        "  replay:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - uses: actions/setup-python@v5\n"
        "        with:\n"
        '          python-version: "3.12"\n'
        "      - run: python -m pip install .\n"
        "      - run: trace-to-eval init\n"
        "      - run: trace-to-eval import traces/*.jsonl\n"
        f"{baseline_note}"
        f"      - run: {command}\n"
        "      - if: always()\n"
        "        uses: actions/upload-artifact@v4\n"
        "        with:\n"
        "          name: trace-to-eval-report\n"
        "          path: trace-to-eval-report.xml\n"
        "          retention-days: 14\n"
    )


def _ci_query_thresholds(base: Thresholds, query: dict[str, list[str]]) -> Thresholds:
    """Merge draft gate values from the CI form onto the resolved configuration."""

    draft: dict[str, Any] = {}
    for key, _ in _THRESHOLD_FLAGS:
        raw = (query.get(key) or [""])[0].strip()
        if raw == "":
            continue
        try:
            number = float(raw)
        except ValueError:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"threshold {key} must be a number"
            ) from None
        if number != number or number in {float("inf"), float("-inf")}:
            raise _RequestError(HTTPStatus.BAD_REQUEST, f"threshold {key} must be finite")
        draft[key] = int(number) if key in _INT_THRESHOLDS else number
    return _merge_thresholds(base, draft) if draft else base


def _ci_query_baseline(store: CaseStore, query: dict[str, list[str]]) -> tuple[bool, str | None]:
    """Return ``(requested, name)``; an explicit empty value means "no baseline"."""

    if "baseline" not in query:
        return False, None
    name = _optional_name((query.get("baseline") or [""])[0], "baseline")
    if name is not None and store.get_baseline(name) is None:
        raise _RequestError(HTTPStatus.BAD_REQUEST, f"unknown baseline: {name}")
    return True, name


def _ci_payload(
    store: CaseStore,
    state: _State,
    thresholds_obj: Thresholds,
    baseline_requested: bool,
    baseline: str | None,
) -> dict[str, Any]:
    config = state.config
    thresholds = thresholds_obj.as_dict()
    if not baseline_requested:
        baselines = store.list_baselines()
        baseline = baselines[0]["name"] if baselines else None
    command = _ci_run_command(thresholds, baseline)
    return {
        "config_path": "trace-to-eval.toml",
        "config_toml": _ci_config_toml(config, thresholds),
        "workflow_path": ".github/workflows/trace-to-eval.yml",
        "workflow": _ci_workflow(command, baseline),
        "command": command,
        "commands": [
            "trace-to-eval init",
            "trace-to-eval import traces/*.jsonl",
            command,
        ],
        "thresholds": thresholds,
        "baselines": [item["name"] for item in store.list_baselines()],
        "baseline": baseline,
        "retention_note": (
            "The generated workflow uploads trace-to-eval-report.xml with "
            "actions/upload-artifact and keeps it for 14 days. Reports contain case IDs, "
            "assertion results, and redacted output only; review the artifact before making "
            "the repository public."
        ),
        "live_note": (
            "The workflow runs the deterministic fake provider only. Live provider calls stay "
            "in an explicit CLI invocation with --allow-live and hard caps."
        ),
    }


# ---------------------------------------------------------------------- handler
class _Handler(BaseHTTPRequestHandler):
    server_version = "trace-to-eval-ui"
    sys_version = ""

    @property
    def state(self) -> _State:
        server = self.server
        assert isinstance(server, _UiServer)
        return server.state

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("%s - %s", self.address_string(), format % args)

    # ------------------------------------------------------------------ verbs
    def do_GET(self) -> None:
        self._dispatch("GET")

    def do_POST(self) -> None:
        self._dispatch("POST")

    def do_DELETE(self) -> None:
        self._dispatch("DELETE")

    def _dispatch(self, method: str) -> None:
        try:
            self._check_host()
            parsed = urlparse(self.path)
            path = parsed.path
            query = parse_qs(parsed.query, keep_blank_values=True)
            if method == "GET" and path in {"/", "/index.html"}:
                self._serve_index()
                return
            if method == "GET" and path.startswith("/assets/"):
                self._serve_asset(path.removeprefix("/assets/"))
                return
            if path.startswith("/api/"):
                if method in {"POST", "DELETE"}:
                    self._check_mutation()
                self._dispatch_api(method, path, query)
                return
            if path == "/favicon.ico":
                self._send_bytes(HTTPStatus.NO_CONTENT, b"", "image/x-icon")
                return
            raise _RequestError(HTTPStatus.NOT_FOUND, "not found")
        except _RequestError as exc:
            self._discard_body()
            self._send_json(exc.status, {"error": exc.message})
        except (ConfigError, TraceToEvalError, ValueError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except Exception:
            logger.exception("unhandled request failure: %s %s", method, self.path)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": "internal server error; see the server log"},
            )

    # ----------------------------------------------------------------- routes
    def _dispatch_api(
        self,
        method: str,
        path: str,
        query: dict[str, list[str]],
    ) -> None:
        segments = [unquote(part) for part in path.split("/") if part]
        if method == "GET" and segments == ["api", "health"]:
            self._send_json(
                HTTPStatus.OK,
                {"ok": True, "version": __version__, "store": self.state.db_path},
            )
            return
        if method == "GET" and segments == ["api", "settings"]:
            with CaseStore(self.state.db_path) as store:
                self._send_json(HTTPStatus.OK, _settings_payload(store, self.state))
            return
        if method == "GET" and segments == ["api", "ci"]:
            with CaseStore(self.state.db_path) as store:
                baseline_requested, baseline = _ci_query_baseline(store, query)
                self._send_json(
                    HTTPStatus.OK,
                    _ci_payload(
                        store,
                        self.state,
                        _ci_query_thresholds(self.state.config.thresholds, query),
                        baseline_requested,
                        baseline,
                    ),
                )
            return
        if method == "GET" and segments == ["api", "baselines"]:
            with CaseStore(self.state.db_path) as store:
                self._send_json(
                    HTTPStatus.OK,
                    {
                        "baselines": [
                            {
                                "name": item["name"],
                                "run_id": item["run_id"],
                                "created_at": item["created_at"],
                                "summary": item["summary"],
                            }
                            for item in store.list_baselines()
                        ]
                    },
                )
            return
        if method == "GET" and segments == ["api", "runs"]:
            limit = _int_query(query, "limit", 20, minimum=1, maximum=100)
            with CaseStore(self.state.db_path) as store:
                runs = store.list_runs(limit)
                items: list[dict[str, Any]] = []
                for run in runs:
                    item = _run_payload(run)
                    resolved = _resolve_baseline(store, run, None)
                    item["baseline"] = resolved
                    item["deltas"] = _deltas(run.get("summary"), _baseline_summary(resolved))
                    items.append(item)
                self._send_json(HTTPStatus.OK, {"runs": items})
            return
        if method == "GET" and len(segments) == 3 and segments[:2] == ["api", "runs"]:
            with CaseStore(self.state.db_path) as store:
                detail_run = store.get_run(segments[2])
                if detail_run is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "unknown run")
                requested = _optional_query(query, "baseline")
                self._send_json(
                    HTTPStatus.OK,
                    _run_detail(store, detail_run, requested=requested),
                )
            return
        if method == "GET" and segments == ["api", "cases"]:
            limit = _int_query(query, "limit", 200, minimum=1, maximum=MAX_CASES)
            search = (query.get("q") or [""])[0].strip().lower()
            tags = _string_list(query.get("tag") or [], "tag")
            with CaseStore(self.state.db_path) as store:
                cases = store.list_cases(tags=tags or None)
                summaries = [_case_summary(case) for case in cases]
                if search:
                    summaries = [item for item in summaries if _matches_search(item, search)]
                total = len(summaries)
                self._send_json(
                    HTTPStatus.OK,
                    {"cases": summaries[:limit], "total": total, "limit": limit},
                )
            return
        if method == "GET" and len(segments) == 3 and segments[:2] == ["api", "cases"]:
            with CaseStore(self.state.db_path) as store:
                case = store.get_case(segments[2])
                if case is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "unknown case")
                requested_run = _optional_query(query, "run")
                requested_baseline = _optional_query(query, "baseline")
                self._send_json(
                    HTTPStatus.OK,
                    _case_detail(
                        store,
                        case,
                        requested_run=requested_run,
                        requested_baseline=requested_baseline,
                    ),
                )
            return
        if method == "GET" and len(segments) == 3 and segments[:2] == ["api", "jobs"]:
            with self.state.job_lock:
                job = self.state.jobs.get(segments[2])
                if job is None:
                    raise _RequestError(HTTPStatus.NOT_FOUND, "unknown job")
                payload = _job_payload(job)
            self._send_json(HTTPStatus.OK, {"job": payload})
            return
        if method == "POST" and len(segments) == 4 and segments[:2] == ["api", "cases"]:
            if segments[3] == "label":
                self._post_label(segments[2])
                return
            if segments[3] == "assertions":
                self._post_assertions(segments[2])
                return
        if method == "POST" and segments == ["api", "run"]:
            self._post_run()
            return
        if method == "DELETE" and len(segments) == 3 and segments[:2] == ["api", "cases"]:
            self._delete_case(segments[2])
            return
        raise _RequestError(HTTPStatus.NOT_FOUND, "not found")

    # --------------------------------------------------------------- mutations
    def _post_label(self, case_id: str) -> None:
        payload = self._read_json()
        label = payload.get("label")
        if label not in {"success", "failure", "unknown"}:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "label must be success, failure, or unknown"
            )
        category = payload.get("category")
        if category in {"", None}:
            category = None
        elif not isinstance(category, str) or category not in FAILURE_CATEGORIES:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                f"category must be one of {sorted(FAILURE_CATEGORIES)}",
            )
        notes = payload.get("notes")
        if notes in {"", None}:
            notes = None
        elif not isinstance(notes, str):
            raise _RequestError(HTTPStatus.BAD_REQUEST, "notes must be a string")
        elif len(notes) > MAX_NOTES:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, f"notes may be at most {MAX_NOTES} characters"
            )
        with CaseStore(self.state.db_path) as store:
            case = store.get_case(case_id)
            if case is None:
                raise _RequestError(HTTPStatus.NOT_FOUND, "unknown case")
            store.add_label(case_id, label, category=category, notes=notes)
            updated = store.get_case(case_id)
            assert updated is not None
            self._send_json(
                HTTPStatus.OK,
                {"case": _case_summary(updated), "label": updated.get("label")},
            )

    def _post_assertions(self, case_id: str) -> None:
        payload = self._read_json()
        if "assertions" not in payload:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "assertions is required")
        with CaseStore(self.state.db_path) as store:
            case = store.get_case(case_id)
            if case is None:
                raise _RequestError(HTTPStatus.NOT_FOUND, "unknown case")
            updated_trace = _apply_assertions(case["trace"], payload["assertions"])
            store.replace_trace(case_id, updated_trace, tags=case.get("tags") or [])
            updated = store.get_case(case_id)
            assert updated is not None
            self._send_json(
                HTTPStatus.OK,
                {
                    "case_id": case_id,
                    "content_hash": updated.get("content_hash"),
                    "editable_assertions": _editable_assertions(updated["trace"]),
                },
            )

    def _delete_case(self, case_id: str) -> None:
        with CaseStore(self.state.db_path) as store:
            if store.get_case(case_id) is None:
                raise _RequestError(HTTPStatus.NOT_FOUND, "unknown case")
            store.delete_case(case_id)
        self._send_json(HTTPStatus.OK, {"deleted": True, "case_id": case_id})

    def _post_run(self) -> None:
        payload = self._read_json()
        unknown = sorted(set(payload) - _RUN_FIELDS)
        if unknown:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                f"unknown run fields: {', '.join(unknown)}; the UI run form accepts "
                f"{', '.join(sorted(_RUN_FIELDS))}",
            )
        provider = payload.get("provider")
        if provider not in {None, "", "fake"}:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST,
                "the UI only starts fake-provider runs; use the CLI for opt-in live calls",
            )
        thresholds = _merge_thresholds(self.state.config.thresholds, payload.get("thresholds"))
        case_ids = _string_list(payload.get("case_ids"), "case_ids")
        tags = _string_list(payload.get("tags"), "tags")
        baseline_name = _optional_name(payload.get("baseline"), "baseline")
        save_baseline = _optional_name(payload.get("save_baseline"), "save_baseline")
        max_cases = payload.get("max_cases")
        if max_cases is not None and (
            not isinstance(max_cases, int)
            or isinstance(max_cases, bool)
            or not 1 <= max_cases <= 1000
        ):
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "max_cases must be an integer between 1 and 1000"
            )
        code_revision = payload.get("code_revision")
        if code_revision in {"", None}:
            code_revision = self.state.config.code_revision
        elif not isinstance(code_revision, str) or len(code_revision) > 120:
            raise _RequestError(
                HTTPStatus.BAD_REQUEST, "code_revision must be a string of at most 120 characters"
            )

        run_config = RunConfig(
            provider="fake",
            model=self.state.config.model,
            prompt_version=self.state.config.prompt_version,
            code_revision=code_revision,
            timeout_s=self.state.config.budget.request_timeout_s,
            baseline_name=baseline_name,
            max_cases=max_cases,
            tags=tags,
            case_ids=case_ids,
            thresholds=thresholds.as_dict(),
        )
        with CaseStore(self.state.db_path) as store:
            if store.count_cases() == 0:
                raise _RequestError(
                    HTTPStatus.CONFLICT,
                    "no cases stored; import traces with the CLI before running the UI",
                )
            if baseline_name is not None and store.get_baseline(baseline_name) is None:
                raise _RequestError(HTTPStatus.BAD_REQUEST, f"unknown baseline: {baseline_name}")
            try:
                selected = select_cases(store, run_config)
            except ConfigError as exc:
                raise _RequestError(HTTPStatus.BAD_REQUEST, str(exc)) from None
            total = len(selected)
            if total == 0:
                raise _RequestError(
                    HTTPStatus.BAD_REQUEST,
                    "no cases match the selected filters; adjust tags, case IDs, or max_cases",
                )

        job = self._start_job(run_config, save_baseline=save_baseline, total=total)
        self._send_json(HTTPStatus.ACCEPTED, {"job": job})

    # ------------------------------------------------------------------- jobs
    def _start_job(
        self,
        run_config: RunConfig,
        *,
        save_baseline: str | None,
        total: int,
    ) -> dict[str, Any]:
        with self.state.job_lock:
            self._prune_jobs_locked()
            for active in self.state.jobs.values():
                if active["status"] == "running":
                    raise _RequestError(
                        HTTPStatus.CONFLICT,
                        "a run is already in progress; wait for it to finish",
                    )
            job_id = f"job_{secrets.token_hex(6)}"
            job: dict[str, Any] = {
                "id": job_id,
                "status": "running",
                "done": 0,
                "total": total,
                "run_id": None,
                "error": None,
                "started_at": utc_now(),
                "finished_at": None,
            }
            self.state.jobs[job_id] = job
            payload = _job_payload(job)
        thread = threading.Thread(
            target=_execute_job,
            args=(self.state, job_id, run_config, save_baseline),
            name=f"trace-to-eval-{job_id}",
            daemon=True,
        )
        thread.start()
        return payload

    def _prune_jobs_locked(self) -> None:
        if len(self.state.jobs) < 20:
            return
        finished = [job_id for job_id, job in self.state.jobs.items() if job["status"] != "running"]
        for job_id in finished[: max(0, len(finished) - 10)]:
            self.state.jobs.pop(job_id, None)

    # ------------------------------------------------------------------ static
    def _serve_index(self) -> None:
        path = self.state.web_root / "index.html"
        try:
            text = path.read_text("utf-8")
        except OSError:
            raise _RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "UI assets unavailable") from None
        body = text.replace("{{CSRF_TOKEN}}", self.state.token).encode("utf-8")
        self._send_bytes(
            HTTPStatus.OK,
            body,
            "text/html; charset=utf-8",
            csp=True,
        )

    def _serve_asset(self, name: str) -> None:
        content_type = _ASSETS.get(name)
        if content_type is None:
            raise _RequestError(HTTPStatus.NOT_FOUND, "not found")
        path = self.state.web_root / name
        try:
            body = path.read_bytes()
        except OSError:
            raise _RequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "UI asset unavailable") from None
        self._send_bytes(HTTPStatus.OK, body, content_type)

    # ------------------------------------------------------------------ guards
    def _check_host(self) -> None:
        host_header = self.headers.get("Host", "")
        if not host_header:
            raise _RequestError(HTTPStatus.MISDIRECTED_REQUEST, "Host header is required")
        if self.state.allow_remote:
            return
        hostname = urlparse(f"//{host_header}").hostname
        if hostname is None or hostname.lower() not in self.state.allowed_hosts:
            raise _RequestError(
                HTTPStatus.MISDIRECTED_REQUEST,
                "unexpected Host header; use the local UI URL printed at startup",
            )

    def _check_mutation(self) -> None:
        token = self.headers.get("X-Trace-To-Eval-Token", "")
        if not token or not secrets.compare_digest(token, self.state.token):
            raise _RequestError(
                HTTPStatus.FORBIDDEN,
                "missing or invalid local UI token; reload the UI and try again",
            )
        origin = self.headers.get("Origin")
        if origin:
            parsed = urlparse(origin)
            origin_host = (parsed.hostname or "").lower()
            if parsed.scheme not in {"http", "https"}:
                raise _RequestError(HTTPStatus.FORBIDDEN, "cross-origin mutation rejected")
            if self.state.allow_remote:
                request_host = (
                    urlparse(f"//{self.headers.get('Host', '')}").hostname or ""
                ).lower()
                if not origin_host or origin_host != request_host:
                    raise _RequestError(HTTPStatus.FORBIDDEN, "cross-origin mutation rejected")
            elif origin_host not in self.state.allowed_hosts:
                raise _RequestError(HTTPStatus.FORBIDDEN, "cross-origin mutation rejected")

    def _discard_body(self) -> None:
        """Consume an unread request body so rejecting a request cannot reset the socket."""
        if getattr(self, "_body_read", False):
            return
        self._body_read = True
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return
        try:
            length = int(raw_length)
        except ValueError:
            return
        if 0 < length <= MAX_BODY_BYTES:
            with contextlib.suppress(OSError):
                self.rfile.read(length)

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if content_type != "application/json":
            self._discard_body()
            raise _RequestError(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Content-Type must be application/json"
            )
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise _RequestError(HTTPStatus.LENGTH_REQUIRED, "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError:
            raise _RequestError(HTTPStatus.BAD_REQUEST, "invalid Content-Length") from None
        if length < 0 or length > MAX_BODY_BYTES:
            raise _RequestError(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"request body must be at most {MAX_BODY_BYTES} bytes",
            )
        raw = self.rfile.read(length)
        self._body_read = True
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise _RequestError(HTTPStatus.BAD_REQUEST, "request body must be valid JSON") from None
        if not isinstance(value, dict):
            raise _RequestError(HTTPStatus.BAD_REQUEST, "request body must be a JSON object")
        return value

    # ------------------------------------------------------------------ output
    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError):
            logger.exception("could not serialize API response")
            status = HTTPStatus.INTERNAL_SERVER_ERROR
            body = b'{"error":"response could not be serialized"}'
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
        *,
        csp: bool = False,
    ) -> None:
        self.send_response(int(status))
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        if csp:
            self.send_header("Content-Security-Policy", _CSP)
        self.end_headers()
        if body:
            self.wfile.write(body)


# ------------------------------------------------------------------ detail views
def _matches_search(item: dict[str, Any], search: str) -> bool:
    label = item.get("label") or {}
    model = item.get("model") or {}
    source = item.get("source") or {}
    haystack = " ".join(
        str(value)
        for value in (
            item.get("case_id"),
            item.get("trace_id"),
            item.get("preview"),
            " ".join(item.get("tags") or []),
            label.get("label") if isinstance(label, dict) else "",
            label.get("category") if isinstance(label, dict) else "",
            label.get("notes") if isinstance(label, dict) else "",
            model.get("name") if isinstance(model, dict) else "",
            source.get("app") if isinstance(source, dict) else "",
            source.get("environment") if isinstance(source, dict) else "",
        )
        if value
    ).lower()
    return search in haystack


def _run_detail(
    store: CaseStore,
    run: dict[str, Any],
    *,
    requested: str | None = None,
) -> dict[str, Any]:
    results = store.results_for_run(run["run_id"])
    baseline = _resolve_baseline(store, run, requested)
    baseline_summary = _baseline_summary(baseline)
    baseline_results: dict[str, dict[str, Any]] = {}
    if isinstance(baseline, dict) and isinstance(baseline.get("run_id"), str):
        baseline_results = {
            item["case_id"]: item for item in store.results_for_run(baseline["run_id"])
        }

    comparison: list[dict[str, Any]] = []
    for result in results:
        case = store.get_case(result["case_id"])
        other = baseline_results.get(result["case_id"])
        comparison.append(
            {
                "case": (
                    _case_summary(case) if case is not None else _deleted_case(result["case_id"])
                ),
                "candidate": result,
                "baseline": other,
                "changed": bool(other) and _results_differ(other, result),
                "new_case": other is None,
                "divergence": _first_divergence(other, result),
            }
        )

    gate: dict[str, Any] | None = None
    if run.get("status") == "completed" and isinstance(run.get("summary"), dict):
        try:
            thresholds = Thresholds.from_mapping((run.get("config") or {}).get("thresholds") or {})
            summary = reconstruct_summary(store, run["run_id"])
            snapshot = (
                BaselineSnapshot.from_summary(baseline_summary)
                if isinstance(baseline_summary, dict)
                else None
            )
            gate = evaluate_gates(summary, thresholds, baseline=snapshot).as_dict()
        except (ConfigError, KeyError, TypeError, ValueError):
            gate = None

    return {
        "run": _run_payload(run),
        "results": results,
        "comparison": comparison,
        "baseline": baseline,
        "deltas": _deltas(run.get("summary"), baseline_summary),
        "clusters": _failure_clusters(results),
        "gate": gate,
    }


def _case_detail(
    store: CaseStore,
    case: dict[str, Any],
    *,
    requested_run: str | None = None,
    requested_baseline: str | None = None,
) -> dict[str, Any]:
    history = store.results_for_case(case["case_id"], limit=20)
    selected_run_id = requested_run
    if selected_run_id is None and history:
        selected_run_id = history[0]["run_id"]
    selected_run = store.get_run(selected_run_id) if selected_run_id else None
    if requested_run is not None and selected_run is None:
        raise _RequestError(HTTPStatus.NOT_FOUND, "unknown run")
    selected_result = None
    if selected_run_id is not None:
        selected_result = next(
            (item for item in history if item["run_id"] == selected_run_id),
            None,
        )
    baseline: dict[str, Any] | None = None
    baseline_result = None
    if selected_run is not None:
        baseline = _resolve_baseline(store, selected_run, requested_baseline)
        if baseline is not None and isinstance(baseline.get("run_id"), str):
            baseline_result = next(
                (
                    item
                    for item in store.results_for_case(case["case_id"], limit=100)
                    if item["run_id"] == baseline["run_id"]
                ),
                None,
            )
    return {
        "case": _case_summary(case),
        "trace": case["trace"],
        "editable_assertions": _editable_assertions(case["trace"]),
        "labels": store.list_labels(case["case_id"])[-20:],
        "history": history,
        "run": _run_payload(selected_run) if selected_run is not None else None,
        "result": selected_result,
        "baseline": baseline,
        "baseline_result": baseline_result,
        "divergence": _first_divergence(baseline_result, selected_result),
        "failure_categories": sorted(FAILURE_CATEGORIES),
    }


# --------------------------------------------------------------------- jobs
def _job_payload(job: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": job["id"],
        "status": job["status"],
        "done": job["done"],
        "total": job["total"],
        "run_id": job["run_id"],
        "error": job["error"],
        "started_at": job["started_at"],
        "finished_at": job["finished_at"],
    }


def _set_job(state: _State, job_id: str, **fields: Any) -> None:
    with state.job_lock:
        job = state.jobs.get(job_id)
        if job is not None:
            job.update(fields)


def _execute_job(
    state: _State,
    job_id: str,
    run_config: RunConfig,
    save_baseline: str | None,
) -> None:
    def progress(done: int, total: int) -> None:
        _set_job(state, job_id, done=done, total=total)

    try:
        with CaseStore(state.db_path) as store:
            script = FakeScript.load(state.config.fake_script) if state.config.fake_script else None
            provider = FakeProvider(model=state.config.model, script=script)
            summary: RunSummary = run_replay(
                store,
                provider,
                run_config,
                progress=progress,
            )
            if save_baseline is not None:
                store.save_baseline(
                    save_baseline,
                    summary.run_id,
                    summary.metrics_for_baseline(),
                )
            _set_job(
                state,
                job_id,
                status="completed",
                run_id=summary.run_id,
                done=summary.total_cases,
                total=summary.total_cases,
                finished_at=utc_now(),
            )
    except Exception as exc:  # surfaced to the UI with a bounded message
        logger.exception("UI replay failed")
        _set_job(
            state,
            job_id,
            status="failed",
            error=truncate(str(exc), max_length=500),
            finished_at=utc_now(),
        )
