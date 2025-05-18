"""Command-line interface.

Commands are thin: parse, validate, delegate, render. Exit codes are stable for CI:

* ``0`` success
* ``1`` quality regression or failed gate
* ``2`` infrastructure failure (provider/network/budget)
* ``3`` usage or configuration error
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .budget import BudgetLimits, BudgetTracker, day_start, month_start, require_live_consent
from .config import Config, load_config
from .errors import (
    BudgetExceeded,
    ConfigError,
    LiveCallsDisabled,
    ProviderError,
    SchemaValidationError,
    TraceToEvalError,
)
from .gates import (
    EXIT_INFRASTRUCTURE,
    EXIT_OK,
    EXIT_QUALITY,
    EXIT_USAGE,
    BaselineSnapshot,
    GateReport,
    Thresholds,
    evaluate_gates,
    exit_code_for,
)
from .providers import FakeProvider
from .providers.base import Provider
from .providers.fake import FakeScript
from .providers.live import AnthropicProvider, OpenAICompatibleProvider
from .redact import RedactionConfig, redact_trace
from .report import render_json, render_junit, render_markdown, render_terminal, write_report
from .rubric import RubricJudge
from .runner import RunConfig, RunSummary, reconstruct_summary, run_replay
from .schema import (
    FAILURE_CATEGORIES,
    MAX_TRACE_FILE_BYTES,
    MAX_TRACE_LINE_CHARS,
    case_id_for_trace,
    load_json_schema,
    normalize_trace,
    parse_trace_line,
)
from .store import CaseStore

_FORMATS = ("terminal", "json", "markdown", "junit")


class _UsageError(Exception):
    """A command was invoked with invalid arguments."""


# --------------------------------------------------------------------- helpers
def _print(message: str = "") -> None:
    print(message)


def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr)


def _resolve_db(args: argparse.Namespace, config: Config) -> str:
    return str(getattr(args, "db", None) or config.db)


def _open_store(args: argparse.Namespace, config: Config) -> CaseStore:
    return CaseStore(_resolve_db(args, config))


def _check_file_size(path: Path) -> None:
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
    if size > MAX_TRACE_FILE_BYTES:
        raise ConfigError(f"{path}: file is {size} bytes; limit is {MAX_TRACE_FILE_BYTES} bytes")


def _iter_jsonl(path: Path) -> list[tuple[int, str]]:
    _check_file_size(path)
    try:
        text = path.read_text("utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
    entries = [(index, line) for index, line in enumerate(text.splitlines(), start=1)]
    for index, line in entries:
        if len(line) > MAX_TRACE_LINE_CHARS:
            raise ConfigError(
                f"{path}:{index}: line is {len(line)} characters; limit is {MAX_TRACE_LINE_CHARS}"
            )
    return entries


def _load_trace_file(path: Path) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        entries = _iter_jsonl(path)
    else:
        _check_file_size(path)
        try:
            decoded = json.loads(path.read_text("utf-8"))
        except OSError as exc:
            raise ConfigError(f"cannot read {path}: {exc.strerror}") from None
        except json.JSONDecodeError as exc:
            raise ConfigError(f"{path} is not valid JSON: {exc.msg}") from None
        entries = (
            [(-1, json.dumps(item, ensure_ascii=False)) for item in decoded]
            if isinstance(decoded, list)
            else [(-1, json.dumps(decoded, ensure_ascii=False))]
        )

    traces: list[dict[str, Any]] = []
    for line_number, line in entries:
        if not line.strip():
            continue
        trace, errors = parse_trace_line(line)
        if errors:
            where = f"{path}:{line_number}" if line_number > 0 else str(path)
            raise SchemaValidationError([f"{where}: {error}" for error in errors])
        assert trace is not None
        traces.append(trace)
    return traces


def _parse_thresholds(
    args: argparse.Namespace, config: Config, base: Thresholds | None = None
) -> Thresholds:
    base = base or config.thresholds
    overrides: dict[str, Any] = {}
    mapping = {
        "min_pass_rate": "min_pass_rate",
        "max_total_cost_usd": "max_total_cost_usd",
        "max_avg_cost_usd": "max_avg_cost_usd",
        "max_p95_latency_ms": "max_p95_latency_ms",
        "max_infrastructure_errors": "max_infrastructure_errors",
        "max_signal_failures": "max_signal_failures",
        "max_pass_rate_drop": "max_pass_rate_drop",
        "max_cost_increase_pct": "max_cost_increase_pct",
        "max_latency_increase_pct": "max_latency_increase_pct",
    }
    for arg_name, field_name in mapping.items():
        value = getattr(args, arg_name, None)
        if value is not None:
            overrides[field_name] = value
    if not overrides:
        return base
    merged = base.as_dict() | overrides
    return Thresholds.from_mapping(merged)


def _budget_limits(args: argparse.Namespace, config: Config) -> BudgetLimits:
    base = config.budget
    overrides: dict[str, Any] = {}
    if getattr(args, "max_requests", None) is not None:
        overrides["max_requests_per_run"] = args.max_requests
    if getattr(args, "max_run_cost_usd", None) is not None:
        overrides["max_cost_usd_per_run"] = args.max_run_cost_usd
    if getattr(args, "max_tokens", None) is not None:
        overrides["max_tokens_per_run"] = args.max_tokens
    if getattr(args, "timeout", None) is not None:
        overrides["request_timeout_s"] = args.timeout
    if not overrides:
        return base
    return BudgetLimits.from_mapping(base.as_dict() | overrides)


def _build_provider(
    args: argparse.Namespace,
    config: Config,
    store: CaseStore,
) -> tuple[Provider, BudgetTracker | None]:
    provider_name = (getattr(args, "provider", None) or config.provider).lower()
    model = getattr(args, "model", None) or config.model
    script_path = getattr(args, "fake_script", None) or config.fake_script

    if provider_name == "fake":
        script = FakeScript.load(script_path) if script_path else None
        return FakeProvider(model=model, script=script), None

    limits = _budget_limits(args, config)
    require_live_consent(
        allow_live_flag=bool(getattr(args, "allow_live", False)),
        environment_value=os.environ.get("TRACE_TO_EVAL_ALLOW_LIVE"),
        limits=limits,
    )
    tracker = BudgetTracker(
        limits=limits,
        daily_spend_usd=store.ledger_spend_since(day_start()),
        monthly_spend_usd=store.ledger_spend_since(month_start()),
    )
    base_url = getattr(args, "base_url", None) or config.base_url
    provider: Provider
    if provider_name in {"openai", "openai-compatible", "openai_compatible"}:
        provider = OpenAICompatibleProvider(model=model, budget=tracker, base_url=base_url)
    elif provider_name in {"anthropic", "claude"}:
        provider = AnthropicProvider(model=model, budget=tracker, base_url=base_url)
    else:
        raise _UsageError(
            f"unknown provider {provider_name!r}; use fake, openai-compatible, or anthropic"
        )
    return provider, tracker


def _redaction_config(args: argparse.Namespace, config: Config) -> RedactionConfig | None:
    if getattr(args, "no_redact", False):
        return None
    return config.redaction


def _emit_report(
    summary: RunSummary,
    gate_report: GateReport,
    *,
    fmt: str,
    out: str | None,
    unicode: bool,
    color: bool,
) -> None:
    if fmt == "json":
        content = render_json(summary, gate_report)
    elif fmt == "markdown":
        content = render_markdown(summary, gate_report)
    elif fmt == "junit":
        content = render_junit(summary, gate_report)
    else:
        content = render_terminal(summary, gate_report, unicode=unicode, color=color)
    if out:
        write_report(out, content)
        _print(f"wrote {fmt} report to {out}")
    else:
        _print(content)


def _baseline_snapshot(store: CaseStore, name: str | None) -> BaselineSnapshot | None:
    if not name:
        return None
    stored = store.get_baseline(name)
    if stored is None:
        return None
    return BaselineSnapshot.from_summary(stored["summary"])


# -------------------------------------------------------------------- commands
def _cmd_init(args: argparse.Namespace, config: Config) -> int:
    db_path = _resolve_db(args, config)
    with CaseStore(db_path) as store:
        cases = store.count_cases()
    _print(f"initialized {db_path} ({cases} case(s))")
    _print("next: trace-to-eval import traces.jsonl")
    return EXIT_OK


def _cmd_import(args: argparse.Namespace, config: Config) -> int:
    paths = [Path(item).expanduser() for item in args.paths]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise _UsageError(f"trace file(s) not found: {missing}")

    redaction = _redaction_config(args, config)
    imported = 0
    duplicates = 0
    redactions = 0
    records: list[dict[str, Any]] = []
    with _open_store(args, config) as store:
        for path in paths:
            for trace in _load_trace_file(path):
                if args.tag:
                    tags = trace.setdefault("tags", [])
                    for tag in args.tag:
                        if tag not in tags:
                            tags.append(tag)
                normalized = normalize_trace(trace)
                case_id = case_id_for_trace(normalized)
                payload = normalized
                report = None
                if redaction is not None:
                    payload, report = redact_trace(normalized, redaction)
                    redactions += report.total
                stored = store.add_case(
                    case_id,
                    payload,
                    source_path=str(path),
                    redaction=report,
                )
                if stored:
                    imported += 1
                else:
                    duplicates += 1
                if args.label:
                    store.add_label(
                        case_id,
                        args.label,
                        category=args.category,
                        notes=args.notes,
                    )
                records.append({"case_id": case_id, "stored": stored, "source": str(path)})
        total = store.count_cases()

    if args.json:
        _print(
            json.dumps(
                {
                    "imported": imported,
                    "duplicates": duplicates,
                    "redactions": redactions,
                    "total_cases": total,
                    "cases": records,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        _print(
            f"imported {imported} case(s), {duplicates} duplicate(s), "
            f"{redactions} redaction(s); store now has {total} case(s)"
        )
    return EXIT_OK


def _cmd_list(args: argparse.Namespace, config: Config) -> int:
    with _open_store(args, config) as store:
        cases = store.list_cases(tags=args.tag or None)
        if args.limit is not None:
            cases = cases[: args.limit]
        rows = [
            {
                "case_id": case["case_id"],
                "tags": case["tags"],
                "label": (case.get("label") or {}).get("label"),
                "source": case["source_path"],
                "trace_id": case["trace_id"],
            }
            for case in cases
        ]
    if args.json:
        _print(json.dumps(rows, ensure_ascii=False, indent=2))
        return EXIT_OK
    if not rows:
        _print("no cases stored; run trace-to-eval import <file>")
        return EXIT_OK
    width = max(len(row["case_id"]) for row in rows)
    for row in rows:
        label = row["label"] or "-"
        tags = ",".join(row["tags"]) or "-"
        _print(f"{row['case_id']:<{width}}  {label:<8}  {tags}")
    _print(f"\n{len(rows)} case(s)")
    return EXIT_OK


def _cmd_label(args: argparse.Namespace, config: Config) -> int:
    with _open_store(args, config) as store:
        try:
            store.add_label(args.case_id, args.label, category=args.category, notes=args.notes)
        except KeyError as exc:
            raise _UsageError(str(exc.args[0])) from None
        labels = store.list_labels(args.case_id)
    if args.json:
        _print(json.dumps(labels, ensure_ascii=False, indent=2))
    else:
        _print(f"labeled {args.case_id} as {args.label} ({len(labels)} label record(s))")
    return EXIT_OK


def _cmd_run(args: argparse.Namespace, config: Config) -> int:
    thresholds = _parse_thresholds(args, config)
    rubric_model = getattr(args, "rubric_model", None) or config.rubric_model
    run_config = RunConfig(
        provider=(getattr(args, "provider", None) or config.provider).lower(),
        model=getattr(args, "model", None) or config.model,
        prompt_version=getattr(args, "prompt_version", None) or config.prompt_version,
        code_revision=getattr(args, "code_revision", None)
        or config.code_revision
        or os.environ.get("GITHUB_SHA", "unknown"),
        timeout_s=_budget_limits(args, config).request_timeout_s,
        baseline_name=getattr(args, "baseline", None),
        rubric=bool(getattr(args, "rubric", False) or config.rubric),
        rubric_model=rubric_model,
        max_cases=getattr(args, "max_cases", None),
        tags=list(getattr(args, "tag", None) or []),
        case_ids=list(getattr(args, "case", None) or []),
        thresholds=thresholds.as_dict(),
    )

    with _open_store(args, config) as store:
        if store.count_cases() == 0:
            raise _UsageError("no cases stored; run trace-to-eval import <file> first")
        provider, tracker = _build_provider(args, config, store)
        judge = RubricJudge(provider) if run_config.rubric else None
        try:
            summary = run_replay(
                store,
                provider,
                run_config,
                rubric_judge=judge,
            )
        except BudgetExceeded as exc:
            _warn(f"replay stopped by budget: {exc}")
            latest = store.latest_run()
            if latest is None:
                raise
            summary = reconstruct_summary(store, latest["run_id"])
            gate_report = evaluate_gates(summary, thresholds, baseline=None)
            _emit_report(
                summary,
                gate_report,
                fmt=args.format,
                out=args.out,
                unicode=not args.ascii,
                color=args.color,
            )
            if tracker is not None:
                _warn(f"usage: {json.dumps(tracker.summary(), sort_keys=True)}")
            return EXIT_INFRASTRUCTURE

        if getattr(args, "save_baseline", None):
            store.save_baseline(args.save_baseline, summary.run_id, summary.metrics_for_baseline())
            _print(f"saved baseline {args.save_baseline!r} at run {summary.run_id}")

        baseline = _baseline_snapshot(store, args.baseline)
        if args.baseline and baseline is None:
            raise _UsageError(f"unknown baseline: {args.baseline}")
        gate_report = evaluate_gates(summary, thresholds, baseline=baseline)
        _emit_report(
            summary,
            gate_report,
            fmt=args.format,
            out=args.out,
            unicode=not args.ascii,
            color=args.color,
        )
        if tracker is not None:
            _warn(f"usage: {json.dumps(tracker.summary(), sort_keys=True)}")
        return exit_code_for(summary, gate_report)


def _cmd_report(args: argparse.Namespace, config: Config) -> int:
    with _open_store(args, config) as store:
        run_id = args.run_id
        if run_id in {"latest", ""}:
            latest = store.latest_run()
            if latest is None:
                raise _UsageError("no runs stored yet")
            run_id = latest["run_id"]
        row = store.get_run(run_id)
        if row is None:
            raise _UsageError(f"unknown run: {run_id}")
        stored = (row.get("config") or {}).get("thresholds") or {}
        thresholds = _parse_thresholds(
            args, config, Thresholds.from_mapping(stored) if stored else None
        )
        summary = reconstruct_summary(store, run_id)
        baseline_name = args.baseline or summary.baseline_name
        baseline = _baseline_snapshot(store, baseline_name)
        if baseline_name and baseline is None:
            raise _UsageError(f"unknown baseline: {baseline_name}")
        gate_report = evaluate_gates(summary, thresholds, baseline=baseline)
        _emit_report(
            summary,
            gate_report,
            fmt=args.format,
            out=args.out,
            unicode=not args.ascii,
            color=args.color,
        )
        return exit_code_for(summary, gate_report)


def _cmd_export(args: argparse.Namespace, config: Config) -> int:
    with _open_store(args, config) as store:
        run_id = args.run_id
        if run_id in {"latest", ""}:
            latest = store.latest_run()
            if latest is None:
                raise _UsageError("no runs stored yet")
            run_id = latest["run_id"]
        try:
            summary = reconstruct_summary(store, run_id)
        except KeyError:
            raise _UsageError(f"unknown run: {run_id}") from None
        payload = summary.as_dict(include_results=True)
        row = store.get_run(run_id) or {}
        stored = (row.get("config") or {}).get("thresholds") or {}
        payload["exit_code"] = exit_code_for(
            summary,
            evaluate_gates(
                summary,
                Thresholds.from_mapping(stored) if stored else Thresholds(),
                baseline=_baseline_snapshot(store, summary.baseline_name),
            ),
        )
        content = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
        if args.out:
            write_report(args.out, content)
            _print(f"exported run {run_id} to {args.out}")
        else:
            _print(content)
    return EXIT_OK


def _cmd_schema(args: argparse.Namespace, config: Config) -> int:
    _print(json.dumps(load_json_schema(), ensure_ascii=False, indent=2, sort_keys=True))
    return EXIT_OK


def _cmd_validate(args: argparse.Namespace, config: Config) -> int:
    path = Path(args.path).expanduser()
    if not path.is_file():
        raise _UsageError(f"file not found: {path}")
    failures = 0
    total = 0
    for line_number, line in _iter_jsonl(path):
        if not line.strip():
            continue
        total += 1
        _trace, errors = parse_trace_line(line)
        if errors:
            failures += 1
            _print(f"{path}:{line_number}: invalid")
            for error in errors[:5]:
                _print(f"  {error}")
    _print(f"{total - failures}/{total} valid")
    return EXIT_OK if failures == 0 else EXIT_QUALITY


def _cmd_budget(args: argparse.Namespace, config: Config) -> int:
    with _open_store(args, config) as store:
        today = store.ledger_spend_since(day_start())
        month = store.ledger_spend_since(month_start())
        entries = store.ledger_entries(limit=args.limit)
    payload = {
        "spend_today_usd": round(today, 6),
        "spend_this_month_usd": round(month, 6),
        "limits": config.budget.as_dict(),
        "recent_entries": entries,
    }
    if args.json:
        _print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return EXIT_OK
    _print(f"spend today:  ${today:.6f}")
    _print(f"spend month:  ${month:.6f}")
    _print(f"limits:       {json.dumps(config.budget.as_dict(), sort_keys=True)}")
    if entries:
        _print("")
        _print("recent ledger entries")
        for entry in entries:
            status = "ok" if entry["success"] else f"error:{entry['error_class'] or 'unknown'}"
            _print(
                f"  {entry['started_at']}  {entry['workflow']:<8} "
                f"{entry['provider']}/{entry['model']}  "
                f"${entry['estimated_cost_usd']:.6f}  {status}"
            )
    return EXIT_OK


def _cmd_serve(args: argparse.Namespace, config: Config) -> int:
    from .server import serve as serve_ui

    return serve_ui(
        config,
        _resolve_db(args, config),
        host=args.host,
        port=args.port,
        open_browser=args.open_browser,
        allow_remote=args.allow_remote,
    )


# ---------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="trace-to-eval",
        description="Turn AI traces and production failures into versioned regression tests.",
    )
    parser.add_argument("--version", action="version", version=f"trace-to-eval {__version__}")
    parser.add_argument("--config", help="path to trace-to-eval.toml or pyproject.toml")
    parser.add_argument("--db", help="path to the SQLite case store")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="create the local case store")
    init.set_defaults(func=_cmd_init)

    imp = subparsers.add_parser("import", help="import trace JSON/JSONL files")
    imp.add_argument("paths", nargs="+", help="trace file(s) to import")
    imp.add_argument("--label", choices=["success", "failure", "unknown"])
    imp.add_argument(
        "--category",
        choices=sorted(FAILURE_CATEGORIES),
        help="failure category for the attached label",
    )
    imp.add_argument("--notes", help="label notes")
    imp.add_argument("--tag", action="append", help="tag to apply (repeatable)")
    imp.add_argument("--no-redact", action="store_true", help="skip redaction (discouraged)")
    imp.add_argument("--json", action="store_true", help="emit machine-readable output")
    imp.set_defaults(func=_cmd_import)

    listing = subparsers.add_parser("list", help="list stored cases")
    listing.add_argument("--tag", action="append", help="filter by tag (repeatable)")
    listing.add_argument("--limit", type=int)
    listing.add_argument("--json", action="store_true")
    listing.set_defaults(func=_cmd_list)

    label = subparsers.add_parser("label", help="attach a human label to a case")
    label.add_argument("case_id")
    label.add_argument("--label", required=True, choices=["success", "failure", "unknown"])
    label.add_argument("--category", choices=sorted(FAILURE_CATEGORIES))
    label.add_argument("--notes")
    label.add_argument("--json", action="store_true")
    label.set_defaults(func=_cmd_label)

    run = subparsers.add_parser("run", help="replay cases and evaluate assertions")
    run.add_argument("--provider", help="fake (default), openai-compatible, or anthropic")
    run.add_argument("--model", help="model identifier")
    run.add_argument("--fake-script", help="JSON script describing fake model behavior")
    run.add_argument("--base-url", help="override the provider base URL")
    run.add_argument("--prompt-version", help="prompt version label for this run")
    run.add_argument("--code-revision", help="code revision label for this run")
    run.add_argument("--case", action="append", help="replay only this case (repeatable)")
    run.add_argument("--tag", action="append", help="replay only cases with this tag")
    run.add_argument("--max-cases", type=int)
    run.add_argument("--baseline", help="baseline name used for regression gates")
    run.add_argument("--save-baseline", help="save this run as a named baseline")
    run.add_argument("--rubric", action="store_true", help="enable rubric signal scoring")
    run.add_argument("--rubric-model", help="model used for rubric scoring")
    run.add_argument("--format", choices=_FORMATS, default="terminal")
    run.add_argument("--out", help="write the report to a file instead of stdout")
    run.add_argument("--ascii", action="store_true", help="ASCII-only terminal output")
    run.add_argument("--color", action="store_true", help="ANSI color in terminal output")
    run.add_argument("--allow-live", action="store_true", help="allow live provider calls")
    run.add_argument("--max-requests", type=int, help="hard request cap for live calls")
    run.add_argument("--max-run-cost-usd", type=float, help="hard cost cap for live calls")
    run.add_argument("--max-tokens", type=int, help="hard token cap for live calls")
    run.add_argument("--timeout", type=float, help="per-request timeout in seconds")
    run.add_argument("--min-pass-rate", type=float)
    run.add_argument("--max-total-cost-usd", type=float)
    run.add_argument("--max-avg-cost-usd", type=float)
    run.add_argument("--max-p95-latency-ms", type=float)
    run.add_argument("--max-infrastructure-errors", type=int)
    run.add_argument("--max-signal-failures", type=int)
    run.add_argument("--max-pass-rate-drop", type=float)
    run.add_argument("--max-cost-increase-pct", type=float)
    run.add_argument("--max-latency-increase-pct", type=float)
    run.set_defaults(func=_cmd_run)

    report = subparsers.add_parser("report", help="re-render a stored run")
    report.add_argument("run_id", nargs="?", default="latest")
    report.add_argument("--baseline", help="baseline name used for regression gates")
    report.add_argument("--format", choices=_FORMATS, default="terminal")
    report.add_argument("--out", help="write the report to a file instead of stdout")
    report.add_argument("--ascii", action="store_true")
    report.add_argument("--color", action="store_true")
    report.add_argument("--min-pass-rate", type=float)
    report.add_argument("--max-total-cost-usd", type=float)
    report.add_argument("--max-avg-cost-usd", type=float)
    report.add_argument("--max-p95-latency-ms", type=float)
    report.add_argument("--max-infrastructure-errors", type=int)
    report.add_argument("--max-signal-failures", type=int)
    report.add_argument("--max-pass-rate-drop", type=float)
    report.add_argument("--max-cost-increase-pct", type=float)
    report.add_argument("--max-latency-increase-pct", type=float)
    report.set_defaults(func=_cmd_report)

    export = subparsers.add_parser("export", help="export a run as JSON")
    export.add_argument("run_id")
    export.add_argument("--out", help="write JSON to a file instead of stdout")
    export.set_defaults(func=_cmd_export)

    schema = subparsers.add_parser("schema", help="print the trace JSON Schema")
    schema.set_defaults(func=_cmd_schema)

    validate = subparsers.add_parser("validate", help="validate a trace JSONL file")
    validate.add_argument("path")
    validate.set_defaults(func=_cmd_validate)

    budget = subparsers.add_parser("budget", help="show recorded spend and limits")
    budget.add_argument("--limit", type=int, default=20)
    budget.add_argument("--json", action="store_true")
    budget.set_defaults(func=_cmd_budget)

    serve = subparsers.add_parser("serve", help="run the local trace-to-eval web UI")
    serve.add_argument("--host", default="127.0.0.1", help="bind address (loopback by default)")
    serve.add_argument("--port", type=int, default=8765, help="port (0 chooses a free port)")
    serve.add_argument(
        "--open",
        action="store_true",
        dest="open_browser",
        help="open the UI in the default browser",
    )
    serve.add_argument(
        "--allow-remote",
        action="store_true",
        help="allow binding to a non-loopback address (no authentication)",
    )
    serve.set_defaults(func=_cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        return int(args.func(args, config))
    except _UsageError as exc:
        _warn(str(exc))
        return EXIT_USAGE
    except (ConfigError, LiveCallsDisabled, SchemaValidationError) as exc:
        _warn(str(exc))
        return EXIT_USAGE
    except ProviderError as exc:
        _warn(f"provider failure [{exc.error_class}]: {exc}")
        return EXIT_INFRASTRUCTURE
    except TraceToEvalError as exc:
        _warn(str(exc))
        return EXIT_USAGE
    except KeyboardInterrupt:
        _warn("interrupted")
        return EXIT_INFRASTRUCTURE


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
