"""A dependency-free JSON Schema subset used to validate model output.

Supported keywords are the ones the trace schema permits in ``json_schema`` expectations.
Unsupported constructs are rejected at load time by ``schema._validate_json_schema``, so
this validator never silently ignores a constraint.
"""

from __future__ import annotations

import re
from typing import Any


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    if expected == "integer":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        return isinstance(value, float) and value.is_integer()
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return False


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
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    return type(value).__name__


def validate_schema(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate ``value`` against ``schema``; returns error strings with data paths."""
    errors: list[str] = []
    _validate(value, schema, path, errors)
    return errors


def _validate(value: Any, schema: dict[str, Any], path: str, errors: list[str]) -> None:
    expected_type = schema.get("type")
    if expected_type is not None:
        allowed = [expected_type] if isinstance(expected_type, str) else list(expected_type)
        if not any(_type_matches(value, item) for item in allowed):
            errors.append(f"{path}: expected {' or '.join(allowed)}, got {_type_name(value)}")
            return

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant value")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value not in allowed set")

    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        for name in required:
            if name not in value:
                errors.append(f"{path}.{name}: required property is missing")
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            if name in properties:
                _validate(item, properties[name], f"{path}.{name}", errors)
            elif additional is False:
                errors.append(f"{path}.{name}: property is not allowed")
            elif isinstance(additional, dict):
                _validate(item, additional, f"{path}.{name}", errors)

    if isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                _validate(item, items, f"{path}[{index}]", errors)
        min_items = schema.get("minItems")
        if min_items is not None and len(value) < min_items:
            errors.append(f"{path}: expected at least {min_items} items")
        max_items = schema.get("maxItems")
        if max_items is not None and len(value) > max_items:
            errors.append(f"{path}: expected at most {max_items} items")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: expected at least {min_length} characters")
        max_length = schema.get("maxLength")
        if max_length is not None and len(value) > max_length:
            errors.append(f"{path}: expected at most {max_length} characters")
        pattern = schema.get("pattern")
        if pattern is not None and re.search(pattern, value) is None:
            errors.append(f"{path}: does not match required pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: expected value >= {minimum}")
        maximum = schema.get("maximum")
        if maximum is not None and value > maximum:
            errors.append(f"{path}: expected value <= {maximum}")

    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if branches is None:
            continue
        matched = 0
        for branch in branches:
            branch_errors: list[str] = []
            _validate(value, branch, path, branch_errors)
            if not branch_errors:
                matched += 1
        if key == "anyOf" and matched == 0:
            errors.append(f"{path}: value does not match any allowed schema")
        if key == "oneOf" and matched != 1:
            errors.append(f"{path}: value must match exactly one allowed schema, matched {matched}")

    all_of = schema.get("allOf")
    if all_of is not None:
        for branch in all_of:
            _validate(value, branch, path, errors)
