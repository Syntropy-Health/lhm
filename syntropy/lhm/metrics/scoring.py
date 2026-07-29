"""Interpretable per-domain scoring engine (health-dashboard A&D D-D / §2.4 / §3.3).

Pure functions over typed ``(tz-aware datetime, float)`` samples → a
:class:`DomainScore` — **no LLM anywhere in this module** (G3; enforced by
``tests/unit/test_lhm_boundary_contract.py``), no raw text in the stats path.

Behind the ``[lhm-scoring]`` optional extra (``LHM/pyproject.toml``,
declared next to ``units``): statsmodels covers all four v1 estimators;
sklearn stays in the extra for isotonic/monotone calibration when a domain
needs it. The guarded import degrades — extra missing → the module still
imports, ``SCORING_AVAILABLE`` is ``False``, and :func:`score_domain`
returns the deterministic STUB with ``method="stub"`` (the degradation is
*recorded*; R5b keeps stub scores off every surface via :func:`is_ready`).

All scores: declared range **[0, 100]**, personal-trajectory framed,
in-range by construction (clip + ``params["clipped"]`` flag on an
out-of-range raw statistic — never raw).
"""

from __future__ import annotations

import importlib.util
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Mapping, Sequence


def _scoring_available() -> bool:
    """The ``[lhm-scoring]`` guard (A&D §2.4), availability-probe form.

    The A&D sketches ``try: import statsmodels.api`` — semantically kept,
    but the probe is ``find_spec`` + the actual imports are LAZY inside the
    estimators: the L0 app-independence gate (``test_l0_skeleton``
    allowlist) requires ``import syntropy.lhm.*`` to stay lean, exactly
    like the publish extra's lazy httpx. Poisoned/absent → ``False``.
    """
    try:
        return (
            importlib.util.find_spec("statsmodels") is not None
            and importlib.util.find_spec("scipy") is not None
        )
    except (ImportError, ValueError):
        # ValueError: a poisoned `sys.modules[...] = None` entry.
        return False


SCORING_AVAILABLE = _scoring_available()

#: The declared score range every v1 domain shares (A&D §3.3).
SCORE_MIN = 0.0
SCORE_MAX = 100.0

#: The four accepted v1 domains (A&D §3.3 — principal-confirmed picks).
V1_DOMAINS: tuple[str, ...] = (
    "consistency",
    "sentiment_trajectory",
    "nutrition_regularity",
    "symptom_load",
)

#: Readiness floor — minimum effective samples per domain (A&D §3.4).
DEFAULT_N_MIN = 7
N_MIN: dict[str, int] = {domain: DEFAULT_N_MIN for domain in V1_DOMAINS}

#: Readiness floor — the samples must cover at least this many distinct
#: observation days (A&D §3.4: "across >= 14 distinct days").
MIN_DISTINCT_DAYS = 14

#: Default estimator window (days) — the personal rolling baseline.
WINDOW_DAYS = 28

#: The app's ``SeverityScale.ORDINAL`` value range, mirrored as a NUMERIC
#: contract (mild=1 … severe=3). LHM never imports the app (R2 boundary) —
#: the adapter maps severity labels to these ordinals before the SDK sees
#: them; renaming/rescaling app-side is a contract change, not a UI choice.
ORDINAL_MIN = 1.0
ORDINAL_MAX = 3.0

#: Exponential-weighting half-life for symptom load (days).
_EW_HALF_LIFE_DAYS = 7.0

#: One typed sample: (tz-aware observation time, numeric value).
Sample = tuple[datetime, float]


@dataclass(frozen=True)
class DomainScore:
    """One domain's scored result + provenance (A&D §2.4 contract)."""

    domain: str
    score: float
    score_min: float = SCORE_MIN
    score_max: float = SCORE_MAX
    n_effective: int = 0
    interval: tuple[float, float] | None = None
    #: Estimator provenance — ``"stub"`` iff the real engine did not run.
    method: str = "stub"
    params: Mapping[str, object] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Shared helpers (pure, tz-strict)
# --------------------------------------------------------------------------- #


def _require_aware(dt: datetime, what: str) -> datetime:
    if dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None:
        raise ValueError(f"{what} must be timezone-aware (got naive {dt!r})")
    return dt


def _window(samples: Sequence[Sample], *, as_of: datetime, window_days: int) -> list[Sample]:
    """Chronologically sorted in-window samples; rejects naive timestamps."""
    _require_aware(as_of, "as_of")
    start = as_of - timedelta(days=window_days)
    kept = [
        (t, float(v)) for t, v in samples if start <= _require_aware(t, "sample timestamp") <= as_of
    ]
    kept.sort(key=lambda s: s[0])
    return kept


def _distinct_days(samples: Sequence[Sample]) -> int:
    return len({t.date() for t, _ in samples})


def _clipped_score(raw: float, params: dict[str, object]) -> float:
    """Clip into the declared range; flag the clip (never surface raw)."""
    if raw < SCORE_MIN or raw > SCORE_MAX or math.isnan(raw):
        params["clipped"] = True
        if math.isnan(raw):
            return SCORE_MIN
        return min(SCORE_MAX, max(SCORE_MIN, raw))
    params.setdefault("clipped", False)
    return raw


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2.0


# --------------------------------------------------------------------------- #
# The four accepted v1 estimators (A&D §3.3) — statsmodels-only
# --------------------------------------------------------------------------- #


def score_consistency(
    samples: Sequence[Sample], *, as_of: datetime, window_days: int = WINDOW_DAYS
) -> DomainScore:
    """Adherence consistency: 28d binomial proportion + Wilson interval.

    Samples: ``TOOK_INTERVENTION`` adherence events — value ``>= 0.5`` is
    "taken" (mirrors the app's taken=1.0 / skipped=0.0 mapping).
    """
    kept = _window(samples, as_of=as_of, window_days=window_days)
    params: dict[str, object] = {
        "window_days": window_days,
        "distinct_days": _distinct_days(kept),
    }
    n = len(kept)
    if n == 0:
        return DomainScore(
            domain="consistency",
            score=_clipped_score(0.0, params),
            n_effective=0,
            method="binomial_wilson",
            params=params,
        )
    from statsmodels.stats.proportion import proportion_confint  # lazy — extra

    successes = sum(1 for _, v in kept if v >= 0.5)
    proportion = successes / n
    lo, hi = proportion_confint(successes, n, alpha=0.05, method="wilson")
    params.update(successes=successes, n=n)
    return DomainScore(
        domain="consistency",
        score=_clipped_score(100.0 * proportion, params),
        n_effective=n,
        interval=(100.0 * float(lo), 100.0 * float(hi)),
        method="binomial_wilson",
        params=params,
    )


def score_sentiment_trajectory(
    samples: Sequence[Sample], *, as_of: datetime, window_days: int = WINDOW_DAYS
) -> DomainScore:
    """Sentiment trajectory: robust z vs own 28d baseline + Theil–Sen slope,
    bounded-logistic mapped into [0, 100] (50 == flat/at-baseline).

    Samples: the existing deterministic check-in sentiment series.
    """
    kept = _window(samples, as_of=as_of, window_days=window_days)
    params: dict[str, object] = {
        "window_days": window_days,
        "distinct_days": _distinct_days(kept),
    }
    n = len(kept)
    if n < 2:
        return DomainScore(
            domain="sentiment_trajectory",
            score=_clipped_score(50.0, params),
            n_effective=n,
            method="robust_z_theil_sen",
            params=params,
        )
    values = [v for _, v in kept]
    baseline = _median(values)
    mad = _median([abs(v - baseline) for v in values])
    robust_scale = 1.4826 * mad if mad > 0 else 1e-9

    recent_cut = as_of - timedelta(days=7)
    recent = [v for t, v in kept if t >= recent_cut] or values[-max(1, n // 4) :]
    z_recent = (sum(recent) / len(recent) - baseline) / robust_scale

    from scipy.stats import theilslopes  # lazy — statsmodels dep, extra

    t_days = [(t - kept[0][0]).total_seconds() / 86400.0 for t, _ in kept]
    slope = float(theilslopes(values, t_days)[0])
    #: slope expressed as robust units of change over a fortnight.
    z_slope = slope * 14.0 / robust_scale

    signal = max(-8.0, min(8.0, 0.5 * z_recent + 0.5 * z_slope))
    raw = 100.0 / (1.0 + math.exp(-signal))
    params.update(z_recent=z_recent, theil_sen_slope_per_day=slope, signal=signal)
    return DomainScore(
        domain="sentiment_trajectory",
        score=_clipped_score(raw, params),
        n_effective=n,
        method="robust_z_theil_sen",
        params=params,
    )


def score_nutrition_regularity(
    samples: Sequence[Sample], *, as_of: datetime, window_days: int = WINDOW_DAYS
) -> DomainScore:
    """Logging regularity: inverted, clipped coefficient of variation of
    daily ``LOGGED_FOOD`` event counts across the window (zero-days count).
    """
    kept = _window(samples, as_of=as_of, window_days=window_days)
    params: dict[str, object] = {
        "window_days": window_days,
        "distinct_days": _distinct_days(kept),
    }
    n = len(kept)
    if n == 0:
        return DomainScore(
            domain="nutrition_regularity",
            score=_clipped_score(0.0, params),
            n_effective=0,
            method="inverse_cv",
            params=params,
        )
    counts_by_day: dict[object, int] = {}
    for t, _ in kept:
        counts_by_day[t.date()] = counts_by_day.get(t.date(), 0) + 1
    counts = [
        counts_by_day.get((as_of - timedelta(days=offset)).date(), 0)
        for offset in range(window_days)
    ]
    mean = sum(counts) / len(counts)
    variance = sum((c - mean) ** 2 for c in counts) / len(counts)
    cv_raw = math.sqrt(variance) / mean if mean > 0 else float("inf")
    cv_used = min(1.0, cv_raw)
    if cv_raw > 1.0:
        params["clipped"] = True
    params.update(cv_raw=cv_raw if math.isfinite(cv_raw) else "inf")
    return DomainScore(
        domain="nutrition_regularity",
        score=_clipped_score(100.0 * (1.0 - cv_used), params),
        n_effective=n,
        method="inverse_cv",
        params=params,
    )


def score_symptom_load(
    samples: Sequence[Sample], *, as_of: datetime, window_days: int = WINDOW_DAYS
) -> DomainScore:
    """Symptom load: exponentially-weighted mean of severity ordinals
    (``SeverityScale.ORDINAL`` mirror: mild=1 … severe=3), inverted to
    [0, 100] — higher score = lower reported load (neutral magnitude).
    """
    kept = _window(samples, as_of=as_of, window_days=window_days)
    params: dict[str, object] = {
        "window_days": window_days,
        "distinct_days": _distinct_days(kept),
        "ew_half_life_days": _EW_HALF_LIFE_DAYS,
    }
    n = len(kept)
    if n == 0:
        # No reported symptoms in the window → no load. Never ready
        # (n_effective 0) — the value exists for provenance, not surfacing.
        return DomainScore(
            domain="symptom_load",
            score=_clipped_score(SCORE_MAX, params),
            n_effective=0,
            method="ew_mean_inverted",
            params=params,
        )
    decay = math.log(2.0) / _EW_HALF_LIFE_DAYS
    weights = [math.exp(-decay * (as_of - t).total_seconds() / 86400.0) for t, _ in kept]
    ew_mean = sum(w * v for w, (_, v) in zip(weights, kept)) / sum(weights)
    load = (ew_mean - ORDINAL_MIN) / (ORDINAL_MAX - ORDINAL_MIN)
    params.update(ew_mean=ew_mean)
    return DomainScore(
        domain="symptom_load",
        score=_clipped_score(100.0 * (1.0 - load), params),
        n_effective=n,
        method="ew_mean_inverted",
        params=params,
    )


# --------------------------------------------------------------------------- #
# Dispatcher + the recorded stub degradation
# --------------------------------------------------------------------------- #

_ESTIMATORS: dict[str, Callable[..., DomainScore]] = {
    "consistency": score_consistency,
    "sentiment_trajectory": score_sentiment_trajectory,
    "nutrition_regularity": score_nutrition_regularity,
    "symptom_load": score_symptom_load,
}


def _stub_score(
    domain: str, samples: Sequence[Sample], *, as_of: datetime, window_days: int
) -> DomainScore:
    """The deterministic degradation: neutral score, ``method="stub"``.

    A stub is not a functional model — it records sample provenance so the
    refresh job can persist an observable row, and :func:`is_ready` keeps
    it off every surface (R5b fail-closed).
    """
    kept = _window(samples, as_of=as_of, window_days=window_days)
    return DomainScore(
        domain=domain,
        score=50.0,
        n_effective=len(kept),
        method="stub",
        params={
            "window_days": window_days,
            "distinct_days": _distinct_days(kept),
            "stub": True,
        },
    )


def score_domain(
    domain: str,
    samples: Sequence[Sample],
    *,
    as_of: datetime,
    window_days: int = WINDOW_DAYS,
) -> DomainScore:
    """Score one v1 domain; unknown domain → ``KeyError`` (fail loud).

    Extra missing → the recorded stub (``method="stub"``), never a crash
    and never a fake real-estimator provenance.
    """
    if domain not in _ESTIMATORS:
        raise KeyError(f"unknown scoring domain {domain!r} — v1 set: {V1_DOMAINS}")
    if not SCORING_AVAILABLE:
        return _stub_score(domain, samples, as_of=as_of, window_days=window_days)
    return _ESTIMATORS[domain](samples, as_of=as_of, window_days=window_days)


# --------------------------------------------------------------------------- #
# Readiness (R5b — consumed by HD-7's refresh job; a stub can NEVER surface)
# --------------------------------------------------------------------------- #


def is_ready(
    score: DomainScore,
    domain: str | None = None,
    *,
    claim_gate_passed: bool = False,
    valence_gate_passed: bool = False,
) -> bool:
    """The A&D §3.4 readiness predicate — pure, fail-closed on EVERY conjunct.

    ``ready = SCORING_AVAILABLE and method != "stub" and n_effective >=
    N_MIN[domain] and distinct_days >= MIN_DISTINCT_DAYS and in-range and
    claim_gate_passed and valence_gate_passed``.

    The gate flags DEFAULT to ``False`` — a caller that never ran the
    claim/valence gates cannot mark a score ready by omission.
    """
    key = domain if domain is not None else score.domain
    if key not in N_MIN:
        return False
    try:
        distinct_days = int(score.params.get("distinct_days", 0))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
    return (
        SCORING_AVAILABLE
        and score.method != "stub"
        and score.n_effective >= N_MIN[key]
        and distinct_days >= MIN_DISTINCT_DAYS
        and score.score_min <= score.score <= score.score_max
        and claim_gate_passed
        and valence_gate_passed
    )


__all__ = [
    "DEFAULT_N_MIN",
    "MIN_DISTINCT_DAYS",
    "N_MIN",
    "ORDINAL_MAX",
    "ORDINAL_MIN",
    "SCORE_MAX",
    "SCORE_MIN",
    "SCORING_AVAILABLE",
    "V1_DOMAINS",
    "WINDOW_DAYS",
    "DomainScore",
    "Sample",
    "is_ready",
    "score_consistency",
    "score_domain",
    "score_nutrition_regularity",
    "score_sentiment_trajectory",
    "score_symptom_load",
]
