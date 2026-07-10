"""The deterministic intent→data binder (design 0001 §3).

``resolve_intent(intent, adapter, user_scope=…)`` resolves the intent's
input/outcome QUERIES against the adapter's ACTUAL records — deterministic
string/code matching inside the SDK, NOT the LLM's job. The LLM narrows
language; the SDK binds data — so a hallucinated column is structurally
incapable of becoming a metric: an unresolvable query raises the typed
:class:`UnresolvableIntentError` refusal instead of guessing.

Matching is deliberately simple and reproducible (no fuzzy-scoring dep):
WHOLE-TOKEN subset containment only — the query's casefolded token set must
be a subset of the display's ("sleep quality" ⊆ "Sleep quality
(self-rated)"). Raw-substring matching is deliberately absent: "iron" must
never bind "environment score" — binding the WRONG real record is worse
than a refusal. A query matching MORE THAN ONE distinct record refuses too
(:class:`AmbiguousIntentError`) — the binder never merges distinct
substances/series under one metric.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from lhm.models.mapping import OrderRecord, RegimenStartRecord, SeriesRecord, _require_aware
from lhm.nl.intent import MetricIntent
from lhm.tools.pipeline import LhmAdapter

_TOKEN_RE = re.compile(r"[a-z0-9]+")


class UnresolvableIntentError(LookupError):
    """Typed refusal: an intent query matched NO adapter record (D10).

    Carries which side failed (``kind``), the query text, and the displays
    that WERE available — in-process diagnostics only (C9: callers must not
    log the candidates alongside user identifiers).
    """

    def __init__(self, kind: str, query: str, candidates: tuple[str, ...]) -> None:
        self.kind = kind
        self.query = query
        self.candidates = candidates
        super().__init__(
            f"intent {kind} query {query!r} matched no adapter record — "
            f"refusing to guess ({len(candidates)} candidate(s) available); "
            "the NL layer never invents data (design 0001 D10)"
        )


class AmbiguousIntentError(LookupError):
    """Typed refusal: an intent query matched >1 DISTINCT adapter record.

    Silently merging distinct substances/series under one metric would make
    the label misrepresent the data — refusal beats guessing. The message
    carries only the COUNT and the query; the distinct displays ride the
    ``matched_displays`` attribute for in-process diagnostics only (C9:
    callers must not log them alongside user identifiers — symmetric with
    :class:`UnresolvableIntentError`).
    """

    def __init__(self, kind: str, query: str, matched_displays: tuple[str, ...]) -> None:
        self.kind = kind
        self.query = query
        self.matched_displays = matched_displays
        super().__init__(
            f"intent {kind} query {query!r} matched {len(matched_displays)} distinct "
            "records — refusing to merge distinct data under one metric; narrow the "
            "query (the displays ride the .matched_displays attribute, never this message)"
        )


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(text.casefold()))


def _matches(query: str, display: str) -> bool:
    """Whole-token match ONLY: every query token must appear in the display.

    No raw-substring branch in either direction — "iron" ⊄ "environment
    score", "sleep" ⊄ "Sleepiness (Epworth)": a near-miss refuses instead
    of binding the wrong real record.
    """
    q = set(_tokens(query))
    d = set(_tokens(display))
    return bool(q) and bool(d) and q <= d


@dataclass(frozen=True)
class ResolvedIntent:
    """The binder's product: intent bound to REAL adapter records.

    Everything downstream (``fetch → analyze``) consumes these records via
    :meth:`as_adapter`, so the pipeline runs on exactly the data the binder
    resolved — nothing more, nothing invented.
    """

    user_scope: str
    intent: MetricIntent
    orders: tuple[OrderRecord, ...]
    regimen_starts: tuple[RegimenStartRecord, ...]
    series: tuple[SeriesRecord, ...]

    def as_adapter(self) -> LhmAdapter:
        """An :class:`LhmAdapter` view over the resolved records only."""
        return _ResolvedAdapter(self)


class _ResolvedAdapter:
    """LhmAdapter over a ResolvedIntent's records (feeds ``fetch`` as-is)."""

    def __init__(self, resolved: ResolvedIntent) -> None:
        self._resolved = resolved

    async def read_orders(self, user_scope: str) -> Sequence[OrderRecord]:
        return self._resolved.orders

    async def read_regimen_starts(self, user_scope: str) -> Sequence[RegimenStartRecord]:
        return self._resolved.regimen_starts

    async def read_outcome_series(self, user_scope: str) -> Sequence[SeriesRecord]:
        return self._resolved.series


def _window_series(record: SeriesRecord, window_days: int) -> SeriesRecord:
    """Restrict a series to the trailing ``window_days`` (anchor: its latest point).

    Tz-awareness is enforced up front (the fetch-side ``_require_aware``
    contract) — a naive ``observed_at`` refuses with a labeled
    ``MappingError`` naming the offending point, never a bare ``TypeError``
    out of the naive/aware datetime math below.
    """
    if not record.points:
        return record
    for i, p in enumerate(record.points):
        _require_aware(p.observed_at, f"{record.metric_display}.points[{i}].observed_at")
    anchor = max(p.observed_at for p in record.points)
    cutoff = anchor - timedelta(days=window_days)
    kept = tuple(p for p in record.points if p.observed_at >= cutoff)
    return dataclasses.replace(record, points=kept)


async def resolve_intent(
    intent: MetricIntent,
    adapter: LhmAdapter,
    *,
    user_scope: str,
) -> ResolvedIntent:
    """Bind a :class:`MetricIntent` to the adapter's actual records (D10).

    - ``input_query`` must match ≥1 order (display or curated code);
      matching regimen starts ride along (empty is fine — ``fetch`` falls
      back to the earliest order).
    - ``outcome_query`` must match ≥1 outcome series; matched series are
      windowed to the intent's ``window_days``.
    - Either side unresolvable → :class:`UnresolvableIntentError` (typed
      refusal, never a guess).
    - Either side matching >1 DISTINCT display/series →
      :class:`AmbiguousIntentError` (typed refusal, never a merge).
      Same-display repeats (e.g. re-purchases) are ONE substance — fine.
    """
    orders = tuple(await adapter.read_orders(user_scope))
    starts = tuple(await adapter.read_regimen_starts(user_scope))
    series = tuple(await adapter.read_outcome_series(user_scope))

    matched_orders = tuple(
        o
        for o in orders
        if _matches(intent.input_query, o.item_display)
        or (o.code and _matches(intent.input_query, o.code))
    )
    if not matched_orders:
        raise UnresolvableIntentError(
            "input", intent.input_query, tuple(o.item_display for o in orders)
        )
    distinct_inputs = tuple(dict.fromkeys(o.item_display for o in matched_orders))
    if len(distinct_inputs) > 1:
        raise AmbiguousIntentError("input", intent.input_query, distinct_inputs)

    matched_starts = tuple(s for s in starts if _matches(intent.input_query, s.substance_display))

    matched_series = tuple(
        _window_series(rec, intent.window_days)
        for rec in series
        if _matches(intent.outcome_query, rec.metric_display)
    )
    if not matched_series:
        raise UnresolvableIntentError(
            "outcome", intent.outcome_query, tuple(rec.metric_display for rec in series)
        )
    # ``metric_display`` IS the pipeline's series_key (fetch builds
    # ``OutcomeRef(series_key=rec.metric_display)``) — distinctness here is
    # distinctness of the downstream series identity.
    distinct_outcomes = tuple(dict.fromkeys(rec.metric_display for rec in matched_series))
    if len(distinct_outcomes) > 1:
        raise AmbiguousIntentError("outcome", intent.outcome_query, distinct_outcomes)

    return ResolvedIntent(
        user_scope=user_scope,
        intent=intent,
        orders=matched_orders,
        regimen_starts=matched_starts,
        series=matched_series,
    )


__all__ = [
    "AmbiguousIntentError",
    "ResolvedIntent",
    "UnresolvableIntentError",
    "resolve_intent",
]
