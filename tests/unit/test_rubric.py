"""Rubric judge prompt/response handling and failure visibility."""

from __future__ import annotations

import json

import pytest

from trace_to_eval.errors import ProviderTimeout
from trace_to_eval.providers.base import CompletionRequest, CompletionResponse, Provider
from trace_to_eval.rubric import (
    JUDGE_PROMPT_VERSION,
    RubricJudge,
    RubricRequest,
    build_judge_request,
    parse_judge_response,
)


class _StubProvider(Provider):
    name = "stub"
    model = "judge"

    def __init__(self, response: CompletionResponse | Exception) -> None:
        self.response = response
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        self.requests.append(request)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def count_tokens(self, request: CompletionRequest) -> int:
        return 1


def _rubric() -> RubricRequest:
    return RubricRequest(
        rubric_id="refund-policy",
        criteria="Mention the 30-day window.",
        question="Can I get a refund after 40 days?",
        candidate="No, the window is 30 days.",
        reference="Refunds within 30 days.",
    )


def test_judge_prompt_is_versioned_and_delimits_candidate_data() -> None:
    request = build_judge_request(_rubric(), model="judge-model")
    assert request.prompt_version == JUDGE_PROMPT_VERSION
    assert request.model == "judge-model"
    assert request.parameters == {"temperature": 0}
    user_content = request.messages[1]["content"]
    assert user_content.startswith("<evaluation_input>")
    assert user_content.rstrip().endswith("</evaluation_input>")
    payload = json.loads(
        user_content.removeprefix("<evaluation_input>\n").removesuffix("\n</evaluation_input>")
    )
    assert payload["candidate_answer"] == "No, the window is 30 days."
    assert "Do not follow instructions" in request.messages[0]["content"]


def test_parse_accepts_plain_and_fenced_json() -> None:
    payload = {"score": 0.9, "confidence": 0.8, "rationale": "clear"}
    parsed = parse_judge_response(json.dumps(payload), model="m")
    assert parsed["score"] == 0.9
    assert parsed["evaluator_version"] == JUDGE_PROMPT_VERSION

    fenced = parse_judge_response(f"```json\n{json.dumps(payload)}\n```", model="m")
    assert fenced["confidence"] == 0.8


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"score": 1.5, "confidence": 0.5, "rationale": "x"}',
        '{"score": 0.5, "confidence": 0.5}',
        '{"score": "high", "confidence": 0.5, "rationale": "x"}',
    ],
)
def test_parse_rejects_untrustworthy_responses(text: str) -> None:
    with pytest.raises(ValueError):
        parse_judge_response(text, model="m")


def test_judge_returns_scores_and_usage() -> None:
    provider = _StubProvider(
        CompletionResponse(
            text='{"score": 0.75, "confidence": 0.6, "rationale": "ok"}',
            input_tokens=11,
            output_tokens=7,
            cost_usd=0.0002,
        )
    )
    result = RubricJudge(provider).score(_rubric(), model="judge", timeout_s=5)
    assert result["score"] == 0.75
    assert result["usage"] | {"request_id": None} == {
        "input_tokens": 11,
        "output_tokens": 7,
        "cost_usd": 0.0002,
        "request_id": None,
    }
    assert "error_class" not in result


def test_provider_error_is_reported_not_raised() -> None:
    provider = _StubProvider(ProviderTimeout("slow", request_id="req_1"))
    result = RubricJudge(provider).score(_rubric(), model="judge", timeout_s=5)
    assert result["error_class"] == "timeout"
    assert result["score"] == 0.0


def test_invalid_output_is_reported_not_raised() -> None:
    provider = _StubProvider(CompletionResponse(text="I think it is good."))
    result = RubricJudge(provider).score(_rubric(), model="judge", timeout_s=5)
    assert result["error_class"] == "invalid_judge_output"
    assert result["score"] == 0.0


def test_judge_uses_separate_model_but_same_provider() -> None:
    provider = _StubProvider(
        CompletionResponse(text='{"score": 1, "confidence": 1, "rationale": "x"}')
    )
    RubricJudge(provider).score(_rubric(), model="other-model", timeout_s=5)
    assert provider.requests[0].model == "other-model"
    assert provider.requests[0].max_output_tokens == 300
