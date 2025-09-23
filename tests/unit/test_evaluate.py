"""Deterministic assertions and the separation of signals from failures."""

from __future__ import annotations

from typing import Any

from trace_to_eval.evaluate import evaluate_output


def _output(text: str = "", **extra: Any) -> dict[str, Any]:
    return {"text": text, **extra}


def _names(outcome: Any) -> list[str]:
    return [item.name for item in outcome.assertions]


def test_no_expectations_produces_no_checks() -> None:
    outcome = evaluate_output(None, _output("anything"))
    assert outcome.assertions == []
    assert outcome.passed is True


def test_text_equals_and_normalization() -> None:
    exact = evaluate_output({"text_equals": "hello world"}, _output("hello world"))
    assert exact.passed

    messy = evaluate_output({"text_equals": "hello world"}, _output("  hello   world \n"))
    assert not messy.passed

    normalized = evaluate_output(
        {"text_equals": "hello world", "normalize": True}, _output("  hello   world \n")
    )
    assert normalized.passed


def test_text_contains_and_not_contains() -> None:
    outcome = evaluate_output(
        {"text_contains": ["30 days"], "text_not_contains": ["guaranteed"]},
        _output("Refund within 30 days."),
    )
    assert outcome.passed

    failed = evaluate_output({"text_not_contains": ["guaranteed"]}, _output("guaranteed!"))
    assert [item.name for item in failed.deterministic_failures] == ["text_not_contains"]


def test_json_schema_uses_structured_field_when_present() -> None:
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {"id": {"type": "string"}},
        "additionalProperties": False,
    }
    good = evaluate_output({"json_schema": schema}, _output("", structured={"id": "x"}))
    assert good.passed

    bad = evaluate_output({"json_schema": schema}, _output("", structured={"id": 1}))
    assert not bad.passed
    assert bad.deterministic_failures[0].name == "json_schema"


def test_json_schema_falls_back_to_parsing_text() -> None:
    schema = {"type": "object", "required": ["id"]}
    outcome = evaluate_output({"json_schema": schema}, _output('{"id": "x"}'))
    assert outcome.passed

    broken = evaluate_output({"json_schema": schema}, _output("not json"))
    assert broken.deterministic_failures[0].message == "output text is not valid JSON"


def test_json_equals_compares_structured_value() -> None:
    outcome = evaluate_output(
        {"json_equals": {"a": 1, "b": [2]}}, _output("", structured={"b": [2], "a": 1})
    )
    assert outcome.passed

    mismatch = evaluate_output({"json_equals": {"a": 1}}, _output("", structured={"a": 2}))
    assert not mismatch.passed


def test_citations_required_sources_and_pattern() -> None:
    expectations = {
        "citations": {
            "required": True,
            "sources": ["policy#refunds"],
            "pattern": r"\[policy#[a-z-]+\]",
        }
    }
    good = evaluate_output(expectations, _output("See [policy#refunds]."))
    assert good.passed

    missing = evaluate_output(expectations, _output("See the policy."))
    assert {item.name for item in missing.deterministic_failures} == {
        "citations.sources",
        "citations.pattern",
    }


def test_citation_pattern_not_required_when_disabled() -> None:
    outcome = evaluate_output(
        {"citations": {"required": False, "pattern": r"\[doc#\d+\]"}}, _output("no citation")
    )
    assert outcome.passed


def test_tool_call_expectations() -> None:
    calls = [
        {"name": "get_order_status", "arguments": {"order_id": "A-1"}},
        {"name": "delete_account", "arguments": {"account_id": "x"}},
    ]
    outcome = evaluate_output(
        {
            "tool_calls": {
                "allowed": ["get_order_status"],
                "forbidden": ["delete_account"],
                "required": ["get_order_status"],
                "arg_equals": [{"name": "get_order_status", "arguments": {"order_id": "A-1"}}],
            }
        },
        _output("", tool_calls=calls),
    )
    names = {item.name for item in outcome.deterministic_failures}
    assert names == {"tool_calls.allowed", "tool_calls.forbidden"}

    wrong_args = evaluate_output(
        {
            "tool_calls": {
                "arg_equals": [{"name": "get_order_status", "arguments": {"order_id": "A-2"}}]
            }
        },
        _output("", tool_calls=calls),
    )
    assert wrong_args.deterministic_failures[0].details["mismatched_keys"] == ["order_id"]

    absent = evaluate_output(
        {"tool_calls": {"required": ["get_order_status"]}}, _output("", tool_calls=[])
    )
    assert absent.deterministic_failures[0].name == "tool_calls.required"


def test_regex_rules_on_fields() -> None:
    outcome = evaluate_output(
        {
            "rules": [
                {"type": "regex", "field": "text", "pattern": r"\d+ days", "mode": "must_match"},
                {
                    "type": "regex",
                    "field": "text",
                    "pattern": r"guaranteed",
                    "mode": "must_not_match",
                },
            ]
        },
        _output("Refund within 30 days."),
    )
    assert outcome.passed

    missing = evaluate_output(
        {"rules": [{"type": "regex", "field": "missing", "pattern": "x", "mode": "must_match"}]},
        _output("anything"),
    )
    assert missing.deterministic_failures[0].message.startswith("field 'missing' is missing")


def test_cost_and_latency_limits_are_inclusive() -> None:
    outcome = evaluate_output(
        {"max_cost_usd": 0.01, "max_latency_ms": 100},
        _output("ok"),
        cost_usd=0.01,
        latency_ms=100,
    )
    assert outcome.passed

    over = evaluate_output(
        {"max_cost_usd": 0.01, "max_latency_ms": 100},
        _output("ok"),
        cost_usd=0.0101,
        latency_ms=101,
    )
    assert {item.name for item in over.deterministic_failures} == {
        "max_cost_usd",
        "max_latency_ms",
    }


def test_human_label_disagreement_is_a_signal_not_a_failure() -> None:
    outcome = evaluate_output({"human_label": "success"}, _output("ok"), human_label="failure")
    assert outcome.passed is True
    assert [item.name for item in outcome.signal_failures] == ["human_label"]


def test_missing_human_label_does_not_invent_one() -> None:
    outcome = evaluate_output({"human_label": "success"}, _output("ok"))
    assert "human_label" not in _names(outcome)


def test_rubric_without_evaluator_is_skipped_and_not_a_failure() -> None:
    rubric = {
        "id": "tone",
        "version": "rubric-1",
        "criteria": "Friendly and concise.",
        "min_score": 0.8,
    }
    outcome = evaluate_output({"rubric": rubric}, _output("ok"))
    assert outcome.passed
    skipped = next(item for item in outcome.assertions if item.name == "rubric.tone")
    assert skipped.status == "skip"


def test_rubric_score_below_minimum_is_a_signal_failure() -> None:
    rubric = {
        "id": "tone",
        "version": "rubric-1",
        "criteria": "Friendly.",
        "min_score": 0.8,
    }
    outcome = evaluate_output(
        {"rubric": rubric},
        _output("ok"),
        rubric_result={
            "score": 0.4,
            "confidence": 0.9,
            "evaluator": "fake",
            "evaluator_version": "judge-1",
            "rationale": "curt",
        },
    )
    assert outcome.passed
    assert outcome.signal_failures[0].name == "rubric.tone"
