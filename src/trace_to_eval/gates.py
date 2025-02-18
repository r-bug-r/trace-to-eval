"""Quality, cost, latency, and baseline-regression gates for CI.

Gate evaluation is pure and deterministic: it reads a run summary and an optional baseline
summary and returns explicit pass/fail results plus an exit code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .errors import ConfigError
from .runner import RunSummary

EXIT_OK = 0
EXIT_QUALITY = 1
EXIT_INFRASTRUCTURE = 2
EXIT_USAGE = 3


@dataclass(frozen=True)
class Thresholds:
    """Absolute limits for one run plus optional baseline regression limits."""

    min_pass_rate: float | None = None
    max_total_cost_usd: float | None = None
    max_avg_cost_usd: float | None = None
    max_p95_latency_ms: float | None = None
    max_infrastructure_errors: int | None = 0
    max_signal_failures: int | None = None
    max_pass_rate_drop: float | None = None
    max_cost_increase_pct: float | None = None
    max_latency_increase_pct: float | None = None

    def __post_init__(self) -> None:
        if self.min_pass_rate is not None and not 0.0 <= self.min_pass_rate <= 1.0:
            raise ConfigError("min_pass_rate must be within [0, 1]")
        for name in ("max_total_cost_usd", "max_avg_cost_usd", "max_p95_latency_ms"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ConfigError(f"{name} must be non-negative")
        for name in ("max_infrastructure_errors", "max_signal_failures"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ConfigError(f"{name} must be non-negative")
        for name in ("max_pass_rate_drop", "max_cost_increase_pct", "max_latency_increase_pct"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ConfigError(f"{name} must be non-negative")

    @classmethod
    def from_mapping(cls, value: dict[str, Any]) -> Thresholds:
        known = {item.name for item in cls.__dataclass_fields__.values()}
        unknown = sorted(set(value) - known)
        if unknown:
            raise ConfigError(f"unknown threshold fields: {unknown}")
        return cls(**value)

    def as_dict(self) -> dict[str, Any]:
        return {item.name: getattr(self, item.name) for item in self.__dataclass_fields__.values()}


class BaselineMetrics(Protocol):
    """The subset of a stored baseline needed for regression gates."""

    @property
    def pass_rate(self) -> float: ...

    @property
    def avg_cost_usd(self) -> float: ...

    @property
    def p95_latency_ms(self) -> float: ...


@dataclass(frozen=True)
class BaselineSnapshot:
    """A persisted baseline summary, decoupled from a live run object."""

    pass_rate: float
    avg_cost_usd: float
    p95_latency_ms: float

    @classmethod
    def from_summary(cls, summary: dict[str, Any]) -> BaselineSnapshot:
        try:
            return cls(
                pass_rate=float(summary["pass_rate"]),
                avg_cost_usd=float(summary["avg_cost_usd"]),
                p95_latency_ms=float(summary["p95_latency_ms"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ConfigError(f"stored baseline is missing usable metrics: {exc}") from None


@dataclass
class GateResult:
    name: str
    passed: bool
    detail: str
    actual: float | int | None = None
    limit: float | int | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "actual": self.actual,
            "limit": self.limit,
        }


@dataclass
class GateReport:
    gates: list[GateResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(gate.passed for gate in self.gates)

    @property
    def failed_gates(self) -> list[GateResult]:
        return [gate for gate in self.gates if not gate.passed]

    def as_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "gates": [gate.as_dict() for gate in self.gates]}


def _percent_change(current: float, previous: float) -> float | None:
    if previous == 0:
        return None if current == 0 else float("inf")
    return (current - previous) / previous * 100.0


def evaluate_gates(
    summary: RunSummary,
    thresholds: Thresholds,
    *,
    baseline: BaselineMetrics | None = None,
) -> GateReport:
    report = GateReport()

    if thresholds.max_infrastructure_errors is not None:
        report.gates.append(
            GateResult(
                name="infrastructure_errors",
                passed=summary.infrastructure_errors <= thresholds.max_infrastructure_errors,
                detail=(
                    f"{summary.infrastructure_errors} infrastructure error(s); "
                    f"limit {thresholds.max_infrastructure_errors}"
                ),
                actual=summary.infrastructure_errors,
                limit=thresholds.max_infrastructure_errors,
            )
        )

    if thresholds.min_pass_rate is not None:
        report.gates.append(
            GateResult(
                name="min_pass_rate",
                passed=summary.pass_rate >= thresholds.min_pass_rate,
                detail=(
                    f"pass rate {summary.pass_rate:.1%} (minimum {thresholds.min_pass_rate:.1%})"
                ),
                actual=round(summary.pass_rate, 4),
                limit=thresholds.min_pass_rate,
            )
        )

    if thresholds.max_total_cost_usd is not None:
        report.gates.append(
            GateResult(
                name="max_total_cost_usd",
                passed=summary.total_cost_usd <= thresholds.max_total_cost_usd,
                detail=(
                    f"total cost ${summary.total_cost_usd:.6f} "
                    f"(limit ${thresholds.max_total_cost_usd:.6f})"
                ),
                actual=round(summary.total_cost_usd, 6),
                limit=thresholds.max_total_cost_usd,
            )
        )

    if thresholds.max_avg_cost_usd is not None:
        report.gates.append(
            GateResult(
                name="max_avg_cost_usd",
                passed=summary.avg_cost_usd <= thresholds.max_avg_cost_usd,
                detail=(
                    f"average cost ${summary.avg_cost_usd:.6f} per case "
                    f"(limit ${thresholds.max_avg_cost_usd:.6f})"
                ),
                actual=round(summary.avg_cost_usd, 6),
                limit=thresholds.max_avg_cost_usd,
            )
        )

    if thresholds.max_p95_latency_ms is not None:
        report.gates.append(
            GateResult(
                name="max_p95_latency_ms",
                passed=summary.p95_latency_ms <= thresholds.max_p95_latency_ms,
                detail=(
                    f"p95 latency {summary.p95_latency_ms:.0f}ms "
                    f"(limit {thresholds.max_p95_latency_ms:.0f}ms)"
                ),
                actual=round(summary.p95_latency_ms, 3),
                limit=thresholds.max_p95_latency_ms,
            )
        )

    if thresholds.max_signal_failures is not None:
        report.gates.append(
            GateResult(
                name="signal_failures",
                passed=summary.signal_failures <= thresholds.max_signal_failures,
                detail=(
                    f"{summary.signal_failures} rubric/label signal failure(s) "
                    f"(limit {thresholds.max_signal_failures}); signals never replace "
                    "deterministic assertions"
                ),
                actual=summary.signal_failures,
                limit=thresholds.max_signal_failures,
            )
        )

    if baseline is not None:
        if thresholds.max_pass_rate_drop is not None:
            drop = baseline.pass_rate - summary.pass_rate
            report.gates.append(
                GateResult(
                    name="pass_rate_regression",
                    passed=drop <= thresholds.max_pass_rate_drop,
                    detail=(
                        f"pass rate {summary.pass_rate:.1%} vs baseline "
                        f"{baseline.pass_rate:.1%} (allowed drop "
                        f"{thresholds.max_pass_rate_drop:.1%})"
                    ),
                    actual=round(drop, 4),
                    limit=thresholds.max_pass_rate_drop,
                )
            )
        if thresholds.max_cost_increase_pct is not None:
            change = _percent_change(summary.avg_cost_usd, baseline.avg_cost_usd)
            if change is None:
                passed = True
                detail = "baseline and current average cost are both $0"
            elif change == float("inf"):
                passed = False
                detail = "average cost increased from $0; any nonzero cost is a regression"
            else:
                passed = change <= thresholds.max_cost_increase_pct
                detail = (
                    f"average cost ${summary.avg_cost_usd:.6f} vs baseline "
                    f"${baseline.avg_cost_usd:.6f} ({change:+.1f}%; allowed "
                    f"+{thresholds.max_cost_increase_pct:.1f}%)"
                )
            report.gates.append(
                GateResult(
                    name="cost_regression",
                    passed=passed,
                    detail=detail,
                    actual=None if change is None else round(change, 2),
                    limit=thresholds.max_cost_increase_pct,
                )
            )
        if thresholds.max_latency_increase_pct is not None:
            change = _percent_change(summary.p95_latency_ms, baseline.p95_latency_ms)
            if change is None:
                passed = True
                detail = "baseline and current p95 latency are both 0ms"
            elif change == float("inf"):
                passed = False
                detail = "p95 latency increased from 0ms; any nonzero latency is a regression"
            else:
                passed = change <= thresholds.max_latency_increase_pct
                detail = (
                    f"p95 latency {summary.p95_latency_ms:.0f}ms vs baseline "
                    f"{baseline.p95_latency_ms:.0f}ms ({change:+.1f}%; allowed "
                    f"+{thresholds.max_latency_increase_pct:.1f}%)"
                )
            report.gates.append(
                GateResult(
                    name="latency_regression",
                    passed=passed,
                    detail=detail,
                    actual=None if change is None else round(change, 2),
                    limit=thresholds.max_latency_increase_pct,
                )
            )
    elif any(
        value is not None
        for value in (
            thresholds.max_pass_rate_drop,
            thresholds.max_cost_increase_pct,
            thresholds.max_latency_increase_pct,
        )
    ):
        report.gates.append(
            GateResult(
                name="baseline_available",
                passed=False,
                detail="regression thresholds were configured but no baseline was found",
            )
        )

    if summary.failed > 0:
        report.gates.append(
            GateResult(
                name="case_failures",
                passed=False,
                detail=f"{summary.failed} case(s) failed deterministic assertions",
                actual=summary.failed,
                limit=0,
            )
        )

    return report


def exit_code_for(summary: RunSummary, gate_report: GateReport) -> int:
    """Map outcomes to a stable CI exit code."""
    if summary.infrastructure_errors > 0:
        return EXIT_INFRASTRUCTURE
    if not gate_report.passed:
        return EXIT_QUALITY
    return EXIT_OK


def verdict(summary: RunSummary, gate_report: GateReport) -> str:
    code = exit_code_for(summary, gate_report)
    if code == EXIT_INFRASTRUCTURE:
        return "infrastructure_failure"
    if code == EXIT_QUALITY:
        return "quality_regression"
    return "pass"
