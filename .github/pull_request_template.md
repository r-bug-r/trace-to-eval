## What changed

Describe the failure mode and the fix.

## Verification

```bash
# commands run, with observed results
```

## Checklist

- [ ] Tests fail before and pass after, or the change is documentation-only.
- [ ] `python -m ruff format --check .` passes.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m mypy src` passes.
- [ ] `python -m pytest` passes.
- [ ] Fixtures are synthetic; no secrets or personal data.
- [ ] User-visible behavior is documented and in `CHANGELOG.md`.
- [ ] No new runtime dependency, or the dependency is justified above.
- [ ] Live provider calls were not run in CI.
