"""Deterministic assertions plus a separately identified rubric signal.

Deterministic assertions decide pass/fail. Model-assisted rubric scores are always labeled
as signals with an evaluator version and confidence, and are never silently converted into
ground truth.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from .jsoncheck import validate_schema
from .util import deep_get, normalize_text, truncate

Status = Literal["pass", "fail", "skip"]
Kind = Literal["deterministic", "signal"]

MAX_DETAIL = 300


@dataclass
class AssertionResult:
    name: str
    kind: Kind
    status: Status
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def failed(self) -> bool:
        return self.status == "fail" and self.kind == "deterministic"

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "status": self.status,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class EvaluationOutcome:
    assertions: list[AssertionResult]
    rubric: dict[str, Any] | None = None

    @property
    def deterministic_failures(self) -> list[AssertionResult]:
        return [item for item in self.assertions if item.failed]

    @property
    def signal_failures(self) -> list[AssertionResult]:
        return [item for item in self.assertions if item.kind == "signal" and item.status == "fail"]

    @property
    def passed(self) -> bool:
        return not self.deterministic_failures


def evaluate_output(
    expectations: dict[str, Any] | None,
    output: dict[str, Any],
    *,
    cost_usd: float = 0.0,
    latency_ms: float = 0.0,
    human_label: str | None = None,
    rubric_result: dict[str, Any] | None = None,
) -> EvaluationOutcome:
    """Run every configured assertion against one replay output."""
    checks: list[AssertionResult] = []
    expectations = expectations or {}
    raw_text = output.get("text")
    text = raw_text if isinstance(raw_text, str) else ""
    normalize = bool(expectations.get("normalize", False))
    comparable = normalize_text(text) if normalize else text

    if "text_equals" in expectations:
        expected = expectations["text_equals"]
        target = normalize_text(expected) if normalize else expected
        checks.append(
            AssertionResult(
                name="text_equals",
                kind="deterministic",
                status="pass" if comparable == target else "fail",
                message=(
                    "output text matches expected text"
                    if comparable == target
                    else f"expected {truncate(target, max_length=MAX_DETAIL)}, "
                    f"got {truncate(comparable, max_length=MAX_DETAIL)}"
                ),
            )
        )

    for needle in expectations.get("text_contains") or []:
        present = needle in text
        checks.append(
            AssertionResult(
                name="text_contains",
                kind="deterministic",
                status="pass" if present else "fail",
                message=(
                    "output contains required text"
                    if present
                    else (
                        f"output is missing required text {truncate(needle, max_length=MAX_DETAIL)}"
                    )
                ),
                details={"needle": truncate(needle, max_length=MAX_DETAIL)},
            )
        )

    for needle in expectations.get("text_not_contains") or []:
        present = needle in text
        checks.append(
            AssertionResult(
                name="text_not_contains",
                kind="deterministic",
                status="fail" if present else "pass",
                message=(
                    f"output contains forbidden text {truncate(needle, max_length=MAX_DETAIL)}"
                    if present
                    else "output does not contain forbidden text"
                ),
                details={"needle": truncate(needle, max_length=MAX_DETAIL)},
            )
        )

    schema = expectations.get("json_schema")
    if schema is not None:
        structured, parse_error = _structured_payload(output)
        if parse_error is not None:
            checks.append(
                AssertionResult(
                    name="json_schema",
                    kind="deterministic",
                    status="fail",
                    message=parse_error,
                )
            )
        else:
            errors = validate_schema(structured, schema)
            checks.append(
                AssertionResult(
                    name="json_schema",
                    kind="deterministic",
                    status="pass" if not errors else "fail",
                    message=(
                        "structured output satisfies the schema"
                        if not errors
                        else f"schema violation: {truncate(errors[0], max_length=MAX_DETAIL)}"
                    ),
                    details={"error_count": len(errors)},
                )
            )

    if "json_equals" in expectations:
        structured, parse_error = _structured_payload(output)
        expected = expectations["json_equals"]
        if parse_error is not None:
            checks.append(
                AssertionResult(
                    name="json_equals",
                    kind="deterministic",
                    status="fail",
                    message=parse_error,
                )
            )
        else:
            matches = structured == expected
            checks.append(
                AssertionResult(
                    name="json_equals",
                    kind="deterministic",
                    status="pass" if matches else "fail",
                    message=(
                        "structured output matches expected value"
                        if matches
                        else "structured output differs from expected value"
                    ),
                )
            )

    citations = expectations.get("citations")
    if isinstance(citations, dict) and citations:
        checks.extend(_citation_checks(citations, text))

    tool_expectations = expectations.get("tool_calls")
    if tool_expectations:
        checks.extend(_tool_checks(tool_expectations, output.get("tool_calls") or []))

    for rule in expectations.get("rules") or []:
        checks.append(_rule_check(rule, output))

    if "max_cost_usd" in expectations:
        limit = float(expectations["max_cost_usd"])
        within = cost_usd <= limit
        checks.append(
            AssertionResult(
                name="max_cost_usd",
                kind="deterministic",
                status="pass" if within else "fail",
                message=(
                    f"cost ${cost_usd:.6f} within ${limit:.6f}"
                    if within
                    else f"cost ${cost_usd:.6f} exceeds ${limit:.6f}"
                ),
                details={"cost_usd": round(cost_usd, 6), "limit": limit},
            )
        )

    if "max_latency_ms" in expectations:
        limit = float(expectations["max_latency_ms"])
        within = latency_ms <= limit
        checks.append(
            AssertionResult(
                name="max_latency_ms",
                kind="deterministic",
                status="pass" if within else "fail",
                message=(
                    f"latency {latency_ms:.0f}ms within {limit:.0f}ms"
                    if within
                    else f"latency {latency_ms:.0f}ms exceeds {limit:.0f}ms"
                ),
                details={"latency_ms": round(latency_ms, 3), "limit": limit},
            )
        )

    expected_label = expectations.get("human_label")
    if expected_label is not None and human_label is not None:
        agrees = expected_label == human_label
        checks.append(
            AssertionResult(
                name="human_label",
                kind="signal",
                status="pass" if agrees else "fail",
                message=(
                    f"recorded human label is {human_label!r}"
                    if agrees
                    else f"expectation label {expected_label!r} disagrees with human label "
                    f"{human_label!r}"
                ),
                details={"expected": expected_label, "human": human_label},
            )
        )

    rubric = expectations.get("rubric")
    if rubric is not None:
        checks.append(_rubric_check(rubric, rubric_result))

    return EvaluationOutcome(assertions=checks, rubric=rubric_result)


def _structured_payload(output: dict[str, Any]) -> tuple[Any, str | None]:
    if "structured" in output and output["structured"] is not None:
        return output["structured"], None
    text = output.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "no structured output and no text to parse"
    try:
        return json.loads(text), None
    except json.JSONDecodeError:
        return None, "output text is not valid JSON"


def _citation_checks(citations: dict[str, Any], text: str) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    sources = citations.get("sources") or []
    pattern = citations.get("pattern")
    required = bool(citations.get("required", bool(sources) or bool(pattern)))

    missing = [source for source in sources if source not in text]
    results.append(
        AssertionResult(
            name="citations.sources",
            kind="deterministic",
            status="fail" if missing and required else "pass",
            message=(
                f"output omits {len(missing)} required citation source(s)"
                if missing and required
                else "citation sources present"
            ),
            details={"missing": [truncate(item, max_length=120) for item in missing]},
        )
    )

    if pattern:
        compiled = re.compile(pattern)
        found = compiled.search(text) is not None
        results.append(
            AssertionResult(
                name="citations.pattern",
                kind="deterministic",
                status="pass" if found or not required else "fail",
                message=(
                    "citation pattern present"
                    if found
                    else "output has no citation matching the required pattern"
                ),
            )
        )
    return results


def _tool_checks(
    expectations: dict[str, Any], tool_calls: list[dict[str, Any]]
) -> list[AssertionResult]:
    results: list[AssertionResult] = []
    names = [str(call.get("name", "")) for call in tool_calls if isinstance(call, dict)]

    allowed = expectations.get("allowed")
    if allowed is not None:
        unexpected = sorted({name for name in names if name not in set(allowed)})
        results.append(
            AssertionResult(
                name="tool_calls.allowed",
                kind="deterministic",
                status="fail" if unexpected else "pass",
                message=(
                    f"disallowed tool call(s): {', '.join(unexpected)}"
                    if unexpected
                    else "all tool calls are allowed"
                ),
                details={"unexpected": unexpected},
            )
        )

    forbidden = expectations.get("forbidden")
    if forbidden is not None:
        present = sorted({name for name in names if name in set(forbidden)})
        results.append(
            AssertionResult(
                name="tool_calls.forbidden",
                kind="deterministic",
                status="fail" if present else "pass",
                message=(
                    f"forbidden tool call(s): {', '.join(present)}"
                    if present
                    else "no forbidden tool calls"
                ),
                details={"present": present},
            )
        )

    required = expectations.get("required")
    if required is not None:
        missing = [name for name in required if name not in names]
        results.append(
            AssertionResult(
                name="tool_calls.required",
                kind="deterministic",
                status="fail" if missing else "pass",
                message=(
                    f"missing required tool call(s): {', '.join(missing)}"
                    if missing
                    else "all required tool calls present"
                ),
                details={"missing": missing},
            )
        )

    for index, expected in enumerate(expectations.get("arg_equals") or []):
        name = expected["name"]
        expected_args = expected["arguments"]
        matching = [
            call for call in tool_calls if isinstance(call, dict) and call.get("name") == name
        ]
        if not matching:
            results.append(
                AssertionResult(
                    name=f"tool_calls.arg_equals[{index}]",
                    kind="deterministic",
                    status="fail",
                    message=f"tool {name!r} was not called",
                    details={"tool": name},
                )
            )
            continue
        mismatches: list[str] = []
        for call in matching:
            raw_actual = call.get("arguments")
            actual: dict[str, Any] = raw_actual if isinstance(raw_actual, dict) else {}
            mismatches = [
                key
                for key, value in expected_args.items()
                if key not in actual or actual[key] != value
            ]
            if not mismatches:
                break
        results.append(
            AssertionResult(
                name=f"tool_calls.arg_equals[{index}]",
                kind="deterministic",
                status="fail" if mismatches else "pass",
                message=(
                    f"tool {name!r} arguments differ on: {', '.join(mismatches)}"
                    if mismatches
                    else f"tool {name!r} arguments match"
                ),
                details={"tool": name, "mismatched_keys": mismatches},
            )
        )
    return results


def _rule_check(rule: dict[str, Any], output: dict[str, Any]) -> AssertionResult:
    field_path = rule["field"]
    name = f"rule.{field_path}"
    try:
        value = deep_get(output, field_path)
    except (KeyError, IndexError):
        return AssertionResult(
            name=name,
            kind="deterministic",
            status="fail",
            message=f"field {field_path!r} is missing from the output",
            details={"field": field_path},
        )
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    matched = re.search(rule["pattern"], rendered) is not None
    should_match = rule["mode"] == "must_match"
    passed = matched if should_match else not matched
    return AssertionResult(
        name=name,
        kind="deterministic",
        status="pass" if passed else "fail",
        message=(
            f"field {field_path!r} {'matches' if matched else 'does not match'} the rule"
            if passed
            else f"field {field_path!r} violates the {rule['mode']} rule"
        ),
        details={"field": field_path, "mode": rule["mode"]},
    )


def _rubric_check(rubric: dict[str, Any], rubric_result: dict[str, Any] | None) -> AssertionResult:
    name = f"rubric.{rubric['id']}"
    if rubric_result is None:
        return AssertionResult(
            name=name,
            kind="signal",
            status="skip",
            message="no rubric evaluator configured; score is not inferred",
            details={"evaluator_version": rubric["version"], "criteria": rubric["criteria"]},
        )
    score = float(rubric_result.get("score", 0.0))
    min_score = float(rubric.get("min_score", 0.0))
    confidence = rubric_result.get("confidence")
    passed = score >= min_score
    return AssertionResult(
        name=name,
        kind="signal",
        status="pass" if passed else "fail",
        message=(
            f"rubric score {score:.2f} meets minimum {min_score:.2f}"
            if passed
            else f"rubric score {score:.2f} is below minimum {min_score:.2f}"
        ),
        details={
            "score": score,
            "min_score": min_score,
            "confidence": confidence,
            "evaluator": rubric_result.get("evaluator", "unknown"),
            "evaluator_version": rubric_result.get("evaluator_version", rubric["version"]),
            "rationale": truncate(rubric_result.get("rationale", ""), max_length=MAX_DETAIL),
        },
    )
