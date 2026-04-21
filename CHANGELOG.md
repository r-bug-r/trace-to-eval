# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog
and the project uses semantic versioning.

## Unreleased

### Added

- Repository documentation: architecture, threat model, privacy, operations, development.
- Contribution, security, governance, and conduct policies.
- Synthetic examples for chat, retrieval-augmented generation, and tool-calling traces.
- Composite GitHub Action and a scriptable end-to-end smoke scenario.
- Local web UI (`trace-to-eval serve`): overview, runs, run detail, cases, case detail,
  CI setup, and settings over the same SQLite store. Loopback-only, token-protected,
  zero-dependency, and restricted to the deterministic fake provider.
- CI setup view that generates a copyable `trace-to-eval.toml`, a GitHub Actions workflow,
  and the matching CLI command from editable gate values, with a local replay action.

### Fixed

- The case-delete confirmation no longer survives client-side navigation: any open modal is
  closed when a route renders, so it cannot cover an unrelated view.
- Control borders met neither theme's non-text contrast requirement (2.1:1). Both
  `--color-border-strong` values are darkened to at least 3.7:1 against every surface.
- Navigation links are now 44 px tall and no longer wrap mid-word at 320 px; the mobile
  header wraps between links instead of clipping "Settings".
- The case search reports its result count through a polite live region and shows an
  explicit "No cases match this search." row instead of an empty table.

## 0.1.0 - 2026-10-20

### Added

- Provider-neutral trace schema `1.0` with embedded JSON Schema.
- JSON/JSONL import with schema validation, normalization, content-addressed case IDs,
  idempotent re-imports, and pre-persistence redaction.
- SQLite case store with append-only labels, runs, results, baselines, and a live-call ledger.
- Deterministic assertions: text, JSON Schema subset, structured subset, field rules,
  citations, tool-call allow/forbid/require/argument checks, and human-label agreement.
- Fake provider with echo, mutate, cost, latency, and error scripting.
- Opt-in OpenAI-compatible and Anthropic adapters with fail-closed consent, request, token,
  per-run cost, daily, and monthly caps.
- Absolute and baseline-relative gates for pass rate, cost, latency, infrastructure errors,
  and signal failures.
- Terminal, JSON, Markdown, and JUnit reports with untrusted-content escaping.
- CLI commands: `init`, `import`, `list`, `label`, `run`, `report`, `export`, `schema`,
  `validate`, `budget`.
- CI for format, lint, strict types, tests, dependency audit, and secret scanning.
