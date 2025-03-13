"""Opt-in live provider adapters (OpenAI-compatible and Anthropic).

These adapters are never used by default, never used in CI, and never retried when a
failure is permanent. They require explicit consent, a positive per-run cost cap, and a
request cap. API keys come from the environment and are never written to disk, logs,
reports, or the ledger.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from ..budget import BudgetTracker
from ..errors import (
    ConfigError,
    ProviderAuthError,
    ProviderBadRequest,
    ProviderRateLimited,
    ProviderResponseError,
    ProviderTimeout,
    ProviderUnavailable,
)
from ..pricing import Pricing
from ..util import safe_text
from .base import CompletionRequest, CompletionResponse, Provider, ToolCall

_USER_AGENT = "trace-to-eval/0.1 (+https://github.com/trace-to-eval/trace-to-eval)"
_MAX_BODY = 2_000_000


def _scrub(text: str, secrets: list[str]) -> str:
    scrubbed = text
    for secret in secrets:
        if secret:
            scrubbed = scrubbed.replace(secret, "[REDACTED:key]")
    return safe_text(scrubbed, max_length=600)


def _classify_http_error(status: int, body: str, *, secrets: list[str]) -> Exception:
    detail = _scrub(body, secrets)
    if status in (401, 403):
        return ProviderAuthError(f"provider rejected credentials (HTTP {status}): {detail}")
    if status == 429:
        return ProviderRateLimited(f"provider rate limit (HTTP 429): {detail}")
    if status >= 500:
        return ProviderUnavailable(f"provider unavailable (HTTP {status}): {detail}")
    if status == 408:
        return ProviderTimeout(f"provider request timeout (HTTP 408): {detail}")
    return ProviderBadRequest(f"provider rejected request (HTTP {status}): {detail}")


@dataclass
class _HttpResult:
    status: int
    body: str


class _HttpProvider(Provider):
    """Shared HTTP behavior: bounded timeout, no redirects, classified failures."""

    live = True

    def __init__(
        self,
        *,
        model: str,
        api_key_env: str,
        pricing: Pricing,
        budget: BudgetTracker,
        name: str,
    ) -> None:
        self.model = model
        self.api_key_env = api_key_env
        self.pricing = pricing
        self.budget = budget
        self.name = name

    def _api_key(self) -> str:
        value = os.environ.get(self.api_key_env, "").strip()
        if not value:
            raise ProviderAuthError(
                f"missing provider credential: set {self.api_key_env} in the local environment"
            )
        return value

    def _post(
        self, url: str, payload: dict[str, Any], headers: dict[str, str], *, timeout_s: float
    ) -> _HttpResult:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        # The URL is built from a validated http(s) base URL in ``_base_url``.
        request = urllib.request.Request(url, data=body, method="POST")  # noqa: S310
        for key, value in headers.items():
            request.add_header(key, value)
        request.add_header("Content-Type", "application/json")
        request.add_header("User-Agent", _USER_AGENT)
        try:
            with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310
                raw = response.read(_MAX_BODY + 1)
                if len(raw) > _MAX_BODY:
                    raise ProviderResponseError("provider response exceeded the size limit")
                return _HttpResult(status=response.status, body=raw.decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            raw = exc.read(_MAX_BODY) if exc.fp is not None else b""
            raise _classify_http_error(
                exc.code, raw.decode("utf-8", "replace"), secrets=[self._api_key()]
            ) from None
        except TimeoutError as exc:
            raise ProviderTimeout(f"provider call timed out after {timeout_s:.1f}s") from exc
        except urllib.error.URLError as exc:
            reason = str(exc.reason)
            if isinstance(exc.reason, TimeoutError):
                raise ProviderTimeout(f"provider call timed out after {timeout_s:.1f}s") from exc
            raise ProviderUnavailable(f"provider connection failed: {safe_text(reason)}") from None

    def _call_with_retries(
        self,
        send: Callable[[float], CompletionResponse],
        request: CompletionRequest,
        *,
        timeout_s: float,
        estimated_cost: float,
    ) -> CompletionResponse:
        attempts = 0
        max_attempts = self.budget.limits.max_retries + 1
        while True:
            attempts += 1
            self.budget.check(
                estimated_input_tokens=self.count_tokens(request),
                estimated_cost_usd=estimated_cost,
            )
            try:
                response = send(timeout_s)
            except (ProviderRateLimited, ProviderUnavailable, ProviderTimeout) as exc:
                self.budget.record(
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    success=False,
                    error_class=exc.error_class,
                )
                if attempts >= max_attempts or not exc.retryable:
                    raise
                time.sleep(self.budget.limits.retry_backoff_s * (2 ** (attempts - 1)))
                continue
            self.budget.record(
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
                success=True,
            )
            return response

    def count_tokens(self, request: CompletionRequest) -> int:
        return max(1, (len(request.prompt_text()) + 3) // 4)

    def _estimate_cost(self, request: CompletionRequest) -> float:
        input_tokens = self.count_tokens(request)
        output_budget = request.max_output_tokens or 1024
        return self.pricing.estimate(request.model, input_tokens, output_budget)


class OpenAICompatibleProvider(_HttpProvider):
    """Works with OpenAI and any server exposing the chat-completions shape."""

    def __init__(
        self,
        *,
        model: str,
        budget: BudgetTracker,
        pricing: Pricing | None = None,
        base_url: str | None = None,
        api_key_env: str = "TRACE_TO_EVAL_OPENAI_API_KEY",
    ) -> None:
        super().__init__(
            model=model,
            api_key_env=api_key_env,
            pricing=pricing or Pricing.load(),
            budget=budget,
            name="openai-compatible",
        )
        resolved = base_url or os.environ.get(
            "TRACE_TO_EVAL_OPENAI_BASE_URL", "https://api.openai.com/v1"
        )
        if not resolved.startswith(("http://", "https://")):
            raise ConfigError("base_url must be an http(s) URL")
        self.base_url = resolved.rstrip("/")

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        api_key = self._api_key()
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": request.messages,
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.get("name"),
                        "description": tool.get("description", ""),
                        "parameters": tool.get("schema") or {"type": "object"},
                    },
                }
                for tool in request.tools
            ]
        if request.parameters:
            payload.update(
                {
                    key: value
                    for key, value in request.parameters.items()
                    if key
                    in {
                        "temperature",
                        "top_p",
                        "max_tokens",
                        "max_completion_tokens",
                        "stop",
                        "seed",
                        "response_format",
                    }
                }
            )
        if request.max_output_tokens is not None:
            payload.setdefault("max_tokens", request.max_output_tokens)

        def send(effective_timeout: float) -> CompletionResponse:
            started = time.perf_counter()
            result = self._post(
                f"{self.base_url}/chat/completions",
                payload,
                {"Authorization": f"Bearer {api_key}"},
                timeout_s=effective_timeout,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            return self._parse(result, request, latency_ms)

        return self._call_with_retries(
            send, request, timeout_s=timeout_s, estimated_cost=self._estimate_cost(request)
        )

    def _parse(
        self, result: _HttpResult, request: CompletionRequest, latency_ms: float
    ) -> CompletionResponse:
        try:
            decoded = json.loads(result.body)
        except json.JSONDecodeError:
            raise ProviderResponseError("provider returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ProviderResponseError("provider returned an unexpected payload")
        choices = decoded.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ProviderResponseError("provider response has no choices")
        first = choices[0] if isinstance(choices[0], dict) else {}
        raw_message = first.get("message")
        message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else {}
        content = message.get("content")
        text = content if isinstance(content, str) else ""
        tool_calls: list[ToolCall] = []
        for raw_call in message.get("tool_calls") or []:
            function = raw_call.get("function") if isinstance(raw_call, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            if not isinstance(name, str) or not name:
                continue
            arguments = _parse_arguments(function.get("arguments"))
            tool_calls.append(ToolCall(name=name, arguments=arguments, id=raw_call.get("id")))
        raw_usage = decoded.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = int(usage.get("prompt_tokens", 0) or 0)
        output_tokens = int(usage.get("completion_tokens", 0) or 0)
        cost = self.pricing.estimate(request.model, input_tokens, output_tokens)
        finish_reason = first.get("finish_reason")
        return CompletionResponse(
            text=text,
            tool_calls=tool_calls,
            finish_reason=str(finish_reason) if finish_reason else "stop",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            request_id=decoded.get("id") if isinstance(decoded.get("id"), str) else None,
            model=request.model,
            provider=self.name,
        )


class AnthropicProvider(_HttpProvider):
    """Anthropic Messages API adapter."""

    def __init__(
        self,
        *,
        model: str,
        budget: BudgetTracker,
        pricing: Pricing | None = None,
        base_url: str | None = None,
        api_key_env: str = "TRACE_TO_EVAL_ANTHROPIC_API_KEY",
        max_tokens: int = 1024,
    ) -> None:
        super().__init__(
            model=model,
            api_key_env=api_key_env,
            pricing=pricing or Pricing.load(),
            budget=budget,
            name="anthropic",
        )
        resolved = base_url or os.environ.get(
            "TRACE_TO_EVAL_ANTHROPIC_BASE_URL", "https://api.anthropic.com"
        )
        if not resolved.startswith(("http://", "https://")):
            raise ConfigError("base_url must be an http(s) URL")
        self.base_url = resolved.rstrip("/")
        self.max_tokens = max_tokens

    def complete(self, request: CompletionRequest, *, timeout_s: float) -> CompletionResponse:
        api_key = self._api_key()
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            if role in {"system", "developer"}:
                if message.get("content"):
                    system_parts.append(str(message["content"]))
                continue
            if role == "tool":
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id", ""),
                                "content": str(message.get("content") or ""),
                            }
                        ],
                    }
                )
                continue
            blocks: list[dict[str, Any]] = []
            if message.get("content"):
                blocks.append({"type": "text", "text": str(message["content"])})
            for call in message.get("tool_calls") or []:
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": call.get("id") or "toolu_replay",
                        "name": call.get("name"),
                        "input": call.get("arguments") or {},
                    }
                )
            messages.append(
                {"role": "assistant" if role == "assistant" else "user", "content": blocks}
            )
        if not messages:
            messages = [{"role": "user", "content": [{"type": "text", "text": "(empty)"}]}]

        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens or self.max_tokens,
            "messages": messages,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.tools:
            payload["tools"] = [
                {
                    "name": tool.get("name"),
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("schema") or {"type": "object"},
                }
                for tool in request.tools
            ]

        def send(effective_timeout: float) -> CompletionResponse:
            started = time.perf_counter()
            result = self._post(
                f"{self.base_url}/v1/messages",
                payload,
                {
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout_s=effective_timeout,
            )
            latency_ms = (time.perf_counter() - started) * 1000.0
            return self._parse(result, request, latency_ms)

        return self._call_with_retries(
            send, request, timeout_s=timeout_s, estimated_cost=self._estimate_cost(request)
        )

    def _parse(
        self, result: _HttpResult, request: CompletionRequest, latency_ms: float
    ) -> CompletionResponse:
        try:
            decoded = json.loads(result.body)
        except json.JSONDecodeError:
            raise ProviderResponseError("provider returned invalid JSON") from None
        if not isinstance(decoded, dict):
            raise ProviderResponseError("provider returned an unexpected payload")
        content = decoded.get("content")
        if not isinstance(content, list):
            raise ProviderResponseError("provider response has no content blocks")
        texts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text" and isinstance(block.get("text"), str):
                texts.append(block["text"])
            elif block.get("type") == "tool_use":
                name = block.get("name")
                if isinstance(name, str) and name:
                    arguments = block.get("input")
                    tool_calls.append(
                        ToolCall(
                            name=name,
                            arguments=arguments if isinstance(arguments, dict) else {},
                            id=block.get("id") if isinstance(block.get("id"), str) else None,
                        )
                    )
        raw_usage = decoded.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cost = self.pricing.estimate(request.model, input_tokens, output_tokens)
        stop_reason = decoded.get("stop_reason")
        return CompletionResponse(
            text="".join(texts),
            tool_calls=tool_calls,
            finish_reason=str(stop_reason) if stop_reason else "stop",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            latency_ms=latency_ms,
            request_id=decoded.get("id") if isinstance(decoded.get("id"), str) else None,
            model=request.model,
            provider=self.name,
        )


def _parse_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        if not raw.strip():
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError:
            return {"_raw": safe_text(raw, max_length=400)}
        return decoded if isinstance(decoded, dict) else {"_raw": decoded}
    return {}
