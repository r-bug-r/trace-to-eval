"""End-to-end CLI behavior: import, replay, gates, reports, and exit codes."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

from trace_to_eval.cli import main
from trace_to_eval.schema import case_id_for_trace, normalize_trace
from trace_to_eval.store import CaseStore


def _run(*args: str) -> int:
    return main(list(args))


@pytest.fixture
def workspace(tmp_path: Path, fixtures_dir: Path) -> dict[str, Any]:
    return {
        "db": str(tmp_path / "cases.db"),
        "traces": str(fixtures_dir / "traces.jsonl"),
        "script": str(fixtures_dir / "fake-script.json"),
        "out": tmp_path,
    }


def test_full_baseline_then_regression_workflow(workspace: dict[str, Any]) -> None:
    db = workspace["db"]
    assert _run("--db", db, "init") == 0
    assert _run("--db", db, "import", workspace["traces"], "--tag", "fixture") == 0

    baseline_report = workspace["out"] / "baseline.json"
    code = _run(
        "--db",
        db,
        "run",
        "--model",
        "fake-baseline-v1",
        "--fake-script",
        workspace["script"],
        "--save-baseline",
        "base",
        "--min-pass-rate",
        "1.0",
        "--format",
        "json",
        "--out",
        str(baseline_report),
    )
    assert code == 0
    baseline = json.loads(baseline_report.read_text("utf-8"))
    assert baseline["verdict"] == "pass"
    assert baseline["totals"]["cases"] == 5
    assert baseline["totals"]["passed"] == 5
    assert baseline["totals"]["failed"] == 0
    assert baseline["totals"]["infrastructure_errors"] == 0
    assert baseline["totals"]["signal_failures"] == 0
    assert baseline["totals"]["pass_rate"] == 1.0

    regressed_report = workspace["out"] / "regressed.json"
    code = _run(
        "--db",
        db,
        "run",
        "--model",
        "fake-regressed-v2",
        "--fake-script",
        workspace["script"],
        "--baseline",
        "base",
        "--min-pass-rate",
        "1.0",
        "--max-cost-increase-pct",
        "10",
        "--max-latency-increase-pct",
        "10",
        "--format",
        "json",
        "--out",
        str(regressed_report),
    )
    assert code == 1
    regressed = json.loads(regressed_report.read_text("utf-8"))
    assert regressed["verdict"] == "quality_regression"
    assert regressed["exit_code"] == 1
    gate_names = {gate["name"] for gate in regressed["gates"]["gates"] if not gate["passed"]}
    assert gate_names == {
        "case_failures",
        "cost_regression",
        "latency_regression",
        "min_pass_rate",
    }
    failed_case = next(item for item in regressed["results"] if item["status"] == "fail")
    assert failed_case["assertions"]

    junit = workspace["out"] / "report.xml"
    assert _run("--db", db, "report", "latest", "--format", "junit", "--out", str(junit)) == 1
    root = ET.parse(junit).getroot()  # noqa: S314 - parses our own output
    assert root.tag == "testsuite"
    assert int(root.attrib["tests"]) == 5
    assert int(root.attrib["failures"]) >= 1

    markdown = workspace["out"] / "report.md"
    assert _run("--db", db, "report", "latest", "--format", "markdown", "--out", str(markdown)) == 1
    text = markdown.read_text("utf-8")
    assert "quality_regression" in text
    assert "cost_regression" in text

    export = workspace["out"] / "export.json"
    assert _run("--db", db, "export", "latest", "--out", str(export)) == 0
    exported = json.loads(export.read_text("utf-8"))
    assert exported["exit_code"] == 1
    assert len(exported["results"]) == 5


def test_infrastructure_failure_exits_2(workspace: dict[str, Any]) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    _run("--db", db, "import", workspace["traces"])
    report = workspace["out"] / "infra.json"
    code = _run(
        "--db",
        db,
        "run",
        "--model",
        "fake-broken-v1",
        "--fake-script",
        workspace["script"],
        "--format",
        "json",
        "--out",
        str(report),
    )
    assert code == 2
    payload = json.loads(report.read_text("utf-8"))
    assert payload["verdict"] == "infrastructure_failure"
    assert payload["totals"]["infrastructure_errors"] == 4


def test_usage_errors_exit_3(workspace: dict[str, Any]) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    _run("--db", db, "import", workspace["traces"])

    assert _run("--db", db, "run", "--tag", "no-such-tag") == 3
    assert _run("--db", db, "run", "--case", "case_missing") == 3
    assert _run("--db", db, "run", "--baseline", "missing") == 3
    assert _run("--db", db, "report", "run_missing") == 3
    assert _run("--db", db, "import", str(workspace["out"] / "nope.jsonl")) == 3
    assert _run("--db", db, "run", "--provider", "openai", "--model", "gpt-4o-mini") == 3
    assert _run("--db", db, "run", "--provider", "nope") == 3


def test_import_is_idempotent_and_reports_duplicates(
    workspace: dict[str, Any], capsys: Any
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    capsys.readouterr()
    assert _run("--db", db, "import", workspace["traces"], "--json") == 0
    first = json.loads(capsys.readouterr().out)
    assert first["imported"] == 5
    assert first["duplicates"] == 0

    assert _run("--db", db, "import", workspace["traces"], "--json") == 0
    second = json.loads(capsys.readouterr().out)
    assert second["imported"] == 0
    assert second["duplicates"] == 5
    assert second["total_cases"] == 5


def test_invalid_trace_file_is_rejected_without_partial_import(
    workspace: dict[str, Any], tmp_path: Path
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"schema_version": "1.0", "surprise": true}\n', encoding="utf-8")
    assert _run("--db", db, "import", str(bad)) == 3
    with CaseStore(db) as store:
        assert store.count_cases() == 0


def test_oversized_trace_file_is_rejected(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    monkeypatch.setattr("trace_to_eval.cli.MAX_TRACE_FILE_BYTES", 10)
    big = tmp_path / "big.jsonl"
    big.write_text('{"schema_version": "1.0"}\n', encoding="utf-8")
    assert _run("--db", db, "import", str(big)) == 3
    with CaseStore(db) as store:
        assert store.count_cases() == 0


def test_oversized_trace_line_is_rejected(
    workspace: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    monkeypatch.setattr("trace_to_eval.cli.MAX_TRACE_LINE_CHARS", 10)
    long_line = tmp_path / "long.jsonl"
    long_line.write_text('{"schema_version": "1.0"}\n', encoding="utf-8")
    assert _run("--db", db, "import", str(long_line)) == 3
    with CaseStore(db) as store:
        assert store.count_cases() == 0


def test_import_redacts_before_storage(workspace: dict[str, Any], tmp_path: Path) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    secret = tmp_path / "secret.jsonl"
    trace = {
        "schema_version": "1.0",
        "input": {
            "messages": [
                {"role": "user", "content": "my key is sk-proj-abcdefghijklmnopqrstuvwxyz012345"}
            ]
        },
        "output": {"text": "contact alice@example.com"},
        "model": {"name": "fake-baseline-v1"},
    }
    secret.write_text(json.dumps(trace) + "\n", encoding="utf-8")
    assert _run("--db", db, "import", str(secret), "--json") == 0

    with CaseStore(db) as store:
        stored = store.list_cases()[0]
        serialized = json.dumps(stored["trace"])
        assert "sk-proj-" not in serialized
        assert "alice@example.com" not in serialized
        assert stored["redaction"]["total"] == 2


def test_label_command_appends_and_reports_unknown_cases(
    workspace: dict[str, Any], capsys: Any
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    _run("--db", db, "import", workspace["traces"])
    with CaseStore(db) as store:
        case_id = store.list_cases()[0]["case_id"]
    capsys.readouterr()

    assert _run("--db", db, "label", case_id, "--label", "failure", "--category", "quality") == 0
    assert _run("--db", db, "label", case_id, "--label", "success") == 0
    assert _run("--db", db, "label", "case_missing", "--label", "success") == 3
    with pytest.raises(SystemExit) as rejected:
        _run("--db", db, "label", case_id, "--label", "failure", "--category", "wrong-tool")
    assert rejected.value.code == 2

    with CaseStore(db) as store:
        labels = store.list_labels(case_id)
    assert [item["label"] for item in labels] == ["failure", "success"]


def test_validate_command_returns_quality_exit_for_invalid_lines(
    workspace: dict[str, Any], tmp_path: Path
) -> None:
    good = workspace["traces"]
    assert _run("validate", good) == 0

    bad = tmp_path / "mixed.jsonl"
    bad.write_text('{"schema_version": "1.0"}\nnot json\n', encoding="utf-8")
    assert _run("validate", str(bad)) == 1


def test_schema_command_prints_the_versioned_document(capsys: Any) -> None:
    assert _run("schema") == 0
    document = json.loads(capsys.readouterr().out)
    assert document["properties"]["schema_version"]["const"] == "1.0"


def test_budget_command_reports_zero_before_any_live_call(
    workspace: dict[str, Any], capsys: Any
) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    capsys.readouterr()
    assert _run("--db", db, "budget", "--json") == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["spend_today_usd"] == 0.0
    assert payload["recent_entries"] == []


def test_case_id_is_stable_across_imports(workspace: dict[str, Any], fixtures_dir: Path) -> None:
    db = workspace["db"]
    _run("--db", db, "init")
    _run("--db", db, "import", workspace["traces"])
    lines = (fixtures_dir / "traces.jsonl").read_text("utf-8").splitlines()
    expected = {case_id_for_trace(normalize_trace(json.loads(line))) for line in lines if line}
    with CaseStore(db) as store:
        assert {case["case_id"] for case in store.list_cases()} == expected
