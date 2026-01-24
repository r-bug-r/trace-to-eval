# Examples

Synthetic traces for the three shapes the schema supports. Nothing here contains real user
data.

| File | Shape |
| --- | --- |
| `traces/chat.jsonl` | Multi-message chat with citation and text assertions. |
| `traces/rag.jsonl` | Retrieved context with a required citation. |
| `traces/tools.jsonl` | Tool-calling agent with allowed/forbidden tools and argument checks. |

## Try it

```bash
trace-to-eval --db .trace-to-eval/examples.db init
trace-to-eval --db .trace-to-eval/examples.db import examples/traces/*.jsonl
trace-to-eval --db .trace-to-eval/examples.db run \
  --fake-script tests/fixtures/fake-script.json --format markdown
```

The traces use the fake provider name `fake-baseline-v1`, so no credentials or network access
are required. Expectations are evaluated against the recorded output; replay replaces it only
when a fake script or live provider is configured.

## Authoring a trace

1. Start from the closest example and keep `schema_version` at `1.0`.
2. Record the smallest input that reproduces the failure.
3. Add expectations that would have caught it: text, tool, citation, or structured checks.
4. Set `human_label` only when a person has reviewed the case.
5. Remove secrets before committing. Import redaction is a safety net, not a license to commit
   real data.
