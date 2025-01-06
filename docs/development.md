# Development

## Verified environment

| Tool | Version |
| --- | --- |
| Python | 3.12.10 |
| pytest | 9.1.1 |
| mypy | 2.4.0 (strict) |
| ruff | 0.17.1 |
| jsonschema | 4.26.0 (test-only) |
| Git | 2.53.0 |

Record the versions actually used when you run the commands below.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
```

There is no runtime lockfile because the package has zero runtime dependencies. Development
tools are constrained by the ranges in `pyproject.toml`; CI installs them from there. If a
dependency is ever added at runtime, add a lockfile at the same time.

## Commands

| Task | Command |
| --- | --- |
| Format | `python -m ruff format .` |
| Format check | `python -m ruff format --check .` |
| Lint | `python -m ruff check .` |
| Type check | `python -m mypy src` |
| Unit tests | `python -m pytest tests/unit` |
| Integration tests | `python -m pytest tests/integration` |
| Security tests | `python -m pytest tests/security` |
| Full suite | `python -m pytest` |
| Smoke scenario | `python scripts/smoke.py` |
| Dependency audit | `python -m pip_audit` |

`make check` runs format check, lint, type check, tests, and the smoke scenario.

## Fixtures and data policy

- All committed fixtures are synthetic. Never commit real traces, secrets, or personal data.
- Redaction runs on import; tests assert that secrets never reach the store.
- Tests run with the fake provider. Live calls are never part of CI.
- New assertion types need a fixture that demonstrates both pass and fail.

## Test ladder

| Layer | Location | Proves |
| --- | --- | --- |
| Unit | `tests/unit` | Schema, redaction, assertions, gates, budget, store, providers. |
| Integration | `tests/integration` | Full CLI workflow: import, label, baseline, regression, reports. |
| Security | `tests/security` | Report escaping, ANSI/control stripping, secret persistence. |
| Smoke | `scripts/smoke.py` | The documented end-to-end scenario with real exit codes. |

## Release

1. Update `CHANGELOG.md` with actual changes.
2. Confirm `make check` passes.
3. Tag `vX.Y.Z`; `.github/workflows/release.yml` builds sdist/wheel and attaches them to the
   GitHub release.
4. PyPI publication is intentionally not automated until the interface-stability criteria in
   the portfolio plan are met.
