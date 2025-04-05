"""Replay runner: provider call, evaluation, persistence, and summary.

Replay has no implicit side effects: tool calls produced by a model are compared, never
executed. Every live attempt is written to the cost ledger, including failures.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import BudgetExceeded, ConfigError, ProviderError
from .evaluate import AssertionResult, evaluate_output
from .providers.base import CompletionRequest, Provider
from .rubric import RubricJudge, RubricRequest
from .store import CaseStore, ResultRecord, new_run_id
from .util import percentile, utc_now

MAX_ERROR_MESSAGE = 500


@dataclass
class RunConfig:
    provider: str
    model: str
    prompt_version: str = "unversioned"
    code_revision: str = "unknown"
    timeout_s: float = 60.0
    baseline_name: str | None = None
    rubric: bool = False
    rubric_model: str | None = None
    max_cases: int | None = None
    tags: list[str] = field(default_factory=list)
    case_ids: list[str] = field(default_factory=list)
    thresholds: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "code_revision": self.code_revision,
            "timeout_s": self.timeout_s,
            "baseline_name": self.baseline_name,
            "rubric": self.rubric,
            "rubric_model": self.rubric_model,
            "max_cases": self.max_cases,
            "tags": list(self.tags),
            "case_ids": list(self.case_ids),
            "thresholds": dict(self.thresholds),
        }


@dataclass
class CaseResult:
    case_id: str
    status: str
    assertions: list[AssertionResult]
    output: dict[str, Any]
    cost_usd: float
    latency_ms: float
    error_class: str | None = None
    error_message: str | None = None
    human_label: str | None = None
    label_agreement: bool | None = None
    rubric: dict[str, Any] | None = None
    tags: list[str] = field(default_factory=list)

    @property
    def failures(self) -> list[AssertionResult]:
        return [item for item in self.assertions if item.failed]

    @property
    def signal_failures(self) -> list[AssertionResult]:
        return [item for item in self.assertions if item.kind == "signal" and item.status == "fail"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "status": self.status,
            "failures": [item.as_dict() for item in self.failures],
            "signal_failures": [item.as_dict() for item in self.signal_failures],
            "assertions": [item.as_dict() for item in self.assertions],
            "output": self.output,
            "cost_usd": round(self.cost_usd, 6),
            "latency_ms": round(self.latency_ms, 3),
            "error_class": self.error_class,
            "error_message": self.error_message,
            "human_label": self.human_label,
            "label_agreement": self.label_agreement,
            "rubric": self.rubric,
            "tags": list(self.tags),
        }


@dataclass
class RunSummary:
    run_id: str
    started_at: str
    finished_at: str
    provider: str
    model: str
    prompt_version: str
    code_revision: str
    baseline_name: str | None
    total_cases: int
    passed: int
    failed: int
    infrastructure_errors: int
    signal_failures: int
    label_disagreements: int
    skipped_rubrics: int
    pass_rate: float
    total_cost_usd: float
    avg_cost_usd: float
    p95_latency_ms: float
    avg_latency_ms: float
    results: list[CaseResult] = field(default_factory=list)

    def as_dict(self, *, include_results: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "provider": self.provider,
            "model": self.model,
            "prompt_version": self.prompt_version,
            "code_revision": self.code_revision,
            "baseline_name": self.baseline_name,
            "totals": {
                "cases": self.total_cases,
                "passed": self.passed,
                "failed": self.failed,
                "infrastructure_errors": self.infrastructure_errors,
                "signal_failures": self.signal_failures,
                "label_disagreements": self.label_disagreements,
                "skipped_rubrics": self.skipped_rubrics,
                "pass_rate": round(self.pass_rate, 4),
            },
            "cost": {
                "total_usd": round(self.total_cost_usd, 6),
                "avg_usd": round(self.avg_cost_usd, 6),
            },
            "latency": {
                "p95_ms": round(self.p95_latency_ms, 3),
                "avg_ms": round(self.avg_latency_ms, 3),
            },
        }
        if include_results:
            payload["results"] = [item.as_dict() for item in self.results]
        return payload

    def metrics_for_baseline(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed": self.passed,
            "failed": self.failed,
            "infrastructure_errors": self.infrastructure_errors,
            "pass_rate": round(self.pass_rate, 4),
            "total_cost_usd": round(self.total_cost_usd, 6),
            "avg_cost_usd": round(self.avg_cost_usd, 6),
            "p95_latency_ms": round(self.p95_latency_ms, 3),
            "avg_latency_ms": round(self.avg_latency_ms, 3),
        }


def build_request(case: dict[str, Any], config: RunConfig, *, model: str) -> CompletionRequest:
    trace = case["trace"]
    metrics = trace.get("metrics") or {}
    model_meta = trace.get("model") or {}
    parameters: dict[str, Any] = dict(model_meta.get("parameters") or {})
    parameters["source_metrics"] = {
        "cost_usd": metrics.get("cost_usd", 0.0),
        "latency_ms": metrics.get("latency_ms", 0.0),
    }
    return CompletionRequest(
        model=model,
        messages=list((trace.get("input") or {}).get("messages") or []),
        tools=list(trace.get("tools") or []),
        parameters=parameters,
        case_id=case["case_id"],
        trace_id=trace.get("trace_id"),
        prompt_version=config.prompt_version,
        source_output=dict(trace.get("output") or {}),
    )


def select_cases(store: CaseStore, config: RunConfig) -> list[dict[str, Any]]:
    if config.case_ids:
        missing = [case_id for case_id in config.case_ids if store.get_case(case_id) is None]
        if missing:
            raise ConfigError(f"unknown case id(s): {', '.join(missing)}")
    cases = store.list_cases(
        case_ids=config.case_ids or None,
        tags=config.tags or None,
    )
    if not cases:
        raise ConfigError("no cases matched the selected case/tag filters")
    if config.max_cases is not None:
        cases = cases[: config.max_cases]
    return cases


def run_replay(
    store: CaseStore,
    provider: Provider,
    config: RunConfig,
    *,
    rubric_judge: RubricJudge | None = None,
    ledger_workflow: str = "replay",
    progress: Callable[[int, int], None] | None = None,
) -> RunSummary:
    """Replay selected cases and persist results. Returns a deterministic summary.

    ``progress`` is called with ``(completed, total)`` after each persisted case. It is
    observational only; raising from the callback aborts the run.
    """
    cases = select_cases(store, config)
    run_id = new_run_id()
    started_at = utc_now()
    store.create_run(
        run_id,
        provider=config.provider,
        model=config.model,
        prompt_version=config.prompt_version,
        code_revision=config.code_revision,
        baseline_name=config.baseline_name,
        config=config.as_dict(),
    )

    results: list[CaseResult] = []
    for case in cases:
        trace = case["trace"]
        request = build_request(case, config, model=config.model)
        human_label = (case.get("label") or {}).get("label")
        started = time.perf_counter()
        try:
            response = provider.complete(request, timeout_s=config.timeout_s)
        except BudgetExceeded:
            raise
        except ProviderError as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            store.add_ledger_entry(
                run_id=run_id,
                workflow=ledger_workflow,
                provider=config.provider,
                model=config.model,
                request_id=exc.request_id,
                input_tokens=0,
                output_tokens=0,
                estimated_cost_usd=0.0,
                cache_hit=False,
                success=False,
                error_class=exc.error_class,
            )
            result = CaseResult(
                case_id=case["case_id"],
                status="infrastructure_error",
                assertions=[
                    AssertionResult(
                        name="provider_call",
                        kind="deterministic",
                        status="fail",
                        message=f"provider failure classified as {exc.error_class}",
                        details={"error_class": exc.error_class},
                    )
                ],
                output={},
                cost_usd=0.0,
                latency_ms=elapsed,
                error_class=exc.error_class,
                error_message=str(exc)[:MAX_ERROR_MESSAGE],
                human_label=human_label,
                tags=case["tags"],
            )
            _persist(store, run_id, result)
            results.append(result)
            if progress is not None:
                progress(len(results), len(cases))
            continue

        elapsed = (time.perf_counter() - started) * 1000.0
        latency_ms = response.latency_ms or elapsed
        output = response.output_payload()
        store.add_ledger_entry(
            run_id=run_id,
            workflow=ledger_workflow,
            provider=config.provider,
            model=config.model,
            request_id=response.request_id,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            estimated_cost_usd=response.cost_usd,
            cache_hit=response.cached,
            success=True,
            error_class=None,
        )

        rubric_result = None
        if config.rubric and rubric_judge is not None:
            rubric_spec = (trace.get("expectations") or {}).get("rubric")
            if rubric_spec is not None:
                question = _last_user_message(trace)
                rubric_result = rubric_judge.score(
                    RubricRequest(
                        rubric_id=str(rubric_spec["id"]),
                        criteria=str(rubric_spec["criteria"]),
                        question=question,
                        candidate=str(output.get("text", "")),
                        reference=(trace.get("output") or {}).get("text"),
                    ),
                    model=config.rubric_model or config.model,
                    timeout_s=config.timeout_s,
                )
                usage = rubric_result.get("usage") or {}
                store.add_ledger_entry(
                    run_id=run_id,
                    workflow="rubric",
                    provider=config.provider,
                    model=config.rubric_model or config.model,
                    request_id=usage.get("request_id"),
                    input_tokens=int(usage.get("input_tokens", 0) or 0),
                    output_tokens=int(usage.get("output_tokens", 0) or 0),
                    estimated_cost_usd=float(usage.get("cost_usd", 0.0) or 0.0),
                    cache_hit=False,
                    success="error_class" not in rubric_result,
                    error_class=rubric_result.get("error_class"),
                )

        outcome = evaluate_output(
            trace.get("expectations"),
            output,
            cost_usd=response.cost_usd,
            latency_ms=latency_ms,
            human_label=human_label,
            rubric_result=rubric_result,
        )
        status = "pass" if outcome.passed else "fail"
        label_agreement = None
        if human_label is not None:
            label_agreement = (status == "pass") == (human_label == "success")
        result = CaseResult(
            case_id=case["case_id"],
            status=status,
            assertions=outcome.assertions,
            output=output,
            cost_usd=response.cost_usd,
            latency_ms=latency_ms,
            human_label=human_label,
            label_agreement=label_agreement,
            rubric=rubric_result,
            tags=case["tags"],
        )
        _persist(store, run_id, result)
        results.append(result)
        if progress is not None:
            progress(len(results), len(cases))

    summary = summarize(
        run_id=run_id,
        started_at=started_at,
        config=config,
        results=results,
    )
    store.finish_run(run_id, "completed", summary.metrics_for_baseline())
    return summary


def _persist(store: CaseStore, run_id: str, result: CaseResult) -> None:
    store.add_result(
        run_id,
        ResultRecord(
            case_id=result.case_id,
            status=result.status,
            output_text=str(result.output.get("text", "")),
            assertions=[item.as_dict() for item in result.assertions],
            output_json=result.output or None,
            cost_usd=result.cost_usd,
            latency_ms=result.latency_ms,
            error_class=result.error_class,
            error_message=result.error_message,
            human_label=result.human_label,
            label_agreement=result.label_agreement,
            rubric=result.rubric,
        ),
    )


def summarize(
    *,
    run_id: str,
    started_at: str,
    config: RunConfig,
    results: list[CaseResult],
) -> RunSummary:
    total = len(results)
    passed = sum(1 for item in results if item.status == "pass")
    failed = sum(1 for item in results if item.status == "fail")
    infrastructure = sum(1 for item in results if item.status == "infrastructure_error")
    signal_failures = sum(1 for item in results if item.signal_failures)
    disagreements = sum(1 for item in results if item.label_agreement is False)
    skipped_rubrics = sum(
        1
        for item in results
        for assertion in item.assertions
        if assertion.name.startswith("rubric.") and assertion.status == "skip"
    )
    costs = [item.cost_usd for item in results]
    latencies = [item.latency_ms for item in results]
    return RunSummary(
        run_id=run_id,
        started_at=started_at,
        finished_at=utc_now(),
        provider=config.provider,
        model=config.model,
        prompt_version=config.prompt_version,
        code_revision=config.code_revision,
        baseline_name=config.baseline_name,
        total_cases=total,
        passed=passed,
        failed=failed,
        infrastructure_errors=infrastructure,
        signal_failures=signal_failures,
        label_disagreements=disagreements,
        skipped_rubrics=skipped_rubrics,
        pass_rate=(passed / total) if total else 0.0,
        total_cost_usd=sum(costs),
        avg_cost_usd=(sum(costs) / total) if total else 0.0,
        p95_latency_ms=percentile(latencies, 0.95),
        avg_latency_ms=(sum(latencies) / total) if total else 0.0,
        results=results,
    )


def _last_user_message(trace: dict[str, Any]) -> str:
    messages = (trace.get("input") or {}).get("messages") or []
    for message in reversed(messages):
        if message.get("role") == "user" and message.get("content"):
            return str(message["content"])
    return ""


def reconstruct_summary(store: CaseStore, run_id: str) -> RunSummary:
    """Rebuild a summary from persisted rows (used by ``report`` and baseline gates)."""
    run = store.get_run(run_id)
    if run is None:
        raise KeyError(f"unknown run: {run_id}")
    config = RunConfig(**{**run["config"], "provider": run["provider"], "model": run["model"]})
    results: list[CaseResult] = []
    for row in store.results_for_run(run_id):
        assertions = [AssertionResult(**item) for item in row["assertions"]]
        results.append(
            CaseResult(
                case_id=row["case_id"],
                status=row["status"],
                assertions=assertions,
                output=row["output_json"] or {"text": row["output_text"] or ""},
                cost_usd=row["cost_usd"],
                latency_ms=row["latency_ms"],
                error_class=row["error_class"],
                error_message=row["error_message"],
                human_label=row["human_label"],
                label_agreement=row["label_agreement"],
                rubric=row["rubric"],
                tags=(store.get_case(row["case_id"]) or {}).get("tags", []),
            )
        )
    return summarize(
        run_id=run_id,
        started_at=run["started_at"],
        config=config,
        results=results,
    )
