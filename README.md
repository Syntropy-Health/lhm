# LHM — Longevity Health Metrics SDK

**One plain-English question about your own health data → a claim-safe,
uncertainty-carrying personal metric → published to your dashboard.**

```python
import lhm

result = await lhm.ask(
    "How does my magnesium intake relate to my sleep quality?",
    adapter=my_data,            # your data, your process — nothing leaves it
    interpreter=my_llm,         # bring your own LLM (or skip NL entirely)
)
result.suggestions                    # tiered candidates: low / mid / strong
await result.publish(my_dashboard)    # → your syntropy-journal dashboard
```

That's the whole loop: **language → analysis → metric suggestions →
publication**. Your inputs (supplements, food, regimen starts) and outcomes
(journal series, wearables, check-ins) are mapped onto standard FHIR
resources, analyzed as a single-subject observational series with named
statistical corrections, tiered, gated for claim-safety, and pushed to a
dashboard you own.

> **The honesty layer is structural, not prose.** Every metric is a personal
> correlation/trend — never a causal, treatment, or clinical claim. The
> claim-scope guard (`lhm.metrics.claims.assert_claim_safe`) gates every
> persist, publish, and render: banned clinical-efficacy vocabulary fails
> loudly, and a metric cannot exist without its uncertainty caveats.

## Install

```bash
pip install lhm                # core: clinical mapping + pipeline + contracts
pip install "lhm[units]"       # + UCUM quantity validation (pint/ucumvert)
pip install "lhm[stats]"       # + numerical estimators (v2 — see roadmap)
pip install "lhm[publish]"     # + the HTTP publisher (httpx transport)
```

Core stays lean by design: `fhir.resources` (typed FHIR R4B, pydantic v2) and
nothing else — no web framework, no ORM, no LLM, no network I/O.

## How it works

```
"How does my magnesium relate to my sleep?"
      │
      ▼  Interpreter (BYO-LLM — optional; every step below is typed & callable directly)
MetricIntent(input="magnesium", outcome="sleep quality", window=90d)
      │
      ▼  fetch(adapter, user)         # your data → FHIR-mapped, time-aligned series
AlignedData(regimen start marked, rx-tier flagged)
      │
      ▼  analyze(aligned)             # six named n-of-1 corrections, tier surface
CandidateSignal(tier=mid, interval, corrections, caveats)
      │
      ▼  manage("add", candidate)     # the R10 claim gate — banned lexicon fails here
MetricSpec + VizPayload               # the persistence + render contracts
      │
      ▼  publish(target)              # self-hosted journals, or cloud w/ API key
your dashboard
```

**The NL layer is sugar, never load-bearing.** Every stage consumes and
returns typed models you can construct by hand — `lhm.ask()` is a
convenience composition, not a magic box. The LLM only narrows language to a
`MetricIntent`; the SDK binds it to your actual data deterministically, so a
hallucinated column can't silently become a metric.

## Bring your own data (the only required integration)

Implement three async readers — that's the whole adapter contract:

```python
class MyAdapter:
    async def read_orders(self, user_scope): ...          # → [OrderRecord]
    async def read_regimen_starts(self, user_scope): ...  # → [RegimenStartRecord]
    async def read_outcome_series(self, user_scope): ...  # → [SeriesRecord]
```

CSV, SQLite, a wearable export, a full app backend — anything that can fill
those records. `user_scope` is an opaque token you choose; LHM never sees a
name, email, or free-text note (check-in notes reach it only as an
in-boundary sentiment float you derive).

## Publish: self-hosted first, cloud by API key

The publish contract (`lhm-publish/v1`) is ONE versioned envelope —
`MetricSpec` + `VizPayload` + an idempotency key — POSTed to a
syntropy-journal backend:

```python
from lhm import SyntropyTarget

# Self-hosted: your own syntropy-journal instance
target = SyntropyTarget(base_url="https://journal.my-lab.dev")

# Cloud (commercial): the SAME contract — an API key is the only delta
target = SyntropyTarget(
    base_url="https://app.syntropyhealth.bio",
    api_key=os.environ["SYNTROPY_API_KEY"],     # never persisted by the SDK
)

receipt = await result.publish(target)
receipt.dashboard_url                            # deep link to the rendered card
```

Publishing is idempotent (at-least-once safe), claim-gated *before*
transport, and Rx-tier metrics structurally refuse public surfaces. The SDK
never stores your key.

## Status & roadmap

| Layer | What | Status |
|---|---|---|
| **L0** — contracts | `MetricSpec`, `lhm-viz/v1` schema, claim-scope guard | ✅ shipped |
| **L1** — clinical mapping | orders/regimens/series → FHIR R4B; synthetic fixtures; PHI/secret gates | ✅ shipped |
| **L2** — pipeline v0.1 | `fetch → analyze → manage`; six correction hooks (contract-tested stubs); tier surface | ✅ shipped |
| **L3** — journals adoption | reference adapters, dashboard binding, the `/api/lhm/v1/metrics` route | ✅ shipped |
| **L4** — NL + publish | `lhm.nl` (`MetricIntent`, `Interpreter`, `resolve_intent`), `lhm.publish` (envelope, targets, publishers), `lhm.ask` | ✅ shipped — [design 0001](docs/design/0001-nl-to-publish-pipeline.md) |
| **L4** — docs pillar | MkDocs site, quickstart built on `lhm.ask`, repo split + PyPI publish | 🔜 next |
| **v2** — real estimators | `lhm[stats]`: AR(1)/detrend/ITS/posterior + FDR in the six hooks; ratified tier cutoffs | 📐 hooks in place |
| **cloud** — hosted dashboard | API-key issuance, workspaces, the commercial surface | 🗺 vision |

**Packaging:** LHM lives in the syntropy-journal repo through L3 (maximum-
coupling build phase), then splits to its own repo + PyPI at L4 so
`pip install lhm` is real and the app consumes it like any dependency —
decision record: [design 0002](docs/design/0002-packaging-strategy.md).

v0.1 proves shapes and plumbing; the deterministic stubs make every contract
testable today. The full design for the NL→publish pipeline — use cases,
dependency tiers (numerical / clinical / models), the publish data models,
and the API-key semantics — lives in
[`docs/design/0001-nl-to-publish-pipeline.md`](docs/design/0001-nl-to-publish-pipeline.md).

## Design principles

- **App-independent**: bring your own adapter, LLM, and render surface.
  (Syntropy's journal app is the first consumer, not a dependency.)
- **Clinical models, not bespoke schemas**: FHIR R4B via
  [`fhir.resources`](https://pypi.org/project/fhir.resources/).
- **Versioned contracts**: `lhm-viz/v1` (render) and `lhm-publish/v1`
  (transport) — additive-only within a version; JSON schemas ship in the
  package.
- **Privacy-blind by construction**: in-process data only; no payload
  logging; opaque subject tokens; all bundled fixtures synthetic
  (scan-gated in CI).
- **Honest by construction**: correlation language, uncertainty intervals,
  verbatim caveats, and a lexicon gate that fails the build — not a
  disclaimer paragraph.

## Quickstart (v0.1 — the shapes that already run)

```python
from lhm import InputRef, MetricSpec, OutcomeRef, to_viz_payload, VizPoint

spec = MetricSpec(
    metric_key="mag_sleep",
    label="Magnesium intake and sleep quality — personal trend",
    inputs=(InputRef(resource="NutritionOrder", code="supplement-order",
                     display="Magnesium glycinate"),),
    outcome=OutcomeRef(kind="journal_series", series_key="sleep_quality"),
    confidence="mid",
    caveats=(
        "Personal n-of-1 association from your own records — observational, not causal.",
        "Estimates carry an uncertainty interval and may not generalize.",
    ),
    rx_input=False,
)
payload = to_viz_payload(spec, series=[VizPoint(ts=..., value=...)])  # render-gated
```

And the L2 pipeline end-to-end against synthetic data:

```python
from lhm.fixtures import synthetic_order, synthetic_regimen_start, synthetic_series
from lhm.tools import analyze, fetch, manage

aligned = await fetch(my_adapter, "subject-001")
signals = await analyze(aligned)                  # tiered, corrections-threaded
outcome = await manage("add", signals[0])         # claim-gated, persistence-ready
outcome.parameterization                          # the C4 JSONB your store persists
```

## License

Apache-2.0. Runtime dependencies are BSD/MIT-family only (attributions in
[`NOTICE`](NOTICE)); no copyleft in the graph, re-verified at every release.
All example data is synthetic.
