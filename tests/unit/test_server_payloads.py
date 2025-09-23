"""Validation and presentation helpers used by the local UI API.

These helpers decide what the UI may store and what it shows. A defect here either
lets an invalid trace into the store or renders a misleading comparison, so each
test pins a user-visible contract rather than an implementation detail.
"""

from __future__ import annotations

import json
import tomllib
from typing import Any

import pytest

from trace_to_eval.config import Config
from trace_to_eval.gates import Thresholds
from trace_to_eval.schema import normalize_trace
from trace_to_eval.server import (
    MAX_ASSERTION_CHARS,
    MAX_ASSERTIONS,
    MAX_LIST_ITEMS,
    MAX_NAME_CHARS,
    _apply_assertions,
    _ci_config_toml,
    _ci_query_thresholds,
    _ci_run_command,
    _ci_workflow,
    _deltas,
    _editable_assertions,
    _failure_clusters,
    _first_divergence,
    _int_query,
    _matches_search,
    _merge_thresholds,
    _optional_name,
    _percent_delta,
    _RequestError,
    _settings_payload,
    _State,
    _string_list,
)
from trace_to_eval.store import CaseStore


def _status(callable_: Any, *args: Any) -> int:
    with pytest.raises(_RequestError) as excinfo:
        callable_(*args)
    return int(excinfo.value.status)


# ------------------------------------------------------------------ assertions
def test_apply_assertions_rewrites_every_assertion_kind(traces: list[dict[str, Any]]) -> None:
    trace = normalize_trace(traces[0])
    updated = _apply_assertions(
        trace,
        [
            {"type": "text_equals", "value": "Refunds are available within 30 days."},
            {"type": "text_contains", "value": "[policy#refunds]"},
            {"type": "text_not_contains", "value": "guaranteed refund"},
        ],
    )

    expectations = updated["expectations"]
    assert expectations["text_equals"] == "Refunds are available within 30 days."
    assert expectations["text_contains"] == ["[policy#refunds]"]
    assert expectations["text_not_contains"] == ["guaranteed refund"]
    assert trace["expectations"] != updated["expectations"], "input trace must not be mutated"


def test_apply_assertions_preserves_unrelated_expectations(traces: list[dict[str, Any]]) -> None:
    trace = normalize_trace(traces[2])
    assert "tool_calls" in trace["expectations"]
    original_tools = json.dumps(trace["expectations"]["tool_calls"], sort_keys=True)

    updated = _apply_assertions(trace, [{"type": "text_contains", "value": "order"}])

    assert json.dumps(updated["expectations"]["tool_calls"], sort_keys=True) == original_tools


def test_apply_assertions_with_empty_list_drops_expectations(
    traces: list[dict[str, Any]],
) -> None:
    trace = normalize_trace(traces[0])
    trace["expectations"] = {"text_equals": "old answer"}
    updated = _apply_assertions(trace, [])

    assert "expectations" not in updated


@pytest.mark.parametrize(
    ("assertions", "message"),
    [
        ("not-a-list", "assertions must be an array"),
        ([{"type": "text_matches", "value": "x"}], "type must be one of"),
        ([{"type": "text_equals", "value": ""}], "non-empty string"),
        ([{"type": "text_equals"}], "non-empty string"),
        ([{"type": "text_equals", "value": "x", "note": "y"}], "unknown fields"),
        (
            [{"type": "text_equals", "value": "a"}, {"type": "text_equals", "value": "b"}],
            "only be set once",
        ),
        (
            [
                {"type": "text_contains", "value": "a"},
                {"type": "text_contains", "value": "a"},
            ],
            "duplicate values",
        ),
    ],
)
def test_apply_assertions_rejects_invalid_input(
    traces: list[dict[str, Any]],
    assertions: Any,
    message: str,
) -> None:
    trace = normalize_trace(traces[0])
    with pytest.raises(_RequestError) as excinfo:
        _apply_assertions(trace, assertions)
    assert excinfo.value.status == 400
    assert message in excinfo.value.message


def test_apply_assertions_enforces_size_limits(traces: list[dict[str, Any]]) -> None:
    trace = normalize_trace(traces[0])
    too_many = [
        {"type": "text_contains", "value": f"needle-{index}"} for index in range(MAX_ASSERTIONS + 1)
    ]
    assert _status(_apply_assertions, trace, too_many) == 400

    too_long = [{"type": "text_contains", "value": "x" * (MAX_ASSERTION_CHARS + 1)}]
    assert _status(_apply_assertions, trace, too_long) == 400


def test_editable_assertions_round_trip_through_apply(traces: list[dict[str, Any]]) -> None:
    trace = normalize_trace(traces[0])
    trace["expectations"] = {
        "text_equals": "answer",
        "text_contains": ["a", "b"],
        "text_not_contains": ["c"],
    }

    editable = _editable_assertions(trace)

    assert editable == [
        {"type": "text_equals", "value": "answer"},
        {"type": "text_contains", "value": "a"},
        {"type": "text_contains", "value": "b"},
        {"type": "text_not_contains", "value": "c"},
    ]
    assert _editable_assertions(_apply_assertions(trace, editable)) == editable


# ------------------------------------------------------------------ query input
def test_int_query_applies_default_and_bounds() -> None:
    def query(raw: str) -> int:
        return _int_query({"limit": [raw]}, "limit", 20, minimum=1, maximum=100)

    assert query("") == 20
    assert query("5") == 5
    for bad in ("0", "101", "abc"):
        assert _status(query, bad) == 400


def test_string_list_strips_and_deduplicates() -> None:
    assert _string_list([" alpha ", "alpha", "beta"], "tag") == ["alpha", "beta"]
    assert _string_list(None, "tag") == []


@pytest.mark.parametrize(
    "value",
    [
        "alpha",
        [""],
        ["   "],
        [1],
        ["x" * 201],
        [f"item-{index}" for index in range(MAX_LIST_ITEMS + 1)],
    ],
)
def test_string_list_rejects_invalid_entries(value: Any) -> None:
    assert _status(_string_list, value, "tag") == 400


def test_optional_name_accepts_safe_identifiers_only() -> None:
    assert _optional_name(None, "baseline") is None
    assert _optional_name("", "baseline") is None
    assert _optional_name("base.v1-nightly_2", "baseline") == "base.v1-nightly_2"
    for bad in ("has space", "a/b", "a\u0000b", "x" * (MAX_NAME_CHARS + 1)):
        assert _status(_optional_name, bad, "baseline") == 400
    assert _status(_optional_name, 3, "baseline") == 400


# ------------------------------------------------------------------ thresholds
def test_merge_thresholds_overrides_only_given_fields() -> None:
    base = Thresholds(min_pass_rate=0.9, max_avg_cost_usd=1.5)
    merged = _merge_thresholds(base, {"min_pass_rate": 1.0})

    assert merged.min_pass_rate == 1.0
    assert merged.max_avg_cost_usd == 1.5


def test_merge_thresholds_returns_base_when_absent() -> None:
    base = Thresholds()
    assert _merge_thresholds(base, None) is base


@pytest.mark.parametrize(
    "value",
    [
        "strict",
        {"unknown_field": 1},
        {"min_pass_rate": "1.0"},
        {"max_infrastructure_errors": True},
        {"min_pass_rate": 2.0},
    ],
)
def test_merge_thresholds_rejects_invalid_overrides(value: Any) -> None:
    assert _status(_merge_thresholds, Thresholds(), value) == 400


# ------------------------------------------------------------------ comparisons
def test_percent_delta_returns_none_for_zero_baseline() -> None:
    assert _percent_delta(5.0, 0.0) is None
    assert _percent_delta(150.0, 100.0) == pytest.approx(50.0)
    assert _percent_delta(50.0, 100.0) == pytest.approx(-50.0)


def test_deltas_report_pass_rate_points_and_percentages() -> None:
    current = {"pass_rate": 0.8, "avg_cost_usd": 0.02, "p95_latency_ms": 300.0}
    baseline = {"pass_rate": 1.0, "avg_cost_usd": 0.01, "p95_latency_ms": 200.0}

    assert _deltas(current, baseline) == {
        "available": True,
        "pass_rate": -0.2,
        "cost_pct": 100.0,
        "latency_pct": 50.0,
    }


@pytest.mark.parametrize(
    ("current", "baseline"),
    [
        (None, {"pass_rate": 1.0, "avg_cost_usd": 0.0, "p95_latency_ms": 0.0}),
        ({"pass_rate": 1.0}, {"pass_rate": 1.0, "avg_cost_usd": 0.0, "p95_latency_ms": 0.0}),
        ({"pass_rate": "x", "avg_cost_usd": 0, "p95_latency_ms": 0}, {}),
    ],
)
def test_deltas_are_unavailable_without_complete_summaries(
    current: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
) -> None:
    result = _deltas(current, baseline)
    assert result == {
        "available": False,
        "pass_rate": None,
        "cost_pct": None,
        "latency_pct": None,
    }


def _result(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "case_id": "case_1",
        "status": "pass",
        "output_json": {"text": "same"},
        "assertions": [],
    }
    result.update(overrides)
    return result


def test_first_divergence_reports_status_then_text_line_then_assertions() -> None:
    assert _first_divergence(_result(), _result()) is None

    status = _first_divergence(_result(), _result(status="fail"))
    assert status == {"field": "status", "baseline": "pass", "candidate": "fail"}

    text = _first_divergence(
        _result(output_json={"text": "line one\nline two"}),
        _result(output_json={"text": "line one\nchanged"}),
    )
    assert text == {
        "field": "output.text",
        "line": 2,
        "baseline": "line two",
        "candidate": "changed",
    }

    assertions = _first_divergence(
        _result(assertions=[{"name": "contains", "status": "pass"}]),
        _result(assertions=[{"name": "contains", "status": "fail"}]),
    )
    assert assertions == {
        "field": "assertions",
        "assertion": "contains",
        "baseline": "pass",
        "candidate": "fail",
    }


def test_first_divergence_is_none_when_a_result_is_missing() -> None:
    assert _first_divergence(None, _result()) is None
    assert _first_divergence(_result(), None) is None


def test_failure_clusters_group_and_order_by_count() -> None:
    results = [
        _result(
            case_id="case_a",
            status="fail",
            assertions=[
                {
                    "kind": "deterministic",
                    "name": "contains_refund",
                    "status": "fail",
                    "message": "missing anchor",
                }
            ],
        ),
        _result(
            case_id="case_b",
            status="fail",
            assertions=[
                {
                    "kind": "deterministic",
                    "name": "contains_refund",
                    "status": "fail",
                    "message": "missing anchor",
                }
            ],
        ),
        _result(
            case_id="case_c",
            status="infrastructure_error",
            error_class="RateLimit",
            error_message="provider throttled",
        ),
        _result(case_id="case_d", status="pass"),
        _result(case_id="case_e", status="fail"),
    ]

    clusters = _failure_clusters(results)

    assert [cluster["key"] for cluster in clusters] == [
        "assertion:contains_refund",
        "infrastructure:RateLimit",
        "unclassified",
    ]
    assert clusters[0]["count"] == 2
    assert clusters[0]["case_ids"] == ["case_a", "case_b"]
    assert clusters[1]["kind"] == "infrastructure"
    assert clusters[2]["example"] == ""


# ------------------------------------------------------------------ list views
def test_matches_search_checks_case_model_source_and_label() -> None:
    item = {
        "case_id": "case_abc123",
        "trace_id": "tr_chat_001",
        "model": {"name": "fake-baseline-v1", "provider": "fake"},
        "source": {"app": "support-copilot", "environment": "production"},
        "label": {"label": "failure", "category": "wrong_answer"},
        "tags": ["refund"],
    }

    assert _matches_search(item, "case_abc")
    assert _matches_search(item, "fake-baseline")
    assert _matches_search(item, "support-copilot")
    assert _matches_search(item, "wrong_answer")
    assert _matches_search(item, "refund")
    assert not _matches_search(item, "unrelated")


# ------------------------------------------------------------------ settings
def test_settings_payload_never_exposes_the_csrf_token(tmp_path: Any) -> None:
    db = str(tmp_path / "cases.db")
    state = _State(
        db_path=db,
        config=Config(),
        token="secret-token-value",
        allowed_hosts=frozenset({"127.0.0.1"}),
        web_root=tmp_path,
    )
    with CaseStore(db) as store:
        payload = _settings_payload(store, state)

    encoded = json.dumps(payload)
    assert "secret-token-value" not in encoded
    assert payload["live"]["ui_enabled"] is False
    assert payload["counts"] == {"cases": 0, "runs": 0}
    assert payload["failure_categories"] == sorted(payload["failure_categories"])


def test_settings_payload_reports_configured_adapters(tmp_path: Any) -> None:
    db = str(tmp_path / "cases.db")
    state = _State(
        db_path=db,
        config=Config(provider="anthropic"),
        token="t",
        allowed_hosts=frozenset({"127.0.0.1"}),
        web_root=tmp_path,
    )
    with CaseStore(db) as store:
        payload = _settings_payload(store, state)

    adapters = {adapter["name"]: adapter["status"] for adapter in payload["adapters"]}
    assert adapters["fake"] == "enabled"
    assert adapters["anthropic"] == "configured"
    assert adapters["openai-compatible"] == "available"


# -------------------------------------------------------------------- CI setup
def test_ci_config_toml_only_lists_configured_gates() -> None:
    config = Config(db="cases.db", model="fake-baseline-v1")
    text = _ci_config_toml(
        config,
        {"min_pass_rate": 0.85, "max_signal_failures": 2, "max_p95_latency_ms": None},
    )

    assert "[trace_to_eval]" in text
    assert 'db = "cases.db"' in text
    assert "min_pass_rate = 0.85" in text
    assert "max_signal_failures = 2" in text
    assert "max_p95_latency_ms" not in text
    parsed = tomllib.loads(text)["trace_to_eval"]
    assert parsed["thresholds"] == {"min_pass_rate": 0.85, "max_signal_failures": 2}


def test_ci_config_toml_explains_an_empty_gate_set() -> None:
    text = _ci_config_toml(Config(), {"min_pass_rate": None})

    assert "# No thresholds set" in text
    assert tomllib.loads(text)["trace_to_eval"]["thresholds"] == {}


def test_ci_run_command_maps_each_gate_to_its_cli_flag() -> None:
    command = _ci_run_command(
        {
            "min_pass_rate": 0.9,
            "max_total_cost_usd": 1.5,
            "max_infrastructure_errors": 0,
            "max_pass_rate_drop": 0.05,
        },
        "release",
    )

    assert command.startswith("trace-to-eval run --provider fake --baseline release")
    assert "--min-pass-rate 0.9" in command
    assert "--max-total-cost-usd 1.5" in command
    assert "--max-infrastructure-errors 0" in command
    assert "--max-pass-rate-drop 0.05" in command
    assert command.endswith("--format junit --out trace-to-eval-report.xml")


def test_ci_run_command_omits_the_baseline_flag_when_unset() -> None:
    command = _ci_run_command({"min_pass_rate": 0.5}, None)

    assert "--baseline" not in command
    assert "--min-pass-rate 0.5" in command


def test_ci_workflow_uploads_the_report_even_after_a_failure() -> None:
    workflow = _ci_workflow("trace-to-eval run --provider fake", "release")

    assert "on: [pull_request]" in workflow
    assert "# Baseline `release` must already exist" in workflow
    assert "      - run: trace-to-eval run --provider fake\n" in workflow
    assert "      - if: always()\n" in workflow
    assert "retention-days: 14" in workflow
    assert workflow.index("if: always()") < workflow.index("actions/upload-artifact@v4")


def test_ci_query_thresholds_merges_blank_and_numeric_values() -> None:
    base = Thresholds(min_pass_rate=0.75, max_signal_failures=1)
    merged = _ci_query_thresholds(
        base,
        {"min_pass_rate": [""], "max_signal_failures": ["3"], "max_total_cost_usd": ["2.5"]},
    )

    assert merged.min_pass_rate == 0.75
    assert merged.max_signal_failures == 3
    assert merged.max_total_cost_usd == 2.5


def test_ci_query_thresholds_rejects_non_numeric_and_infinite_gates() -> None:
    base = Thresholds()
    assert _status(_ci_query_thresholds, base, {"min_pass_rate": ["high"]}) == 400
    assert _status(_ci_query_thresholds, base, {"min_pass_rate": ["nan"]}) == 400
    assert _status(_ci_query_thresholds, base, {"max_total_cost_usd": ["inf"]}) == 400
