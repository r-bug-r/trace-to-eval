"""Redaction of secrets and configurable personal data before persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

ALL_CATEGORIES = frozenset(
    {
        "private_keys",
        "api_keys",
        "bearer_tokens",
        "secret_assignments",
        "credit_cards",
        "emails",
        "phone_numbers",
        "ipv4_addresses",
    }
)

DEFAULT_CATEGORIES = frozenset(
    {
        "private_keys",
        "api_keys",
        "bearer_tokens",
        "secret_assignments",
        "credit_cards",
        "emails",
    }
)

_PATTERNS: dict[str, re.Pattern[str]] = {
    "private_keys": re.compile(
        r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
        re.DOTALL,
    ),
    "api_keys": re.compile(
        r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,}"
        r"|gh[pousr]_[A-Za-z0-9]{20,}"
        r"|github_pat_[A-Za-z0-9_]{20,}"
        r"|AKIA[0-9A-Z]{16}"
        r"|ASIA[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_-]{35}"
        r"|xox[baprs]-[A-Za-z0-9-]{10,})\b"
    ),
    "bearer_tokens": re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    "secret_assignments": re.compile(
        r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret"
        r"|secret|password|passwd)\b\s*[:=]\s*)([\"']?)([A-Za-z0-9_./+=-]{8,})(\2)"
    ),
    "emails": re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    "phone_numbers": re.compile(
        r"(?<![\w])(?:\+\d{1,3}[\s.-]?)?(?:\(\d{2,4}\)|\d{2,4})[\s.-]\d{3,4}[\s.-]\d{3,4}(?![\w])"
    ),
    "ipv4_addresses": re.compile(r"(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])"),
}

_CREDIT_CARD_RE = re.compile(r"(?<![\d-])(?:\d[ -]?){12,18}\d(?![\d-])")
_MAX_CUSTOM_PATTERN = 300

_SKIP_PATHS = frozenset(
    {
        ("expectations", "json_schema"),
    }
)


@dataclass(frozen=True)
class CustomPattern:
    name: str
    pattern: re.Pattern[str]


@dataclass
class RedactionConfig:
    """Which data classes to redact. Defaults favor safety over minimal changes."""

    categories: frozenset[str] = DEFAULT_CATEGORIES
    custom: tuple[CustomPattern, ...] = ()

    def __post_init__(self) -> None:
        unknown = set(self.categories) - ALL_CATEGORIES
        if unknown:
            raise ValueError(f"unknown redaction categories: {sorted(unknown)}")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> RedactionConfig:
        unknown = set(value) - {"categories", "custom"}
        if unknown:
            raise ValueError(f"unknown redaction config fields: {sorted(unknown)}")
        categories = value.get("categories", sorted(DEFAULT_CATEGORIES))
        if not isinstance(categories, list) or not all(
            isinstance(item, str) for item in categories
        ):
            raise ValueError("redaction categories must be an array of strings")
        unknown_categories = set(categories) - ALL_CATEGORIES
        if unknown_categories:
            raise ValueError(f"unknown redaction categories: {sorted(unknown_categories)}")
        custom_value = value.get("custom", [])
        if not isinstance(custom_value, list):
            raise ValueError("redaction custom patterns must be an array")
        custom: list[CustomPattern] = []
        for index, item in enumerate(custom_value):
            if not isinstance(item, dict):
                raise ValueError(f"custom pattern {index} must be an object")
            extra = set(item) - {"name", "pattern"}
            if extra:
                raise ValueError(f"custom pattern {index} has unknown fields: {sorted(extra)}")
            name = item.get("name")
            pattern = item.get("pattern")
            if not isinstance(name, str) or not name.strip():
                raise ValueError(f"custom pattern {index} needs a non-empty name")
            if not isinstance(pattern, str) or not pattern:
                raise ValueError(f"custom pattern {index} needs a pattern")
            if len(pattern) > _MAX_CUSTOM_PATTERN:
                raise ValueError(f"custom pattern {index} exceeds {_MAX_CUSTOM_PATTERN} characters")
            try:
                compiled = re.compile(pattern)
            except re.error as exc:
                raise ValueError(f"custom pattern {index} is invalid: {exc.msg}") from exc
            custom.append(CustomPattern(name=name, pattern=compiled))
        return cls(categories=frozenset(categories), custom=tuple(custom))


@dataclass
class RedactionReport:
    total: int = 0
    by_category: dict[str, int] = field(default_factory=dict)

    def add(self, category: str, count: int = 1) -> None:
        if count <= 0:
            return
        self.total += count
        self.by_category[category] = self.by_category.get(category, 0) + count

    def merge(self, other: RedactionReport) -> None:
        self.total += other.total
        for category, count in other.by_category.items():
            self.by_category[category] = self.by_category.get(category, 0) + count

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "by_category": dict(sorted(self.by_category.items()))}


def _luhn_valid(digits: str) -> bool:
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        value = int(character)
        if index % 2 == parity:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def _redact_credit_cards(text: str, report: RedactionReport) -> str:
    def replace(match: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", match.group(0))
        if _luhn_valid(digits):
            report.add("credit_cards")
            return "[REDACTED:credit_card]"
        return match.group(0)

    return _CREDIT_CARD_RE.sub(replace, text)


def _redact_ipv4(text: str, report: RedactionReport) -> str:
    def replace(match: re.Match[str]) -> str:
        parts = match.group(0).split(".")
        if all(part.isdigit() and 0 <= int(part) <= 255 for part in parts):
            report.add("ipv4_addresses")
            return "[REDACTED:ipv4_address]"
        return match.group(0)

    return _PATTERNS["ipv4_addresses"].sub(replace, text)


def redact_text(text: str, config: RedactionConfig | None = None) -> tuple[str, RedactionReport]:
    """Redact one string. Idempotent: placeholders do not match the patterns again."""
    cfg = config or RedactionConfig()
    report = RedactionReport()
    result = text
    for category in (
        "private_keys",
        "api_keys",
        "bearer_tokens",
        "secret_assignments",
        "emails",
    ):
        if category not in cfg.categories:
            continue
        if category == "secret_assignments":

            def replace_assignment(match: re.Match[str]) -> str:
                report.add("secret_assignments")
                return f"{match.group(1)}{match.group(2)}[REDACTED:secret]{match.group(4)}"

            result = _PATTERNS[category].sub(replace_assignment, result)
        else:
            placeholder = f"[REDACTED:{category.rstrip('s')}]"
            result, count = _PATTERNS[category].subn(placeholder, result)
            report.add(category, count)
    if "credit_cards" in cfg.categories:
        result = _redact_credit_cards(result, report)
    if "phone_numbers" in cfg.categories:
        result, count = _PATTERNS["phone_numbers"].subn("[REDACTED:phone]", result)
        report.add("phone_numbers", count)
    if "ipv4_addresses" in cfg.categories:
        result = _redact_ipv4(result, report)
    for custom in cfg.custom:
        placeholder = f"[REDACTED:{custom.name}]"

        def _replace(_match: re.Match[str], value: str = placeholder) -> str:
            return value

        result, count = custom.pattern.subn(_replace, result)
        report.add(f"custom:{custom.name}", count)
    return result, report


def redact_trace(
    trace: dict[str, Any], config: RedactionConfig | None = None
) -> tuple[dict[str, Any], RedactionReport]:
    """Recursively redact captured data before persistence.

    Assertion definitions are preserved: regex patterns and JSON Schema documents are
    validation logic, not captured user data.
    """
    report = RedactionReport()
    redacted = _redact_value(trace, config or RedactionConfig(), report, path=())
    assert isinstance(redacted, dict)
    return redacted, report


def _redact_value(
    value: Any,
    config: RedactionConfig,
    report: RedactionReport,
    *,
    path: tuple[str, ...],
) -> Any:
    if isinstance(value, str):
        redacted, partial = redact_text(value, config)
        report.merge(partial)
        return redacted
    if isinstance(value, list):
        return [
            _redact_value(item, config, report, path=(*path, str(index)))
            for index, item in enumerate(value)
        ]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            child_path = (*path, str(key))
            if child_path in _SKIP_PATHS:
                result[key] = item
                continue
            if path[:2] == ("expectations", "rules") and key == "pattern":
                result[key] = item
                continue
            if path == ("expectations", "citations") and key == "pattern":
                result[key] = item
                continue
            result[str(key)] = _redact_value(item, config, report, path=child_path)
        return result
    return value
