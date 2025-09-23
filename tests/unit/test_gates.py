"""Gate evaluation, thresholds, and baseline regression arithmetic."""

from __future__ import annotations

import pytest

from trace_to_eval.errors import ConfigError
from trace_to_eval.gates import BaselineSnapshot, Thresholds, evaluate_gates
from trace_to_eval.runner import RunSummary


def _summary(**overrides: object) -> RunSummary:
    values: dict[str, object] = {
        "run_id": "run_test",
        "started_at": "2026-10-20T00:00:00Z",
        "finished_at": "2026-10-20T00:01:00Z",
        "provider": "fake",
        "model": "fake-baseline-v1",
        "prompt_version": "unversioned",
        "code_revision": "unknown",
        "baseline_name": None,
        "total_cases": 10,
        "passed": 10,
        "failed": 0,
        "infrastructure_errors": 0,
        "signal_failures": 0,
        "label_disagreements": 0,
        "skipped_rubrics": 0,
        "pass_rate": 1.0,
        "total_cost_usd": 1.0,
        "avg_cost_usd": 0.1,
        "p95_latency_ms": 100.0,
        "avg_latency_ms": 50.0,
        "results": [],
    }
    values.update(overrides)
    return RunSummary(**values)  # type: ignore[arg-type]


def _gate_names(report: object) -> set[str]:
    return {gate.name for gate in report.gates}  # type: ignore[attr-defined]


def _failed_gate_names(report: object) -> set[str]:
    return {gate.name for gate in report.failed_gates}  # type: ignore[attr-defined]


def test_default_thresholds_only_gate_infrastructure_errors() -> None:
    report = evaluate_gates(_summary(infrastructure_errors=1), Thresholds())
    assert not report.passed
    assert _gate_names(report) == {"infrastructure_errors"}


def test_min_pass_rate_boundary_is_inclusive() -> None:
    thresholds = Thresholds(min_pass_rate=0.9)
    assert evaluate_gates(_summary(pass_rate=0.9), thresholds).passed
    assert not evaluate_gates(_summary(pass_rate=0.89), thresholds).passed


def test_absolute_cost_and_latency_gates() -> None:
    thresholds = Thresholds(max_total_cost_usd=2.0, max_avg_cost_usd=0.2, max_p95_latency_ms=200)
    assert evaluate_gates(_summary(), thresholds).passed

    over = evaluate_gates(
        _summary(total_cost_usd=2.5, avg_cost_usd=0.25, p95_latency_ms=250), thresholds
    )
    assert _failed_gate_names(over) == {
        "max_total_cost_usd",
        "max_avg_cost_usd",
        "max_p95_latency_ms",
    }


def test_baseline_regression_gates_use_percentages() -> None:
    baseline = BaselineSnapshot(pass_rate=1.0, avg_cost_usd=0.10, p95_latency_ms=100.0)
    thresholds = Thresholds(
        max_pass_rate_drop=0.1, max_cost_increase_pct=10.0, max_latency_increase_pct=10.0
    )
    ok = evaluate_gates(
        _summary(pass_rate=0.9, avg_cost_usd=0.11, p95_latency_ms=110.0),
        thresholds,
        baseline=baseline,
    )
    assert ok.passed

    regressed = evaluate_gates(
        _summary(pass_rate=0.89, avg_cost_usd=0.111, p95_latency_ms=111.0),
        thresholds,
        baseline=baseline,
    )
    assert _failed_gate_names(regressed) == {
        "pass_rate_regression",
        "cost_regression",
        "latency_regression",
    }


def test_zero_baseline_cost_only_regresses_when_current_spend_is_positive() -> None:
    baseline = BaselineSnapshot(pass_rate=1.0, avg_cost_usd=0.0, p95_latency_ms=0.0)
    thresholds = Thresholds(max_cost_increase_pct=10.0)

    free = evaluate_gates(_summary(avg_cost_usd=0.0), thresholds, baseline=baseline)
    assert free.passed

    paid = evaluate_gates(_summary(avg_cost_usd=0.000001), thresholds, baseline=baseline)
    assert not paid.passed
    assert "cost_regression" in _gate_names(paid)


def test_regression_thresholds_without_a_baseline_fail_closed() -> None:
    thresholds = Thresholds(max_cost_increase_pct=1.0, max_latency_increase_pct=1.0)
    report = evaluate_gates(_summary(), thresholds)
    assert not report.passed
    assert _failed_gate_names(report) == {"baseline_available"}


def test_thresholds_reject_invalid_values_and_unknown_fields() -> None:
    with pytest.raises(ConfigError, match="min_pass_rate"):
        Thresholds(min_pass_rate=1.5)
    with pytest.raises(ConfigError, match="non-negative"):
        Thresholds(max_total_cost_usd=-1.0)
    with pytest.raises(ConfigError, match="unknown threshold fields"):
        Thresholds.from_mapping({"min_passrate": 0.5})


def test_baseline_snapshot_rejects_incomplete_metrics() -> None:
    with pytest.raises(ConfigError, match="stored baseline is missing usable metrics"):
        BaselineSnapshot.from_summary({"pass_rate": 1.0})
