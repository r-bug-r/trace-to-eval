# Trace-to-Eval

Turn AI traces and production failures into versioned regression tests.

Trace-to-Eval is a local-first CLI and GitHub Action. It imports provider-neutral JSONL
traces, redacts secrets before persistence, replays cases against a fake or live provider,
evaluates deterministic assertions, and fails CI when quality, cost, or latency regresses.

```text
JSONL traces -> schema validation -> redaction -> SQLite case store
             -> replay (fake or live provider) -> assertions and gates
             -> terminal / JSON / Markdown / JUnit report -> CI exit code
```

- Every important AI failure becomes a repeatable test.
- No hosted account, telemetry, or cloud storage is required.
- The default path is zero-cost: synthetic fixtures, deterministic fake providers, SQLite.
- Live model calls are opt-in, budget-capped, and recorded in a local cost ledger.

## Requirements

- Python 3.12+
- Git (only for development)
- No runtime dependencies

## Install

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# POSIX:   source .venv/bin/activate
python -m pip install -e .
trace-to-eval --version
```

Development extras (pytest, ruff, mypy, jsonschema, pip-audit):

```bash
python -m pip install -e ".[dev]"
```

## Quickstart

```bash
trace-to-eval init
trace-to-eval import tests/fixtures/traces.jsonl --tag fixture
trace-to-eval list --json
```

Label a case using the ID printed by `list`:

```bash
trace-to-eval label case_<id> --label failure --category tool_call \
  --notes "called delete_account for a read-only question"
```

Replay every case with the deterministic fake provider and save a baseline:

```bash
trace-to-eval run --fake-script tests/fixtures/fake-script.json \
  --save-baseline base --format json --out baseline.json
```

Replay a changed model and fail on regressions:

```bash
trace-to-eval run --model fake-regressed-v2 \
  --fake-script tests/fixtures/fake-script.json \
  --baseline base --min-pass-rate 1.0 \
  --max-cost-increase-pct 10 --max-latency-increase-pct 10 \
  --format markdown --out regressed.md
echo $?   # 1: quality regression
```

Re-render the stored run later. Thresholds and the baseline name are persisted with the run,
so `report` reproduces the same gates without repeating every flag:

```bash
trace-to-eval report latest --format junit --out report.xml
trace-to-eval export latest --out run.json
```

A complete scripted scenario lives in `scripts/smoke.py`:

```bash
python scripts/smoke.py
```

## Commands

| Command | Purpose |
| --- | --- |
| `init` | Create the SQLite case store. |
| `import <paths...>` | Validate, redact, normalize, and store JSON or JSONL traces. Idempotent by case ID. |
| `list [--tag] [--limit] [--json]` | Show stored cases with their latest label and tags. |
| `label <case_id> --label success\|failure\|unknown` | Append a human label with optional category and notes. |
| `run` | Replay cases, evaluate assertions, apply gates, and render a report. |
| `report <run_id\|latest>` | Re-render a stored run using its persisted thresholds. |
| `export <run_id\|latest>` | Export a run, results, and exit code as JSON. |
| `schema` | Print the versioned trace JSON Schema. |
| `validate <path>` | Validate a trace JSONL file without importing it. |
| `budget` | Show local ledger spend and configured limits. |
| `serve [--host] [--port] [--open]` | Run the local, token-protected web UI over the case store. |

Global options: `--config <path>` and `--db <path>`. Environment override:
`TRACE_TO_EVAL_DB`.

## Trace schema

The schema is versioned as `1.0` and ships in
`src/trace_to_eval/data/trace-v1.schema.json`. Print it with `trace-to-eval schema`.

```json
{
  "schema_version": "1.0",
  "trace_id": "tr_chat_001",
  "created_at": "2026-10-18T09:12:00Z",
  "source": {"app": "support-copilot", "environment": "production"},
  "input": {"messages": [{"role": "user", "content": "Can I get a refund after 40 days?"}]},
  "output": {"text": "Refunds are available within 30 days.", "finish_reason": "stop"},
  "model": {"provider": "fake", "name": "fake-baseline-v1", "parameters": {"temperature": 0}},
  "metrics": {"latency_ms": 820, "input_tokens": 96, "output_tokens": 38, "cost_usd": 0.0021},
  "outcome": {"status": "success"},
  "expectations": {"text_contains": ["30 days"], "text_not_contains": ["guaranteed"]}
}
```

Examples for chat, retrieval-augmented generation, and tool-calling agents are in
`examples/traces/`.

Supported expectations:

| Key | Assertion |
| --- | --- |
| `text_equals`, `text_contains`, `text_not_contains` | Text assertions, optionally NFKC-normalized with `normalize: true`. |
| `json_schema` | Output must parse as JSON and satisfy the schema. |
| `json_equals` | Parsed output must equal the expected JSON value. |
| `rules` | Deterministic field rules: `equals`, `matches`, `required`, `must_match`, `must_not_match`. |
| `citations` | Required citation sources appear in the output. |
| `tool_calls.allowed` / `forbidden` / `required` / `arg_equals` | Tool-call safety and argument checks. |
| `human_label` | Compare replay outcome with the recorded human label. |
| `rubric` | Optional model-assisted signal with confidence and evaluator version. |

Replay never executes tool calls. It only compares recorded or simulated calls.

## Gates and exit codes

| Exit code | Meaning |
| --- | --- |
| `0` | All assertions and gates passed. |
| `1` | Quality regression: failed assertions, signal failures, pass-rate, cost, or latency gate. |
| `2` | Infrastructure failure: provider timeout, rate limit, auth error, invalid response, budget stop. |
| `3` | Usage or configuration error. |

Absolute gates: `--min-pass-rate`, `--max-total-cost-usd`, `--max-avg-cost-usd`,
`--max-p95-latency-ms`, `--max-infrastructure-errors`, `--max-signal-failures`.
Baseline-relative gates: `--max-pass-rate-drop`, `--max-cost-increase-pct`,
`--max-latency-increase-pct`. Configuring a relative gate without a baseline fails closed.

An infrastructure failure is never reported as a quality pass.

## GitHub Action

`.github/workflows` contains the project CI. To use Trace-to-Eval in another repository,
copy `action.yml` and `scripts/action_entry.py`, or run the CLI directly:

```yaml
name: trace-to-eval
on: [pull_request]
jobs:
  replay:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: python -m pip install -e .
      - run: trace-to-eval init
      - run: trace-to-eval import traces/*.jsonl
      - run: trace-to-eval run --format junit --out trace-to-eval-report.xml
      - if: always()
        uses: actions/upload-artifact@v4
        with:
          name: trace-to-eval-report
          path: trace-to-eval-report.xml
```

The composite action in `action.yml` wraps the same flow with inputs for traces, database,
baseline, thresholds, and output format.

## Local UI

`trace-to-eval serve` starts a loopback-only web UI over the same SQLite store. It is a
read/write view of the CLI's state, not a hosted service:

```bash
trace-to-eval serve --open
```

| View | What it does |
| --- | --- |
| Overview | Latest run, case counts, and the next action. |
| Runs | Start a deterministic fake-provider replay, follow its progress, and open the report. |
| Run detail | Gates, baseline comparison, failure clusters, and per-case results. |
| Cases | Search and filter stored traces by tag, label, and failure category. |
| Case detail | Trace timeline, redaction markers, assertions, and human labels. |
| CI | Threshold inputs, generated `trace-to-eval.toml` and workflow, and a copyable command. |
| Settings | Resolved configuration, thresholds, budget, retention, and adapters. |

The server binds `127.0.0.1` only, rejects unknown `Host` headers and cross-origin
mutations, and requires a per-process token that is injected into the page. It has no
accounts, telemetry, or uploads. `--allow-remote` exists for a trusted network only and
adds no authentication; do not expose the port to the internet.

The UI only runs the deterministic fake provider. Live provider calls stay in the CLI with
`--allow-live` and hard caps.

## Live providers

Live calls are disabled by default and fail closed. To enable one bounded run:

```bash
export TRACE_TO_EVAL_ALLOW_LIVE=1
export TRACE_TO_EVAL_OPENAI_API_KEY=...      # or TRACE_TO_EVAL_ANTHROPIC_API_KEY
trace-to-eval run --provider openai-compatible --model <model> \
  --allow-live --max-requests 20 --max-run-cost-usd 0.50 --max-tokens 50000
```

The adapter records provider, model, request ID, tokens, latency, and estimated cost in the
local ledger. Keys are read from the environment and are never written to the store, reports,
or ledger. `budget` shows spend. Ollama is supported through the OpenAI-compatible adapter
with `--base-url http://127.0.0.1:11434/v1` and any non-empty
`TRACE_TO_EVAL_OPENAI_API_KEY` value; it is optional and never required for tests or CI.

## Privacy and security

- Redaction runs before persistence: API keys, bearer tokens, private keys, emails, phone
  numbers, credit cards, IPv4 addresses, and configurable custom patterns.
- Reports treat trace content as untrusted: control characters and ANSI escapes are stripped,
  Markdown/XML are escaped, and fields are length-bounded.
- The case store is a local SQLite file. Back it up with `CaseStore.backup()` or copy it while
  no process is writing.
- See `docs/threat-model.md`, `docs/privacy.md`, and `SECURITY.md`.

## Development

```bash
python -m pip install -e ".[dev]"
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
python scripts/smoke.py
```

`make check` runs the same sequence on POSIX shells.

## Limitations

- No hosted dashboard, accounts, or benchmark leaderboard.
- Rubric scoring is a separately identified signal, not ground truth.
- Live-provider smoke tests are opt-in and are not run in CI.
- SQLite is single-process friendly; concurrent writers are not supported.
- The GitHub Action and packaging are prepared but not published to a marketplace or PyPI.

## License

MIT. See `LICENSE`.
