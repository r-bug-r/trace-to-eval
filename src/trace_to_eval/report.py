"""Machine-readable and human-readable reports.

All report renderers treat stored model output and case text as untrusted: control
characters are stripped, lines are bounded, and XML/Markdown are escaped.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from typing import Any

from .gates import GateReport, exit_code_for, verdict
from .runner import CaseResult, RunSummary
from .util import safe_text

MAX_REPORT_LINE = 160
MAX_FAILURES_SHOWN = 20


def _one_line(value: str, *, limit: int = MAX_REPORT_LINE) -> str:
    return safe_text(value.replace("\n", " "), max_length=limit)


def _status_marker(status: str, *, unicode: bool) -> str:
    if status == "pass":
        return "✓" if unicode else "PASS"
    if status == "fail":
        return "✗" if unicode else "FAIL"
    return "!" if unicode else "ERR"


def render_terminal(
    summary: RunSummary,
    gate_report: GateReport,
    *,
    unicode: bool = True,
    color: bool = False,
    max_failures: int = MAX_FAILURES_SHOWN,
) -> str:
    """Render a compact run summary for a terminal."""
    ok = "✓" if unicode else "OK"
    bad = "✗" if unicode else "FAIL"

    def paint(text: str, code: str) -> str:
        if not color:
            return text
        return f"\x1b[{code}m{text}\x1b[0m"

    def verdict_mark(passed: bool) -> str:
        return paint(ok, "32") if passed else paint(bad, "31")

    lines: list[str] = []
    lines.append(f"Trace-to-Eval  {summary.run_id}")
    lines.append(
        f"{summary.provider} / {summary.model}  ·  prompt {summary.prompt_version}  ·  "
        f"rev {summary.code_revision}"
    )
    if summary.baseline_name:
        lines.append(f"baseline: {summary.baseline_name}")
    lines.append("")

    lines.append(
        f"CASES  {summary.total_cases} total  ·  {summary.passed} passed  ·  "
        f"{summary.failed} failed  ·  {summary.infrastructure_errors} infra"
    )
    lines.append(f"COST   ${summary.total_cost_usd:.6f} total  ·  ${summary.avg_cost_usd:.6f} avg")
    lines.append(
        f"LAT    p95 {summary.p95_latency_ms:.0f}ms  ·  avg {summary.avg_latency_ms:.0f}ms"
    )
    if summary.signal_failures or summary.label_disagreements:
        lines.append(
            f"SIGNAL {summary.signal_failures} rubric failure(s)  ·  "
            f"{summary.label_disagreements} label disagreement(s)  ·  "
            f"{summary.skipped_rubrics} skipped rubric(s)"
        )

    failures = [item for item in summary.results if item.status != "pass"]
    if failures:
        lines.append("")
        lines.append("FAILED CASES")
        for item in failures[:max_failures]:
            marker = _status_marker(item.status, unicode=unicode)
            lines.append(f"  {marker} {_one_line(item.case_id, limit=80)}")
            if item.error_class:
                lines.append(
                    f"      provider error [{_one_line(item.error_class, limit=60)}] "
                    f"{_one_line(item.error_message or '', limit=120)}"
                )
            for assertion in item.failures:
                lines.append(
                    f"      {_one_line(assertion.name, limit=60)}: "
                    f"{_one_line(assertion.message, limit=140)}"
                )
        remaining = len(failures) - max_failures
        if remaining > 0:
            lines.append(f"  … {remaining} more failing case(s); see the JSON report")

    if gate_report.gates:
        lines.append("")
        lines.append("GATES")
        for gate in gate_report.gates:
            lines.append(f"  {verdict_mark(gate.passed)} {gate.name:<24} {_one_line(gate.detail)}")

    code = exit_code_for(summary, gate_report)
    outcome = verdict(summary, gate_report)
    headline = f"{outcome}  (exit {code})"
    lines.append("")
    lines.append(paint(headline, "32") if code == 0 else paint(headline, "31"))
    return "\n".join(lines)


def render_json(
    summary: RunSummary,
    gate_report: GateReport,
    *,
    include_results: bool = True,
) -> str:
    """Stable machine-readable report consumed by CI and other tools."""
    payload = summary.as_dict(include_results=include_results)
    payload["gates"] = gate_report.as_dict()
    payload["verdict"] = verdict(summary, gate_report)
    payload["exit_code"] = exit_code_for(summary, gate_report)
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_markdown(summary: RunSummary, gate_report: GateReport) -> str:
    """GitHub-comment-friendly Markdown."""
    code = exit_code_for(summary, gate_report)
    outcome = verdict(summary, gate_report)
    lines: list[str] = []
    lines.append(f"## Trace-to-Eval: {outcome}")
    lines.append("")
    lines.append(f"`{summary.run_id}` · `{summary.provider}` / `{summary.model}`")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("| --- | --- |")
    lines.append(f"| Cases | {summary.total_cases} |")
    lines.append(f"| Passed | {summary.passed} |")
    lines.append(f"| Failed | {summary.failed} |")
    lines.append(f"| Infrastructure errors | {summary.infrastructure_errors} |")
    lines.append(f"| Pass rate | {summary.pass_rate:.1%} |")
    lines.append(f"| Total cost | ${summary.total_cost_usd:.6f} |")
    lines.append(f"| Average cost | ${summary.avg_cost_usd:.6f} |")
    lines.append(f"| p95 latency | {summary.p95_latency_ms:.0f} ms |")
    lines.append(f"| Exit code | {code} |")
    lines.append("")

    failures = [item for item in summary.results if item.status != "pass"]
    if failures:
        lines.append("### Failing cases")
        lines.append("")
        lines.append("| Case | Status | Assertion | Detail |")
        lines.append("| --- | --- | --- | --- |")
        for item in failures[:MAX_FAILURES_SHOWN]:
            if item.failures:
                for assertion in item.failures:
                    lines.append(
                        f"| `{_md(item.case_id)}` | {item.status} | "
                        f"`{_md(assertion.name)}` | {_md(assertion.message)} |"
                    )
            else:
                lines.append(
                    f"| `{_md(item.case_id)}` | {item.status} | provider | "
                    f"{_md(item.error_message or item.error_class or '')} |"
                )
        lines.append("")

    if gate_report.gates:
        lines.append("### Gates")
        lines.append("")
        lines.append("| Gate | Result | Detail |")
        lines.append("| --- | --- | --- |")
        for gate in gate_report.gates:
            mark = "pass" if gate.passed else "**fail**"
            lines.append(f"| `{_md(gate.name)}` | {mark} | {_md(gate.detail)} |")
        lines.append("")
    return "\n".join(lines)


def _md(value: str) -> str:
    text = _one_line(value, limit=MAX_REPORT_LINE)
    for char in ("|", "`", "<", ">"):
        text = text.replace(char, "\\" + char)
    return text


def render_junit(summary: RunSummary, gate_report: GateReport) -> str:
    """JUnit XML so existing CI test reporters can display replay results."""
    suite = ET.Element(
        "testsuite",
        {
            "name": f"trace-to-eval {summary.provider}/{summary.model}",
            "tests": str(summary.total_cases),
            "failures": str(summary.failed),
            "errors": str(summary.infrastructure_errors),
            "skipped": "0",
            "time": f"{sum(item.latency_ms for item in summary.results) / 1000.0:.3f}",
            "timestamp": summary.started_at,
        },
    )
    properties = ET.SubElement(suite, "properties")
    for key, value in (
        ("run_id", summary.run_id),
        ("provider", summary.provider),
        ("model", summary.model),
        ("prompt_version", summary.prompt_version),
        ("code_revision", summary.code_revision),
        ("verdict", verdict(summary, gate_report)),
        ("total_cost_usd", f"{summary.total_cost_usd:.6f}"),
        ("p95_latency_ms", f"{summary.p95_latency_ms:.3f}"),
    ):
        ET.SubElement(properties, "property", {"name": key, "value": _xml_attr(value)})

    for item in summary.results:
        case = ET.SubElement(
            suite,
            "testcase",
            {
                "name": _xml_attr(item.case_id),
                "classname": _xml_attr(f"{summary.provider}.{summary.model}"),
                "time": f"{item.latency_ms / 1000.0:.3f}",
            },
        )
        if item.status == "pass":
            continue
        if item.status == "infrastructure_error":
            error = ET.SubElement(
                case,
                "error",
                {"type": _xml_attr(item.error_class or "infrastructure_error"), "message": ""},
            )
            error.text = _xml_text(item.error_message or item.error_class or "provider failure")
            continue
        failure = ET.SubElement(case, "failure", {"message": _failure_summary(item)})
        failure.text = _xml_text(_failure_detail(item))
    return ET.tostring(suite, encoding="unicode")


def _failure_summary(item: CaseResult) -> str:
    if item.failures:
        return _xml_attr(f"{len(item.failures)} assertion failure(s)")
    return "case failed"


def _failure_detail(item: CaseResult) -> str:
    chunks = [
        f"{assertion.name} [{assertion.kind}]: {assertion.message}" for assertion in item.failures
    ]
    if item.error_message:
        chunks.append(f"provider error: {item.error_message}")
    return "\n".join(chunks)


def _xml_attr(value: Any) -> str:
    return safe_text(str(value), max_length=400).replace("\n", " ")


def _xml_text(value: Any) -> str:
    return safe_text(str(value), max_length=4000)


def write_report(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        if not content.endswith("\n"):
            handle.write("\n")
