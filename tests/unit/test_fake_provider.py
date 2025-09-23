"""Deterministic fake provider behavior, including script validation."""

from __future__ import annotations

from typing import Any

import pytest

from trace_to_eval.errors import FakeScriptError, ProviderTimeout
from trace_to_eval.providers.base import CompletionRequest
from trace_to_eval.providers.fake import FakeProvider, FakeScript


def _request(
    *,
    model: str = "fake-baseline-v1",
    case_id: str = "case_1",
    trace_id: str | None = "tr_1",
    source: dict[str, Any] | None = None,
) -> CompletionRequest:
    return CompletionRequest(
        model=model,
        messages=[{"role": "user", "content": "hi"}],
        case_id=case_id,
        trace_id=trace_id,
        source_output=source
        or {"text": "recorded", "tool_calls": [{"name": "lookup", "arguments": {"q": "x"}}]},
    )


def test_echo_replays_recorded_output_deterministically() -> None:
    provider = FakeProvider()
    first = provider.complete(_request(), timeout_s=5)
    second = provider.complete(_request(), timeout_s=5)
    assert first.text == "recorded"
    assert first.tool_calls[0].name == "lookup"
    assert first.request_id == second.request_id
    assert first.request_id != provider.complete(_request(case_id="case_2"), timeout_s=5).request_id


def test_mutate_applies_cost_and_latency_multipliers() -> None:
    script = FakeScript.from_mapping(
        {
            "models": {
                "fake-regressed-v2": {
                    "mode": "mutate",
                    "text_suffix": " (v2)",
                    "cost_multiplier": 3.0,
                    "latency_multiplier": 2.0,
                }
            }
        }
    )
    provider = FakeProvider(script=script)
    response = provider.complete(_request(model="fake-regressed-v2"), timeout_s=5)
    baseline = FakeProvider().complete(_request(), timeout_s=5)
    assert response.text == "recorded (v2)"
    assert response.cost_usd == pytest.approx(baseline.cost_usd * 3)
    assert response.latency_ms == pytest.approx(baseline.latency_ms * 2, rel=0.2)


def test_case_override_by_trace_id_replaces_tool_calls() -> None:
    script = FakeScript.from_mapping(
        {
            "cases": {
                "tr_1": {
                    "mode": "mutate",
                    "text": "fixed answer",
                    "tool_calls": [{"name": "lookup", "arguments": {"q": "fixed"}}],
                }
            }
        }
    )
    response = FakeProvider(script=script).complete(_request(), timeout_s=5)
    assert response.text == "fixed answer"
    assert response.tool_calls[0].arguments == {"q": "fixed"}


def test_tool_argument_override_changes_only_targeted_tool() -> None:
    script = FakeScript.from_mapping(
        {"models": {"m": {"tool_argument_overrides": {"lookup": {"q": "changed"}}}}}
    )
    response = FakeProvider(script=script).complete(_request(model="m"), timeout_s=5)
    assert response.tool_calls[0].arguments == {"q": "changed"}


def test_drop_tool_calls_removes_recorded_calls() -> None:
    script = FakeScript.from_mapping({"models": {"m": {"drop_tool_calls": True}}})
    response = FakeProvider(script=script).complete(_request(model="m"), timeout_s=5)
    assert response.tool_calls == []


def test_error_injection_raises_with_request_id() -> None:
    script = FakeScript.from_mapping({"models": {"m": {"error": "timeout"}}})
    with pytest.raises(ProviderTimeout) as excinfo:
        FakeProvider(script=script).complete(_request(model="m"), timeout_s=5)
    assert excinfo.value.error_class == "timeout"
    assert excinfo.value.request_id


def test_zero_timeout_is_an_infrastructure_error() -> None:
    with pytest.raises(ProviderTimeout):
        FakeProvider().complete(_request(), timeout_s=0)


@pytest.mark.parametrize(
    "payload",
    [
        {"models": {"m": {"mode": "rewrite"}}},
        {"models": {"m": {"cost_multiplier": -1}}},
        {"models": {"m": {"error": "explode"}}},
        {"models": {"m": {"unknown": 1}}},
        {"models": {"m": {"tool_calls": [{"arguments": {}}]}}},
        {"cases": []},
    ],
)
def test_invalid_scripts_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(FakeScriptError):
        FakeScript.from_mapping(payload)


def test_unknown_model_falls_back_to_echo() -> None:
    response = FakeProvider().complete(_request(model="never-configured"), timeout_s=5)
    assert response.text == "recorded"
