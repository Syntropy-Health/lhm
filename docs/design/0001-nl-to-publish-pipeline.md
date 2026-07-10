# LHM Design 0001 — The NL-to-Publish Pipeline

**Status:** IMPLEMENTED (L4 slice 1, 2026-07-10, SDK v0.1.0). Contracts and
data models designed here; `lhm.nl` (`MetricIntent`, `Interpreter`,
`resolve_intent`), `lhm.publish` (`PublishEnvelope`, `SyntropyTarget`,
`HttpPublisher`, `InProcessPublisher`), and `lhm.ask` now ship in the SDK,
contract-tested against the app's `/api/lhm/v1/metrics` route. This document
remains the commitment surface the README and the guide tree wire against.

---

## 1. The one-liner

```python
import lhm

result = await lhm.ask(
    "How does my magnesium intake relate to my sleep quality?",
    adapter=my_adapter,          # your data (D7-style adapter — see §4)
    interpreter=my_interpreter,  # your LLM (BYO — see §3)
)
result.suggestions          # tiered candidate metrics (low / mid / strong)
await result.publish(target)  # → your syntropy-journal dashboard (see §5)
```

One natural-language sentence in; a claim-safe, tiered, uncertainty-carrying
personal metric out; one call to put it on a dashboard. Everything between is
the pipeline the SDK already ships (L0–L2) plus two new seams designed here:
the **Interpreter** (language → metric intent) and the **Publisher**
(metric → dashboard surface).

```
  "How does my magnesium relate to my sleep?"
        │
        ▼
  ┌────────────┐   MetricIntent    ┌──────────────────────────────┐
  │ Interpreter │ ────────────────▶ │ fetch → align → analyze →    │
  │  (BYO-LLM)  │                   │ tier  (L1/L2, SHIPPED)       │
  └────────────┘                   └──────────────┬───────────────┘
                                                  │ CandidateSignal(s)
                                                  ▼
                                   ┌──────────────────────────────┐
                                   │ manage — R10 claim gate       │
                                   │ (SHIPPED)                     │
                                   └──────────────┬───────────────┘
                                                  │ MetricSpec + VizPayload
                                                  ▼
                                   ┌──────────────────────────────┐
                                   │ Publisher → syntropy-journal  │
                                   │ self-hosted │ cloud (API key) │
                                   └──────────────────────────────┘
```

## 2. Use cases (the three personas)

| # | Persona | Entry | Data | Render surface |
|---|---|---|---|---|
| U1 | **Self-hoster / developer** | `pip install lhm`, `lhm.ask(...)` in a script or notebook | Their own adapter (CSV, SQLite, wearable export) | Their self-hosted syntropy-journal dashboard (or raw `VizPayload` into anything) |
| U2 | **Shrine app user** | Chat ("track this for me") — the app's agent IS the interpreter | The journals adapters (orders, journal series, check-ins) | The shrine health-metrics tabs (L3 binding) |
| U3 | **Cloud customer (commercial)** | Same SDK; `SyntropyTarget(api_key=...)` | Their adapter or Syntropy-ingested data | The **cloud-hosted** Syntropy dashboard, authenticated by API key |

U2 is the reference implementation of the whole loop; U1 is the OSS promise;
U3 is the commercial path — same contract, one added credential.

## 3. The Interpreter seam (language → `MetricIntent`)

**App-independence holds: the SDK ships NO LLM dependency.** Interpretation
is a Protocol the consumer implements (the Shrine agent implements it with
its own provider chain; a self-hoster can back it with any LLM, or skip NL
entirely and construct `MetricIntent` by hand — the NL layer is sugar, never
load-bearing).

```python
class MetricIntent(BaseModel):            # lhm.nl (SHIPPED, L4)
    """The structured reading of the user's question — LLM-OUTPUT-SHAPED,
    but constructible by hand (the NL layer is optional sugar)."""
    input_query:   str          # what the user consumes ("magnesium")
    outcome_query: str          # what they observe ("sleep quality")
    window_days:   int = 90     # analysis window
    raw_prompt:    str = ""     # provenance (never leaves the process — C9)

class Interpreter(Protocol):              # lhm.nl (SHIPPED, L4)
    async def interpret(self, prompt: str) -> MetricIntent: ...
```

Resolution from `MetricIntent` to concrete adapter series (fuzzy-matching
"magnesium" onto the user's actual order records) is `resolve_intent(intent,
adapter) -> ResolvedIntent` — deterministic string/code matching inside the
SDK, NOT the LLM's job (the LLM narrows language; the SDK binds data — keeps
hallucinated bindings structurally impossible).

## 4. The data seam (already shipped — restated as the contract)

`LhmAdapter` (L2, SHIPPED) is the only thing a consumer must implement:
three async readers returning the D2 record shapes (`OrderRecord`,
`RegimenStartRecord`, `SeriesRecord`). Everything downstream — FHIR mapping,
alignment, corrections, tiering, the claim gate — is SDK-owned.

## 5. The Publisher seam (metric → dashboard)

### 5.1 The envelope (the data model that crosses the wire)

```python
class PublishEnvelope(BaseModel):         # lhm.publish (SHIPPED, L4)
    """Everything a syntropy-journal backend needs to surface one metric.

    Versioned like the viz contract: additive-only within v1."""
    envelope_version: Literal["lhm-publish/v1"]
    spec:             MetricSpec        # the C4 parameterization source
    payload:          VizPayload        # the lhm-viz/v1 render contract
    user_scope:       str               # OPAQUE subject token (C9 — never PII)
    idempotency_key:  str               # sha256(user_scope + metric_key + spec-hash)
    published_at:     datetime          # tz-aware, client-stamped
```

The backend persists `spec` as its `CustomMetric` JSONB parameterization
(C4), renders from `payload`, and dedupes on `idempotency_key` — publishing
is at-least-once safe by construction.

### 5.2 The target + transport

```python
class SyntropyTarget(BaseModel):          # lhm.publish (SHIPPED)
    base_url: str                         # https required; plain http only for
                                          # loopback OR explicit allow_insecure=True
    api_key:  SecretStr | None = None     # cloud credential — masked in repr/dumps
    timeout_s: float = 10.0
    public_surface: bool = False          # True ⇒ Rx metrics refuse structurally
    allow_insecure: bool = False          # explicit opt-in: cleartext key on http

class Publisher(Protocol):
    async def publish(self, envelope: PublishEnvelope) -> PublishReceipt: ...
    async def unpublish(self, user_scope: str, metric_key: str) -> None: ...

class PublishReceipt(BaseModel):
    accepted:    bool
    metric_key:  str
    dashboard_url: str | None = None      # deep link to the rendered card
    reason:      str = ""
```

Two shipped-with-SDK implementations (SHIPPED, L4):

- **`HttpPublisher`** — POSTs the envelope to
  `{base_url}/api/lhm/v1/metrics` (self-hosted journals; the route landed
  app-side in L3). Auth (shipped surface): the `X-Syntropy-Key` API-key
  header when `api_key` is set — the ENTIRE difference between self-hosted
  and cloud (U3: same envelope, same route shape, hosted base_url); a
  session/service-token path is a POSSIBLE post-L4/U3 addition, not built.
- **`InProcessPublisher`** — the U2 path: no HTTP; hands the envelope to an
  injected app callback (the journals MCP `manage_metrics` wrapper). This is
  how the Shrine chat publishes today's L2 `ManageOutcome` — the envelope
  formalizes what crosses that boundary. `public_surface` is a **required
  keyword argument** (no default): the Rx/public gate must never default
  open, so the caller states the surface explicitly —
  `InProcessPublisher(callback, public_surface=False)`.

### 5.3 The gate stays in front of the wire

`publish()` re-runs `assert_claim_safe` on the envelope's label + caveats
before ANY transport (R10 gates persist AND render — publishing is both).
An Rx-input metric refuses `public_surface=True` targets structurally.

## 6. Dependency plan (planned tiers — nothing new lands until its phase)

| Tier | Packages | License | Phase | Why |
|---|---|---|---|---|
| **Clinical (core)** | `fhir.resources==8.3.x` (+`fhir-core`, pydantic) | BSD/MIT | SHIPPED (L0) | typed FHIR R4B mapping |
| **Units (extra `lhm[units]`)** | `pint`, `ucumvert` | BSD/MIT | SHIPPED (L0) | UCUM quantity validation |
| **Numerical (extra `lhm[stats]`)** | `numpy`, `scipy`, `statsmodels` | BSD | v2 (estimators) | AR(1)/detrend/ITS/posterior — the six C2 hook bodies |
| **Honest inference** | (within `statsmodels`; evaluate `arviz` only if a full Bayesian posterior is ratified) | BSD/Apache | v2 | interval + FDR across the metric family |
| **Models / NL** | **NONE in the SDK.** `Interpreter` is BYO-LLM; an optional `lhm[nl-openai]` convenience shim is a POST-v1 decision, never core | — | L4 design gate | app-independence (D1) + no key handling in the OSS package |
| **Transport** | `httpx` (already transitively present app-side; SDK gets it via extra `lhm[publish]`) | BSD | L3/L4 | `HttpPublisher` |

Rule carried from D2: BSD/MIT-family only, exact-pin the single-maintainer
clinical deps, no copyleft — re-verified by the R7 NOTICE gate each release.

## 7. Commercial path (U3) — the API-key contract

- The **same** `PublishEnvelope`/route as self-hosted: the product IS the
  hosted dashboard + key issuance, not a different protocol.
- Key semantics (designed now, built with the cloud offering): scoped to a
  workspace; `user_scope` values are namespaced under the key server-side
  (a key can never publish into another workspace's subjects); rate limits
  + key rotation are dashboard-side concerns, invisible to the SDK.
- The SDK never stores keys — `SyntropyTarget.api_key` is caller-supplied
  per call/session (12-factor: environment or secret manager on the
  consumer's side).

## 8. Phasing (extends the A&D L-ladder)

| Phase | Ships |
|---|---|
| L3 (SHIPPED) | journals adapters + `CustomMetric` store + MCP wrappers + dashboard binding + the app-side `/api/lhm/v1/metrics` route |
| L4 (SHIPPED) | `lhm.nl` with the WORKING deterministic binder (`MetricIntent`, `Interpreter`, `resolve_intent`) + `lhm.publish` with the real httpx `HttpPublisher` (retry/backoff, size-capped reads) and `InProcessPublisher`; `lhm.ask`; the plain-Markdown guide tree under `docs/` (no MkDocs site, no stub transports) |
| v2 | real estimators (`lhm[stats]`), tier cutoffs ratified, `lhm.ask` end-to-end against a live journals backend |
| commercial | hosted dashboard + API-key issuance (same contract; ops/product scope) |

## 9. Decisions of record

- **D10 — NL is sugar, never load-bearing:** every NL capability has a
  hand-constructible typed equivalent; the LLM narrows language, the SDK
  binds data. No LLM dependency in core, ever.
- **D11 — one publish contract for self-hosted and cloud:** the API key is
  the only delta. Versioned envelope (`lhm-publish/v1`), additive-only,
  idempotency-keyed, claim-gated before transport.
- **D12 — dependency tiers as extras:** core stays lean (clinical only);
  numerical/transport/NL arrive as opt-in extras aligned to phases.
