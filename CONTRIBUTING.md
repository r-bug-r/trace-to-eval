# Contributing

Trace-to-Eval is a small, local-first tool. Contributions should keep the core deterministic,
dependency-free, and reviewable.

## Before you start

- Read `docs/architecture.md` and `docs/development.md`.
- Search existing issues and pull requests.
- For a behavior change, open an issue describing the failure mode and the expected exit code.
- Never include real traces, credentials, or personal data in an issue, test, or commit.

## Development flow

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
python -m pytest
python scripts/smoke.py
```

Before opening a pull request:

```bash
python -m ruff format --check .
python -m ruff check .
python -m mypy src
python -m pytest
```

## What a good change includes

- A test that fails before the change and passes after, at the lowest useful layer.
- Fixtures that are synthetic and minimal.
- Documentation updates for user-visible behavior: README, schema, exit codes, or docs.
- A changelog entry under `Unreleased`.

## Commit messages

Use an imperative subject line, optionally scoped:

```text
report: escape tool-call arguments in Markdown
schema: reject non-string citation sources
```

Keep commits focused. Do not reformat unrelated files.

## Pull requests

- Describe the failure mode, the change, and the verification you ran.
- State any new dependency and why it is required.
- State whether live providers were exercised. Do not run live calls in CI.
- Maintainers may ask for a smaller change or a design note in `docs/decisions/`.

## Review and merge

- One maintainer approval is required.
- CI must pass: format, lint, types, tests, dependency audit, secret scan.
- Squash or rebase; do not merge with failing or skipped required checks.

## Security reports

Do not open a public issue. Follow `SECURITY.md`.
