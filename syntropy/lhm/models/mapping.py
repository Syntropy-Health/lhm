"""D2 mapping layer (L1) — app-domain records → FHIR R4B resources.

Pure functions, fixture-tested; the app-side adapter (A&D D7) feeds them —
the SDK never reads a DB, never sees Reflex/SQLModel (D1). Every emitted
resource uses the **R4B** line exclusively (``lhm.models.FHIR_VERSION``);
mixing emit lines in one payload is a contract error.

The lean four-resource mapping (A&D D2):

- purchases/orders   → :func:`map_order` → ``NutritionOrder`` (supplement/
  food) or ``MedicationRequest`` (Rx — the user-private tier per C1b; the
  consuming app MUST store Rx-derived resources in the user-private store,
  never a shared/analytics one).
- regimen starts     → :func:`map_regimen_start` → ``MedicationStatement``
  (the regimen start-date marker R1 needs).
- journal/wearable/check-in series → :func:`map_series` →
  ``list[Observation]`` with UCUM-coded ``Quantity`` values.

PHI boundary (C9, HARD): ``subject_ref`` is an OPAQUE app-scoped subject
token — never an email, name, or external identifier. Check-in note TEXT is
never copied into any resource: :func:`map_series` consumes notes only
through the app-injected in-boundary ``sentiment_fn`` (D3) and emits the
resulting float. The note stays app-side.

Terminology (D2): no terminology library — codes are plain ``Coding`` /
``CodeableConcept`` fields against the small in-SDK table below; an external
terminology server over HTTP is the documented upgrade path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Sequence

from fhir.resources.R4B.codeableconcept import CodeableConcept
from fhir.resources.R4B.coding import Coding
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.nutritionorder import NutritionOrder
from fhir.resources.R4B.observation import Observation
from fhir.resources.R4B.quantity import Quantity
from fhir.resources.R4B.reference import Reference

from syntropy.lhm.errors import LhmError

#: The in-SDK coding system for LHM's curated code table (D2 — no external
#: terminology dep; codes below are the v0.1 curated set, updated by PR).
LHM_CODE_SYSTEM = "https://lhm.syntropyhealth.bio/fhir/codes"

#: UCUM — the unit system every Quantity carries (via the optional
#: ``syntropy-lhm[units]`` extra for validation; the URI is emitted regardless).
UCUM_SYSTEM = "http://unitsofmeasure.org"

#: Curated code table (v0.1). Key → display. Callers pass the KEY as
#: ``code``; unknown keys are emitted as-is under LHM_CODE_SYSTEM (the table
#: curates display names, it does not gate emission — fail-open by design so
#: new app-side metrics don't need an SDK release).
CURATED_CODES: dict[str, str] = {
    "supplement-order": "Supplement order",
    "rx-order": "Prescription order",
    "regimen-start": "Regimen start",
    "journal-metric": "Journal series metric",
    "wearable-metric": "Wearable series metric",
    "checkin-sentiment": "Check-in sentiment (in-boundary derived)",
}

#: The Observation code key :func:`map_series` uses for sentiment points.
SENTIMENT_CODE = "checkin-sentiment"


class MappingError(ValueError, LhmError):
    """A record cannot be mapped — bad/missing fields (fail loud, pure)."""


# --------------------------------------------------------------------------- #
# Input records — plain frozen shapes the D7 adapter normalizes into.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class OrderRecord:
    """One purchase/order event (Stripe / prescription / intake / Shopify)."""

    subject_ref: str  #: OPAQUE app-scoped subject token (never PHI)
    item_display: str  #: what was ordered (display text, claim-safe)
    occurred_at: datetime  #: tz-aware purchase timestamp
    is_rx: bool = False  #: True → MedicationRequest (user-private tier, C1b)
    code: str = ""  #: curated-table key; "" → the class default
    source: str = ""  #: "stripe" | "prescription" | "intake" | "shopify"


@dataclass(frozen=True)
class RegimenStartRecord:
    """The regimen start-date marker (R1's alignment anchor)."""

    subject_ref: str
    substance_display: str
    started_at: datetime  #: tz-aware
    code: str = ""
    dose_value: float | None = None
    dose_unit: str = ""  #: UCUM code (e.g. "mg"); "" when dose is unknown


@dataclass(frozen=True)
class SeriesPoint:
    """One outcome-series sample: numeric value OR a check-in note."""

    observed_at: datetime  #: tz-aware
    value: float | None = None  #: numeric sample (journal/wearable)
    note: str | None = None  #: check-in note — consumed ONLY via sentiment_fn


@dataclass(frozen=True)
class SeriesRecord:
    """An outcome series (journal / wearable / check-in) for one metric."""

    subject_ref: str
    metric_display: str  #: e.g. "Sleep quality (self-rated)"
    unit: str = ""  #: UCUM code for numeric values; "" for unitless scores
    code: str = ""  #: curated-table key; "" → "journal-metric"
    points: Sequence[SeriesPoint] = field(default_factory=tuple)


# --------------------------------------------------------------------------- #
# Internal helpers
# --------------------------------------------------------------------------- #


def _require_aware(dt: datetime, what: str) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise MappingError(f"{what} must be timezone-aware (got naive {dt!r})")
    return dt


def _subject(subject_ref: str) -> Reference:
    token = (subject_ref or "").strip()
    if not token:
        raise MappingError("subject_ref is required (opaque app-scoped token)")
    if "@" in token:
        # C9: the token must be OPAQUE — an email-shaped subject_ref is a
        # PHI leak at the source. (Name-shaped tokens can't be detected
        # structurally; opacity beyond this check is the adapter's contract.)
        raise MappingError("subject_ref must be an opaque token, not an email")
    return Reference(reference=f"Patient/{token}")


def _concept(code: str, display: str) -> CodeableConcept:
    key = (code or "").strip()
    text = (display or "").strip()
    if not text:
        raise MappingError("display text is required")
    coding = [Coding(system=LHM_CODE_SYSTEM, code=key, display=CURATED_CODES.get(key, text))]
    return CodeableConcept(coding=coding if key else None, text=text)


# --------------------------------------------------------------------------- #
# The three D2 mapping functions
# --------------------------------------------------------------------------- #


def map_order(record: OrderRecord) -> NutritionOrder | MedicationRequest:
    """Map a purchase/order to its FHIR resource.

    Rx orders (``is_rx=True``) become ``MedicationRequest`` — the C1b
    user-private tier: the CONSUMER is responsible for storing these in the
    user-private store only. Everything else becomes ``NutritionOrder``.
    Both carry ``status="completed"`` / ``intent="order"`` — LHM maps
    completed purchases, not carts.
    """
    when = _require_aware(record.occurred_at, "OrderRecord.occurred_at")
    subject = _subject(record.subject_ref)
    if record.is_rx:
        return MedicationRequest(
            status="completed",
            intent="order",
            subject=subject,
            medicationCodeableConcept=_concept(record.code or "rx-order", record.item_display),
            authoredOn=when,
        )
    order = NutritionOrder(
        status="completed",
        intent="order",
        patient=subject,
        dateTime=when,
    )
    # The ordered product rides NutritionOrder.supplement (QG fix 2026-07-09:
    # oralDiet models a THERAPEUTIC DIET class, not a product — supplement.type
    # + supplement.productName is the R4B slot for an ordered supplement).
    order.supplement = [
        {
            "type": _concept(record.code or "supplement-order", record.item_display),
            "productName": record.item_display,
        }
    ]
    return order


def map_regimen_start(record: RegimenStartRecord) -> MedicationStatement:
    """Map a regimen start to the ``MedicationStatement`` start marker."""
    when = _require_aware(record.started_at, "RegimenStartRecord.started_at")
    stmt = MedicationStatement(
        status="active",
        subject=_subject(record.subject_ref),
        medicationCodeableConcept=_concept(
            record.code or "regimen-start", record.substance_display
        ),
        effectiveDateTime=when,
    )
    if record.dose_value is not None:
        dosage_dose = Quantity(
            value=record.dose_value,
            unit=record.dose_unit or None,
            system=UCUM_SYSTEM if record.dose_unit else None,
            code=record.dose_unit or None,
        )
        stmt.dosage = [{"doseAndRate": [{"doseQuantity": dosage_dose}]}]
    return stmt


def map_series(
    record: SeriesRecord,
    sentiment_fn: Callable[[str], float] | None = None,
) -> list[Observation]:
    """Map an outcome series to ``Observation``s (UCUM quantities).

    - Numeric points emit one Observation each under the record's code.
      Sign/scale are CALLER-OWNED (wearable deltas can be negative; the
      corrections/fit layer owns normalization) — the mapping does not guard
      magnitude.
    - Note points emit a SENTIMENT Observation **only when** the app injected
      ``sentiment_fn`` (D3 — the classifier runs in-boundary; the note TEXT is
      never copied into the resource). Note points without a ``sentiment_fn``
      are skipped (counted, not silently lost — see the returned list length
      vs ``len(record.points)`` app-side).
    - A point with neither ``value`` nor ``note`` is a bad record → raises.
    """
    subject = _subject(record.subject_ref)
    metric_concept = _concept(record.code or "journal-metric", record.metric_display)
    sentiment_concept = _concept(SENTIMENT_CODE, CURATED_CODES[SENTIMENT_CODE])
    out: list[Observation] = []
    for i, pt in enumerate(record.points):
        when = _require_aware(pt.observed_at, f"points[{i}].observed_at")
        if pt.value is None and pt.note is None:
            raise MappingError(f"points[{i}]: neither value nor note — unmappable sample")
        if pt.value is not None:
            out.append(
                Observation(
                    status="final",
                    subject=subject,
                    code=metric_concept,
                    effectiveDateTime=when,
                    valueQuantity=Quantity(
                        value=pt.value,
                        unit=record.unit or None,
                        system=UCUM_SYSTEM if record.unit else None,
                        code=record.unit or None,
                    ),
                )
            )
        if pt.note is not None and sentiment_fn is not None:
            raw = sentiment_fn(pt.note)
            try:
                score = float(raw)
            except (TypeError, ValueError) as exc:
                raise MappingError(
                    f"points[{i}]: sentiment_fn returned a non-numeric ({raw!r})"
                ) from exc
            out.append(
                Observation(
                    status="final",
                    subject=subject,
                    code=sentiment_concept,
                    effectiveDateTime=when,
                    # Sentiment is a unitless score in [-1, 1] by convention;
                    # the SDK does not clamp — the injected classifier owns
                    # its scale, the FitBlock layer owns normalization.
                    valueQuantity=Quantity(value=score),
                )
            )
    return out


__all__ = [
    "CURATED_CODES",
    "LHM_CODE_SYSTEM",
    "MappingError",
    "OrderRecord",
    "RegimenStartRecord",
    "SENTIMENT_CODE",
    "SeriesPoint",
    "SeriesRecord",
    "UCUM_SYSTEM",
    "map_order",
    "map_regimen_start",
    "map_series",
]
