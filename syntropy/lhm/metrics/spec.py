"""MetricSpec — the single committed metric shape (A&D D4, ADR-0002).

One frozen pydantic model describes a synthesized n-of-1 metric end to end:
what it reads (``inputs``), what it explains (``outcome``), how it is
parameterized (``form`` — the C4 zero-migration discriminator), what the fit
produced (``FitBlock``), and the honesty layer (``confidence`` tier +
``caveats`` + the ``rx_input`` C1b bit).

ADR-0002: this shape ABSORBS PRD P3's committed
``MyMetric(input_sources, fitting_target).optimize(method=…)`` API —
``inputs`` ≙ input_sources, ``outcome`` ≙ fitting_target, and the optimizer
enters (v2) through ``FitBlock.method``, never as a user-facing call.

App-independence (A&D D1): NO Reflex, NO SQLModel, NO app imports — the
consuming app persists specs (its ``CustomMetric`` store) and renders
payloads (``lhm.contracts.viz``); this module only defines shapes.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: FHIR resource kinds an input reference may point at (A&D D2 — the lean
#: four-resource mapping; ``Observation`` is outcome-side only).
InputResource = Literal["NutritionOrder", "MedicationRequest", "MedicationStatement"]

#: Outcome series sources (C3): journal series, check-in-derived sentiment,
#: wearable series — all normalized app-side into aligned ``Observation``s.
OutcomeKind = Literal["journal_series", "checkin_sentiment", "wearable_series"]

#: The ADR-0002 estimator slot. ``stub`` = the deterministic v0.1 fallback
#: (also the recorded degradation when the ``[lhm-scoring]`` extra is
#: absent — A&D D-D); ``ols_standardized`` = the real association estimator
#: the analyze seam runs when the extra imports cleanly (HD-4);
#: ``adam`` / ``grid`` re-enter through the correction/fit layer in v2.
FitMethod = Literal["stub", "ols_standardized", "adam", "grid"]

Confidence = Literal["low", "mid", "strong"]

#: The ONE source of truth for the metric-key shape — URL-path-safe by
#: construction (no ``/``, ``?``, ``#``, ``.`` …). Reused wherever a
#: metric_key is interpolated into a URL (e.g. the publisher's unpublish).
METRIC_KEY_PATTERN = r"^[a-z][a-z0-9_]*$"
METRIC_KEY_MAX_LENGTH = 64


class InputRef(BaseModel):
    """One input ("order"/regimen) the metric reads, FHIR-typed (A&D D2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    resource: InputResource
    #: The clinical code carried as a plain FHIR ``Coding`` code value —
    #: no terminology-server dep (A&D D2); display is human-facing.
    code: str
    display: str = ""


class OutcomeRef(BaseModel):
    """The outcome series the metric explains (the P3 ``fitting_target``)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: OutcomeKind
    series_key: str


class CorrectionResult(BaseModel):
    """One named C2 correction hook's contribution to a fit (R4 contract).

    v0.1 carries name + applied + note only — the typed numeric payload
    arrives with the real estimators (L2/v2). Defined NOW so the FitBlock
    field type is stable across phases.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    applied: bool
    note: str = ""


class FitBlock(BaseModel):
    """The fit result (C4): posterior + effective sample + score + method.

    ``method`` is the ADR-0002 reserved estimator slot — always ``"stub"``
    in v0.1. ``corrections`` threads the R4 hook results (contract-shaped;
    numerics deferred).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    posterior_mean: float
    posterior_ci: tuple[float, float]
    n_effective: int = Field(ge=0)
    fit_score: float
    method: FitMethod = "stub"
    corrections: tuple[CorrectionResult, ...] = ()


class MetricSpec(BaseModel):
    """A synthesized, pinnable n-of-1 metric (the ONE committed shape).

    Persistence contract (C4, app-side): the consuming app stores this as
    the JSONB ``parameterization`` of its ``CustomMetric`` row; the ``form``
    discriminator lets future shapes store+read without migration. Only
    ``mid``/``strong`` confidence specs persist; candidates stay ephemeral.

    The honesty layer is STRUCTURAL: ``caveats`` render verbatim (R6) and
    ``lhm.metrics.claims.assert_claim_safe`` gates every persist AND render
    (R10). ``rx_input`` is the C1b tier bit — set by the app's ADAPTER from
    the source record type, never by an LLM (A&D D9; unknown sources fail
    CLOSED to ``True``).

    Size caps (additive tightening of ``lhm-publish/v1``): the
    persist→render path is bounded at the shape — ``metric_key`` ≤ 64,
    ``label`` ≤ 200, ``caveats`` ≤ 8 items of ≤ 500 chars each. Every
    previously-valid spec of sane size still validates; only oversized
    payloads are newly refused.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_key: str = Field(
        min_length=1, max_length=METRIC_KEY_MAX_LENGTH, pattern=METRIC_KEY_PATTERN
    )
    label: str = Field(min_length=1, max_length=200)
    inputs: tuple[InputRef, ...] = Field(min_length=1)
    outcome: OutcomeRef
    form: Literal["linear_standardized"] = "linear_standardized"
    fit: FitBlock | None = None
    confidence: Confidence
    caveats: tuple[Annotated[str, StringConstraints(max_length=500)], ...] = Field(
        min_length=1, max_length=8
    )
    rx_input: bool


__all__ = [
    "METRIC_KEY_MAX_LENGTH",
    "METRIC_KEY_PATTERN",
    "Confidence",
    "CorrectionResult",
    "FitBlock",
    "FitMethod",
    "InputRef",
    "InputResource",
    "MetricSpec",
    "OutcomeKind",
    "OutcomeRef",
]
