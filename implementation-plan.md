# Trace-to-Eval — Implementation Plan

> Project-specific section extracted from `../open-source-projects-plan.md`. The shared portfolio guide in that source remains authoritative.

## Shared Development Guide (authoritative source excerpt)

## 2. Shared Development Guide

### 2.1 Repository setup

Each project should begin with:

```text
repo/
  README.md
  LICENSE
  CONTRIBUTING.md
  CODE_OF_CONDUCT.md
  SECURITY.md
  CHANGELOG.md
  GOVERNANCE.md
  docs/
    architecture.md
    threat-model.md
    privacy.md
    operations.md
    decisions/
  src/
  tests/
    unit/
    integration/
    fixtures/
  examples/
  scripts/
  .github/
    workflows/
      ci.yml
      release.yml
      security.yml
```

Recommended baseline:

- TypeScript or Python, chosen according to the ecosystem being integrated.
- A pinned runtime and lockfile.
- Formatting, linting, static typing, unit tests, integration tests, and a smoke command.
- Dependency vulnerability scanning and secret scanning in CI.
- Docker or a documented local install for server projects.
- SQLite for the first local deployment; PostgreSQL only when multi-user concurrency requires it.
- OpenTelemetry-compatible spans for request, model, tool, policy, and persistence events.

Do not create five repositories before validating the first product. Use a monorepo only if shared packages are genuinely reused; otherwise independent repositories make adoption and ownership clearer.

### 2.2 Development loop

1. Write a one-sentence user problem and a measurable acceptance criterion.
2. Add a threat or failure case before adding automation.
3. Implement deterministic domain logic first.
4. Add a provider adapter behind a narrow interface.
5. Test model responses with fixtures and contract checks; never rely only on live calls.
6. Add one end-to-end scenario using a fake provider.
7. Run the real provider only in opt-in integration tests with spend limits.
8. Document data flow and failure behavior.
9. Release a small version with a changelog entry.
10. Collect feedback and update the decision log.

### 2.3 Model integration contract

All projects that call a model should expose an interface equivalent to:

```text
complete(request) -> response
count_tokens(request) -> estimate
stream(request) -> events
```

The adapter must:

- Set request and total spend limits.
- Record provider, model, request ID, input/output token counts, latency, and estimated cost.
- Support cancellation and timeouts.
- Classify retryable versus permanent failures.
- Redact secrets before logs.
- Never retry non-idempotent side effects.
- Permit a deterministic fake implementation for tests.

Prompt templates are versioned artifacts. Store structured output schemas and validate model output before it reaches business logic.

### 2.4 Security baseline

For every project:

- Threat-model secrets, untrusted input, model prompt injection, data exfiltration, and denial of service.
- Treat model output as untrusted data.
- Use allowlists for tools, hosts, paths, repositories, and file types.
- Require explicit authorization for destructive operations.
- Use least-privilege tokens and separate development, test, and production credentials.
- Keep user content out of analytics by default.
- Publish a security contact and a supported-version policy.
- Add a private vulnerability-reporting path before public launch.

### 2.5 Testing ladder

| Layer | What it proves | Required examples |
|---|---|---|
| Unit | Pure rules and transformations | Policy precedence, parsers, budget math, redaction |
| Contract | Provider and integration schemas | Model response validation, webhook payloads, GitHub API fixtures |
| Integration | Components work together | Database, queue, provider fake, repository fixture |
| End-to-end | User workflow works | Install, authorize, run, approve, report, recover |
| Security | Abuse cases are contained | Prompt injection, path traversal, secret leakage, replay abuse |
| Live smoke | Real deployment works | One bounded request using a configured provider |

A test is useful only when it could fail for a plausible user-visible defect. Do not test copied values, source text, or a bare “does not throw” outcome.

### 2.6 API-cost controls

Before requesting funding, implement an auditable cost ledger:

```text
run_id
project
workflow
provider
model
request_id
started_at
input_tokens
output_tokens
estimated_cost
cache_hit
success
error_class
```

Controls:

- Development budget per day.
- Per-run and per-user request limits.
- Hard monthly cap.
- Cheap model for classification and routing; stronger model only for ambiguous work.
- Batch processing for offline evaluation where available.
- Cache immutable prompts and repository metadata when safe.
- Local fake/provider for CI.
- Human approval before expensive or externally visible actions.

The application should show exactly why funded API usage is necessary: evaluation, model comparison, adversarial testing, or the product's core workflow. Do not describe ordinary personal coding assistance as project API usage.

### 2.7 Release and provenance

Every release should include:

- Version, date, and release notes generated from actual history.
- Source commit identifier.
- Reproducible build or container digest where applicable.
- Test and security-scan status.
- Known limitations.
- Migration notes.
- SBOM for distributable binaries or containers when practical.

For historical context, create files such as `docs/retrospectives/2026-10-initial-research.md` with explicit labels. Do not manufacture dates or rewrite existing history to imply work that did not happen.

### 2.8 Community and governance

Start with a benevolent-maintainer model and publish the path toward broader governance:

- Public issue templates for bugs, features, security, and support.
- A maintainer decision log.
- A `good first issue` policy with real bounded tasks.
- Response-time goals stated as goals, not promises.
- Contributor license terms only if legally necessary and clearly explained.
- A roadmap based on user evidence.
- No silent telemetry; make opt-in and document it.

### 2.9 Funding evidence packet

Maintain a `funding/` folder outside the product runtime or a private application workspace containing:

- Repository URL, license, and release history.
- Current project start date.
- Maintainer role and ownership evidence.
- User counts defined precisely.
- Downloads, dependent repositories, external contributors, merged PRs, and issue activity with source links.
- Pilot permissions and anonymized case studies.
- API usage ledger and monthly forecast.
- Privacy and data-retention policy.
- Milestones funded by credits.
- What remains open-source and self-hostable.
- Honest limitations and program-fit gaps.

As of the official pages reviewed for this plan, OpenAI's Codex for Open Source program says eligible maintainers may receive six months of ChatGPT Pro with Codex, conditional Codex Security, and API credits for eligible open-source maintainer workflows; selection and scope are discretionary. Anthropic's Claude for Open Source page advertises six months of Claude Max 20x and lists signals including 500 dependent repos, 100 dependent packages, 200,000 combined monthly downloads, 100 merged PRs into repositories not owned in the prior 12 months, 20 external contributors, or an OpenSSF criticality score of 0.4 or above. Verify current terms before applying because program pages and benefits change.
### 2.10 Momus gap assessment and resolution

The initial plan is **conditionally approved**, with the following gaps resolved here before execution:

| Gap | Severity | Resolution |
|---|---|---|
| Provider, runtime, and deployment choices were open-ended | High | Default to Python 3.12, SQLite, local process execution, and a CLI-first vertical slice. Add a hosted adapter only after local acceptance tests pass. |
| “Zero cost” was not operationally defined | Critical | The $0 path below uses local software, public-repository infrastructure, deterministic fakes, and local models. Paid API calls, paid hosting, paid design tools, and paid domains are not required. |
| Free-service limits and billing risk were unspecified | High | Each optional service has a purpose, a free-use boundary, a no-card preference, and a local fallback. Current limits must be rechecked before use. |
| UI work had no acceptance contract | High | The separate UI/UX plan defines screens, states, tokens, accessibility, responsive breakpoints, and rendered verification gates for every project. |
| Legal and data-use boundaries were incomplete | High | Add license, third-party terms, consent, deletion, retention, export, and model-provider disclosure gates below. |
| Some adoption targets were difficult to measure | Medium | Define each metric's numerator, denominator, time window, source, and consent status before publishing it. |

### 2.11 Default $0 development stack

The following is the default path for the first 90 days. It assumes the developer already has a computer and internet access; it does not treat electricity, an existing device, or personal time as a cash expense. No credit card, paid API, paid hosting plan, paid domain, paid analytics service, or paid design subscription is required.

| Need | Default $0 choice | Why | Fallback or limit |
|---|---|---|---|
| Runtime | Python 3.12, `uv` or `venv`, `pytest`, `ruff`, `mypy` | Fast CLI/server iteration and broad document/API libraries | Use the system Python package manager if `uv` is unavailable |
| Source control | Public GitHub repository with Git | Issues, discussions, releases, and transparent history | Any public Git host works; keep exported release archives |
| CI | GitHub Actions on a public repository | GitHub documents public-repository Actions usage as free | Run locally when quotas or service availability change |
| Static project site | GitHub Pages | GitHub documents Pages availability for public repositories on GitHub Free | Serve `docs/` locally; do not buy a domain |
| Database | SQLite and local filesystem | No account, network, or recurring cost | PostgreSQL only for a later, separately funded deployment |
| Model in development | Deterministic fake provider | Zero cost, repeatable, safe for CI | Ollama with a locally downloaded model when hardware permits |
| Local inference | Ollama or another local runtime | Ollama documents local execution on supported desktop systems | Smaller model, CPU-only mode, or fake provider if hardware is insufficient |
| OCR | Tesseract plus local PDF/text tools | Local and reproducible for document fixtures | Use text fixtures when OCR dependencies are unavailable |
| UI design | Plain Markdown/CSS tokens or self-hosted Penpot | Penpot describes its design tool as free and open source | Do not block implementation on a design SaaS account |
| Browser verification | Playwright plus `@axe-core/playwright` | Playwright documents accessibility testing with axe-core | Manual browser checks if browser binaries cannot be installed |
| Feedback | GitHub Issues, Discussions, and volunteer interviews | Public and auditable, with no form or analytics subscription | Local notes with consent and redaction |
| Demo hosting | Localhost first; GitHub Pages for static fixture demos | Avoids server bills and secret handling | A public demo is optional, never a prerequisite for correctness |

The only model-dependent test in CI should be an explicit, opt-in live smoke test. All normal CI, screenshots, benchmarks, and regression tests must run with fakes or local models.

### 2.12 Free-tier and billing guardrails

Free tiers are service policies, not permanent guarantees. Treat the linked provider documentation as the source of truth at implementation time. The plan must remain executable when every hosted service is unavailable.

**GitHub:** Use a public repository for source, issues, Actions, and Pages. GitHub documentation states that public-repository Actions usage is free and that Pages is available on GitHub Free for public repositories. Do not enable paid runners, private-repository overages, marketplace actions with charges, or a paid organization plan.

**Cloudflare:** Optional only for a stateless public demo or webhook relay. The Workers documentation currently describes a Free plan with a daily request limit. Do not put personal documents, provider keys, or persistent production data there. Set a local-only default and remove the integration if a payment method or billing upgrade is required.

**Supabase/Neon:** Optional only for a disposable, non-sensitive pilot after local SQLite works. Never make the product depend on a hosted database for development. Do not upload repository content, household documents, API keys, or private traces to a free hosted database without explicit consent and a documented retention policy. Stop using the service before its free quota or inactivity policy creates operational risk.

**Model providers:** No provider API is required for development. Do not spend personal funds to create benchmark data. Use fake responses and local inference. Apply for credits only after the product has a bounded workload, a cost ledger, a hard cap, and a public artifact the credits will produce.

**No-billing rule:** If a provider asks for a card, enables automatic overage, requires a paid plan, or cannot expose a hard usage cap, it is not part of the $0 path. Every optional integration must have an environment flag, a local fallback, and a removal test.

### 2.13 Concrete local development workflow

1. Install Python 3.12, Git, a code editor, and optionally Ollama. Verify versions and record them in `docs/development.md`.
2. Create a public repository with the actual current date, an OSI-approved license, `pyproject.toml`, lockfile, and `Makefile` or equivalent task runner.
3. Implement domain rules and a fake provider before credentials, webhooks, or hosted services.
4. Keep all fixtures synthetic. Put secrets only in local environment variables; add a secret-scanning pre-commit check.
5. Run `format`, `lint`, `typecheck`, `unit`, `integration`, `security`, and `smoke` tasks locally before pushing.
6. Push to a public repository and let free public CI repeat the same commands.
7. Use GitHub Pages only for static documentation and fixture demos; never expose a secret through a static build.
8. Run the UI through a local dev server and Playwright at 320, 390, 768, 1024, and 1440 CSS pixels.
9. Record model-free benchmark results, screenshots, accessibility output, and API-cost ledger entries in the release evidence.
10. Add a real provider only after the fake-provider path is stable, with a daily cap of zero by default and an explicit opt-in for a bounded smoke run.

### 2.14 Required project gates before any paid usage

No project may request or consume funded API credits until all gates pass:

- The core user workflow works with a fake provider.
- A local-model path has been attempted or the reason it is infeasible is documented.
- A request budget, token budget, timeout, retry policy, and monthly hard cap are implemented.
- The cost ledger records provider, model, request ID, input/output tokens, outcome, and estimated cost.
- Sensitive input is redacted before logs and optional persistence.
- The provider can be disabled without breaking the deterministic workflow.
- A benchmark fixture set and regression threshold exist.
- A human approval gate exists for externally visible or destructive actions.
- The funding application names the exact public artifact produced by the requested usage.

### 2.15 Legal, data, and operational gates

Before public pilots:

- Choose and publish an OSI-approved license compatible with every dependency.
- Generate a dependency inventory and review licenses for model runtimes, OCR, icons, fonts, and SDKs.
- Publish `SECURITY.md`, a privacy notice, retention defaults, deletion behavior, and a contact method.
- Do not process private repository content, household documents, or personal data without explicit permission and a documented purpose.
- Keep pilot data separate from public fixtures. Use synthetic or redacted examples for demos.
- Obtain consent before publishing screenshots, repository names, quotes, metrics, or case studies.
- Document whether model providers retain inputs, whether training is enabled, and how users can disable provider calls.
- Define incident response: revoke credentials, stop processing, preserve minimal evidence, notify affected users, and document remediation.
- Define backup behavior and deletion verification for every persisted data class.
- Add rate limits, payload-size limits, timeout limits, and abuse reporting before accepting public traffic.

### 2.16 Feasibility decision

The five projects are not equal in execution risk. The plan is feasible only if work remains sequential:

- **Build now:** OSS Maintainer Autopilot with fake-provider support and Trace-to-Eval fixtures.
- **Build after the first pilot:** Trace-to-Eval as a standalone CLI.
- **Do not start until a security reviewer is available:** Open Agent Firewall.
- **Do not start until a real routing/budget use case exists:** AI Gateway Cost Firewall.
- **Prototype only after the developer can support local document security:** Open Home File.

The exit decision for each project is evidence-based: stop, continue, or narrow scope after the stated pilot metrics. A project does not advance because its implementation is interesting or because an API grant is available.
### 2.17 $0 source register

Re-check these official pages immediately before enabling an optional service. The plan relies on the documented public-repository and free-plan behavior, not on a permanent promise from any provider:

- GitHub Actions billing: https://docs.github.com/en/billing/concepts/product-billing/github-actions
- GitHub Pages: https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages
- Cloudflare Workers limits: https://developers.cloudflare.com/workers/platform/limits/
- Supabase pricing: https://supabase.com/pricing
- Ollama local execution: https://docs.ollama.com/local
- Playwright accessibility testing: https://playwright.dev/docs/accessibility-testing
- Penpot open-source design tool: https://penpot.app/design/design-tool

If an official page changes a limit, requires billing, or removes a needed feature, use the local fallback and update `docs/development.md`; do not silently add a paid dependency.

---


---

---

---

# 4. Project Plan: Trace-to-Eval
**Zero-cost development mode:** Import synthetic JSONL fixtures, use a local SQLite case store, deterministic fake model versions, and local CI. Add Ollama only as an optional local comparison; no hosted traces or paid model calls are required.


## Goal and wedge

Build a CLI and GitHub Action that turns real AI interactions, tool traces, and production failures into versioned regression tests.

**One-line promise:** Every important AI failure becomes a repeatable test.

**Primary audience:** Developers shipping chatbots, agents, retrieval systems, and model-powered workflows.

## MVP requirements

1. Define a provider-neutral JSONL trace schema.
2. Capture messages, tool calls, tool results, model metadata, latency, cost, and outcome labels.
3. Redact secrets and configurable personal data before persistence.
4. Permit human labels and failure categories.
5. Replay traces against a new model, prompt version, or code revision.
6. Compare expected properties, not only exact text.
7. Produce a readable terminal and machine-readable report.
8. Fail a GitHub Action on configured quality, cost, or latency regressions.
9. Provide fake providers and deterministic fixtures.
10. Export data without requiring a hosted account.

Out of scope: a hosted observability product, an all-purpose benchmark leaderboard, and claims that one LLM judge is ground truth.

## Architecture and data flow

```text
SDK / JSONL import
    -> schema validation
    -> redaction and normalization
    -> case store
    -> replay runner
    -> provider adapter or local fake
    -> assertion and evaluator pipeline
    -> report renderer
    -> CI exit code / artifact
```

Evaluation types:

- Exact or normalized structured output.
- Schema validity.
- Deterministic business-rule checks.
- Retrieval citation presence.
- Tool-call safety assertions.
- Human label comparison.
- Model-assisted rubric only as a separately identified signal.

## Implementation sequence

1. Specify and version the trace schema with examples for chat, RAG, and multi-step tools.
2. Implement parser, validator, redactor, and stable case IDs.
3. Implement a local case store and CLI commands: `import`, `label`, `run`, `report`, `export`.
4. Implement fake provider and deterministic assertions before live providers.
5. Add Claude and OpenAI-compatible adapters with cost and latency capture.
6. Add trace replay with prompt/model version metadata and no implicit side effects.
7. Add rubric evaluators with confidence and evaluator-version fields.
8. Add GitHub Action with artifact upload and threshold configuration.
9. Publish a small public benchmark with synthetic or consented data.
10. Integrate the runner into the maintainer autopilot project for its own regression tests.

## Verification strategy

- Same trace produces the same case ID after normalization.
- Secret redaction does not alter non-sensitive fields.
- A tool call with a changed argument fails the appropriate assertion.
- A provider timeout is reported as an infrastructure failure, not a model-quality pass.
- A quality regression exits CI with a nonzero code and an actionable report.
- A cost regression is detected even when the answer text is unchanged.
- Model-assisted judge disagreement is visible and never silently converted into truth.

Smoke scenario: import five fixture traces, label two failures, run them against two fake model versions, observe one quality failure and one cost failure, and inspect the CI-compatible report.

## Rollout and traction

- Release the trace schema and CLI first; these are the easiest artifacts to adopt.
- Publish integrations for one web framework and one agent framework.
- Offer a “production failure to test” example repository.
- Ask early users to contribute anonymized failure categories rather than private traces.
- Track package installs, GitHub Action runs, public integrations, and repeated users.

90-day success metrics:

- 50 public or private repositories using the CLI or Action.
- 1,000 recorded evaluation cases, with provenance fields complete.
- At least five external contributors or integration authors.
- At least ten regression reports linked to actual changes.
- Demonstrated reduction in repeated failure recurrence for one pilot.

## Risks and mitigations

- **LLM judge bias:** Prefer deterministic assertions; require human review for high-impact claims.
- **Sensitive traces leak:** Local-first storage, explicit redaction, encryption at rest, no default cloud sync.
- **Benchmark gaming:** Publish dataset generation and evaluation versions; separate training data from test data.
- **Provider changes alter results:** Record exact model identifiers and rerun baselines after model updates.
- **Too broad for a first release:** Keep the trace schema and CLI as the core; defer hosted dashboards.

---
