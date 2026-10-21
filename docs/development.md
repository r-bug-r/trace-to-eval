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

## Web UI verification

The browser gates are manual because the UI has no JavaScript build step or test runner.
Run them before any UI release and paste the observed results into the pull request.

```bash
PYTHONPATH=src python -m trace_to_eval --db .tmp-ui/ui.db import examples/traces/*.jsonl
PYTHONPATH=src python -m trace_to_eval --db .tmp-ui/ui.db serve --port 8765
```

`.tmp-*` is gitignored, so these commands never touch committed data.

| Gate | Method | Pass condition |
| --- | --- | --- |
| Route rendering | Open every route below at 320, 390, 768, 1024, and 1440 px. | No route shows an error view; `document.documentElement.scrollWidth` equals `clientWidth`. |
| Automated a11y | Inject `axe-core` and run `wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa` on every route, in light and dark. | Zero violations. |
| Keyboard | `Tab` from a fresh load through every control on every route. | Order matches visual order, every stop draws a focus ring, no control is skipped, no focus lands behind a modal. |
| Modal | Open the case-delete dialog with `Enter`, tab past its last control, press `Escape`. | Focus stays on dialog controls, `Escape` closes, focus returns to the trigger. |
| Contrast | Recompute `docs/design-tokens.md` against `tokens.css`. | Every listed pair meets its required ratio. |
| Touch targets | Measure `button`, `a`, `select`, `input`, `textarea` boxes. | No visible control is under 44 px tall. |
| Reduced motion | Emulate `prefers-reduced-motion: reduce`. | Motion tokens resolve to `0ms`; no transition or animation runs. |

Routes: `#/`, `#/cases`, `#/cases/<case_id>`, `#/runs`, `#/runs/<run_id>`, `#/ci`, `#/settings`.

At 200% zoom a 1440 px window lays out at 720 CSS px, which the width sweep above already
covers; check text-only reflow by emulating 320 px with a 200% browser font size.

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
