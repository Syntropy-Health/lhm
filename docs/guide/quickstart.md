# Quickstart — five minutes to a claim-safe personal metric

LHM turns your own health records — orders, regimen starts, journal series —
into tiered, uncertainty-carrying **personal association metrics**, and can
push them to a dashboard. Every metric is a correlation/trend in *your own
data*, never a causal or efficacy claim; the [honesty layer](honesty-layer.md)
enforces that structurally.

This page walks the whole pipeline **by hand** — no LLM, no network — using
the SDK's bundled synthetic fixtures. The NL front door (`lhm.ask`) is
covered in [Natural language & `lhm.ask`](nl-and-ask.md).

## Install

```bash
pip install lhm                # core: clinical mapping + pipeline + contracts
pip install "lhm[units]"       # + UCUM quantity validation (pint/ucumvert)
pip install "lhm[publish]"     # + the HTTP publisher (httpx transport)

# pip install "lhm[stats]"     # v2 — not yet published (the numerical-
#                              # estimator extra arrives with the v2
#                              # estimators; it does not exist today)
```

Core is lean by design: `fhir.resources` (typed FHIR R4B on pydantic v2) and
nothing else — no web framework, no ORM, no LLM, no network I/O. Python
≥ 3.11.

## The 5-minute path

The pipeline is four ordinary async functions, each with typed inputs and
outputs — the NL layer is optional sugar on top of exactly these calls:

```
fetch (align your records)  →  analyze (tier + corrections)  →  manage (claim gate)  →  to_viz_payload (render contract)
```

Run this script as-is — it uses `lhm.fixtures`, the deterministic synthetic
generators (fixed epoch, obviously-fake compound names, opaque subject
tokens — zero personal data by construction):

```python
import asyncio

from lhm import VizPoint, analyze, fetch, manage, to_viz_payload
from lhm.fixtures import synthetic_order, synthetic_regimen_start, synthetic_series

SEED = 11  # deterministic — same seed, same records, same numbers


class FixtureAdapter:
    """The three-reader adapter contract, backed by synthetic fixtures."""

    async def read_orders(self, user_scope):
        return [synthetic_order(SEED)]

    async def read_regimen_starts(self, user_scope):
        return [synthetic_regimen_start(SEED)]

    async def read_outcome_series(self, user_scope):
        return [synthetic_series(SEED, n=28, with_notes=False)]


async def main() -> None:
    # 1. fetch — adapter records in, time-aligned (input, outcome) series out
    aligned = await fetch(FixtureAdapter(), "syn-subject-011")
    data = aligned[0]
    print(f"aligned pairs: {len(data.pairs)}, regimen start: {data.regimen_start.date()}")

    # 2. analyze — one tiered, corrections-threaded candidate per outcome
    candidates = await analyze(aligned)
    best = candidates[0]
    print(f"candidate: {best.metric_key}")
    print(f"tier: {best.confidence}, fit_score: {best.fit.fit_score:.3f}")
    print(f"corrections recorded: {[c.name for c in best.fit.corrections]}")

    # 3. manage — the claim gate; only mid/strong candidates persist
    outcome = await manage("add", best)
    print(f"accepted: {outcome.accepted} — {outcome.reason}")

    # 4. to_viz_payload — the render contract (re-runs the claim gate)
    series = [VizPoint(ts=p.at, value=p.outcome_value) for p in data.pairs]
    payload = to_viz_payload(outcome.spec, series, regimen_start=data.regimen_start)
    print(f"payload: {payload.contract}, {len(payload.series)} points, "
          f"caveats: {len(payload.caveats)}")


asyncio.run(main())
```

Output:

```text
aligned pairs: 28, regimen start: 2026-01-08
candidate: sig_synthetic_energy_rating_a644fd
tier: mid, fit_score: 0.260
corrections recorded: ['temporal_dependence', 'non_stationarity', 'confounders', 'causal_direction', 'honest_inference', 'data_quality']
accepted: True — claim-gate passed — persistence-ready
payload: lhm-viz/v1, 28 points, caveats: 2
```

What each stage handed over:

1. **`fetch(adapter, user_scope)`** read the three record kinds from your
   adapter, enforced tz-awareness at the boundary, and produced one
   `AlignedData` per outcome series: daily-grain `(input_active,
   outcome_value)` pairs plus the regimen start-date marker.
2. **`analyze(aligned)`** produced one `CandidateSignal` per outcome: a
   confidence tier (`low` / `mid` / `strong`), a `FitBlock` with a
   provisional interval, and all six named correction hooks recorded (v0.1
   ships deterministic stubs — the hooks record what the v2 estimators will
   do there; `FitBlock.method == "stub"` says so honestly).
3. **`manage("add", candidate)`** ran the claim-scope gate and answered with
   a `ManageOutcome`: the validated `MetricSpec` plus its JSON
   `parameterization` — the exact payload a consuming store persists. LHM
   never writes anywhere itself; the persist is yours.
4. **`to_viz_payload(spec, series, …)`** built the versioned `lhm-viz/v1`
   render payload, re-running the claim gate so a render surface can never
   be handed claim-violating text.

## When the evidence is weak, the SDK says no

Change `SEED = 11` to `SEED = 1` (a series with no real contrast) and the
same script refuses at stage 3 — low-tier candidates are ephemeral by
contract, never persistable:

```text
tier: low
accepted: False — low-confidence candidates are ephemeral (C4 — only mid/strong persist)
```

This is the SDK's signature move: **a typed, reasoned no** instead of a
shaky metric. You'll meet it again in the NL binder's refusals and the
publisher's gates.

## Where next

- Feed your **own data** in: [Adapters](adapters.md) — three async readers
  are the whole integration.
- Ask in **plain language**: [Natural language & `lhm.ask`](nl-and-ask.md).
- Put a metric on a **dashboard**: [Publishing](publish.md).
- Understand the **guarantees**: [The honesty layer](honesty-layer.md).
