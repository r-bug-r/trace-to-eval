"""Redaction of secrets and personal data before persistence."""

from __future__ import annotations

from typing import Any

import pytest

from trace_to_eval.redact import RedactionConfig, redact_text, redact_trace


def _categories(report: Any) -> set[str]:
    return set(report.by_category)


@pytest.mark.parametrize(
    ("text", "category"),
    [
        ("key sk-proj-abcdefghijklmnopqrstuvwxyz012345", "api_keys"),
        ("token ghp_abcdefghijklmnopqrstuvwxyz012345", "api_keys"),
        ("Authorization: Bearer abc.def.ghi-123", "bearer_tokens"),
        ("password=hunter2hunter2", "secret_assignments"),
        ("contact alice@example.com please", "emails"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "private_keys"),
    ],
)
def test_default_categories_are_redacted(text: str, category: str) -> None:
    redacted, report = redact_text(text)
    assert category in _categories(report)
    assert redacted != text
    assert "REDACTED" in redacted


def test_phone_and_ipv4_are_opt_in() -> None:
    text = "call +1 415-555-0100 from 10.0.0.7"
    _, default_report = redact_text(text)
    assert "phone_numbers" not in _categories(default_report)
    assert "ipv4_addresses" not in _categories(default_report)

    _, all_report = redact_text(
        text,
        RedactionConfig(
            categories=frozenset(
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
        ),
    )
    assert "phone_numbers" in _categories(all_report)
    assert "ipv4_addresses" in _categories(all_report)


def test_luhn_valid_card_is_redacted_and_invalid_is_kept() -> None:
    valid, valid_report = redact_text("card 4111 1111 1111 1111")
    assert "credit_cards" in _categories(valid_report)
    assert "4111" not in valid

    invalid, invalid_report = redact_text("card 4111 1111 1111 1112")
    assert "credit_cards" not in _categories(invalid_report)
    assert "4111 1111 1111 1112" in invalid


def test_custom_pattern_is_applied() -> None:
    config = RedactionConfig.from_mapping(
        {"categories": [], "custom": [{"name": "employee_id", "pattern": r"EMP-\d{6}"}]}
    )
    redacted, report = redact_text("owner EMP-123456", config)
    assert report.by_category == {"custom:employee_id": 1}
    assert redacted == "owner [REDACTED:employee_id]"


def test_redaction_is_idempotent() -> None:
    text = "mail alice@example.com key sk-proj-abcdefghijklmnopqrstuvwxyz012345"
    once, _ = redact_text(text)
    twice, report = redact_text(once)
    assert twice == once
    assert report.total == 0


def test_redact_trace_preserves_assertion_patterns_and_schema() -> None:
    trace: dict[str, Any] = {
        "schema_version": "1.0",
        "input": {"messages": [{"role": "user", "content": "alice@example.com"}]},
        "output": {"text": "reply to alice@example.com"},
        "model": {"name": "fake-baseline-v1"},
        "expectations": {
            "rules": [{"path": "output.text", "matches": r"[a-z]+@example\.com"}],
            "citations": {"pattern": r"\[doc#[a-z-]+\]"},
            "json_schema": {"type": "string", "pattern": r"^[a-z]+$"},
        },
    }
    redacted, report = redact_trace(trace)
    assert report.total == 2
    assert "alice@example.com" not in redacted["input"]["messages"][0]["content"]
    assert redacted["expectations"] == trace["expectations"]


def test_unknown_category_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown redaction categories"):
        RedactionConfig(categories=frozenset({"passwords"}))
