"""Shared test fixtures.

Every test runs with a clean environment so that a developer's shell cannot change the
outcome (live-call consent, provider keys, or an ambient config file).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"

_ISOLATED_ENV = (
    "TRACE_TO_EVAL_DB",
    "TRACE_TO_EVAL_CONFIG",
    "TRACE_TO_EVAL_ALLOW_LIVE",
    "TRACE_TO_EVAL_DAILY_COST_CAP_USD",
    "TRACE_TO_EVAL_MONTHLY_COST_CAP_USD",
    "TRACE_TO_EVAL_DAILY_REQUEST_CAP",
    "TRACE_TO_EVAL_MONTHLY_REQUEST_CAP",
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GITHUB_SHA",
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _ISOLATED_ENV:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def traces() -> list[dict[str, Any]]:
    """The five synthetic traces used by the end-to-end fixtures."""
    lines = (FIXTURES / "traces.jsonl").read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def fake_script() -> Path:
    return FIXTURES / "fake-script.json"
