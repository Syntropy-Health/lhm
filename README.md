# LHM — Longevity Health Metrics SDK

**Chat-programmable personal health metrics, modelled on clinical standards, honest by
construction.**

LHM turns a plain-English question about your own data — *"how does my Omega-3 intake relate to my
sleep?"* — into a runnable, pinnable **n-of-1 metric**: your inputs (supplements, food, regimen
starts) and outcomes (journal series, wearables) are mapped onto standard FHIR resources, analyzed
as a single-subject observational series with named statistical corrections, and returned as a
personal **association** with an uncertainty interval and caveats.

> **The honesty layer is structural, not prose.** Every metric is a personal correlation/trend —
> never a causal, treatment, or clinical claim. The claim-scope guard
> (`lhm.metrics.claims.assert_claim_safe`) gates every persist and render: banned clinical-efficacy
> vocabulary fails, and a metric cannot exist without association + uncertainty caveats.

## Status: v0.1 (alpha) — API shape first

v0.1 proves the shapes and the plumbing: typed `MetricSpec`, the `lhm-viz/v1` push contract, the
claim-scope guard, and deterministic stub estimators. The real n-of-1 correction estimators
(temporal dependence, non-stationarity, confounders, honest inference with FDR) are the v2 core —
their hooks and contracts are already in place.

## Design

- **App-independent**: no web framework, no ORM, no network I/O — bring your own data adapter and
  render surface. (Syntropy's journal app is the first consumer.)
- **Clinical models, not bespoke schemas**: inputs/outcomes map onto FHIR R4B resources via
  [`fhir.resources`](https://pypi.org/project/fhir.resources/) (pydantic v2).
- **Versioned push contract**: `lhm-viz/v1` (JSON schema ships in the package) — consumers render;
  LHM owns the payload shape; additive-only within v1.
- **Privacy-blind by construction**: LHM operates on already-fetched, in-process data and never
  logs payload content. All bundled fixtures are synthetic.

## Quickstart (v0.1 shapes)

```python
from lhm import InputRef, MetricSpec, OutcomeRef, to_viz_payload, VizPoint

spec = MetricSpec(
    metric_key="omega3_sleep",
    label="Omega-3 intake and sleep duration — personal trend",
    inputs=(InputRef(resource="NutritionOrder", code="omega-3", display="Omega-3"),),
    outcome=OutcomeRef(kind="journal_series", series_key="sleep_duration"),
    confidence="mid",
    caveats=(
        "Personal n-of-1 association from your own records — observational, not causal.",
        "Estimates carry an uncertainty interval and may not generalize.",
    ),
    rx_input=False,
)
payload = to_viz_payload(spec, series=[VizPoint(ts=..., value=...)])  # render-ready
```

## License

Apache-2.0. All example data is synthetic; see `NOTICE` (ships at first release) for third-party
attributions.
