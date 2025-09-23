"""HTTP behavior of the local UI server.

The server is the only component that can mutate the case store from a browser, so
these tests exercise the real HTTP surface: token enforcement, Host checks, asset
delivery, validation errors, and the fake-provider run job. Everything runs against
a temporary database on an ephemeral loopback port.
"""

from __future__ import annotations

import http.client
import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest

from trace_to_eval.config import Config
from trace_to_eval.schema import case_id_for_trace, normalize_trace
from trace_to_eval.server import create_server
from trace_to_eval.store import CaseStore


class Client:
    """Minimal HTTP client bound to the running test server."""

    def __init__(self, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
        content_type: str = "application/json",
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        payload = None if body is None else json.dumps(body).encode("utf-8")
        merged = dict(headers or {})
        if body is not None:
            merged.setdefault("Content-Type", content_type)
        if authenticated:
            merged.setdefault("X-Trace-To-Eval-Token", self.token)
        try:
            connection.request(method, path, body=payload, headers=merged)
            response = connection.getresponse()
            raw = response.read()
            return (
                response.status,
                {key.lower(): value for key, value in response.getheaders()},
                raw,
            )
        finally:
            connection.close()

    def json(self, method: str, path: str, **kwargs: Any) -> tuple[int, Any]:
        status, _, raw = self.request(method, path, **kwargs)
        return status, (json.loads(raw) if raw else None)


@pytest.fixture
def server(tmp_path: Path, fixtures_dir: Path) -> Iterator[Client]:
    db = str(tmp_path / "cases.db")
    config = Config(db=db, fake_script=str(fixtures_dir / "fake-script.json"))
    with CaseStore(db) as store:
        for line in (fixtures_dir / "traces.jsonl").read_text("utf-8").splitlines():
            if not line.strip():
                continue
            trace = normalize_trace(json.loads(line))
            store.add_case(case_id_for_trace(trace), trace, source_path="traces.jsonl")
    instance = create_server(config, db, host="127.0.0.1", port=0)
    _, port = instance.server_address[:2]
    thread = threading.Thread(target=instance.serve_forever, name="t2e-test-ui", daemon=True)
    thread.start()
    try:
        yield Client("127.0.0.1", int(port), instance.state.token)
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


def _wait_for_job(client: Client, job_id: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, payload = client.json("GET", f"/api/jobs/{job_id}")
        assert status == 200
        job = payload["job"]
        if job["status"] != "running":
            return job
        time.sleep(0.05)
    raise AssertionError("run job did not finish in time")


# --------------------------------------------------------------------- assets
def test_index_injects_token_and_security_headers(server: Client) -> None:
    status, headers, body = server.request("GET", "/", authenticated=False)

    assert status == 200
    assert headers["content-type"].startswith("text/html")
    assert headers["x-frame-options"] == "DENY"
    assert headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]
    text = body.decode("utf-8")
    assert server.token in text
    assert "{{CSRF_TOKEN}}" not in text


def test_assets_are_served_with_correct_types(server: Client) -> None:
    status, headers, body = server.request("GET", "/assets/app.css", authenticated=False)
    assert status == 200
    assert headers["content-type"].startswith("text/css")
    assert b"--" in body

    status, headers, _ = server.request("GET", "/assets/app.js", authenticated=False)
    assert status == 200
    assert headers["content-type"].startswith("text/javascript")

    status, payload = server.json("GET", "/assets/missing.js")
    assert status == 404
    assert payload["error"] == "not found"


def test_unknown_route_returns_json_error(server: Client) -> None:
    status, payload = server.json("GET", "/api/unknown")
    assert status == 404
    assert payload == {"error": "not found"}


# ---------------------------------------------------------------------- guards
def test_host_header_is_checked(server: Client) -> None:
    status, payload = server.json("GET", "/api/health", headers={"Host": "evil.example"})
    assert status == 421
    assert "Host header" in payload["error"]


def test_mutations_require_the_local_token(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, payload = server.json(
        "POST",
        f"/api/cases/{case_id}/label",
        body={"label": "failure"},
        authenticated=False,
    )
    assert status == 403
    assert "token" in payload["error"]

    status, _ = server.json(
        "POST",
        f"/api/cases/{case_id}/label",
        body={"label": "failure"},
        headers={"X-Trace-To-Eval-Token": "wrong-token"},
    )
    assert status == 403


def test_cross_origin_mutation_is_rejected(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, payload = server.json(
        "POST",
        f"/api/cases/{case_id}/label",
        body={"label": "failure"},
        headers={"Origin": "https://evil.example"},
    )
    assert status == 403
    assert "cross-origin" in payload["error"]


def test_mutation_requires_json_content_type(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, _, raw = server.request(
        "POST",
        f"/api/cases/{case_id}/label",
        body={"label": "failure"},
        content_type="text/plain",
    )
    assert status == 415
    assert b"application/json" in raw


def test_malformed_body_is_rejected(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]
    status, payload = server.json(
        "POST",
        f"/api/cases/{case_id}/label",
        body=["not", "an", "object"],  # type: ignore[arg-type]
    )
    assert status == 400
    assert payload["error"] == "request body must be a JSON object"


# ----------------------------------------------------------------------- reads
def test_health_and_settings_describe_the_store(server: Client) -> None:
    status, health = server.json("GET", "/api/health")
    assert status == 200
    assert health["ok"] is True

    status, settings = server.json("GET", "/api/settings")
    assert status == 200
    assert settings["counts"]["cases"] == 5
    assert settings["live"]["ui_enabled"] is False
    assert server.token not in json.dumps(settings)


def test_case_list_supports_search_and_tag_filters(server: Client) -> None:
    status, payload = server.json("GET", "/api/cases")
    assert status == 200
    assert payload["total"] == 5
    assert payload["limit"] == 200

    status, filtered = server.json("GET", "/api/cases?q=support-copilot")
    assert status == 200
    assert filtered["total"] == 1
    assert filtered["cases"][0]["source"]["app"] == "support-copilot"

    status, none = server.json("GET", "/api/cases?tag=does-not-exist")
    assert status == 200
    assert none["total"] == 0

    status, payload = server.json("GET", "/api/cases?limit=2")
    assert status == 200
    assert len(payload["cases"]) == 2
    assert payload["total"] == 5


def test_case_list_rejects_bad_limit(server: Client) -> None:
    status, payload = server.json("GET", "/api/cases?limit=0")
    assert status == 400
    assert "between 1 and" in payload["error"]


def test_unknown_case_and_run_return_404(server: Client) -> None:
    status, payload = server.json("GET", "/api/cases/case_missing")
    assert status == 404
    assert payload["error"] == "unknown case"

    status, payload = server.json("GET", "/api/runs/run_missing")
    assert status == 404
    assert payload["error"] == "unknown run"


def test_case_detail_exposes_trace_assertions_and_categories(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, detail = server.json("GET", f"/api/cases/{case_id}")
    assert status == 200
    assert detail["case"]["case_id"] == case_id
    assert detail["trace"]["trace_id"] == detail["case"]["trace_id"]
    assert detail["failure_categories"] == sorted(detail["failure_categories"])
    assert detail["history"] == []
    assert detail["run"] is None

    types = {item["type"] for item in detail["editable_assertions"]}
    assert types <= {"text_equals", "text_contains", "text_not_contains"}


# ------------------------------------------------------------------- mutations
def test_label_update_round_trips_through_the_store(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, payload = server.json(
        "POST",
        f"/api/cases/{case_id}/label",
        body={"label": "failure", "category": "quality", "notes": "missing citation"},
    )
    assert status == 200
    assert payload["case"]["label"]["label"] == "failure"

    status, detail = server.json("GET", f"/api/cases/{case_id}")
    assert status == 200
    assert detail["labels"][-1]["category"] == "quality"
    assert detail["labels"][-1]["notes"] == "missing citation"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"label": "maybe"}, "label must be"),
        ({"label": "failure", "category": "not-a-category"}, "category must be"),
        ({"label": "failure", "notes": "x" * 2001}, "at most 2000 characters"),
        ({"label": "failure", "notes": 5}, "notes must be a string"),
    ],
)
def test_label_validation_rejects_bad_input(
    server: Client,
    body: dict[str, Any],
    message: str,
) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]
    status, payload = server.json("POST", f"/api/cases/{case_id}/label", body=body)
    assert status == 400
    assert message in payload["error"]


def test_label_update_rejects_unknown_case(server: Client) -> None:
    status, payload = server.json(
        "POST", "/api/cases/case_missing/label", body={"label": "success"}
    )
    assert status == 404
    assert payload["error"] == "unknown case"


def test_assertion_update_changes_the_stored_trace(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, payload = server.json(
        "POST",
        f"/api/cases/{case_id}/assertions",
        body={
            "assertions": [
                {"type": "text_equals", "value": "Refunds are available within 30 days."},
                {"type": "text_contains", "value": "30 days"},
            ]
        },
    )
    assert status == 200
    assert payload["editable_assertions"] == [
        {"type": "text_equals", "value": "Refunds are available within 30 days."},
        {"type": "text_contains", "value": "30 days"},
    ]

    status, detail = server.json("GET", f"/api/cases/{case_id}")
    assert status == 200
    expectations = detail["trace"]["expectations"]
    assert expectations["text_equals"] == "Refunds are available within 30 days."
    assert expectations["text_contains"] == ["30 days"]


def test_assertion_update_requires_the_field(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]
    status, payload = server.json("POST", f"/api/cases/{case_id}/assertions", body={})
    assert status == 400
    assert payload["error"] == "assertions is required"


def test_delete_case_removes_it_from_the_list(server: Client) -> None:
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]

    status, payload = server.json("DELETE", f"/api/cases/{case_id}")
    assert status == 200
    assert payload == {"deleted": True, "case_id": case_id}

    status, _ = server.json("GET", f"/api/cases/{case_id}")
    assert status == 404
    assert server.json("GET", "/api/cases")[1]["total"] == 4

    status, payload = server.json("DELETE", f"/api/cases/{case_id}")
    assert status == 404


# ------------------------------------------------------------------------ runs
def test_run_rejects_non_fake_provider(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"provider": "openai"})
    assert status == 400
    assert "fake-provider" in payload["error"]


def test_run_rejects_unknown_fields(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"model": "fake-expressive-v1"})
    assert status == 400
    assert "unknown run fields: model" in payload["error"]


def test_run_rejects_unknown_baseline_and_filters(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"baseline": "missing"})
    assert status == 400
    assert "unknown baseline" in payload["error"]

    status, payload = server.json("POST", "/api/run", body={"case_ids": ["case_missing"]})
    assert status == 400
    assert "unknown case id" in payload["error"]


def test_run_completes_and_appears_in_the_run_list(server: Client) -> None:
    status, payload = server.json(
        "POST",
        "/api/run",
        body={"save_baseline": "base", "thresholds": {"min_pass_rate": 0.0}},
    )
    assert status == 202
    job = _wait_for_job(server, payload["job"]["id"])
    assert job["status"] == "completed", job["error"]
    assert job["done"] == job["total"] == 5
    run_id = job["run_id"]
    assert isinstance(run_id, str)

    status, runs = server.json("GET", "/api/runs")
    assert status == 200
    assert runs["runs"][0]["run_id"] == run_id
    assert runs["runs"][0]["baseline"] is None

    status, detail = server.json("GET", f"/api/runs/{run_id}")
    assert status == 200
    assert detail["run"]["status"] == "completed"
    assert len(detail["results"]) == 5
    assert detail["baseline"] is None
    assert detail["gate"]["passed"] is True
    assert detail["deltas"] == {
        "available": False,
        "pass_rate": None,
        "cost_pct": None,
        "latency_pct": None,
    }
    assert all(entry["new_case"] is True for entry in detail["comparison"])

    status, baselines = server.json("GET", "/api/baselines")
    assert status == 200
    assert [item["name"] for item in baselines["baselines"]] == ["base"]


def test_second_run_reports_a_regression_against_the_baseline(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"save_baseline": "base"})
    assert status == 202
    first = _wait_for_job(server, payload["job"]["id"])
    assert first["status"] == "completed", first["error"]

    # Tighten one assertion so the unchanged fake output now fails: the second run must
    # report that case as changed against the saved baseline.
    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]
    status, _ = server.json(
        "POST",
        f"/api/cases/{case_id}/assertions",
        body={"assertions": [{"type": "text_contains", "value": "text this output lacks"}]},
    )
    assert status == 200

    status, payload = server.json("POST", "/api/run", body={"baseline": "base"})
    assert status == 202
    job = _wait_for_job(server, payload["job"]["id"])
    assert job["status"] == "completed", job["error"]

    status, detail = server.json("GET", f"/api/runs/{job['run_id']}?baseline=base")
    assert status == 200
    assert detail["deltas"]["available"] is True
    assert detail["deltas"]["pass_rate"] < 0
    assert detail["baseline"]["name"] == "base"
    changed = [entry for entry in detail["comparison"] if entry["changed"]]
    assert [entry["case"]["case_id"] for entry in changed] == [case_id]
    assert changed[0]["divergence"]["field"] == "status"
    assert changed[0]["divergence"]["candidate"] == "fail"
    assert detail["clusters"][0]["case_ids"] == [case_id]


def test_run_requires_cases_in_the_store(server: Client) -> None:
    for case in server.json("GET", "/api/cases")[1]["cases"]:
        assert server.json("DELETE", f"/api/cases/{case['case_id']}")[0] == 200

    status, payload = server.json("POST", "/api/run", body={})
    assert status == 409
    assert "no cases stored" in payload["error"]


def test_unknown_job_returns_404(server: Client) -> None:
    status, payload = server.json("GET", "/api/jobs/job_missing")
    assert status == 404
    assert payload["error"] == "unknown job"


def test_case_detail_links_to_a_selected_run(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"save_baseline": "base"})
    assert status == 202
    _wait_for_job(server, payload["job"]["id"])

    status, payload = server.json("POST", "/api/run", body={"baseline": "base"})
    assert status == 202
    run_id = _wait_for_job(server, payload["job"]["id"])["run_id"]

    case_id = server.json("GET", "/api/cases")[1]["cases"][0]["case_id"]
    status, detail = server.json("GET", f"/api/cases/{case_id}?run={run_id}")
    assert status == 200
    assert detail["run"]["run_id"] == run_id
    assert detail["result"]["run_id"] == run_id
    assert detail["baseline"]["name"] == "base"
    assert detail["baseline_result"]["run_id"] == detail["baseline"]["run_id"]
    assert detail["divergence"] is None
    assert next(item["run_id"] for item in detail["history"]) == run_id

    status, payload = server.json("GET", f"/api/cases/{case_id}?run=run_missing")
    assert status == 404
    assert payload["error"] == "unknown run"


def test_run_detail_rejects_unknown_baseline_selector(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={})
    assert status == 202
    job = _wait_for_job(server, payload["job"]["id"])

    status, payload = server.json("GET", f"/api/runs/{job['run_id']}?baseline=nope")
    assert status == 404
    assert "unknown baseline or run" in payload["error"]


def test_run_detail_accepts_another_run_as_baseline(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={})
    assert status == 202
    first = _wait_for_job(server, payload["job"]["id"])["run_id"]

    status, payload = server.json("POST", "/api/run", body={})
    assert status == 202
    second = _wait_for_job(server, payload["job"]["id"])["run_id"]

    status, detail = server.json("GET", f"/api/runs/{second}?baseline={first}")
    assert status == 200
    assert detail["baseline"]["run_id"] == first
    assert detail["baseline"]["name"] is None
    assert detail["deltas"]["available"] is True


def test_concurrent_run_is_rejected_while_one_is_active(
    server: Client, monkeypatch: pytest.MonkeyPatch
) -> None:
    from trace_to_eval import server as server_module

    original = server_module._execute_job
    release = threading.Event()

    def slow_execute(*args: Any, **kwargs: Any) -> None:
        release.wait(timeout=10)
        original(*args, **kwargs)

    monkeypatch.setattr(server_module, "_execute_job", slow_execute)
    status, payload = server.json("POST", "/api/run", body={})
    assert status == 202
    job_id = payload["job"]["id"]
    try:
        status, payload = server.json("POST", "/api/run", body={})
        assert status == 409
        assert "already in progress" in payload["error"]
    finally:
        release.set()
    assert _wait_for_job(server, job_id)["status"] == "completed"


def test_response_never_leaks_the_token_in_headers(server: Client) -> None:
    status, headers, _ = server.request("GET", "/api/health", authenticated=False)
    assert status == 200
    assert server.token not in json.dumps(headers)


def test_url_helpers_are_consistent_with_loopback(server: Client) -> None:
    status, payload = server.json("GET", "/api/health")
    assert status == 200
    parsed = urlsplit(f"http://{server.host}:{server.port}/")
    assert parsed.hostname == "127.0.0.1"
    assert Path(payload["store"]).is_file()


# ---------------------------------------------------------------------- CI setup
def test_ci_payload_generates_copyable_config_and_workflow(server: Client) -> None:
    status, payload = server.json("GET", "/api/ci")
    assert status == 200

    assert payload["config_path"] == "trace-to-eval.toml"
    assert "[trace_to_eval.thresholds]" in payload["config_toml"]
    assert payload["workflow_path"] == ".github/workflows/trace-to-eval.yml"
    assert "actions/upload-artifact@v4" in payload["workflow"]
    assert "retention-days: 14" in payload["workflow"]
    assert payload["commands"][0] == "trace-to-eval init"
    assert payload["commands"][-1] == payload["command"]
    assert "--format junit --out trace-to-eval-report.xml" in payload["command"]
    assert payload["baseline"] is None
    assert payload["baselines"] == []


def test_ci_payload_applies_draft_gates_and_baseline(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"save_baseline": "base"})
    assert status == 202
    assert _wait_for_job(server, payload["job"]["id"])["status"] == "completed"

    status, payload = server.json(
        "GET",
        "/api/ci?min_pass_rate=0.85&max_signal_failures=2&max_p95_latency_ms=1500.5",
    )
    assert status == 200
    assert payload["baseline"] == "base"
    assert payload["baselines"] == ["base"]
    assert "--baseline base" in payload["command"]
    assert "--min-pass-rate 0.85" in payload["command"]
    assert "--max-signal-failures 2" in payload["command"]
    assert "--max-p95-latency-ms 1500.5" in payload["command"]
    thresholds = payload["thresholds"]
    assert thresholds["min_pass_rate"] == 0.85
    assert thresholds["max_signal_failures"] == 2
    assert "min_pass_rate = 0.85" in payload["config_toml"]


def test_ci_payload_honours_an_explicit_empty_baseline(server: Client) -> None:
    status, payload = server.json("POST", "/api/run", body={"save_baseline": "base"})
    assert status == 202
    assert _wait_for_job(server, payload["job"]["id"])["status"] == "completed"

    status, payload = server.json("GET", "/api/ci?baseline=")
    assert status == 200
    assert payload["baseline"] is None
    assert "--baseline" not in payload["command"]
    assert payload["baselines"] == ["base"]


def test_ci_payload_rejects_unknown_baseline_and_bad_gates(server: Client) -> None:
    status, payload = server.json("GET", "/api/ci?baseline=missing")
    assert status == 400
    assert "unknown baseline" in payload["error"]

    status, payload = server.json("GET", "/api/ci?min_pass_rate=1.4")
    assert status == 400
    assert "min_pass_rate" in payload["error"]

    status, payload = server.json("GET", "/api/ci?max_signal_failures=lots")
    assert status == 400
    assert "max_signal_failures" in payload["error"]
