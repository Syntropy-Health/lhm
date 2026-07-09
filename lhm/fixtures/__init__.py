"""Synthetic-data generators (R7) — programmatic, deterministic, ZERO PHI.

Everything here is generated from a caller-supplied seed against a fixed
epoch — no wall-clock, no real identifiers, no lookalike emails/names. The
R7 packaging gate scans this package (source AND generated output) for
PHI/secret patterns; keep it that way.
"""

from lhm.fixtures.synthetic import (
    SYNTHETIC_EPOCH,
    synthetic_order,
    synthetic_regimen_start,
    synthetic_series,
    synthetic_subject_ref,
)

__all__ = [
    "SYNTHETIC_EPOCH",
    "synthetic_order",
    "synthetic_regimen_start",
    "synthetic_series",
    "synthetic_subject_ref",
]
