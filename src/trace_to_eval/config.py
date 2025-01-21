"""Configuration loading from ``trace-to-eval.toml`` or ``pyproject.toml``.

Precedence, lowest to highest: built-in defaults, config file, environment, CLI flags.
Unknown keys are rejected so a typo cannot silently disable a gate.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .budget import BudgetLimits
from .errors import ConfigError
from .gates import Thresholds
from .redact import RedactionConfig

DEFAULT_DB = ".trace-to-eval/cases.db"
_CONFIG_FILENAMES = ("trace-to-eval.toml", "pyproject.toml")
_ALLOWED_KEYS = {
    "db",
    "provider",
    "model",
    "prompt_version",
    "code_revision",
    "fake_script",
    "base_url",
    "rubric",
    "rubric_model",
    "thresholds",
    "budget",
    "redaction",
}


@dataclass
class Config:
    db: str = DEFAULT_DB
    provider: str = "fake"
    model: str = "fake-baseline-v1"
    prompt_version: str = "unversioned"
    code_revision: str = "unknown"
    fake_script: str | None = None
    base_url: str | None = None
    rubric: bool = False
    rubric_model: str | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    budget: BudgetLimits = field(default_factory=BudgetLimits)
    redaction: RedactionConfig = field(default_factory=RedactionConfig)
    source: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "db": self.db,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "code_revision": self.code_revision,
            "fake_script": self.fake_script,
            "base_url": self.base_url,
            "rubric": self.rubric,
            "rubric_model": self.rubric_model,
            "thresholds": self.thresholds.as_dict(),
            "budget": self.budget.as_dict(),
            "source": self.source,
        }


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text("utf-8"))
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc.strerror}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"config file {path} is not valid TOML: {exc}") from None


def _extract_section(document: dict[str, Any], path: Path) -> dict[str, Any]:
    if path.name == "pyproject.toml":
        tool = document.get("tool")
        if not isinstance(tool, dict):
            return {}
        section = tool.get("trace_to_eval")
        if section is None:
            return {}
    else:
        section = document.get("trace_to_eval", document)
    if not isinstance(section, dict):
        raise ConfigError(f"{path}: [tool.trace_to_eval] must be a table")
    return section


def find_config(explicit: str | None, *, root: Path | None = None) -> Path | None:
    if explicit:
        candidate = Path(explicit).expanduser()
        if not candidate.is_file():
            raise ConfigError(f"config file not found: {candidate}")
        return candidate
    base = root or Path.cwd()
    for name in _CONFIG_FILENAMES:
        candidate = base / name
        if candidate.is_file():
            if name == "pyproject.toml":
                document = _read_toml(candidate)
                tool = document.get("tool")
                if not isinstance(tool, dict) or "trace_to_eval" not in tool:
                    continue
            return candidate
    return None


def load_config(explicit: str | None = None, *, root: Path | None = None) -> Config:
    config = Config()
    path = find_config(explicit, root=root)
    if path is None:
        return _apply_environment(config)
    section = _extract_section(_read_toml(path), path)
    unknown = sorted(set(section) - _ALLOWED_KEYS)
    if unknown:
        raise ConfigError(f"{path}: unknown [tool.trace_to_eval] keys: {unknown}")

    for key in (
        "db",
        "provider",
        "model",
        "prompt_version",
        "code_revision",
        "fake_script",
        "base_url",
        "rubric_model",
    ):
        if key in section:
            value = section[key]
            if value is not None and not isinstance(value, str):
                raise ConfigError(f"{path}: {key} must be a string")
            setattr(config, key, value)

    if "rubric" in section:
        if not isinstance(section["rubric"], bool):
            raise ConfigError(f"{path}: rubric must be a boolean")
        config.rubric = section["rubric"]

    if "thresholds" in section:
        if not isinstance(section["thresholds"], dict):
            raise ConfigError(f"{path}: thresholds must be a table")
        config.thresholds = Thresholds.from_mapping(section["thresholds"])
    if "budget" in section:
        if not isinstance(section["budget"], dict):
            raise ConfigError(f"{path}: budget must be a table")
        config.budget = BudgetLimits.from_mapping(section["budget"])
    if "redaction" in section:
        if not isinstance(section["redaction"], dict):
            raise ConfigError(f"{path}: redaction must be a table")
        try:
            config.redaction = RedactionConfig.from_mapping(section["redaction"])
        except ValueError as exc:
            raise ConfigError(f"{path}: {exc}") from None

    config.source = str(path)
    return _apply_environment(config)


def _apply_environment(config: Config) -> Config:
    if os.environ.get("TRACE_TO_EVAL_DB"):
        config.db = os.environ["TRACE_TO_EVAL_DB"]
    return config
