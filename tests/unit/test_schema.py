"""Trace schema validation, normalization, and stable case identity."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from trace_to_eval.schema import (
    case_id_for_trace,
    load_json_schema,
    normalize_trace,
    parse_trace_line,
    validate_trace,
)


def _minimal() -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "input": {"messages": [{"role": "user", "content": "hello"}]},
        "output": {"text": "world"},
        "model": {"name": "fake-baseline-v1"},
    }


def test_bundled_schema_document_is_valid_json() -> None:
    document = load_json_schema()
    assert document["$schema"].startswith("https://json-schema.org/")
    assert document["title"]


def test_every_fixture_trace_validates(traces: list[dict[str, Any]]) -> None:
    assert len(traces) == 5
    for trace in traces:
        assert validate_trace(trace) == []


def test_minimal_trace_validates() -> None:
    assert validate_trace(_minimal()) == []


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda t: t.update({"schema_version": "2.0"}), "unsupported version"),
        (lambda t: t.pop("schema_version"), "schema_version"),
        (lambda t: t.update({"surprise": 1}), "unknown top-level field"),
        (lambda t: t["input"]["messages"][0].update({"role": "root"}), "role"),
        (lambda t: t["output"].update({"text": 5}), "output.text"),
        (lambda t: t["model"].update({"name": ""}), "model.name"),
        (lambda t: t.update({"metrics": {"latency_ms": -1}}), "latency_ms"),
        (lambda t: t.update({"outcome": {"status": "maybe"}}), "outcome.status"),
        (lambda t: t.update({"outcome": {"category": "vibes"}}), "outcome.category"),
        (lambda t: t.update({"tags": "one"}), "tags"),
        (lambda t: t.update({"provenance": []}), "provenance"),
    ],
)
def test_invalid_traces_are_rejected(mutate: Any, fragment: str) -> None:
    trace = _minimal()
    mutate(trace)
    errors = validate_trace(trace)
    assert errors, "expected a validation error"
    assert any(fragment in error for error in errors)


def test_unsupported_json_schema_keyword_is_rejected() -> None:
    trace = _minimal()
    trace["expectations"] = {"json_schema": {"$ref": "#/definitions/x"}}
    assert any("unsupported" in error for error in validate_trace(trace))


def test_invalid_regex_is_rejected_at_import() -> None:
    trace = _minimal()
    trace["expectations"] = {
        "rules": [
            {"type": "regex", "field": "output.text", "pattern": "([a-z", "mode": "must_match"}
        ]
    }
    assert any("invalid regular expression" in error for error in validate_trace(trace))


def test_parse_trace_line_reports_json_errors_without_echoing_content() -> None:
    trace, errors = parse_trace_line('{"secret": "sk-live-abcdefghijklmnopqrstuvwxyz"')
    assert trace is None
    assert errors and "JSON" in errors[0]
    assert "sk-live" not in errors[0]


def test_normalize_is_idempotent_and_strips_surrounding_whitespace() -> None:
    trace = _minimal()
    trace["output"]["text"] = "  padded \r\n"
    once = normalize_trace(trace)
    twice = normalize_trace(copy.deepcopy(once))
    assert once == twice
    assert once["output"]["text"] == "padded"


def test_case_identity_ignores_operational_metadata(traces: list[dict[str, Any]]) -> None:
    original = traces[0]
    changed = copy.deepcopy(original)
    changed["trace_id"] = "tr_other"
    changed["created_at"] = "2020-01-01T00:00:00Z"
    changed["metrics"] = {"latency_ms": 1, "cost_usd": 99.0}
    changed["source"] = {"app": "other"}
    changed["provenance"] = {"collected_by": "someone-else"}
    assert case_id_for_trace(normalize_trace(original)) == case_id_for_trace(
        normalize_trace(changed)
    )


def test_case_identity_tracks_semantic_changes(traces: list[dict[str, Any]]) -> None:
    original = traces[0]
    changed = copy.deepcopy(original)
    changed["output"]["text"] = "Refunds are never available."
    assert case_id_for_trace(normalize_trace(original)) != case_id_for_trace(
        normalize_trace(changed)
    )


def test_case_identity_tracks_expectations(traces: list[dict[str, Any]]) -> None:
    original = traces[0]
    changed = copy.deepcopy(original)
    changed["expectations"]["max_cost_usd"] = 0.0001
    assert case_id_for_trace(normalize_trace(original)) != case_id_for_trace(
        normalize_trace(changed)
    )
