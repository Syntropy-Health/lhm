"""C2 correction-hook registry (L2, A&D D4 / PVR R4) — the rigor layer.

Six NAMED, REGISTERED hooks — one per C2 correction class — invoked exactly
once per ``analyze`` call (R4's spy/contract test). v0.1 bodies are
DETERMINISTIC STUBS: each returns a typed :class:`CorrectionResult` that
records the hook ran and what the v2 estimator will do there. The numerics
are explicitly out of scope (PVR: R4 tests the contract/wiring, not the
statistics); the ADR-0002 ``FitBlock.method`` slot is where the real
estimators enter in v2.

v2 TIER-CUTOFF TABLE (PVR open question 2 — parked here as the v2 target,
per the A&D; values are the DESIGN-time straw table, to be ratified with
the estimator work, NOT read by any v0.1 code):

    tier     | posterior |mean|/sd  | n_effective | fit_score
    ---------|-----------------------|-------------|----------
    strong   | >= 3.0                | >= 30       | >= 0.6
    mid      | >= 1.5                | >= 12       | >= 0.3
    low      | anything below mid    | —           | —

#TODO(v2): real estimator bodies per hook — see each hook's docstring for
its estimator target.
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from lhm.metrics.spec import CorrectionResult

#: A correction hook: consumes the aligned (input, outcome) sample pairs and
#: returns its typed contribution. v0.1 stubs ignore the numerics and return
#: an ``applied=False`` marker with the v2 plan in ``note``.
CorrectionHook = Callable[[Sequence[tuple[float, float]]], CorrectionResult]


def _stub(name: str, v2_note: str) -> CorrectionHook:
    def hook(pairs: Sequence[tuple[float, float]]) -> CorrectionResult:
        return CorrectionResult(
            name=name,
            applied=False,  # v0.1: recorded-not-applied (deterministic stub)
            note=f"stub v0.1 — v2: {v2_note}",
        )

    hook.__name__ = f"correction_{name}"
    return hook


#: The six C2 corrections — names are the R4 contract (spy-tested); order is
#: the invocation order ``analyze`` uses (stable, deterministic).
CORRECTION_HOOKS: Mapping[str, CorrectionHook] = {
    # TODO(v2): AR(1)+lag-aware effective-sample correction.
    "temporal_dependence": _stub(
        "temporal_dependence", "autocorrelation + lag structure → n_effective deflation"
    ),
    # TODO(v2): detrend/difference before fitting.
    "non_stationarity": _stub("non_stationarity", "detrend / difference the series"),
    # TODO(v2): day-of-week/season/illness/concurrent-regimen covariates.
    "confounders": _stub(
        "confounders", "day-of-week, season, illness, concurrent-regimen covariates"
    ),
    # TODO(v2): temporality + interrupted-time-series around the regimen start.
    "causal_direction": _stub(
        "causal_direction", "temporality check + ITS around the regimen start marker"
    ),
    # TODO(v2): Bayesian posterior + FDR across the metric family.
    "honest_inference": _stub(
        "honest_inference", "Bayesian posterior + interval; FDR across many metrics"
    ),
    # TODO(v2): missingness / measurement-error / no-blinding caveat synthesis.
    "data_quality": _stub(
        "data_quality", "missingness, measurement error, no-blinding caveat synthesis"
    ),
}


def run_corrections(
    pairs: Sequence[tuple[float, float]],
) -> tuple[CorrectionResult, ...]:
    """Invoke every registered hook exactly once, in registry order (R4)."""
    return tuple(hook(pairs) for hook in CORRECTION_HOOKS.values())


__all__ = ["CORRECTION_HOOKS", "CorrectionHook", "run_corrections"]
