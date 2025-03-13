"""Provider-neutral model interface.

Mirrors the portfolio contract: ``complete(request) -> response``,
``count_tokens(request) -> estimate``, and ``stream(request) -> events``.
Adapters must record provider, model, request ID, token counts, latency, and estimated
cost, and must support cancellation and timeouts.
"""

from __future__ import annotations

import abc
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"name": self.name, "arguments": self.arguments}
        if self.id is not None:
            payload["id"] = self.id
        return payload


@dataclass
class CompletionRequest:
    """One replay request derived from a stored case.

    ``source_output`` is the recorded output used only by deterministic fake providers.
    It is never sent to a live provider.
    """

    model: str
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] = field(default_factory=list)
    parameters: dict[str, Any] = field(default_factory=dict)
    case_id: str = ""
    trace_id: str | None = None
    prompt_version: str = "unversioned"
    source_output: dict[str, Any] = field(default_factory=dict)
    max_output_tokens: int | None = None

    def prompt_text(self) -> str:
        parts: list[str] = []
        for message in self.messages:
            role = message.get("role", "")
            content = message.get("content")
            parts.append(f"{role}: {content or ''}")
            for call in message.get("tool_calls") or []:
                parts.append(f"{role} tool_call: {call.get('name')}")
        return "\n".join(parts)


@dataclass
class CompletionResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    structured: Any = None
    finish_reason: str = "stop"
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    request_id: str | None = None
    model: str = ""
    provider: str = ""
    cached: bool = False
    raw: dict[str, Any] | None = None

    def output_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"text": self.text}
        if self.tool_calls:
            payload["tool_calls"] = [call.as_dict() for call in self.tool_calls]
        if self.structured is not None:
            payload["structured"] = self.structured
        payload["finish_reason"] = self.finish_reason
        return payload


@dataclass
class StreamEvent:
    kind: Literal["text", "tool_call", "done"]
    text: str = ""
    tool_call: ToolCall | None = None
    response: CompletionResponse | None = None


class Provider(abc.ABC):
    """Narrow adapter interface. Implementations must be side-effect free."""

    name: str
    model: str
    live: bool = False

    @abc.abstractmethod
    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        """Return a single completion or raise a ``ProviderError`` subclass."""

    @abc.abstractmethod
    def count_tokens(self, request: CompletionRequest) -> int:
        """Estimate input tokens for budget enforcement before a call."""

    def stream(self, request: CompletionRequest, *, timeout_s: float) -> Iterator[StreamEvent]:
        """Yield events for a completion. Default implementation chunks the full result."""
        response = self.complete(request, timeout_s=timeout_s)
        if response.text:
            yield StreamEvent(kind="text", text=response.text)
        for call in response.tool_calls:
            yield StreamEvent(kind="tool_call", tool_call=call)
        yield StreamEvent(kind="done", response=response)
