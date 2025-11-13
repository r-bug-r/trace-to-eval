"""End-to-end smoke scenario for Trace-to-Eval.

Runs the documented workflow against the synthetic fixtures in a temporary directory and
asserts the observable exit codes and report content. Stdlib only; no installation required.

Usage:
    python scripts/smoke.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"


def run(args: list[str], *, expect: int, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    command = [sys.executable, "-m", "trace_to_eval", *args]
    print(f"$ {' '.join(command)}")
    result = subprocess.run(  # noqa: S603 - command is built from this script's own argv
        command,
        cwd=ROOT,
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=False,
    )
    if result.returncode != expect:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        raise SystemExit(f"expected exit {expect}, got {result.returncode}: {' '.join(args)}")
    print(f"  exit {result.returncode}")
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="trace-to-eval-smoke-") as tmp:
        work = Path(tmp)
        db = work / "cases.db"
        baseline = work / "baseline.json"
        regressed = work / "regressed.md"
        junit = work / "report.xml"
        exported = work / "export.json"

        env = os.environ.copy()
        env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
        env.pop("TRACE_TO_EVAL_DB", None)

        run(["--db", str(db), "init"], expect=0, env=env)
        run(
            ["--db", str(db), "import", str(FIXTURES / "traces.jsonl"), "--tag", "fixture"],
            expect=0,
            env=env,
        )

        listed = run(["--db", str(db), "list", "--json"], expect=0, env=env)
        cases = {case["trace_id"]: case["case_id"] for case in json.loads(listed.stdout)}
        if len(cases) != 5:
            raise SystemExit(f"expected 5 cases, got {len(cases)}")
        if "tr_fail_005" not in cases or "tr_tool_003" not in cases:
            raise SystemExit(f"missing expected fixtures: {sorted(cases)}")

        run(
            [
                "--db",
                str(db),
                "label",
                cases["tr_fail_005"],
                "--label",
                "failure",
                "--category",
                "tool_call",
                "--notes",
                "destructive tool call for a read-only question",
            ],
            expect=0,
            env=env,
        )
        run(
            [
                "--db",
                str(db),
                "label",
                cases["tr_tool_003"],
                "--label",
                "success",
                "--category",
                "tool_call",
                "--notes",
                "correct read-only tool",
            ],
            expect=0,
            env=env,
        )

        run(
            [
                "--db",
                str(db),
                "run",
                "--fake-script",
                str(FIXTURES / "fake-script.json"),
                "--save-baseline",
                "base",
                "--format",
                "json",
                "--out",
                str(baseline),
            ],
            expect=0,
            env=env,
        )
        baseline_doc = json.loads(baseline.read_text("utf-8"))
        if baseline_doc["verdict"] != "pass":
            raise SystemExit(f"baseline verdict was {baseline_doc['verdict']}")

        run(
            [
                "--db",
                str(db),
                "run",
                "--model",
                "fake-regressed-v2",
                "--fake-script",
                str(FIXTURES / "fake-script.json"),
                "--baseline",
                "base",
                "--min-pass-rate",
                "1.0",
                "--max-cost-increase-pct",
                "10",
                "--max-latency-increase-pct",
                "10",
                "--format",
                "markdown",
                "--out",
                str(regressed),
            ],
            expect=1,
            env=env,
        )
        report = regressed.read_text("utf-8")
        for needle in (
            "quality_regression",
            "min_pass_rate",
            "cost_regression",
            "latency_regression",
        ):
            if needle not in report:
                raise SystemExit(f"regressed report missing {needle!r}")

        run(
            ["--db", str(db), "report", "latest", "--format", "junit", "--out", str(junit)],
            expect=1,
            env=env,
        )
        run(
            ["--db", str(db), "export", "latest", "--out", str(exported)],
            expect=0,
            env=env,
        )
        exported_doc = json.loads(exported.read_text("utf-8"))
        if exported_doc["exit_code"] != 1:
            raise SystemExit(f"exported exit_code was {exported_doc['exit_code']}")

        budget = run(["--db", str(db), "budget", "--json"], expect=0, env=env)
        json.loads(budget.stdout)

        print("smoke: pass")
        print(f"  baseline: {baseline}")
        print(f"  regressed report: {regressed}")
        print(f"  junit: {junit}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
