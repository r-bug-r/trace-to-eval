# Threat model

## Assets

- API keys and other credentials in the environment.
- Trace content, which may contain personal data or confidential business text.
- The local SQLite store, exports, and CI artifacts.
- Money spent through live provider calls.
- CI integrity: a gate must not be bypassable by model output or a malformed trace.

## Trust boundaries

| Input | Trust |
| --- | --- |
| JSONL/JSON trace files | Untrusted. Validated, normalized, redacted. |
| Model output and tool-call arguments | Untrusted data. Never executed. |
| Provider HTTP responses | Untrusted. Size-limited, schema-checked, errors sanitized. |
| Configuration and CLI flags | Trusted operator input, still validated. |
| Environment variables | Trusted for credentials; never persisted. |

## Threats and mitigations

| Threat | Mitigation | Residual risk |
| --- | --- | --- |
| Secret leakage into the store or reports | Redaction before persistence; provider errors scrubbed of known secrets; no key logging. | Unknown secret formats require custom patterns. |
| Prompt injection inside imported traces | Model output is data; assertions and gates are Python; no agent executes trace instructions. | A live model can still be influenced by injected input; results remain untrusted. |
| Tool replay causing side effects | Replay compares tool calls and never invokes tools. | Downstream integrations must not add execution. |
| Report injection (ANSI, Markdown, XML, control chars) | `safe_text` strips control characters and bidi overrides; Markdown and XML escaping; bounded fields. | Terminal emulators may interpret other escape classes; output is sanitized at the source. |
| SQL injection | All user values are bound parameters; only `?` placeholders are interpolated. | None known. |
| Oversized payloads / denial of service | File size, message count, body size, and field lengths are bounded; provider bodies are capped. | Extremely large fixture sets can still consume memory. |
| Runaway live spend | Fail-closed consent; request, token, per-run cost, daily, and monthly caps; ledger accounting before/after calls. | A provider that ignores cancellation can exceed wall-clock expectations; cost caps still apply. |
| Retry storms | Bounded retries with exponential backoff; only retryable classes retry. | Provider-side throttling may still occur. |
| Gate bypass via infrastructure errors | Infrastructure failures exit 2 and are never counted as quality passes. | A misconfigured `--max-infrastructure-errors` can mask infrastructure failures; defaults are 0. |
| Malicious baseline data | Baselines are stored from validated summaries; missing fields fail closed. | A user with store write access can alter baselines. |
| Dependency compromise | No runtime dependencies; dev tools are pinned by CI ranges; dependency audit and secret scan in CI. | Build tooling is still a supply-chain surface. |

## Out of scope

- A fully compromised host or terminal.
- Encrypting the local filesystem.
- Multi-tenant access control.
- Guaranteeing that a model-assisted rubric is correct.
