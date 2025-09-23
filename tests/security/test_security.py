"""Security-focused checks: untrusted trace content must never reach reports raw."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from trace_to_eval.evaluate import AssertionResult
from trace_to_eval.gates import Thresholds, evaluate_gates
from trace_to_eval.report import render_junit, render_markdown, render_terminal
from trace_to_eval.runner import CaseResult, RunSummary
from trace_to_eval.schema import validate_trace
from trace_to_eval.store import CaseStore
from trace_to_eval.util import safe_text


def _summary(output_text: str, *, error_message: str | None = None) -> RunSummary:
    result = CaseResult(
        case_id="case_hostile",
        status="infrastructure_error" if error_message else "fail",
        assertions=[
            AssertionResult(
                name="text_contains",
                kind="deterministic",
                status="fail",
                message="output is missing required text",
            )
        ],
        output={"text": output_text},
        cost_usd=0.0,
        latency_ms=1.0,
        error_class="timeout" if error_message else None,
        error_message=error_message,
    )
    return RunSummary(
        run_id="run_hostile",
        started_at="2026-10-20T00:00:00Z",
        finished_at="2026-10-20T00:00:01Z",
        provider="fake",
        model="fake-baseline-v1",
        prompt_version="unversioned",
        code_revision="unknown",
        baseline_name=None,
        total_cases=1,
        passed=0,
        failed=0 if error_message else 1,
        infrastructure_errors=1 if error_message else 0,
        signal_failures=0,
        label_disagreements=0,
        skipped_rubrics=0,
        pass_rate=0.0,
        total_cost_usd=0.0,
        avg_cost_usd=0.0,
        p95_latency_ms=1.0,
        avg_latency_ms=1.0,
        results=[result],
    )


def test_safe_text_strips_ansi_controls_and_bidi_overrides() -> None:
    hostile = "\x1b[31mred\x1b[0m\x00\x07\u202eevil\u2066 text"
    cleaned = safe_text(hostile)
    assert "\x1b" not in cleaned
    assert "\x00" not in cleaned
    assert "\u202e" not in cleaned
    assert "\u2066" not in cleaned
    assert "red" in cleaned
    assert "evil" in cleaned


def test_safe_text_truncates_with_an_ellipsis() -> None:
    cleaned = safe_text("a" * 100, max_length=10)
    assert len(cleaned) == 10
    assert cleaned.endswith("\u2026")


def test_terminal_report_contains_no_escape_sequences() -> None:
    text = render_terminal(
        _summary("\x1b[2J\x1b[31mowned\x1b[0m"),
        evaluate_gates(_summary("x"), Thresholds()),
        unicode=False,
        color=False,
    )
    assert "\x1b[2J" not in text
    assert "\x1b[31m" not in text


def test_markdown_report_escapes_table_breakers() -> None:
    summary = _summary("line one\n| injected | row |\n`code`")
    summary.results[0].assertions[0].message = "bad | `pipe` <tag>"
    text = render_markdown(summary, evaluate_gates(summary, Thresholds()))
    for line in text.splitlines():
        if "injected" in line:
            assert line.count("|") <= 5
    assert "\\|" in text
    assert "\\<tag\\>" in text


def test_junit_report_is_well_formed_for_hostile_text() -> None:
    hostile = ']]></failure><evil attr="1"/><![CDATA['
    summary = _summary(hostile, error_message="\x00boom <script>alert(1)</script>")
    rendered = render_junit(summary, evaluate_gates(summary, Thresholds()))
    root = ET.fromstring(rendered)  # noqa: S314 - parses our own output
    assert root.findall(".//evil") == []
    assert root.find("testcase/error") is not None
    assert "alert(1)" in (root.find("testcase/error").text or "")


def test_validation_errors_do_not_echo_secret_values() -> None:
    trace = {
        "schema_version": "1.0",
        "input": {"messages": [{"role": "user", "content": "hi"}]},
        "output": {"text": "ok"},
        "model": {"name": "fake-baseline-v1"},
        "source": "sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        "outcome": {"status": "sk-proj-abcdefghijklmnopqrstuvwxyz012345"},
    }
    errors = validate_trace(trace)
    assert errors
    joined = "\n".join(errors)
    assert "sk-proj-" not in joined


def test_secret_is_not_persisted_in_the_store_file(tmp_path: Path) -> None:
    db_path = tmp_path / "cases.db"
    secret = "sk-proj-abcdefghijklmnopqrstuvwxyz012345"
    trace = {
        "schema_version": "1.0",
        "input": {"messages": [{"role": "user", "content": f"key {secret}"}]},
        "output": {"text": "stored"},
        "model": {"name": "fake-baseline-v1"},
    }
    from trace_to_eval.redact import redact_trace
    from trace_to_eval.schema import case_id_for_trace, normalize_trace

    normalized = normalize_trace(trace)
    redacted, _ = redact_trace(normalized)
    with CaseStore(db_path) as store:
        store.add_case(case_id_for_trace(normalized), redacted)
    raw = db_path.read_bytes()
    assert secret.encode() not in raw
    assert b"[REDACTED:api_key]" in raw


def test_json_report_has_no_control_characters() -> None:
    summary = _summary("ok\x00\x1b[31m")
    from trace_to_eval.report import render_json

    payload = render_json(summary, evaluate_gates(summary, Thresholds()))
    decoded = json.loads(payload)
    serialized = json.dumps(decoded)
    assert "\x1b" not in serialized
    assert "\x00" not in serialized
