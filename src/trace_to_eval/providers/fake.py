"""Deterministic fake providers. The default provider and the only one CI needs.

A fake provider replays the recorded output of each case, optionally transformed by a
declarative script. Scripts let tests model a new model version, a prompt regression, a
cost increase, a latency spike, or an infrastructure failure without any network call.

Script format (JSON)::

    {
      "models": {
        "fake-baseline-v1": { "mode": "echo" },
        "fake-regressed-v2": {
          "mode": "mutate",
          "text_suffix": " (truncated)",
          "cost_multiplier": 4.0,
          "latency_multiplier": 1.0,
          "tool_argument_overrides": { "search_docs": { "limit": 1 } }
        }
      }
    }

Per-case overrides are supported through
``"cases": {"<case_id or trace_id>": {...behavior...}}``. A case override may supply
``"tool_calls"`` to replace the recorded calls, which models a corrected behavior for a
recorded production failure. A behavior may instead specify ``"error": "<error class>"``
to raise a provider error.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import (
    FakeScriptError,
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from ..util import stable_hash
from .base import CompletionRequest, CompletionResponse, Provider, ToolCall

ERROR_CLASSES = {
    "timeout": ProviderTimeout,
    "rate_limited": ProviderRateLimited,
    "provider_unavailable": ProviderUnavailable,
    "bad_request": ProviderBadRequest,
}

_BEHAVIOR_KEYS = frozenset(
    {
        "mode",
        "text",
        "text_suffix",
        "text_prefix",
        "cost_multiplier",
        "cost_per_1k_tokens",
        "latency_ms",
        "latency_multiplier",
        "tool_argument_overrides",
        "tool_calls",
        "drop_tool_calls",
        "error",
        "finish_reason",
        "cached",
    }
)


@dataclass
class FakeBehavior:
    mode: str = "echo"
    text: str | None = None
    text_suffix: str = ""
    text_prefix: str = ""
    cost_multiplier: float = 1.0
    cost_per_1k_tokens: float | None = None
    latency_ms: float | None = None
    latency_multiplier: float = 1.0
    tool_argument_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    tool_calls: list[dict[str, Any]] | None = None
    drop_tool_calls: bool = False
    error: str | None = None
    finish_reason: str | None = None
    cached: bool = False

    @classmethod
    def from_mapping(cls, value: dict[str, Any], *, where: str) -> FakeBehavior:
        if not isinstance(value, dict):
            raise FakeScriptError(f"{where}: behavior must be an object")
        unknown = sorted(set(value) - _BEHAVIOR_KEYS)
        if unknown:
            raise FakeScriptError(f"{where}: unknown behavior fields {unknown}")
        mode = value.get("mode", "echo")
        if mode not in {"echo", "mutate"}:
            raise FakeScriptError(f"{where}.mode: expected 'echo' or 'mutate'")
        for key in ("text", "text_suffix", "text_prefix", "finish_reason"):
            field_value = value.get(key)
            if field_value is not None and not isinstance(field_value, str):
                raise FakeScriptError(f"{where}.{key}: expected string")
        for key in ("cost_multiplier", "cost_per_1k_tokens", "latency_ms", "latency_multiplier"):
            field_value = value.get(key)
            if field_value is not None and (
                not isinstance(field_value, (int, float)) or isinstance(field_value, bool)
            ):
                raise FakeScriptError(f"{where}.{key}: expected number")
            if field_value is not None and field_value < 0:
                raise FakeScriptError(f"{where}.{key}: expected non-negative number")
        overrides = value.get("tool_argument_overrides", {})
        if not isinstance(overrides, dict) or not all(
            isinstance(key, str) and isinstance(item, dict) for key, item in overrides.items()
        ):
            raise FakeScriptError(f"{where}.tool_argument_overrides: expected object of objects")
        tool_calls = value.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list):
                raise FakeScriptError(f"{where}.tool_calls: expected array")
            for index, call in enumerate(tool_calls):
                if not isinstance(call, dict):
                    raise FakeScriptError(f"{where}.tool_calls[{index}]: expected object")
                if not isinstance(call.get("name"), str) or not call["name"].strip():
                    raise FakeScriptError(
                        f"{where}.tool_calls[{index}].name: required non-empty string"
                    )
                if not isinstance(call.get("arguments", {}), dict):
                    raise FakeScriptError(f"{where}.tool_calls[{index}].arguments: expected object")
        for key in ("drop_tool_calls", "cached"):
            field_value = value.get(key)
            if field_value is not None and not isinstance(field_value, bool):
                raise FakeScriptError(f"{where}.{key}: expected boolean")
        error = value.get("error")
        if error is not None and error not in ERROR_CLASSES:
            raise FakeScriptError(f"{where}.error: expected one of {sorted(ERROR_CLASSES)}")
        return cls(
            mode=mode,
            text=value.get("text"),
            text_suffix=value.get("text_suffix", ""),
            text_prefix=value.get("text_prefix", ""),
            cost_multiplier=float(value.get("cost_multiplier", 1.0)),
            cost_per_1k_tokens=(
                float(value["cost_per_1k_tokens"])
                if value.get("cost_per_1k_tokens") is not None
                else None
            ),
            latency_ms=(
                float(value["latency_ms"]) if value.get("latency_ms") is not None else None
            ),
            latency_multiplier=float(value.get("latency_multiplier", 1.0)),
            tool_argument_overrides={key: dict(item) for key, item in overrides.items()},
            tool_calls=[dict(call) for call in tool_calls] if tool_calls is not None else None,
            drop_tool_calls=bool(value.get("drop_tool_calls", False)),
            error=error,
            finish_reason=value.get("finish_reason"),
            cached=bool(value.get("cached", False)),
        )


@dataclass
class FakeScript:
    models: dict[str, FakeBehavior] = field(default_factory=dict)
    cases: dict[str, FakeBehavior] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> FakeScript:
        source = Path(path).expanduser()
        try:
            raw = json.loads(source.read_text("utf-8"))
        except FileNotFoundError as exc:
            raise FakeScriptError(f"fake script not found: {source}") from exc
        except json.JSONDecodeError as exc:
            raise FakeScriptError(f"fake script is not valid JSON: {exc.msg}") from exc
        return cls.from_mapping(raw)

    @classmethod
    def from_mapping(cls, value: Any) -> FakeScript:
        if not isinstance(value, dict):
            raise FakeScriptError("fake script must be an object")
        unknown = sorted(set(value) - {"models", "cases"})
        if unknown:
            raise FakeScriptError(f"fake script: unknown fields {unknown}")
        models_raw = value.get("models", {})
        cases_raw = value.get("cases", {})
        if not isinstance(models_raw, dict) or not isinstance(cases_raw, dict):
            raise FakeScriptError("fake script: 'models' and 'cases' must be objects")
        models = {
            str(name): FakeBehavior.from_mapping(item, where=f"models.{name}")
            for name, item in models_raw.items()
        }
        cases = {
            str(name): FakeBehavior.from_mapping(item, where=f"cases.{name}")
            for name, item in cases_raw.items()
        }
        return cls(models=models, cases=cases)

    def behavior_for(self, model: str, case_id: str, trace_id: str | None = None) -> FakeBehavior:
        if case_id in self.cases:
            return self.cases[case_id]
        if trace_id and trace_id in self.cases:
            return self.cases[trace_id]
        return self.models.get(model, FakeBehavior())


def estimate_tokens(text: str) -> int:
    """Deterministic token estimate: at least one token per four characters."""
    return max(1, (len(text) + 3) // 4)


class FakeProvider(Provider):
    """Replays recorded outputs deterministically with optional scripted mutations."""

    live = False

    def __init__(
        self,
        model: str = "fake-baseline-v1",
        *,
        script: FakeScript | None = None,
        name: str = "fake",
        sleep: bool = False,
    ) -> None:
        self.name = name
        self.model = model
        self.script = script or FakeScript()
        self._sleep = sleep

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        behavior = self.script.behavior_for(request.model, request.case_id, request.trace_id)
        if behavior.error is not None:
            error_type = ERROR_CLASSES[behavior.error]
            raise error_type(
                f"fake provider raised {behavior.error} for case {request.case_id}",
                request_id=self._request_id(request),
            )
        if timeout_s <= 0:
            raise ProviderTimeout(
                "timeout budget must be positive", request_id=self._request_id(request)
            )

        source = request.source_output or {}
        text = behavior.text if behavior.text is not None else str(source.get("text", ""))
        if behavior.mode == "mutate":
            text = f"{behavior.text_prefix}{text}{behavior.text_suffix}"

        tool_calls: list[ToolCall] = []
        if not behavior.drop_tool_calls:
            raw_calls = (
                behavior.tool_calls
                if behavior.tool_calls is not None
                else list(source.get("tool_calls") or [])
            )
            for raw_call in raw_calls:
                name = str(raw_call.get("name", ""))
                arguments = dict(raw_call.get("arguments") or {})
                if name in behavior.tool_argument_overrides:
                    arguments.update(behavior.tool_argument_overrides[name])
                tool_calls.append(ToolCall(name=name, arguments=arguments, id=raw_call.get("id")))

        input_tokens = self.count_tokens(request)
        output_tokens = estimate_tokens(text)
        source_metrics = request.parameters.get("source_metrics") or {}
        if behavior.cost_per_1k_tokens is not None:
            cost = (input_tokens + output_tokens) / 1000.0 * behavior.cost_per_1k_tokens
        else:
            recorded_cost = float(source_metrics.get("cost_usd", 0.0) or 0.0)
            cost = recorded_cost * behavior.cost_multiplier
        recorded_latency = float(source_metrics.get("latency_ms", 0.0) or 0.0)
        latency = (
            behavior.latency_ms
            if behavior.latency_ms is not None
            else recorded_latency * behavior.latency_multiplier
        )
        if self._sleep and latency > 0:
            time.sleep(min(latency, 50.0) / 1000.0)

        return CompletionResponse(
            text=text,
            tool_calls=tool_calls,
            structured=source.get("structured"),
            finish_reason=behavior.finish_reason or str(source.get("finish_reason", "stop")),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency,
            request_id=self._request_id(request),
            model=request.model,
            provider=self.name,
            cached=behavior.cached,
        )

    def count_tokens(self, request: CompletionRequest) -> int:
        payload = [request.prompt_text()]
        payload.extend(json.dumps(tool, sort_keys=True) for tool in request.tools)
        return estimate_tokens("\n".join(payload))

    @staticmethod
    def _request_id(request: CompletionRequest) -> str:
        digest = stable_hash(
            {"case_id": request.case_id, "model": request.model, "prompt": request.prompt_text()},
            16,
        )
        return f"fake_{digest}"
