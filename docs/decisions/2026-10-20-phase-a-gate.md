# Phase A gate: standalone CLI and test core

- **Date:** 2026-10-20
- **Status:** Accepted
- **Scope:** Trace-to-Eval standalone CLI/test core only

## Context

The portfolio plan defers Trace-to-Eval promotion and integration until OSS Maintainer
Autopilot has a pilot. The kickoff for this session narrowed the entry decision to the
standalone CLI and test core, with Autopilot integration and public release explicitly
deferred. This record captures the evidence required to close Phase A without claiming the
deferred work.

## Gate criteria

| Criterion | Evidence | Status |
| --- | --- | --- |
| Schema `1.0` validates chat, RAG, and tool traces | `trace-to-eval validate examples/traces/*.jsonl`; `schema.py` tests | Pass |
| Deterministic assertions cover text, JSON, tools, citations, human labels | `tests/unit/test_evaluate.py` | Pass |
| Redaction precedes persistence | `tests/unit/test_redact.py`, `tests/security/` | Pass |
| Idempotent, content-addressed case IDs | `tests/unit/test_store.py` | Pass |
| Fake provider is deterministic and scriptable | `tests/unit/test_fake_provider.py` | Pass |
| Gates distinguish quality from infrastructure failures | `tests/unit/test_gates.py`, integration run | Pass |
| Reports escape untrusted model output | `tests/security/` | Pass |
| Baseline-relative cost and latency regressions fail CI | `scripts/smoke.py` | Pass |
| Live calls fail closed without explicit consent and caps | `tests/unit/test_budget.py`, provider tests | Pass |
| CI covers format, lint, strict types, tests, audit, secret scan | `.github/workflows/ci.yml`, `security.yml` | Configured |
| Documentation and policies exist | README, docs, CONTRIBUTING, SECURITY, GOVERNANCE, CoC | Pass |

## Evidence commands

```text
python -m ruff format --check .      -> clean
python -m ruff check .               -> clean
python -m mypy src                   -> success
python -m pytest                     -> 120 passed
python scripts/smoke.py              -> baseline exit 0, regressed exit 1
```

The smoke scenario labels two failures, saves a baseline, replays a mutated model, and asserts
that quality, cost, and latency gates fail with exit code 1.

## Deferred

- Autopilot integration and any shared trace contract.
- Hosted dashboards, accounts, and cloud storage.
- Public benchmark dataset, marketplace publication, and PyPI release.
- Live-provider smoke runs (require opt-in budget and credentials).
- Multi-writer concurrency and Postgres storage.

## Consequences

The CLI can be used locally and in CI for a single repository. The trace schema, CLI flags,
and exit codes are treated as the stable surface for Phase B. Any incompatible change requires
a new schema version or a decision record.
