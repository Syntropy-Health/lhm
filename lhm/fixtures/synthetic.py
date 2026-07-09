"""Deterministic synthetic generators for the D2 mapping inputs (R7).

Design rules (R7 — PHI/secret-safe by construction):

- **Deterministic**: same ``seed`` → identical records within a Python
  version line (the distribution helpers' sequences are not contractually
  frozen across CPython versions; the string-seed itself is stable). Timestamps
  derive from :data:`SYNTHETIC_EPOCH` + seeded offsets — never wall-clock.
- **Unmistakably synthetic**: subject tokens are ``syn-subject-<n>``,
  compounds come from a fake vocabulary ("Synthetic Compound A") — nothing
  resembles a real Clerk id, email, name, or product.
- **No I/O**: pure ``random.Random(seed)`` — importable in a clean env
  (the R7 install-and-synthesize gate does exactly that).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from lhm.models.mapping import (
    OrderRecord,
    RegimenStartRecord,
    SeriesPoint,
    SeriesRecord,
)

#: Fixed epoch every synthetic timestamp derives from (never wall-clock).
SYNTHETIC_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

_COMPOUNDS = (
    "Synthetic Compound A",
    "Synthetic Compound B",
    "Synthetic Blend C",
    "Synthetic Peptide D (fixture)",
)

_METRICS = (
    ("Synthetic sleep score", "", "journal-metric"),
    ("Synthetic resting heart rate", "/min", "wearable-metric"),
    ("Synthetic energy rating", "", "journal-metric"),
)

_NOTES = (
    "synthetic check-in note (fixture text, no PHI)",
    "another synthetic note for classifier exercise",
)


def synthetic_subject_ref(seed: int = 0) -> str:
    """An opaque, unmistakably-synthetic subject token."""
    return f"syn-subject-{seed % 1000:03d}"


def synthetic_order(seed: int = 0, *, is_rx: bool = False) -> OrderRecord:
    rng = random.Random(("order", seed, is_rx).__repr__())
    return OrderRecord(
        subject_ref=synthetic_subject_ref(seed),
        item_display=rng.choice(_COMPOUNDS),
        occurred_at=SYNTHETIC_EPOCH + timedelta(days=rng.randrange(0, 90)),
        is_rx=is_rx,
        code="rx-order" if is_rx else "supplement-order",
        source=rng.choice(("stripe", "prescription", "intake")),
    )


def synthetic_regimen_start(seed: int = 0) -> RegimenStartRecord:
    rng = random.Random(("regimen", seed).__repr__())
    return RegimenStartRecord(
        subject_ref=synthetic_subject_ref(seed),
        substance_display=rng.choice(_COMPOUNDS),
        started_at=SYNTHETIC_EPOCH + timedelta(days=rng.randrange(0, 30)),
        code="regimen-start",
        dose_value=float(rng.choice((250, 500, 5, 10))),
        dose_unit=rng.choice(("mg", "mg/mL")),
    )


def synthetic_series(seed: int = 0, *, n: int = 14, with_notes: bool = True) -> SeriesRecord:
    """A daily outcome series of ``n`` points; every 5th carries a note."""
    rng = random.Random(("series", seed, n, with_notes).__repr__())
    display, unit, code = _METRICS[seed % len(_METRICS)]
    points = []
    for day in range(n):
        note = _NOTES[day % len(_NOTES)] if (with_notes and day % 5 == 0) else None
        points.append(
            SeriesPoint(
                observed_at=SYNTHETIC_EPOCH + timedelta(days=day),
                value=round(rng.uniform(1.0, 10.0), 2),
                note=note,
            )
        )
    return SeriesRecord(
        subject_ref=synthetic_subject_ref(seed),
        metric_display=display,
        unit=unit,
        code=code,
        points=tuple(points),
    )


__all__ = [
    "SYNTHETIC_EPOCH",
    "synthetic_order",
    "synthetic_regimen_start",
    "synthetic_series",
    "synthetic_subject_ref",
]
