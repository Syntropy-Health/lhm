# LHM documentation

Plain-Markdown docs for the LHM SDK — readable on GitHub and PyPI, no site
generator required. Start with the [quickstart](guide/quickstart.md); the
[project README](../README.md) has the elevator pitch and the roadmap.

## Guides

| Page | What it teaches |
|---|---|
| [Quickstart](guide/quickstart.md) | Install, then the 5-minute path: synthetic fixtures → `fetch` → `analyze` → `manage` → `to_viz_payload`, every stage by hand |
| [Adapters](guide/adapters.md) | The `LhmAdapter` contract — the only integration you must write — with a complete CSV-backed example |
| [Natural language & `lhm.ask`](guide/nl-and-ask.md) | `MetricIntent`, the BYO-LLM `Interpreter`, the deterministic binder's refusal semantics, and the one-liner end to end |
| [Publishing](guide/publish.md) | The `lhm-publish/v1` envelope, `SyntropyTarget`, key handling, retry/idempotency semantics, in-process publishing |
| [The honesty layer](guide/honesty-layer.md) | Why the claim-scope guard is structural, the confidence tiers, the caveats-verbatim rule, and the render gates |

## Reference

| Page | What's in it |
|---|---|
| [Errors](reference/errors.md) | Every public exception — when it raises, why, and what to do |

## Design records

The commitment surface the SDK is built against:

- [0001 — the NL-to-publish pipeline](design/0001-nl-to-publish-pipeline.md)
- [0002 — packaging strategy (in-repo → PyPI split)](design/0002-packaging-strategy.md)

## Conventions used in the examples

- **All example data is synthetic.** Subject tokens are opaque
  (`demo-subject-001`, `syn-subject-011`); no example ever contains a name,
  email, or anything shaped like real personal data — the SDK itself refuses
  email-shaped subject tokens (C9).
- **Every code block in these docs has been executed** against the SDK
  version it documents; printed output shown in the docs is real output.
