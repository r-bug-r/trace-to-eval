# Governance

## Model

Trace-to-Eval uses a benevolent-maintainer model during its 0.x phase. The maintainer listed
in `pyproject.toml` is the current decision maker. The goal is to move to a small maintainer
team and documented consensus once there is more than one regular contributor.

## Decisions

- Routine changes: pull request review and merge by a maintainer.
- User-visible behavior, storage format, exit codes, or security posture: a decision record in
  `docs/decisions/` with context, options, and consequences.
- Changes that break the trace schema or CLI contract require a new schema or major version
  and a migration note.

## Roles

| Role | Responsibilities |
| --- | --- |
| Maintainer | Merge rights, releases, security response, roadmap. |
| Reviewer | Review pull requests in an area of expertise; no merge rights required. |
| Contributor | Issues, code, docs, fixtures, reviews. |

## Becoming a maintainer

Sustained, high-quality contributions over at least three months, sound review judgment, and
alignment with the project's local-first and deterministic-first constraints. Existing
maintainers decide by consensus.

## Roadmap input

Roadmap priorities are set by the maintainer from observed user problems, not from feature
volume. The portfolio plan's entry and exit criteria remain authoritative until this project
has independent adoption.

## Conflicts of interest

Maintainers disclose financial or employment relationships that could affect decisions about
providers, dependencies, or hosted services.

## Security and conduct

Security reports follow `SECURITY.md`. Conduct reports follow `CODE_OF_CONDUCT.md` and are
handled privately by the maintainers.
