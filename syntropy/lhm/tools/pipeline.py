"""Pipeline v0.1 (L2, A&D D4/D6): ``fetch → align → analyze → tier → manage``.

Persist stays APP-side (D1): :func:`manage` runs the R10 claim gate and
returns a validated, persistence-ready outcome; the consuming app performs
the actual ``CustomMetric`` write (D7/L3).

v0.1 statistics are DETERMINISTIC STUBS (PVR YAGNI): ``analyze`` computes a
self-normalized strength from the aligned pairs deterministically and maps it
to a tier; the six C2 corrections run as recorded stubs (R4 contract). Real
estimators are v2 (ADR-0002 ``FitBlock.method`` slot).
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

from syntropy.lhm.metrics.claims import assert_claim_safe
from syntropy.lhm.metrics.corrections import run_corrections
from syntropy.lhm.metrics.spec import FitBlock, InputRef, MetricSpec, OutcomeRef
from syntropy.lhm.models.mapping import (
    OrderRecord,
    RegimenStartRecord,
    SeriesRecord,
    _require_aware,
)


class LhmAdapter(Protocol):
    """What the app-side adapter (D7) must supply per user scope.

    ``user_scope`` is the OPAQUE app-scoped subject token (C9 — never PHI).
    The adapter owns all DB/API reads; the SDK never sees the app's models.
    """

    async def read_orders(self, user_scope: str) -> Sequence[OrderRecord]: ...

    async def read_regimen_starts(self, user_scope: str) -> Sequence[RegimenStartRecord]: ...

    async def read_outcome_series(self, user_scope: str) -> Sequence[SeriesRecord]: ...


def outcome_kind_for_code(
    code: str,
) -> Literal["wearable_series", "checkin_sentiment", "journal_series"]:
    """The ``OutcomeRef.kind`` a series record's curated code derives to.

    SINGLE SOURCE OF TRUTH for the code→kind mapping: :func:`fetch` builds
    every downstream ``OutcomeRef(kind=..., series_key=rec.metric_display)``
    through it, and the NL binder keys outcome DISTINCTNESS on the same
    identity — duplicating the mapping would let the two drift apart.
    """
    if code == "wearable-metric":
        return "wearable_series"
    if code == "checkin-sentiment":
        return "checkin_sentiment"
    return "journal_series"


class AlignedPair(BaseModel):
    """One (input-presence, outcome-value) sample at a shared date."""

    model_config = ConfigDict(frozen=True)

    at: datetime
    input_active: float  #: 1.0 when the regimen/input was active at ``at``
    outcome_value: float


class AlignedData(BaseModel):
    """R1's product: time-aligned inputs/outcomes + the start-date marker."""

    model_config = ConfigDict(frozen=True)

    user_scope: str
    inputs: tuple[InputRef, ...]
    outcome: OutcomeRef
    #: The regimen start (R1: "with the regimen start-date marked") — None
    #: when no regimen-start record exists for the input set.
    regimen_start: datetime | None
    pairs: tuple[AlignedPair, ...]
    rx_input: bool


class CandidateSignal(BaseModel):
    """R2's product: one tiered (input → outcome) candidate."""

    model_config = ConfigDict(frozen=True)

    metric_key: str
    label: str
    inputs: tuple[InputRef, ...]
    outcome: OutcomeRef
    fit: FitBlock
    confidence: Literal["low", "mid", "strong"]
    caveats: tuple[str, ...] = Field(min_length=1)
    rx_input: bool


class ManageOutcome(BaseModel):
    """R3's product: the SDK's emit-for-persist / removal decision."""

    model_config = ConfigDict(frozen=True)

    action: Literal["add", "subtract"]
    accepted: bool
    reason: str
    #: Present iff ``accepted`` and ``action == "add"`` — the validated spec
    #: the app persists as the ``CustomMetric`` JSONB parameterization (C4).
    spec: MetricSpec | None = None
    #: The C4 JSONB payload (``spec`` serialized) — persistence-ready.
    parameterization: dict | None = None


# --------------------------------------------------------------------------- #
# fetch — R1
# --------------------------------------------------------------------------- #


async def fetch(adapter: LhmAdapter, user_scope: str) -> tuple[AlignedData, ...]:
    """Unify the adapter's inputs + outcomes into time-aligned series (R1).

    One :class:`AlignedData` per outcome series. Alignment v0.1: daily grain —
    an outcome sample pairs with ``input_active=1.0`` when it falls ON/AFTER
    the earliest matching regimen start (or the earliest order when no
    regimen-start record exists), else ``0.0``. The regimen start-date rides
    the result (the R1 marker; the v2 ITS correction anchors on it).
    """
    orders = tuple(await adapter.read_orders(user_scope))
    starts = tuple(await adapter.read_regimen_starts(user_scope))
    series = tuple(await adapter.read_outcome_series(user_scope))

    # QG HIGH fix: no orders → no inputs → nothing to correlate. Return
    # empty rather than emitting AlignedData that can't form a MetricSpec
    # (inputs has min_length=1) — a normal early-adoption state.
    if not orders:
        return ()

    # QG fix: enforce tz-awareness HERE (fetch bypasses the map_* fns that
    # normally guard it) — naive/aware mixing across adapter sources would
    # otherwise TypeError deep in comparisons. Fail loud at the boundary.
    for o in orders:
        _require_aware(o.occurred_at, "OrderRecord.occurred_at")
    for st in starts:
        _require_aware(st.started_at, "RegimenStartRecord.started_at")
    for rec in series:
        for i, pt in enumerate(rec.points):
            _require_aware(pt.observed_at, f"{rec.metric_display}.points[{i}].observed_at")

    rx_input = any(o.is_rx for o in orders)
    input_refs = tuple(
        InputRef(
            resource="MedicationRequest" if o.is_rx else "NutritionOrder",
            code=o.code or ("rx-order" if o.is_rx else "supplement-order"),
            display=o.item_display,
        )
        for o in orders
    )
    regimen_start: datetime | None = None
    candidates = [s.started_at for s in starts] or [o.occurred_at for o in orders]
    if candidates:
        regimen_start = min(candidates)

    out: list[AlignedData] = []
    for rec in series:
        pairs = tuple(
            AlignedPair(
                at=p.observed_at,
                input_active=(
                    1.0 if (regimen_start is not None and p.observed_at >= regimen_start) else 0.0
                ),
                outcome_value=p.value,
            )
            for p in rec.points
            if p.value is not None
        )
        out.append(
            AlignedData(
                user_scope=user_scope,
                inputs=input_refs,
                outcome=OutcomeRef(
                    kind=outcome_kind_for_code(rec.code), series_key=rec.metric_display
                ),
                regimen_start=regimen_start,
                pairs=pairs,
                rx_input=rx_input,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# analyze — R2 + R4
# --------------------------------------------------------------------------- #

#: v0.1 tier thresholds on the DETERMINISTIC stub strength — the tier
#: SURFACE exists and is plumbed (G1.5); real discrimination is v2.
_TIER_MID = 0.25
_TIER_STRONG = 0.5


def _tier_for(magnitude: float, rx_input: bool) -> Literal["low", "mid", "strong"]:
    """Map a stub strength magnitude to its tier, with the C1b demotion."""
    magnitude = abs(magnitude)
    if magnitude >= _TIER_STRONG:
        tier: Literal["low", "mid", "strong"] = "strong"
    elif magnitude >= _TIER_MID:
        tier = "mid"
    else:
        tier = "low"
    if rx_input and tier == "strong":
        tier = "mid"  # C1b: Rx never surfaces `strong` in v0.1
    return tier


def _stub_strength(pairs: Sequence[AlignedPair]) -> tuple[float, int]:
    """Deterministic self-normalized strength: mean(outcome|active) minus
    mean(outcome|inactive), scaled into [-1, 1] by the outcome range.
    Pure arithmetic — stable across runs (the R2 plumbing stub)."""
    active = [p.outcome_value for p in pairs if p.input_active >= 0.5]
    inactive = [p.outcome_value for p in pairs if p.input_active < 0.5]
    if not active or not inactive:
        # One empty group (regimen predates or postdates EVERY sample) →
        # no contrast exists for the difference-of-means stub: strength 0,
        # tier low, never persistable. Inherent to the v0.1 stub — the v2
        # ITS correction (causal_direction hook) is where this improves.
        return 0.0, len(active)
    values = [p.outcome_value for p in pairs]
    spread = max(values) - min(values)
    if spread == 0:
        return 0.0, len(active)
    delta = (sum(active) / len(active)) - (sum(inactive) / len(inactive))
    return max(-1.0, min(1.0, delta / spread)), len(active)


_BASE_CAVEATS = (
    "Personal n-of-1 observation — a correlation in your own data, not a causal claim.",
    "v0.1 stub estimate: corrections are recorded but not yet applied; interval is provisional.",
)


async def analyze(aligned: Sequence[AlignedData]) -> tuple[CandidateSignal, ...]:
    """Per outcome: a tier-bearing, corrections-bearing, interval-bearing
    candidate (R2's v0.1 shape contract). The six C2 hooks run exactly once
    per call per outcome (R4). NO ``strong`` is ever emitted for an Rx input
    (C1b/G1.5 hard rule)."""
    out: list[CandidateSignal] = []
    for data in aligned:
        raw_pairs = tuple((p.input_active, p.outcome_value) for p in data.pairs)
        corrections = run_corrections(raw_pairs)  # R4: once per analyze, all six
        strength, n_active = _stub_strength(data.pairs)
        magnitude = abs(strength)
        tier = _tier_for(strength, data.rx_input)
        half_width = max(0.1, (1.0 - magnitude) / 2)  # provisional interval (stub)
        fit = FitBlock(
            posterior_mean=strength,
            posterior_ci=(strength - half_width, strength + half_width),
            n_effective=n_active,
            fit_score=magnitude,
            method="stub",
            corrections=corrections,
        )
        # ASCII-only slug (QG fix: MetricSpec.metric_key pins ^[a-z][a-z0-9_]*$
        # — unicode letters pass isalnum() but fail the pattern at manage) +
        # a stable short hash of (kind, series_key) so slug-equal outcome
        # names ("Sleep quality" / "Sleep-quality") can't collide on persist.
        raw_key = data.outcome.series_key.lower()
        series_slug = "".join(
            ch if ("a" <= ch <= "z" or "0" <= ch <= "9") else "_" for ch in raw_key
        ).strip("_")
        digest = hashlib.sha256(
            f"{data.outcome.kind}:{data.outcome.series_key}".encode()
        ).hexdigest()[:6]
        out.append(
            CandidateSignal(
                metric_key=f"sig_{series_slug or 'outcome'}_{digest}",
                label=f"{data.inputs[0].display} ↔ {data.outcome.series_key} (association)",
                inputs=data.inputs,
                outcome=data.outcome,
                fit=fit,
                confidence=tier,
                caveats=_BASE_CAVEATS,
                rx_input=data.rx_input,
            )
        )
    return tuple(out)


# --------------------------------------------------------------------------- #
# manage — R3 (SDK half) + R10 gate + the C4 parameterization (R5's SDK side)
# --------------------------------------------------------------------------- #


async def manage(
    action: Literal["add", "subtract"],
    candidate: CandidateSignal,
) -> ManageOutcome:
    """R10-gate + emit-for-persist (the SDK half of R3; the app writes).

    - ``add``: only ``mid``/``strong`` candidates are persistable (C4 —
      candidates stay ephemeral); the label + caveats MUST pass
      :func:`assert_claim_safe` (R10 gates persist). The outcome carries the
      validated :class:`MetricSpec` AND its C4 JSONB ``parameterization``
      (``form``-discriminated → zero-migration evolution, R5).
    - ``subtract``: always accepted — removal needs no gate; the app deletes
      its ``CustomMetric`` row.
    """
    if action == "subtract":
        return ManageOutcome(action=action, accepted=True, reason="removal — no gate required")

    if candidate.confidence == "low":
        return ManageOutcome(
            action=action,
            accepted=False,
            reason="low-confidence candidates are ephemeral (C4 — only mid/strong persist)",
        )

    # QG fix: don't trust the caller-supplied tier verbatim — re-derive from
    # the fit so a hand-built candidate can't forge a persistable tier onto
    # a sub-threshold fit_score (the tier IS the honesty surface).
    derived = _tier_for(candidate.fit.fit_score, candidate.rx_input)
    if derived == "low":
        return ManageOutcome(
            action=action,
            accepted=False,
            reason=(
                "tier/fit mismatch — the fit_score re-derives to 'low' "
                "(candidates persist only on their own evidence)"
            ),
        )

    # R10 — the persist-side claim gate (the L0-deferred source-pin lands here).
    assert_claim_safe(
        candidate.label,
        candidate.caveats,
        candidate.confidence,
        candidate.rx_input,
        public_surface=False,
    )

    spec = MetricSpec(
        metric_key=candidate.metric_key,
        label=candidate.label,
        inputs=candidate.inputs,
        outcome=candidate.outcome,
        form="linear_standardized",
        fit=candidate.fit,
        confidence=candidate.confidence,
        caveats=candidate.caveats,
        rx_input=candidate.rx_input,
    )
    return ManageOutcome(
        action=action,
        accepted=True,
        reason="claim-gate passed — persistence-ready",
        spec=spec,
        parameterization=spec.model_dump(mode="json"),
    )


__all__ = [
    "AlignedData",
    "AlignedPair",
    "CandidateSignal",
    "LhmAdapter",
    "ManageOutcome",
    "analyze",
    "fetch",
    "manage",
    "outcome_kind_for_code",
]
