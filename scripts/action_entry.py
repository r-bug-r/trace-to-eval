"""Entry point for the composite GitHub Action.

Maps action inputs (environment variables prefixed with ``INPUT_``) to CLI arguments and runs
init, import, and replay. Exits with the replay exit code so the job fails on a regression.
"""

from __future__ import annotations

import glob
import os
import sys
from pathlib import Path

from trace_to_eval.cli import main

EXIT_USAGE = 3


def input_value(name: str, default: str = "") -> str:
    return os.environ.get(f"INPUT_{name.upper().replace('-', '_')}", default).strip()


def flag(name: str, value: str) -> list[str]:
    return [f"--{name}", value] if value else []


def expand(patterns: str) -> list[str]:
    paths: list[str] = []
    for pattern in patterns.replace(",", " ").split():
        matches = sorted(glob.glob(pattern, recursive=True))
        paths.extend(path for path in matches if Path(path).is_file())
    return list(dict.fromkeys(paths))


def main_action() -> int:
    traces = expand(input_value("traces", "traces/*.jsonl"))
    if not traces:
        print(
            "trace-to-eval: no trace files matched INPUT_TRACES",
            file=sys.stderr,
        )
        return EXIT_USAGE

    db = input_value("db", ".trace-to-eval/cases.db")
    base = ["--db", db]

    code = main([*base, "init"])
    if code != 0:
        return code

    import_args = [*base, "import", *traces]
    if input_value("tag"):
        import_args += ["--tag", input_value("tag")]
    code = main(import_args)
    if code != 0:
        return code

    run_args = [*base, "run"]
    for name in (
        "provider",
        "model",
        "fake-script",
        "baseline",
        "min-pass-rate",
        "max-total-cost-usd",
        "max-avg-cost-usd",
        "max-p95-latency-ms",
        "max-infrastructure-errors",
        "max-signal-failures",
        "max-pass-rate-drop",
        "max-cost-increase-pct",
        "max-latency-increase-pct",
        "max-run-cost-usd",
        "max-requests",
        "max-tokens",
        "timeout",
    ):
        run_args += flag(name, input_value(name))

    output_format = input_value("format", "markdown")
    out = input_value("out", f"trace-to-eval-report.{'xml' if output_format == 'junit' else 'md'}")
    run_args += ["--format", output_format, "--out", out]

    if input_value("allow-live").lower() in {"1", "true", "yes"}:
        run_args.append("--allow-live")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write(f"report={out}\n")

    return main(run_args)


if __name__ == "__main__":
    raise SystemExit(main_action())
