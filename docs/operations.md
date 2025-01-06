# Operations

## Install and upgrade

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
```

There are no runtime dependencies. Upgrades are package upgrades; the SQLite schema is
created with `CREATE TABLE IF NOT EXISTS` and additive indexes. Back up the store before a
version change that alters storage.

## Backups

Use the SQLite backup API while the process is idle:

```python
from trace_to_eval.store import CaseStore

with CaseStore(".trace-to-eval/cases.db") as store:
    store.backup("backup/cases-2026-10-20.db")
```

Copying the file while no process is writing is also safe. Do not copy a database mid-write.

## CI

Recommended public-repository workflow:

1. Checkout and install Python 3.12.
2. `python -m pip install -e .`
3. `trace-to-eval init`
4. `trace-to-eval import traces/*.jsonl`
5. `trace-to-eval run --baseline <name> --max-cost-increase-pct 10 ... --format junit --out report.xml`
6. Upload `report.xml` with `actions/upload-artifact` even when the job fails.

Exit codes: `0` pass, `1` quality regression, `2` infrastructure failure, `3` usage error.
Treat `2` as retryable and `1` as a code or prompt change.

## Local UI

`trace-to-eval serve` binds `127.0.0.1:8765` by default and serves the UI from the same
process and the same SQLite file as the CLI. Operational notes:

- Stop the process with `Ctrl+C`. The UI has no background workers or scheduled jobs.
- The per-process CSRF token is regenerated on every start; an open tab from a previous
  process fails its mutations until it is reloaded.
- `--host 0.0.0.0` requires `--allow-remote` and provides no authentication. Use it only
  behind a trusted proxy or not at all.
- `POST /api/run` reuses the CLI runner with the fake provider. A second concurrent run is
  rejected instead of queued.
- Deleting a case from the UI deletes its labels and stored run results. Back up the store
  first if you need the history.
- The UI never writes outside the database; it does not touch reports, ledgers, or trace
  files.

## Baselines

Create a baseline from a known-good run:

```bash
trace-to-eval run --save-baseline main
```

Update it deliberately after reviewing the diff in pass rate, cost, and latency. A baseline is
a stored summary; it does not contain model output.

## Live calls

Live providers require all of:

- `--allow-live`
- `TRACE_TO_EVAL_ALLOW_LIVE=1`
- `--max-run-cost-usd <positive>`
- `--max-requests <positive>`

Optional caps: `--max-tokens`, `--timeout`, daily and monthly caps in configuration. The
ledger records every attempt. Inspect it with `trace-to-eval budget`.

## Incident response

1. Stop processing: terminate the process and disable live calls by unsetting
   `TRACE_TO_EVAL_ALLOW_LIVE`.
2. Revoke any exposed provider key.
3. Preserve minimal evidence: the store, reports, and ledger. Do not copy unredacted input.
4. Determine the affected data class and time window.
5. Delete affected cases, runs, or the store; run `VACUUM` if physical deletion matters.
6. Notify affected users if personal data was exposed.
7. Record the root cause and remediation in `docs/decisions/`.

## Limits

- SQLite supports one writer at a time. Do not run concurrent imports or replays against the
  same store.
- Provider response bodies are capped; oversized responses fail as `invalid_response`.
- Report fields are bounded; very long model output is truncated in reports, not in the store.
