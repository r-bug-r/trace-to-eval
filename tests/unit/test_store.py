"""SQLite store: cases, labels, runs, results, baselines, and the ledger."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from trace_to_eval.redact import RedactionReport
from trace_to_eval.schema import case_id_for_trace, normalize_trace
from trace_to_eval.store import CaseStore, ResultRecord


@pytest.fixture
def store(tmp_path: Path) -> Any:
    with CaseStore(tmp_path / "cases.db") as opened:
        yield opened


def _add(store: CaseStore, trace: dict[str, Any], **kwargs: Any) -> tuple[str, bool]:
    normalized = normalize_trace(trace)
    case_id = case_id_for_trace(normalized)
    stored = store.add_case(case_id, normalized, **kwargs)
    return case_id, stored


def test_add_get_and_deduplicate(store: CaseStore, traces: list[dict[str, Any]]) -> None:
    case_id, stored = _add(store, traces[0], source_path="fixtures.jsonl")
    assert stored is True
    assert store.count_cases() == 1

    again, stored_again = _add(store, traces[0])
    assert again == case_id
    assert stored_again is False
    assert store.count_cases() == 1

    fetched = store.get_case(case_id)
    assert fetched is not None
    assert fetched["trace"]["output"]["text"] == traces[0]["output"]["text"]
    assert fetched["source_path"] == "fixtures.jsonl"
    assert store.get_case("case_missing") is None


def test_tag_filter_requires_every_tag(store: CaseStore, traces: list[dict[str, Any]]) -> None:
    for trace in traces:
        _add(store, trace)
    assert len(store.list_cases()) == 5
    assert len(store.list_cases(tags=["support"])) == 1
    assert len(store.list_cases(tags=["support", "refunds"])) == 1
    assert store.list_cases(tags=["support", "tools"]) == []


def test_labels_are_append_only_with_latest_wins(
    store: CaseStore, traces: list[dict[str, Any]]
) -> None:
    case_id, _ = _add(store, traces[0])
    store.add_label(case_id, "unknown")
    store.add_label(case_id, "failure", category="quality", notes="first triage")
    labels = store.list_labels(case_id)
    assert [item["label"] for item in labels] == ["unknown", "failure"]
    latest = store.latest_label(case_id)
    assert latest is not None
    assert latest["category"] == "quality"
    assert store.get_case(case_id)["label"]["label"] == "failure"  # type: ignore[index]


def test_label_unknown_case_is_an_error(store: CaseStore) -> None:
    with pytest.raises(KeyError, match="unknown case"):
        store.add_label("case_missing", "success")


def test_delete_case_cascades_labels_and_results(
    store: CaseStore, traces: list[dict[str, Any]]
) -> None:
    case_id, _ = _add(store, traces[0])
    store.add_label(case_id, "success")
    store.create_run(
        "run_1",
        provider="fake",
        model="m",
        prompt_version="v",
        code_revision="rev",
        baseline_name=None,
        config={"model": "m"},
    )
    store.add_result("run_1", ResultRecord(case_id=case_id, status="pass", output_text="ok"))
    assert store.delete_case(case_id) is True
    assert store.delete_case(case_id) is False
    assert store.list_labels(case_id) == []
    assert store.results_for_run("run_1") == []


def test_delete_run_cascades_results(store: CaseStore, traces: list[dict[str, Any]]) -> None:
    case_id, _ = _add(store, traces[0])
    store.create_run(
        "run_1",
        provider="fake",
        model="m",
        prompt_version="v",
        code_revision="rev",
        baseline_name=None,
        config={"model": "m"},
    )
    store.add_result("run_1", ResultRecord(case_id=case_id, status="pass", output_text="ok"))
    assert store.delete_run("run_1") is True
    assert store.delete_run("run_1") is False
    assert store.get_run("run_1") is None
    assert store.results_for_run("run_1") == []


def test_run_lifecycle_and_result_roundtrip(store: CaseStore, traces: list[dict[str, Any]]) -> None:
    case_id, _ = _add(store, traces[0])
    store.create_run(
        "run_1",
        provider="fake",
        model="fake-baseline-v1",
        prompt_version="prompt-1",
        code_revision="abc123",
        baseline_name="base",
        config={"model": "fake-baseline-v1", "tags": ["support"]},
    )
    store.add_result(
        "run_1",
        ResultRecord(
            case_id=case_id,
            status="fail",
            output_text="bad",
            output_json={"text": "bad"},
            assertions=[{"name": "text_contains", "kind": "deterministic", "status": "fail"}],
            cost_usd=0.002,
            latency_ms=12.0,
            human_label="failure",
            label_agreement=False,
            rubric={"score": 0.2},
        ),
    )
    store.finish_run("run_1", "quality_regression", {"verdict": "quality_regression"})

    run = store.get_run("run_1")
    assert run is not None
    assert run["status"] == "quality_regression"
    assert run["config"]["tags"] == ["support"]
    assert run["summary"] == {"verdict": "quality_regression"}
    assert store.latest_run()["run_id"] == "run_1"  # type: ignore[index]

    results = store.results_for_run("run_1")
    assert results[0]["label_agreement"] is False
    assert results[0]["rubric"] == {"score": 0.2}
    assert results[0]["assertions"][0]["name"] == "text_contains"


def test_baseline_upsert_replaces_previous(store: CaseStore) -> None:
    for run_id in ("run_1", "run_2"):
        store.create_run(
            run_id,
            provider="fake",
            model="m",
            prompt_version="v",
            code_revision="rev",
            baseline_name=None,
            config={},
        )
    store.save_baseline("base", "run_1", {"pass_rate": 1.0})
    store.save_baseline("base", "run_2", {"pass_rate": 0.5})
    stored = store.get_baseline("base")
    assert stored is not None
    assert stored["run_id"] == "run_2"
    assert stored["summary"] == {"pass_rate": 0.5}
    assert store.get_baseline("missing") is None


def test_ledger_spend_since(store: CaseStore) -> None:
    for cost in (0.01, 0.02):
        store.add_ledger_entry(
            run_id="run_1",
            workflow="replay",
            provider="fake",
            model="m",
            request_id=None,
            input_tokens=10,
            output_tokens=5,
            estimated_cost_usd=cost,
            cache_hit=False,
            success=True,
            error_class=None,
            started_at="2026-10-20T00:00:00Z",
        )
    assert store.ledger_spend_since("2026-10-19T00:00:00Z") == pytest.approx(0.03)
    assert store.ledger_spend_since("2026-10-21T00:00:00Z") == 0.0
    assert len(store.ledger_entries()) == 2


def test_redaction_report_is_persisted(store: CaseStore, traces: list[dict[str, Any]]) -> None:
    report = RedactionReport()
    report.add("emails", 2)
    case_id, _ = _add(store, traces[0], redaction=report)
    fetched = store.get_case(case_id)
    assert fetched is not None
    assert fetched["redaction"]["total"] == 2
    assert fetched["redaction"]["by_category"] == {"emails": 2}


def test_backup_is_a_readable_copy(
    store: CaseStore, traces: list[dict[str, Any]], tmp_path: Path
) -> None:
    _add(store, traces[0])
    target = store.backup(tmp_path / "backup" / "cases.db")
    assert target.exists()
    connection = sqlite3.connect(target)
    try:
        count = connection.execute("SELECT COUNT(*) FROM cases").fetchone()[0]
    finally:
        connection.close()
    assert count == 1
