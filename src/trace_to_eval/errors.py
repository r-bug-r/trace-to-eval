"""Typed failures with explicit retry and outcome classification.

Infrastructure failures must never be reported as a model-quality pass. The runner keeps
the two categories separate and CI treats infrastructure failures as a distinct exit state.
"""

from __future__ import annotations


class TraceToEvalError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(TraceToEvalError):
    """Invalid configuration or missing prerequisite."""


class SchemaValidationError(TraceToEvalError):
    """A trace did not satisfy the versioned schema."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"invalid trace: {errors[0]}" if errors else "invalid trace")


class ProviderError(TraceToEvalError):
    """Provider call failed.

    ``error_class`` is a stable machine identifier used by reports and gates.
    ``retryable`` means a bounded retry may succeed; ``infrastructure`` means the failure
    is about the environment, not model quality.
    """

    error_class = "provider_error"
    retryable = False
    infrastructure = True

    def __init__(self, message: str, *, request_id: str | None = None) -> None:
        self.request_id = request_id
        super().__init__(message)


class ProviderTimeout(ProviderError):
    error_class = "timeout"
    retryable = True


class ProviderRateLimited(ProviderError):
    error_class = "rate_limited"
    retryable = True


class ProviderUnavailable(ProviderError):
    error_class = "provider_unavailable"
    retryable = True


class ProviderAuthError(ProviderError):
    error_class = "auth_error"
    retryable = False


class ProviderBadRequest(ProviderError):
    error_class = "bad_request"
    retryable = False


class ProviderResponseError(ProviderError):
    error_class = "invalid_response"
    retryable = False


class BudgetExceeded(ProviderError):
    error_class = "budget_exceeded"
    retryable = False


class LiveCallsDisabled(ProviderError):
    error_class = "live_calls_disabled"
    retryable = False


class FakeScriptError(ProviderError):
    error_class = "fake_script_error"
    retryable = False


INFRASTRUCTURE_ERROR_CLASSES = frozenset(
    {
        "timeout",
        "rate_limited",
        "provider_unavailable",
        "auth_error",
        "bad_request",
        "invalid_response",
        "budget_exceeded",
        "live_calls_disabled",
        "fake_script_error",
        "provider_error",
    }
)


def is_infrastructure_error(error_class: str | None) -> bool:
    return error_class in INFRASTRUCTURE_ERROR_CLASSES
