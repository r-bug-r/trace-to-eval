"""Request, token, and spend limits for optional live provider calls.

Live calls are disabled by default. Enabling them requires both an explicit CLI flag and
the ``TRACE_TO_EVAL_ALLOW_LIVE=1`` environment variable, and a positive hard cost cap.
Budget checks happen before a request is sent; usage is recorded in the SQLite ledger
after every attempt, including failures.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field

from .errors import BudgetExceeded, ConfigError, LiveCallsDisabled


@dataclass(frozen=True)
class BudgetLimits:
    """Hard limits. ``None`` means unlimited for that dimension."""

    max_requests_per_run: int | None = None
    max_tokens_per_run: int | None = None
    max_cost_usd_per_run: float | None = None
    daily_cost_cap_usd: float | None = None
    monthly_cost_cap_usd: float | None = None
    request_timeout_s: float = 60.0
    max_retries: int = 2
    retry_backoff_s: float = 0.5

    def __post_init__(self) -> None:
        for name in (
            "max_requests_per_run",
            "max_tokens_per_run",
            "max_cost_usd_per_run",
            "daily_cost_cap_usd",
            "monthly_cost_cap_usd",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ConfigError(f"budget limit {name} must be non-negative")
        if self.request_timeout_s <= 0:
            raise ConfigError("request_timeout_s must be positive")
        if self.max_retries < 0:
            raise ConfigError("max_retries must be non-negative")
        if self.retry_backoff_s < 0:
            raise ConfigError("retry_backoff_s must be non-negative")

    @classmethod
    def from_mapping(cls, value: dict[str, object]) -> BudgetLimits:
        known = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ConfigError(f"unknown budget fields: {unknown}")
        return cls(**value)  # type: ignore[arg-type]

    def as_dict(self) -> dict[str, object]:
        return {
            "max_requests_per_run": self.max_requests_per_run,
            "max_tokens_per_run": self.max_tokens_per_run,
            "max_cost_usd_per_run": self.max_cost_usd_per_run,
            "daily_cost_cap_usd": self.daily_cost_cap_usd,
            "monthly_cost_cap_usd": self.monthly_cost_cap_usd,
            "request_timeout_s": self.request_timeout_s,
            "max_retries": self.max_retries,
            "retry_backoff_s": self.retry_backoff_s,
        }


def require_live_consent(
    *, allow_live_flag: bool, environment_value: str | None, limits: BudgetLimits
) -> None:
    """Fail closed unless live usage is explicitly and safely enabled."""
    if not allow_live_flag:
        raise LiveCallsDisabled(
            "live provider calls are disabled; pass --allow-live with TRACE_TO_EVAL_ALLOW_LIVE=1"
        )
    if environment_value != "1":
        raise LiveCallsDisabled(
            "live provider calls require the environment variable TRACE_TO_EVAL_ALLOW_LIVE=1"
        )
    if limits.max_cost_usd_per_run is None or limits.max_cost_usd_per_run <= 0:
        raise LiveCallsDisabled(
            "live provider calls require a positive --max-cost-usd hard cap for this run"
        )
    if limits.max_requests_per_run is None:
        raise LiveCallsDisabled(
            "live provider calls require --max-requests so a run cannot loop unbounded"
        )


@dataclass
class BudgetTracker:
    """Tracks one run's usage and enforces per-run and ledger-backed caps."""

    limits: BudgetLimits
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    daily_spend_usd: float = 0.0
    monthly_spend_usd: float = 0.0
    stopped_reason: str | None = None
    _events: list[dict[str, object]] = field(default_factory=list)

    def check(self, *, estimated_input_tokens: int, estimated_cost_usd: float) -> None:
        """Raise ``BudgetExceeded`` before a request would cross a limit."""
        if (
            self.limits.max_requests_per_run is not None
            and self.requests + 1 > self.limits.max_requests_per_run
        ):
            self.stopped_reason = "max_requests_per_run"
            raise BudgetExceeded(
                f"request budget exhausted ({self.limits.max_requests_per_run} per run)"
            )
        projected = self.input_tokens + self.output_tokens + estimated_input_tokens
        if (
            self.limits.max_tokens_per_run is not None
            and projected > self.limits.max_tokens_per_run
        ):
            self.stopped_reason = "max_tokens_per_run"
            raise BudgetExceeded(
                f"token budget exhausted ({self.limits.max_tokens_per_run} per run)"
            )
        if (
            self.limits.max_cost_usd_per_run is not None
            and self.cost_usd + estimated_cost_usd > self.limits.max_cost_usd_per_run
        ):
            self.stopped_reason = "max_cost_usd_per_run"
            raise BudgetExceeded(
                f"run cost cap would be exceeded (${self.limits.max_cost_usd_per_run:.4f} per run)"
            )
        if (
            self.limits.daily_cost_cap_usd is not None
            and self.daily_spend_usd + self.cost_usd + estimated_cost_usd
            > self.limits.daily_cost_cap_usd
        ):
            self.stopped_reason = "daily_cost_cap_usd"
            raise BudgetExceeded(
                f"daily cost cap would be exceeded (${self.limits.daily_cost_cap_usd:.4f})"
            )
        if (
            self.limits.monthly_cost_cap_usd is not None
            and self.monthly_spend_usd + self.cost_usd + estimated_cost_usd
            > self.limits.monthly_cost_cap_usd
        ):
            self.stopped_reason = "monthly_cost_cap_usd"
            raise BudgetExceeded(
                f"monthly cost cap would be exceeded (${self.limits.monthly_cost_cap_usd:.4f})"
            )

    def record(
        self,
        *,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        success: bool,
        error_class: str | None = None,
    ) -> None:
        self.requests += 1
        self.input_tokens += max(0, input_tokens)
        self.output_tokens += max(0, output_tokens)
        self.cost_usd += max(0.0, cost_usd)
        self._events.append(
            {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "cost_usd": cost_usd,
                "success": success,
                "error_class": error_class,
            }
        )

    def summary(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "stopped_reason": self.stopped_reason,
            "limits": self.limits.as_dict(),
        }


def month_start(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.UTC)
    return (
        current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def day_start(now: dt.datetime | None = None) -> str:
    current = now or dt.datetime.now(dt.UTC)
    return (
        current.replace(hour=0, minute=0, second=0, microsecond=0)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
