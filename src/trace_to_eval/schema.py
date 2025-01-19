"""Versioned trace schema, validation, normalization, and stable case identity."""

from __future__ import annotations

import copy
import json
import re
from importlib import resources
from typing import Any, cast

from .util import canonical_json, normalize_string, stable_hash

SCHEMA_VERSION = "1.0"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

MAX_MESSAGES = 500
MAX_TOOLS = 100
MAX_TAGS = 50
MAX_RULES = 50
MAX_PATTERN_LENGTH = 500
MAX_SCHEMA_DEPTH = 20
MAX_TRACE_FILE_BYTES = 64 * 1024 * 1024
MAX_TRACE_LINE_CHARS = 8 * 1024 * 1024

MESSAGE_ROLES = frozenset({"system", "user", "assistant", "tool", "developer"})
OUTCOME_STATUSES = frozenset({"success", "failure", "unknown"})
HUMAN_LABELS = frozenset({"success", "failure", "unknown"})
FAILURE_CATEGORIES = frozenset(
    {
        "quality",
        "tool_call",
        "retrieval",
        "format",
        "safety",
        "latency",
        "cost",
        "other",
    }
)

_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "trace_id",
        "created_at",
        "source",
        "input",
        "output",
        "tools",
        "model",
        "metrics",
        "outcome",
        "expectations",
        "provenance",
        "tags",
        "metadata",
    }
)

_EXPECTATION_KEYS = frozenset(
    {
        "text_equals",
        "text_contains",
        "text_not_contains",
        "normalize",
        "json_schema",
        "json_equals",
        "citations",
        "tool_calls",
        "rules",
        "max_cost_usd",
        "max_latency_ms",
        "rubric",
        "human_label",
    }
)

_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "additionalProperties",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "anyOf",
        "oneOf",
        "allOf",
        "description",
        "title",
        "default",
    }
)

_JSON_TYPES = frozenset({"object", "array", "string", "number", "integer", "boolean", "null"})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if _is_number(value):
        return "number"
    return type(value).__name__


def validate_trace(trace: Any) -> list[str]:
    """Validate one decoded trace object.

    Returns a list of human-readable errors. An empty list means the trace is valid for
    schema version 1.0. Values are never echoed in error messages.
    """
    errors: list[str] = []
    if not isinstance(trace, dict):
        return [f"$: expected object, got {_type_name(trace)}"]

    unknown = sorted(set(trace) - _TOP_LEVEL_KEYS)
    for key in unknown:
        errors.append(f"$: unknown top-level field {key!r}")

    version = trace.get("schema_version")
    if not isinstance(version, str):
        errors.append("$.schema_version: required string")
    elif version not in SUPPORTED_SCHEMA_VERSIONS:
        errors.append(f"$.schema_version: unsupported version {version!r}")

    trace_id = trace.get("trace_id")
    if trace_id is not None and (not isinstance(trace_id, str) or not trace_id.strip()):
        errors.append("$.trace_id: expected non-empty string when present")

    created_at = trace.get("created_at")
    if created_at is not None:
        if not isinstance(created_at, str):
            errors.append("$.created_at: expected ISO-8601 string")
        else:
            try:
                _parse_iso8601(created_at)
            except ValueError:
                errors.append("$.created_at: expected ISO-8601 string")

    source = trace.get("source")
    if source is not None and not isinstance(source, dict):
        errors.append("$.source: expected object")

    _validate_input(trace.get("input"), errors)
    _validate_output(trace.get("output"), errors)
    _validate_tools(trace.get("tools"), errors)
    _validate_model(trace.get("model"), errors)
    _validate_metrics(trace.get("metrics"), errors)
    _validate_outcome(trace.get("outcome"), errors)
    _validate_expectations(trace.get("expectations"), errors)

    provenance = trace.get("provenance")
    if provenance is not None and not isinstance(provenance, dict):
        errors.append("$.provenance: expected object")

    tags = trace.get("tags")
    if tags is not None:
        if not _is_string_list(tags):
            errors.append("$.tags: expected array of strings")
        elif len(tags) > MAX_TAGS:
            errors.append(f"$.tags: at most {MAX_TAGS} tags")

    metadata = trace.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        errors.append("$.metadata: expected object")

    return errors


def _parse_iso8601(value: str) -> None:
    import datetime as dt

    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    dt.datetime.fromisoformat(text)


def _validate_input(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("$.input: required object")
        return
    unknown = sorted(set(value) - {"messages", "context"})
    for key in unknown:
        errors.append(f"$.input: unknown field {key!r}")
    messages = value.get("messages")
    if not isinstance(messages, list) or not messages:
        errors.append("$.input.messages: required non-empty array")
        return
    if len(messages) > MAX_MESSAGES:
        errors.append(f"$.input.messages: at most {MAX_MESSAGES} messages")
    for index, message in enumerate(messages):
        path = f"$.input.messages[{index}]"
        if not isinstance(message, dict):
            errors.append(f"{path}: expected object")
            continue
        role = message.get("role")
        if role not in MESSAGE_ROLES:
            errors.append(f"{path}.role: expected one of {sorted(MESSAGE_ROLES)}")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            errors.append(f"{path}.content: expected string or null")
        tool_calls = message.get("tool_calls")
        if tool_calls is not None:
            _validate_tool_calls(tool_calls, f"{path}.tool_calls", errors)
        tool_call_id = message.get("tool_call_id")
        if tool_call_id is not None and not isinstance(tool_call_id, str):
            errors.append(f"{path}.tool_call_id: expected string")


def _validate_output(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("$.output: required object")
        return
    unknown = sorted(set(value) - {"text", "tool_calls", "structured", "finish_reason", "messages"})
    for key in unknown:
        errors.append(f"$.output: unknown field {key!r}")
    text = value.get("text")
    if text is not None and not isinstance(text, str):
        errors.append("$.output.text: expected string")
    finish_reason = value.get("finish_reason")
    if finish_reason is not None and not isinstance(finish_reason, str):
        errors.append("$.output.finish_reason: expected string")
    tool_calls = value.get("tool_calls")
    if tool_calls is not None:
        _validate_tool_calls(tool_calls, "$.output.tool_calls", errors)
    messages = value.get("messages")
    if messages is not None and not isinstance(messages, list):
        errors.append("$.output.messages: expected array")


def _validate_tool_calls(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        errors.append(f"{path}: expected array")
        return
    for index, call in enumerate(value):
        call_path = f"{path}[{index}]"
        if not isinstance(call, dict):
            errors.append(f"{call_path}: expected object")
            continue
        unknown = sorted(set(call) - {"name", "arguments", "id"})
        for key in unknown:
            errors.append(f"{call_path}: unknown field {key!r}")
        name = call.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{call_path}.name: required non-empty string")
        arguments = call.get("arguments")
        if not isinstance(arguments, dict):
            errors.append(f"{call_path}.arguments: required object")
        call_id = call.get("id")
        if call_id is not None and not isinstance(call_id, str):
            errors.append(f"{call_path}.id: expected string")


def _validate_tools(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, list):
        errors.append("$.tools: expected array")
        return
    if len(value) > MAX_TOOLS:
        errors.append(f"$.tools: at most {MAX_TOOLS} tools")
    for index, tool in enumerate(value):
        path = f"$.tools[{index}]"
        if not isinstance(tool, dict):
            errors.append(f"{path}: expected object")
            continue
        unknown = sorted(set(tool) - {"name", "description", "schema"})
        for key in unknown:
            errors.append(f"{path}: unknown field {key!r}")
        name = tool.get("name")
        if not isinstance(name, str) or not name.strip():
            errors.append(f"{path}.name: required non-empty string")
        description = tool.get("description")
        if description is not None and not isinstance(description, str):
            errors.append(f"{path}.description: expected string")
        schema = tool.get("schema")
        if schema is not None:
            _validate_json_schema(schema, f"{path}.schema", errors)


def _validate_model(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("$.model: required object")
        return
    unknown = sorted(set(value) - {"provider", "name", "version", "parameters"})
    for key in unknown:
        errors.append(f"$.model: unknown field {key!r}")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append("$.model.name: required non-empty string")
    for key in ("provider", "version"):
        field = value.get(key)
        if field is not None and not isinstance(field, str):
            errors.append(f"$.model.{key}: expected string")
    parameters = value.get("parameters")
    if parameters is not None and not isinstance(parameters, dict):
        errors.append("$.model.parameters: expected object")


def _validate_metrics(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append("$.metrics: expected object")
        return
    unknown = sorted(
        set(value) - {"latency_ms", "input_tokens", "output_tokens", "cost_usd", "cached"}
    )
    for key in unknown:
        errors.append(f"$.metrics: unknown field {key!r}")
    for key in ("latency_ms", "input_tokens", "output_tokens", "cost_usd"):
        field = value.get(key)
        if field is not None and (not _is_number(field) or field < 0):
            errors.append(f"$.metrics.{key}: expected non-negative number")
    cached = value.get("cached")
    if cached is not None and not isinstance(cached, bool):
        errors.append("$.metrics.cached: expected boolean")


def _validate_outcome(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append("$.outcome: expected object")
        return
    unknown = sorted(set(value) - {"status", "labels", "category", "notes"})
    for key in unknown:
        errors.append(f"$.outcome: unknown field {key!r}")
    status = value.get("status")
    if status is not None and status not in OUTCOME_STATUSES:
        errors.append(f"$.outcome.status: expected one of {sorted(OUTCOME_STATUSES)}")
    labels = value.get("labels")
    if labels is not None and not _is_string_list(labels):
        errors.append("$.outcome.labels: expected array of strings")
    category = value.get("category")
    if category is not None:
        if not isinstance(category, str):
            errors.append("$.outcome.category: expected string")
        elif category not in FAILURE_CATEGORIES:
            errors.append(f"$.outcome.category: expected one of {sorted(FAILURE_CATEGORIES)}")
    notes = value.get("notes")
    if notes is not None and not isinstance(notes, str):
        errors.append("$.outcome.notes: expected string")


def _validate_expectations(value: Any, errors: list[str]) -> None:
    if value is None:
        return
    if not isinstance(value, dict):
        errors.append("$.expectations: expected object")
        return
    unknown = sorted(set(value) - _EXPECTATION_KEYS)
    for key in unknown:
        errors.append(f"$.expectations: unknown field {key!r}")

    text_equals = value.get("text_equals")
    if text_equals is not None and not isinstance(text_equals, str):
        errors.append("$.expectations.text_equals: expected string")
    for key in ("text_contains", "text_not_contains"):
        field = value.get(key)
        if field is not None:
            if not _is_string_list(field):
                errors.append(f"$.expectations.{key}: expected array of strings")
            elif any(not item for item in field):
                errors.append(f"$.expectations.{key}: entries must be non-empty")
    normalize = value.get("normalize")
    if normalize is not None and not isinstance(normalize, bool):
        errors.append("$.expectations.normalize: expected boolean")

    schema = value.get("json_schema")
    if schema is not None:
        _validate_json_schema(schema, "$.expectations.json_schema", errors)

    if "json_equals" in value:
        try:
            canonical_json(value["json_equals"])
        except (TypeError, ValueError):
            errors.append("$.expectations.json_equals: expected JSON value")

    citations = value.get("citations")
    if citations is not None:
        if not isinstance(citations, dict):
            errors.append("$.expectations.citations: expected object")
        else:
            for key in sorted(set(citations) - {"required", "sources", "pattern"}):
                errors.append(f"$.expectations.citations: unknown field {key!r}")
            required = citations.get("required")
            if required is not None and not isinstance(required, bool):
                errors.append("$.expectations.citations.required: expected boolean")
            sources = citations.get("sources")
            if sources is not None and not _is_string_list(sources):
                errors.append("$.expectations.citations.sources: expected array of strings")
            pattern = citations.get("pattern")
            if pattern is not None:
                _validate_pattern(pattern, "$.expectations.citations.pattern", errors)

    tool_calls = value.get("tool_calls")
    if tool_calls is not None:
        _validate_tool_expectations(tool_calls, errors)

    rules = value.get("rules")
    if rules is not None:
        if not isinstance(rules, list):
            errors.append("$.expectations.rules: expected array")
        elif len(rules) > MAX_RULES:
            errors.append(f"$.expectations.rules: at most {MAX_RULES} rules")
        else:
            for index, rule in enumerate(rules):
                _validate_rule(rule, f"$.expectations.rules[{index}]", errors)

    for key in ("max_cost_usd", "max_latency_ms"):
        field = value.get(key)
        if field is not None and (not _is_number(field) or field < 0):
            errors.append(f"$.expectations.{key}: expected non-negative number")

    rubric = value.get("rubric")
    if rubric is not None:
        _validate_rubric(rubric, errors)

    human_label = value.get("human_label")
    if human_label is not None and human_label not in HUMAN_LABELS:
        errors.append(f"$.expectations.human_label: expected one of {sorted(HUMAN_LABELS)}")


def _validate_tool_expectations(value: Any, errors: list[str]) -> None:
    if not isinstance(value, dict):
        errors.append("$.expectations.tool_calls: expected object")
        return
    for key in sorted(set(value) - {"allowed", "forbidden", "required", "arg_equals"}):
        errors.append(f"$.expectations.tool_calls: unknown field {key!r}")
    for key in ("allowed", "forbidden", "required"):
        field = value.get(key)
        if field is not None and not _is_string_list(field):
            errors.append(f"$.expectations.tool_calls.{key}: expected array of strings")
    arg_equals = value.get("arg_equals")
    if arg_equals is not None:
        if not isinstance(arg_equals, list):
            errors.append("$.expectations.tool_calls.arg_equals: expected array")
            return
        for index, item in enumerate(arg_equals):
            path = f"$.expectations.tool_calls.arg_equals[{index}]"
            if not isinstance(item, dict):
                errors.append(f"{path}: expected object")
                continue
            for key in sorted(set(item) - {"name", "arguments"}):
                errors.append(f"{path}: unknown field {key!r}")
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                errors.append(f"{path}.name: required non-empty string")
            arguments = item.get("arguments")
            if not isinstance(arguments, dict):
                errors.append(f"{path}.arguments: required object")


def _validate_rule(rule: Any, path: str, errors: list[str]) -> None:
    if not isinstance(rule, dict):
        errors.append(f"{path}: expected object")
        return
    for key in sorted(set(rule) - {"type", "field", "pattern", "mode"}):
        errors.append(f"{path}: unknown field {key!r}")
    if rule.get("type") != "regex":
        errors.append(f"{path}.type: expected 'regex'")
    field = rule.get("field")
    if not isinstance(field, str) or not field.strip():
        errors.append(f"{path}.field: required non-empty string")
    mode = rule.get("mode")
    if mode not in {"must_match", "must_not_match"}:
        errors.append(f"{path}.mode: expected 'must_match' or 'must_not_match'")
    _validate_pattern(rule.get("pattern"), f"{path}.pattern", errors)


def _validate_pattern(pattern: Any, path: str, errors: list[str]) -> None:
    if not isinstance(pattern, str):
        errors.append(f"{path}: required string")
        return
    if len(pattern) > MAX_PATTERN_LENGTH:
        errors.append(f"{path}: at most {MAX_PATTERN_LENGTH} characters")
        return
    try:
        re.compile(pattern)
    except re.error:
        errors.append(f"{path}: invalid regular expression")


def _validate_rubric(rubric: Any, errors: list[str]) -> None:
    if not isinstance(rubric, dict):
        errors.append("$.expectations.rubric: expected object")
        return
    for key in sorted(set(rubric) - {"id", "version", "criteria", "min_score", "confidence"}):
        errors.append(f"$.expectations.rubric: unknown field {key!r}")
    for key in ("id", "version", "criteria"):
        field = rubric.get(key)
        if not isinstance(field, str) or not field.strip():
            errors.append(f"$.expectations.rubric.{key}: required non-empty string")
    min_score = rubric.get("min_score")
    if not _is_number(min_score) or not 0.0 <= cast(float, min_score) <= 1.0:
        errors.append("$.expectations.rubric.min_score: expected number in [0, 1]")
    confidence = rubric.get("confidence")
    if confidence is not None and (
        not _is_number(confidence) or not 0.0 <= cast(float, confidence) <= 1.0
    ):
        errors.append("$.expectations.rubric.confidence: expected number in [0, 1]")


def _validate_json_schema(schema: Any, path: str, errors: list[str], depth: int = 0) -> None:
    if depth > MAX_SCHEMA_DEPTH:
        errors.append(f"{path}: schema nesting exceeds {MAX_SCHEMA_DEPTH}")
        return
    if not isinstance(schema, dict):
        errors.append(f"{path}: expected object")
        return
    for key in sorted(set(schema) - _SCHEMA_KEYWORDS):
        errors.append(f"{path}: unsupported schema keyword {key!r}")
    schema_type = schema.get("type")
    if schema_type is not None:
        if isinstance(schema_type, str):
            if schema_type not in _JSON_TYPES:
                errors.append(f"{path}.type: unsupported type {schema_type!r}")
        elif isinstance(schema_type, list):
            if not schema_type or any(
                not isinstance(item, str) or item not in _JSON_TYPES for item in schema_type
            ):
                errors.append(f"{path}.type: expected valid type name or array of type names")
        else:
            errors.append(f"{path}.type: expected string or array")
    properties = schema.get("properties")
    if properties is not None:
        if not isinstance(properties, dict):
            errors.append(f"{path}.properties: expected object")
        else:
            for name, child in properties.items():
                _validate_json_schema(child, f"{path}.properties.{name}", errors, depth + 1)
    required = schema.get("required")
    if required is not None and not _is_string_list(required):
        errors.append(f"{path}.required: expected array of strings")
    items = schema.get("items")
    if items is not None:
        _validate_json_schema(items, f"{path}.items", errors, depth + 1)
    additional = schema.get("additionalProperties")
    if additional is not None and not isinstance(additional, bool):
        _validate_json_schema(additional, f"{path}.additionalProperties", errors, depth + 1)
    enum = schema.get("enum")
    if enum is not None and not isinstance(enum, list):
        errors.append(f"{path}.enum: expected array")
    if "const" in schema:
        try:
            canonical_json(schema["const"])
        except (TypeError, ValueError):
            errors.append(f"{path}.const: expected JSON value")
    for key in ("minimum", "maximum"):
        field = schema.get(key)
        if field is not None and not _is_number(field):
            errors.append(f"{path}.{key}: expected number")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        field = schema.get(key)
        if field is not None and (
            not isinstance(field, int) or isinstance(field, bool) or field < 0
        ):
            errors.append(f"{path}.{key}: expected non-negative integer")
    pattern = schema.get("pattern")
    if pattern is not None:
        _validate_pattern(pattern, f"{path}.pattern", errors)
    for key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if branches is not None:
            if not isinstance(branches, list) or not branches:
                errors.append(f"{path}.{key}: expected non-empty array")
            else:
                for index, child in enumerate(branches):
                    _validate_json_schema(child, f"{path}.{key}[{index}]", errors, depth + 1)


def parse_trace_line(line: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Decode and validate one JSONL line. Returns ``(trace, errors)``."""
    try:
        decoded = json.loads(line)
    except json.JSONDecodeError as exc:
        return None, [f"line is not valid JSON: {exc.msg} at column {exc.colno}"]
    errors = validate_trace(decoded)
    if errors:
        return None, errors
    assert isinstance(decoded, dict)
    return decoded, []


def normalize_trace(trace: dict[str, Any]) -> dict[str, Any]:
    """Return a normalized copy used for stable identity and storage.

    Strings are NFKC-normalized with line endings canonicalized. Regular-expression
    patterns and JSON Schema definitions are preserved byte-for-byte.
    """
    normalized = _normalize_generic(copy.deepcopy(trace))
    assert isinstance(normalized, dict)
    original = trace
    original_expectations = original.get("expectations")
    normalized_expectations = normalized.get("expectations")
    if isinstance(original_expectations, dict) and isinstance(normalized_expectations, dict):
        if "json_schema" in original_expectations:
            normalized_expectations["json_schema"] = copy.deepcopy(
                original_expectations["json_schema"]
            )
        original_rules = original_expectations.get("rules")
        normalized_rules = normalized_expectations.get("rules")
        if isinstance(original_rules, list) and isinstance(normalized_rules, list):
            for original_rule, normalized_rule in zip(
                original_rules, normalized_rules, strict=False
            ):
                if (
                    isinstance(original_rule, dict)
                    and isinstance(normalized_rule, dict)
                    and "pattern" in original_rule
                ):
                    normalized_rule["pattern"] = original_rule["pattern"]
        original_citations = original_expectations.get("citations")
        normalized_citations = normalized_expectations.get("citations")
        if (
            isinstance(original_citations, dict)
            and isinstance(normalized_citations, dict)
            and "pattern" in original_citations
        ):
            normalized_citations["pattern"] = original_citations["pattern"]
    return normalized


def _normalize_generic(value: Any) -> Any:
    if isinstance(value, str):
        return normalize_string(value)
    if isinstance(value, list):
        return [_normalize_generic(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalize_generic(item) for key, item in value.items()}
    return value


def case_identity_payload(trace: dict[str, Any]) -> dict[str, Any]:
    """Fields that define a case's identity.

    Volatile capture metadata (trace ID, timestamps, source environment, provenance,
    measured latency/cost) and human labels are excluded so the same interaction keeps
    the same case ID across imports and re-labels.
    """
    return {
        "schema_version": trace["schema_version"],
        "input": trace.get("input", {}),
        "output": trace.get("output", {}),
        "tools": trace.get("tools", []),
        "model": trace.get("model", {}),
        "expectations": trace.get("expectations", {}),
        "tags": sorted(trace.get("tags", [])),
    }


def case_id_for_trace(trace: dict[str, Any]) -> str:
    """Compute the stable case ID from a normalized trace."""
    return "case_" + stable_hash(case_identity_payload(trace), 24)


def content_hash(trace: dict[str, Any]) -> str:
    """Full-content hash for provenance and duplicate diagnostics."""
    return stable_hash(trace, 64)


def load_json_schema() -> dict[str, Any]:
    """Load the packaged JSON Schema document."""
    text = resources.files("trace_to_eval.data").joinpath("trace-v1.schema.json").read_text("utf-8")
    decoded: dict[str, Any] = json.loads(text)
    return decoded
