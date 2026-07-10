"""L4 — the NL seam: MetricIntent, the Interpreter Protocol, the binder, ask.

Pins (design 0001 §1/§3, D10):

- NL is sugar: ``MetricIntent`` is hand-constructible; ``raw_prompt`` never
  leaves the process (C9 — excluded from dumps and repr).
- Binding is DETERMINISTIC SDK code: intents resolve against the adapter's
  actual records; a hallucinated input/outcome refuses (typed), never
  guesses.
- ``lhm.ask`` composes interpret → bind → fetch → analyze and exposes
  ``.suggestions`` + ``.publish(target)`` — end-to-end against the synthetic
  fixtures with a stub interpreter, no LLM, no network.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import pytest

from lhm.ask import AskResult, ask
from lhm.fixtures.synthetic import (
    SYNTHETIC_EPOCH,
    synthetic_order,
    synthetic_regimen_start,
    synthetic_series,
    synthetic_subject_ref,
)
from lhm.models.mapping import (
    OrderRecord,
    RegimenStartRecord,
    SeriesPoint,
    SeriesRecord,
)
from lhm.nl import (
    Interpreter,
    MetricIntent,
    UnresolvableIntentError,
    resolve_intent,
)
from lhm.publish import PublishReceipt, SyntropyTarget

SCOPE = "test-subject-001"


class RecordAdapter:
    """In-memory LhmAdapter over explicit records."""

    def __init__(
        self,
        orders: Sequence[OrderRecord] = (),
        starts: Sequence[RegimenStartRecord] = (),
        series: Sequence[SeriesRecord] = (),
    ) -> None:
        self._orders = tuple(orders)
        self._starts = tuple(starts)
        self._series = tuple(series)

    async def read_orders(self, user_scope: str) -> Sequence[OrderRecord]:
        return self._orders

    async def read_regimen_starts(self, user_scope: str) -> Sequence[RegimenStartRecord]:
        return self._starts

    async def read_outcome_series(self, user_scope: str) -> Sequence[SeriesRecord]:
        return self._series


class StubInterpreter:
    """A no-LLM Interpreter: returns a fixed intent, carries the prompt."""

    def __init__(self, intent: MetricIntent) -> None:
        self._intent = intent

    async def interpret(self, prompt: str) -> MetricIntent:
        return self._intent.model_copy(update={"raw_prompt": prompt})


def _order(display: str = "Magnesium glycinate", day: int = 0, **kw) -> OrderRecord:
    return OrderRecord(
        subject_ref=SCOPE,
        item_display=display,
        occurred_at=SYNTHETIC_EPOCH + timedelta(days=day),
        **kw,
    )


def _start(display: str = "Magnesium glycinate", day: int = 5) -> RegimenStartRecord:
    return RegimenStartRecord(
        subject_ref=SCOPE,
        substance_display=display,
        started_at=SYNTHETIC_EPOCH + timedelta(days=day),
    )


def _series(
    display: str = "Sleep quality (self-rated)", values: Sequence[float] = ()
) -> SeriesRecord:
    points = tuple(
        SeriesPoint(observed_at=SYNTHETIC_EPOCH + timedelta(days=i), value=v)
        for i, v in enumerate(values)
    )
    return SeriesRecord(subject_ref=SCOPE, metric_display=display, points=points)


def _contrast_adapter() -> RecordAdapter:
    """Strong-contrast data: outcome jumps when the regimen starts (day 5)."""
    return RecordAdapter(
        orders=(_order(),),
        starts=(_start(day=5),),
        series=(_series(values=(2.0, 2.0, 2.0, 2.0, 2.0, 8.0, 8.0, 8.0, 8.0, 8.0)),),
    )


INTENT = MetricIntent(input_query="magnesium", outcome_query="sleep quality")


# --------------------------------------------------------------------------- #
# MetricIntent — hand-constructible, C9-tight
# --------------------------------------------------------------------------- #


def test_metric_intent_hand_constructible_with_design_defaults():
    intent = MetricIntent(input_query="magnesium", outcome_query="sleep quality")
    assert intent.window_days == 90  # design 0001 §3 default
    assert intent.raw_prompt == ""


def test_raw_prompt_never_leaves_the_process():
    """C9: provenance stays in-process — no dump, no json, no repr."""
    intent = MetricIntent(
        input_query="magnesium",
        outcome_query="sleep",
        raw_prompt="my private free-text question",
    )
    assert "raw_prompt" not in intent.model_dump()
    assert "private" not in intent.model_dump_json()
    assert "private" not in repr(intent)
    assert intent.raw_prompt == "my private free-text question"  # still readable in-process


def test_stub_interpreter_satisfies_the_protocol():
    assert isinstance(StubInterpreter(INTENT), Interpreter)


# --------------------------------------------------------------------------- #
# The deterministic binder
# --------------------------------------------------------------------------- #


async def test_resolve_intent_binds_matching_records():
    adapter = RecordAdapter(
        orders=(_order("Magnesium glycinate"), _order("Synthetic Compound B", day=2)),
        starts=(_start("Magnesium glycinate"), _start("Synthetic Compound B", day=1)),
        series=(
            _series("Sleep quality (self-rated)", values=(1.0, 2.0)),
            _series("Resting heart rate", values=(60.0, 61.0)),
        ),
    )
    resolved = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert tuple(o.item_display for o in resolved.orders) == ("Magnesium glycinate",)
    assert tuple(s.substance_display for s in resolved.regimen_starts) == ("Magnesium glycinate",)
    assert tuple(r.metric_display for r in resolved.series) == ("Sleep quality (self-rated)",)
    assert resolved.user_scope == SCOPE


async def test_resolve_intent_matches_token_subset():
    """ "sleep quality" resolves onto "Sleep quality (self-rated)" and back."""
    adapter = RecordAdapter(
        orders=(_order("Glycinate, magnesium form"),),
        series=(_series("Sleep quality (self-rated)", values=(1.0,)),),
    )
    resolved = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert len(resolved.orders) == 1
    assert len(resolved.series) == 1


async def test_hallucinated_input_refuses_typed():
    """A column the adapter does not have CANNOT become a metric (D10)."""
    adapter = _contrast_adapter()
    intent = MetricIntent(input_query="unicorn dust", outcome_query="sleep quality")
    with pytest.raises(UnresolvableIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert exc.value.kind == "input"
    assert exc.value.query == "unicorn dust"
    assert "refusing to guess" in str(exc.value)


async def test_hallucinated_outcome_refuses_typed():
    adapter = _contrast_adapter()
    intent = MetricIntent(input_query="magnesium", outcome_query="chakra alignment")
    with pytest.raises(UnresolvableIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert exc.value.kind == "outcome"


async def test_resolution_is_deterministic():
    adapter = _contrast_adapter()
    first = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    second = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert first == second


async def test_window_days_trims_stale_points():
    values = tuple(float(i) for i in range(60))  # days 0..59; anchor = day 59
    adapter = RecordAdapter(
        orders=(_order(),),
        series=(_series(values=values),),
    )
    intent = MetricIntent(input_query="magnesium", outcome_query="sleep quality", window_days=10)
    resolved = await resolve_intent(intent, adapter, user_scope=SCOPE)
    kept = resolved.series[0].points
    assert len(kept) == 11  # day 49..59 inclusive (anchor - 10d cutoff)
    assert min(p.observed_at for p in kept) == SYNTHETIC_EPOCH + timedelta(days=49)


# --------------------------------------------------------------------------- #
# QG CODE-1 — whole-token matching only (no raw-substring false positives)
# --------------------------------------------------------------------------- #


async def test_iron_does_not_match_environment_substring():
    """ "iron" is a SUBSTRING of "environment" — must refuse, never bind wrong."""
    adapter = RecordAdapter(
        orders=(_order("Environment Monitor"),),
        series=(_series(values=(1.0, 2.0)),),
    )
    intent = MetricIntent(input_query="iron", outcome_query="sleep quality")
    with pytest.raises(UnresolvableIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert exc.value.kind == "input"


async def test_sleep_does_not_match_sleepiness_epworth():
    """ "sleep" ⊄ tokens of "Sleepiness (Epworth)" — refusal beats a wrong record."""
    adapter = RecordAdapter(
        orders=(_order("Magnesium glycinate"),),
        series=(_series("Sleepiness (Epworth)", values=(1.0, 2.0)),),
    )
    intent = MetricIntent(input_query="magnesium", outcome_query="sleep")
    with pytest.raises(UnresolvableIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert exc.value.kind == "outcome"


async def test_whole_token_matching_still_binds_real_records():
    """magnesium↔"Magnesium glycinate" and "vitamin d3"↔"Vitamin D3" still bind."""
    adapter = RecordAdapter(
        orders=(_order("Vitamin D3"),),
        series=(_series("Sleep quality (self-rated)", values=(1.0, 2.0)),),
    )
    intent = MetricIntent(input_query="vitamin d3", outcome_query="sleep quality")
    resolved = await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert tuple(o.item_display for o in resolved.orders) == ("Vitamin D3",)

    magnesium = await resolve_intent(INTENT, _contrast_adapter(), user_scope=SCOPE)
    assert tuple(o.item_display for o in magnesium.orders) == ("Magnesium glycinate",)


# --------------------------------------------------------------------------- #
# QG round-2 T2-4 — order matching via the curated CODE, and the empty-code
# short-circuit
# --------------------------------------------------------------------------- #


async def test_code_only_match_binds_order():
    """T2-4 arm 1: a query that misses the display but matches the order's
    curated CODE still binds ("magnesium" → code="magnesium")."""
    adapter = RecordAdapter(
        orders=(_order("Some Opaque Brand", code="magnesium"),),
        series=(_series(values=(1.0, 2.0)),),
    )
    resolved = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert tuple(o.item_display for o in resolved.orders) == ("Some Opaque Brand",)


async def test_empty_code_short_circuits_and_refuses_cleanly():
    """T2-4 arm 2: the default code="" short-circuits the code branch — a
    non-matching display refuses with the TYPED error, never crashes."""
    adapter = RecordAdapter(
        orders=(_order("Some Opaque Brand"),),  # code="" (default)
        series=(_series(values=(1.0, 2.0)),),
    )
    with pytest.raises(UnresolvableIntentError) as exc:
        await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert exc.value.kind == "input"


# --------------------------------------------------------------------------- #
# QG CODE-2 — ambiguity refuses (never merge distinct substances/series)
# --------------------------------------------------------------------------- #


async def test_ambiguous_input_query_refuses_typed():
    """ "vitamin" matching Vitamin D3 AND Vitamin C must refuse, not merge."""
    from lhm.nl import AmbiguousIntentError

    adapter = RecordAdapter(
        orders=(_order("Vitamin D3"), _order("Vitamin C", day=1)),
        series=(_series(values=(1.0, 2.0)),),
    )
    intent = MetricIntent(input_query="vitamin", outcome_query="sleep quality")
    with pytest.raises(AmbiguousIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    err = exc.value
    assert err.kind == "input"
    assert err.query == "vitamin"
    # C9 posture: count + query in the message; candidate displays only on
    # the attribute, never in the message text.
    assert "2" in str(err)
    assert "vitamin" in str(err)
    assert "Vitamin D3" not in str(err)
    assert "Vitamin C" not in str(err)
    assert set(err.matched_displays) == {"Vitamin D3", "Vitamin C"}


async def test_repeated_same_display_orders_are_not_ambiguous():
    """Re-purchases (same display, many orders) are ONE substance — binds fine."""
    adapter = RecordAdapter(
        orders=(_order("Vitamin D3"), _order("Vitamin D3", day=3), _order("Vitamin D3", day=9)),
        series=(_series(values=(1.0, 2.0)),),
    )
    intent = MetricIntent(input_query="vitamin d3", outcome_query="sleep quality")
    resolved = await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert len(resolved.orders) == 3


async def test_same_display_different_code_series_refuse_as_ambiguous():
    """C2-1: distinctness keys on the DOWNSTREAM identity (kind, display).

    A wearable-coded and a journal-coded series can share a display ("Sleep
    quality") yet become two DIFFERENT OutcomeRef identities in fetch
    (kind derives from the code) — binding both under one metric would merge
    distinct downstream series. Must refuse, never merge.
    """
    from lhm.nl import AmbiguousIntentError

    wearable = SeriesRecord(
        subject_ref=SCOPE,
        metric_display="Sleep quality",
        code="wearable-metric",
        points=(SeriesPoint(observed_at=SYNTHETIC_EPOCH, value=1.0),),
    )
    journal = SeriesRecord(
        subject_ref=SCOPE,
        metric_display="Sleep quality",
        code="",  # → journal_series downstream
        points=(SeriesPoint(observed_at=SYNTHETIC_EPOCH, value=2.0),),
    )
    adapter = RecordAdapter(orders=(_order(),), series=(wearable, journal))
    with pytest.raises(AmbiguousIntentError) as exc:
        await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    err = exc.value
    assert err.kind == "outcome"
    assert "2" in str(err)  # two distinct downstream identities
    assert "Sleep quality" not in str(err)  # C9: displays never in the message
    assert tuple(err.matched_displays) == ("Sleep quality", "Sleep quality")


async def test_same_display_same_code_series_repeats_still_bind():
    """C2-1 keep-arm: same-display + SAME-code multiplicity is ONE downstream
    identity — repeats bind fine (no false ambiguity)."""
    a = SeriesRecord(
        subject_ref=SCOPE,
        metric_display="Sleep quality",
        code="wearable-metric",
        points=(SeriesPoint(observed_at=SYNTHETIC_EPOCH, value=1.0),),
    )
    b = SeriesRecord(
        subject_ref=SCOPE,
        metric_display="Sleep quality",
        code="wearable-metric",
        points=(SeriesPoint(observed_at=SYNTHETIC_EPOCH + timedelta(days=1), value=2.0),),
    )
    adapter = RecordAdapter(orders=(_order(),), series=(a, b))
    resolved = await resolve_intent(INTENT, adapter, user_scope=SCOPE)
    assert len(resolved.series) == 2


async def test_ambiguous_outcome_query_refuses_typed():
    from lhm.nl import AmbiguousIntentError

    adapter = RecordAdapter(
        orders=(_order(),),
        series=(
            _series("Sleep quality (self-rated)", values=(1.0, 2.0)),
            _series("Sleep depth", values=(3.0, 4.0)),
        ),
    )
    intent = MetricIntent(input_query="magnesium", outcome_query="sleep")
    with pytest.raises(AmbiguousIntentError) as exc:
        await resolve_intent(intent, adapter, user_scope=SCOPE)
    assert exc.value.kind == "outcome"
    assert "Sleep depth" not in str(exc.value)


# --------------------------------------------------------------------------- #
# QG CODE-4 — naive datetimes refuse with a labeled error, never TypeError
# --------------------------------------------------------------------------- #


async def test_naive_observed_at_raises_labeled_error_not_typeerror():
    from datetime import datetime

    from lhm.models.mapping import MappingError

    naive_points = (
        SeriesPoint(observed_at=SYNTHETIC_EPOCH + timedelta(days=1), value=2.0),
        SeriesPoint(observed_at=datetime(2026, 1, 3, 12, 0), value=3.0),  # naive
    )
    adapter = RecordAdapter(
        orders=(_order(),),
        series=(
            SeriesRecord(
                subject_ref=SCOPE,
                metric_display="Sleep quality (self-rated)",
                points=naive_points,
            ),
        ),
    )
    with pytest.raises(MappingError, match="observed_at.*timezone-aware"):
        await resolve_intent(INTENT, adapter, user_scope=SCOPE)


async def test_all_naive_series_also_refuses_labeled():
    from datetime import datetime

    from lhm.models.mapping import MappingError

    adapter = RecordAdapter(
        orders=(_order(),),
        series=(
            SeriesRecord(
                subject_ref=SCOPE,
                metric_display="Sleep quality (self-rated)",
                points=(SeriesPoint(observed_at=datetime(2026, 1, 2), value=2.0),),
            ),
        ),
    )
    with pytest.raises(MappingError, match="timezone-aware"):
        await resolve_intent(INTENT, adapter, user_scope=SCOPE)


# --------------------------------------------------------------------------- #
# QG CODE-5 — query floor at the model (>=1 token of >=3 chars)
# --------------------------------------------------------------------------- #


def test_single_char_input_query_refused_at_model():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetricIntent(input_query="d", outcome_query="sleep quality")


def test_single_char_outcome_query_refused_at_model():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        MetricIntent(input_query="magnesium", outcome_query="d")


def test_query_with_one_long_token_passes_the_floor():
    intent = MetricIntent(input_query="vitamin d3", outcome_query="hrv sleep")
    assert intent.input_query == "vitamin d3"


# --------------------------------------------------------------------------- #
# lhm.ask — the one-liner composition
# --------------------------------------------------------------------------- #


async def test_ask_end_to_end_against_synthetic_fixtures():
    scope = synthetic_subject_ref(0)
    adapter = RecordAdapter(
        orders=(synthetic_order(0),),
        starts=(synthetic_regimen_start(0),),
        series=(synthetic_series(0, with_notes=False),),
    )
    interpreter = StubInterpreter(
        MetricIntent(input_query="synthetic", outcome_query="synthetic sleep score")
    )
    result = await ask(
        "how does my synthetic compound relate to my sleep score?",
        adapter=adapter,
        interpreter=interpreter,
        user_scope=scope,
    )
    assert isinstance(result, AskResult)
    assert result.user_scope == scope
    assert result.intent.raw_prompt.startswith("how does")
    assert len(result.suggestions) == 1
    candidate = result.suggestions[0]
    assert candidate.confidence in ("low", "mid", "strong")
    assert candidate.caveats  # honesty layer always present
    assert callable(result.publish)


async def test_ask_refuses_hallucinated_intent():
    interpreter = StubInterpreter(
        MetricIntent(input_query="unicorn dust", outcome_query="sleep quality")
    )
    with pytest.raises(UnresolvableIntentError):
        await ask("anything", adapter=_contrast_adapter(), interpreter=interpreter)


async def test_ask_suggestions_sorted_best_first():
    """Two same-display series (same series_key — NOT ambiguous, QG CODE-2)
    with different contrast: suggestions come back best-first."""
    strong = _contrast_adapter()
    weak_series = _series(values=(1.0, 10.0, 1.0, 10.0, 1.0, 5.4, 5.6, 5.5, 5.5, 5.5))
    adapter = RecordAdapter(
        orders=strong._orders,
        starts=strong._starts,
        series=(weak_series, *strong._series),
    )
    result = await ask(
        "magnesium vs sleep quality",
        adapter=adapter,
        interpreter=StubInterpreter(INTENT),
        user_scope=SCOPE,
    )
    assert len(result.suggestions) == 2
    scores = [c.fit.fit_score for c in result.suggestions]
    tiers = [c.confidence for c in result.suggestions]
    assert tiers == sorted(tiers, key=lambda t: {"strong": 0, "mid": 1, "low": 2}[t])
    assert scores[0] == max(scores)


async def test_ask_publish_via_mock_transport_end_to_end():
    httpx = pytest.importorskip("httpx")
    requests: list = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "accepted": True,
                "metric_key": "sig_x",
                "dashboard_url": "https://j.example.org/metrics/sig_x",
                "reason": "pinned",
            },
        )

    from lhm.publish import HttpPublisher

    result = await ask(
        "magnesium vs sleep",
        adapter=_contrast_adapter(),
        interpreter=StubInterpreter(INTENT),
        user_scope=SCOPE,
    )
    assert result.suggestions[0].confidence == "strong"
    target = SyntropyTarget(base_url="https://j.example.org")
    publisher = HttpPublisher(target, transport=httpx.MockTransport(handler))
    receipt = await result.publish(target, publisher=publisher)
    assert isinstance(receipt, PublishReceipt)
    assert receipt.accepted
    assert receipt.dashboard_url is not None
    assert len(requests) == 1
    assert requests[0].url.path == "/api/lhm/v1/metrics"


async def test_mutated_banned_suggestion_hard_raises_and_never_touches_transport():
    """T2-7: a hand-mutated (banned-lexicon) best suggestion reaching
    ``AskResult.publish`` is a PROGRAMMING error — ClaimScopeError propagates
    (arm 1) and the transport is never called (arm 2)."""
    import dataclasses

    httpx = pytest.importorskip("httpx")

    from lhm.metrics.claims import ClaimScopeError
    from lhm.publish import HttpPublisher

    result = await ask(
        "magnesium vs sleep",
        adapter=_contrast_adapter(),
        interpreter=StubInterpreter(INTENT),
        user_scope=SCOPE,
    )
    mutated_best = result.suggestions[0].model_copy(
        update={"label": "Magnesium improves sleep quality"}  # banned lexicon
    )
    tampered = dataclasses.replace(result, suggestions=(mutated_best, *result.suggestions[1:]))

    calls: list = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"accepted": True, "metric_key": "sig_x", "reason": "pinned"}
        )

    target = SyntropyTarget(base_url="https://j.example.org")
    publisher = HttpPublisher(target, transport=httpx.MockTransport(handler))
    with pytest.raises(ClaimScopeError):
        await tampered.publish(target, publisher=publisher)
    assert calls == []  # nothing crossed the transport


async def test_ask_publish_refuses_when_nothing_persistable():
    """All-low suggestions → a typed refused receipt, no transport at all."""
    flat = RecordAdapter(
        orders=(_order(),),
        starts=(_start(day=5),),
        # Zero contrast: active/inactive means equal → strength 0 → low tier.
        series=(_series(values=(1.0, 10.0, 1.0, 10.0, 5.5, 5.5, 5.5, 5.5, 5.5, 5.5)),),
    )
    result = await ask(
        "magnesium vs sleep",
        adapter=flat,
        interpreter=StubInterpreter(INTENT),
        user_scope=SCOPE,
    )
    assert {c.confidence for c in result.suggestions} == {"low"}
    receipt = await result.publish(SyntropyTarget(base_url="https://j.example.org"))
    assert not receipt.accepted
    assert "mid/strong" in receipt.reason
