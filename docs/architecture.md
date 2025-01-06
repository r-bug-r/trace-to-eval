# Architecture

## Purpose

Trace-to-Eval converts recorded AI interactions into deterministic regression cases. The core
is a Python 3.12 package with no runtime dependencies and a SQLite store. The CLI is the only
interface; there is no daemon, hosted service, or background sync.

## Data flow

```text
JSON/JSONL file
  -> parse_trace_line            schema.py    decode + validate against trace schema 1.0
  -> normalize_trace             schema.py    NFKC, canonical line endings, stable ordering
  -> case_id_for_trace           schema.py    content-addressed identity
  -> redact_trace                redact.py    secrets and configured PII removed
  -> CaseStore.add_case          store.py     SQLite: cases table
  -> run_replay                  runner.py    per-case provider call + evaluate_output
  -> evaluate_gates              gates.py     absolute and baseline-relative gates
  -> render_*                    report.py    terminal, JSON, Markdown, JUnit
  -> exit_code_for               gates.py     0 pass, 1 quality, 2 infrastructure
```

Replay is side-effect free: model-produced tool calls are compared, never executed.

## Modules

| Module | Responsibility |
| --- | --- |
| `cli.py` | Argument parsing, command orchestration, exit codes, report emission. |
| `config.py` | Defaults, `trace-to-eval.toml` / `pyproject.toml`, environment overrides. |
| `schema.py` | Schema 1.0 validation, normalization, case IDs, embedded JSON Schema. |
| `redact.py` | Pre-persistence redaction with per-category counts and custom patterns. |
| `store.py` | SQLite schema, cases, labels, runs, results, baselines, ledger, backup. |
| `runner.py` | Selection, replay, persistence, summary aggregation. |
| `evaluate.py` | Deterministic assertions and rubric signal assembly. |
| `rubric.py` | Opt-in model-assisted scoring with confidence and evaluator version. |
| `gates.py` | Thresholds, baseline snapshots, gate evaluation, verdict, exit codes. |
| `report.py` | Report renderers with untrusted-content escaping. |
| `budget.py` | Request/token/cost caps, live-call consent, retry accounting. |
| `pricing.py` | Offline price table and per-model estimation. |
| `providers/` | `Provider` contract, deterministic fake, opt-in live adapters. |
| `jsoncheck.py` | Minimal JSON Schema subset used by `json_schema` expectations. |

## Invariants

- **Case identity is content-addressed.** The case ID hashes normalized input, output, tools,
  model, and expectations. Trace metadata such as `trace_id`, `created_at`, `source`, and
  metrics does not affect identity, so re-imports are idempotent.
- **Redaction precedes persistence.** Stored traces and exports contain placeholders, not
  secret values.
- **Deterministic checks are Python.** No gate, threshold, or assertion depends on a model.
- **Infrastructure is not quality.** Provider failures are classified and mapped to exit 2.
- **Reports treat model output as untrusted.** Control characters and ANSI escapes are
  stripped; Markdown and XML are escaped; fields are bounded.
- **Live calls fail closed.** Consent requires `--allow-live`, `TRACE_TO_EVAL_ALLOW_LIVE=1`, a
  positive per-run cost cap, and a request cap.
- **Run thresholds are persisted.** `report` and `export` reuse the thresholds stored with the
  run, with explicit CLI flags as overrides.

## Storage

SQLite tables: `cases`, `labels`, `runs`, `results`, `baselines`, `ledger`, `schema_meta`.
Labels are append-only; the latest row wins. Results cascade-delete with their run. The
ledger records every live attempt, including failures, with `request_id`, token counts,
estimated cost, and error class. `CaseStore.backup()` uses SQLite's backup API.

## Provider contract

```python
complete(request, *, timeout_s) -> CompletionResponse
count_tokens(request) -> int
```

Adapters must classify retryable versus permanent failures, respect timeouts, never retry
non-idempotent work, and report tokens, cost, and latency. The fake provider implements the
same contract and is the default everywhere, including CI.

## Extension points

- Add an assertion by extending `evaluate_output` and documenting the expectation key in the
  schema and README.
- Add a provider by implementing `Provider` in `providers/`, adding consent and budget
  handling, and covering it with a fake-HTTP contract test.
- Add a report format by extending `_FORMATS` and `render_*` in `report.py`.
