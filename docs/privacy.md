# Privacy

## Data collected

Trace-to-Eval processes only files and flags the operator provides. It has no telemetry, no
analytics, no crash reporting, and no network calls unless a live provider is explicitly
enabled.

Stored data classes:

| Data | Location | Default retention |
| --- | --- | --- |
| Redacted traces | SQLite `cases` | Until deleted. |
| Human labels and notes | SQLite `labels` | Until deleted with the case. |
| Run summaries and per-case results | SQLite `runs`, `results` | Until deleted. |
| Baselines | SQLite `baselines` | Until replaced or deleted. |
| Live-call ledger entries | SQLite `ledger` | Until deleted. |
| Reports and exports | Paths chosen by the operator | Operator-controlled. |

## Redaction

Redaction runs before persistence. Built-in categories: API keys, bearer tokens, private
keys, emails, phone numbers, credit cards, IPv4 addresses. Custom patterns can be configured
in `[tool.trace_to_eval.redaction]`. Placeholders use `[REDACTED:<category>]`.

Redaction is best-effort. It cannot identify every secret format. Do not import production
data without reviewing the resulting store and configuring custom patterns.

## Deletion

- Delete one case and its labels/results: `CaseStore.delete_case(case_id)`.
- Delete a run: `CaseStore.delete_run(run_id)`.
- Delete the whole store: remove the SQLite file after closing every process.
- SQLite may retain deleted pages until `VACUUM`. Run `VACUUM` after deleting sensitive data
  if physical removal matters.

## Exports and backups

Exports contain only what is in the store, which is already redacted. Backups are byte copies
of the store and inherit its redaction state. Keep them on storage with the same access
controls as the original.

## Live providers

Live calls are opt-in. When enabled, the request content (input messages and tools) is sent to
the configured provider and is subject to that provider's retention and training policy. The
local ledger stores provider, model, request ID, token counts, cost, and outcome, not the
prompt or completion. Review the provider's terms before enabling live calls on sensitive
data.

## Contact

Report privacy issues through the private channel in `SECURITY.md`.
