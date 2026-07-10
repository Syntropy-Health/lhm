"""The deterministic intent→data binder (design 0001 §3).

``resolve_intent(intent, adapter, user_scope=…)`` resolves the intent's
input/outcome QUERIES against the adapter's ACTUAL records — deterministic
string/code matching inside the SDK, NOT the LLM's job. The LLM narrows
language; the SDK binds data — so a hallucinated column is structurally
incapable of becoming a metric: an unresolvable query raises the typed
:class:`UnresolvableIntentError` refusal instead of guessing.

Matching is deliberately simple and reproducible (no fuzzy-scoring dep):
casefolded substring containment in either direction, or full token-subset
containment ("sleep quality" ⊆ "Sleep quality (self-rated)").
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Sequence

from lhm.models.mapping import OrderRecord, RegimenStartRecord, SeriesRecord
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


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_TOKEN_RE.findall(text.casefold()))


def _matches(query: str, display: str) -> bool:
    """Deterministic match: substring either way, or token-subset."""
    q = " ".join(_tokens(query))
    d = " ".join(_tokens(display))
    if not q or not d:
        return False
    if q in d or d in q:
        return True
    return set(_tokens(query)) <= set(_tokens(display))


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
    """Restrict a series to the trailing ``window_days`` (anchor: its latest point)."""
    if not record.points:
        return record
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

    return ResolvedIntent(
        user_scope=user_scope,
        intent=intent,
        orders=matched_orders,
        regimen_starts=matched_starts,
        series=matched_series,
    )


__all__ = ["ResolvedIntent", "UnresolvableIntentError", "resolve_intent"]
