"""L2 — pipeline v0.1 (A&D D4/D6): fetch/analyze/manage + corrections.

Covers the phase's PVR criteria:
- R1: fetch returns aligned input/outcome series with the regimen start
  marked, from ≥1 order source + journal outcomes.
- R2: analyze returns the tier/corrections/interval SHAPE (stub-driven);
  no ``strong`` for an Rx input.
- R4: all six C2 hooks registered + invoked exactly once per analyze call,
  threading typed CorrectionResults into the FitBlock.
- R5 (SDK side): the C4 parameterization round-trips; the ``form``
  discriminator tolerates unknown future values at read; only mid/strong
  emit for persist; caveats present.
- R10: manage refuses a banned-lexicon label (the L0-deferred persist-side
  claim-gate source-pin).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import syntropy.lhm.metrics.corrections as corrections_mod
from syntropy.lhm.fixtures import synthetic_order, synthetic_regimen_start
from syntropy.lhm.metrics.claims import ClaimScopeError
from syntropy.lhm.metrics.corrections import CORRECTION_HOOKS, run_corrections
from syntropy.lhm.metrics.spec import CorrectionResult
from syntropy.lhm.models.mapping import SeriesPoint, SeriesRecord
from syntropy.lhm.tools import analyze, fetch, manage

_T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _series(values: list[float], display: str = "Synthetic energy rating") -> SeriesRecord:
    return SeriesRecord(
        subject_ref="syn-subject-001",
        metric_display=display,
        unit="",
        code="journal-metric",
        points=tuple(
            SeriesPoint(observed_at=_T0 + timedelta(days=i), value=v) for i, v in enumerate(values)
        ),
    )


class _Adapter:
    """Fixture adapter: regimen starts on day 10 of a 20-day series."""

    def __init__(self, *, is_rx: bool = False, values: list[float] | None = None) -> None:
        self._is_rx = is_rx
        # Planted separation: pre-start plateau 2.0, post-start plateau 8.0
        # → the stub strength is large and DETERMINISTIC (tier exercise).
        self._values = values if values is not None else [2.0] * 10 + [8.0] * 10

    async def read_orders(self, user_scope):
        rec = synthetic_order(1, is_rx=self._is_rx)
        return [rec]

    async def read_regimen_starts(self, user_scope):
        base = synthetic_regimen_start(1)
        return [
            type(base)(
                subject_ref=base.subject_ref,
                substance_display=base.substance_display,
                started_at=_T0 + timedelta(days=10),
                code=base.code,
                dose_value=base.dose_value,
                dose_unit=base.dose_unit,
            )
        ]

    async def read_outcome_series(self, user_scope):
        return [self._values_record()]

    def _values_record(self) -> SeriesRecord:
        return _series(self._values)


class TestFetchR1:
    async def test_aligned_with_start_marker(self) -> None:
        aligned = await fetch(_Adapter(), "syn-subject-001")
        assert len(aligned) == 1
        data = aligned[0]
        assert data.regimen_start == _T0 + timedelta(days=10)  # R1 marker
        assert len(data.pairs) == 20
        # Alignment: samples on/after the start are active.
        assert all(p.input_active == 0.0 for p in data.pairs[:10])
        assert all(p.input_active == 1.0 for p in data.pairs[10:])
        assert data.inputs and data.outcome.series_key

    async def test_rx_order_marks_rx_input(self) -> None:
        aligned = await fetch(_Adapter(is_rx=True), "syn-subject-001")
        assert aligned[0].rx_input is True
        assert aligned[0].inputs[0].resource == "MedicationRequest"

    async def test_no_regimen_start_falls_back_to_earliest_order(self) -> None:
        class NoStarts(_Adapter):
            async def read_regimen_starts(self, user_scope):
                return []

        aligned = await fetch(NoStarts(), "syn-subject-001")
        assert aligned[0].regimen_start == synthetic_order(1).occurred_at


class TestAnalyzeR2:
    async def test_shape_contract_tier_corrections_interval(self) -> None:
        aligned = await fetch(_Adapter(), "syn-subject-001")
        signals = await analyze(aligned)
        assert len(signals) == 1
        sig = signals[0]
        assert sig.confidence in ("low", "mid", "strong")
        assert len(sig.fit.corrections) == 6  # corrections-list-bearing
        lo, hi = sig.fit.posterior_ci
        assert lo < hi  # interval-bearing
        assert sig.caveats and sig.fit.method == "stub"

    async def test_planted_separation_reaches_a_persistable_tier(self) -> None:
        """G1.5: the tier surface is PLUMBED (not discriminating) — a fully
        separated fixture must exercise the mid/strong path."""
        aligned = await fetch(_Adapter(), "syn-subject-001")
        sig = (await analyze(aligned))[0]
        assert sig.confidence in ("mid", "strong")

    async def test_rx_input_never_strong(self) -> None:
        """C1b hard rule: an Rx input never surfaces `strong` in v0.1."""
        aligned = await fetch(_Adapter(is_rx=True), "syn-subject-001")
        sig = (await analyze(aligned))[0]
        assert sig.rx_input is True
        assert sig.confidence != "strong"

    async def test_flat_series_is_low(self) -> None:
        aligned = await fetch(_Adapter(values=[5.0] * 20), "syn-subject-001")
        assert (await analyze(aligned))[0].confidence == "low"

    async def test_deterministic_across_calls(self) -> None:
        aligned = await fetch(_Adapter(), "syn-subject-001")
        a = await analyze(aligned)
        b = await analyze(aligned)
        assert a == b


class TestCorrectionsR4:
    def test_all_six_c2_hooks_registered(self) -> None:
        assert set(CORRECTION_HOOKS) == {
            "temporal_dependence",
            "non_stationarity",
            "confounders",
            "causal_direction",
            "honest_inference",
            "data_quality",
        }

    def test_run_corrections_returns_typed_results_in_registry_order(self) -> None:
        results = run_corrections([(1.0, 2.0)])
        assert [r.name for r in results] == list(CORRECTION_HOOKS)
        assert all(isinstance(r, CorrectionResult) for r in results)

    async def test_each_hook_invoked_exactly_once_per_analyze(self, monkeypatch) -> None:
        """R4's spy/call-count contract."""
        calls: dict[str, int] = {name: 0 for name in CORRECTION_HOOKS}

        def spying(name, original):
            def hook(pairs):
                calls[name] += 1
                return original(pairs)

            return hook

        monkeypatch.setattr(
            corrections_mod,
            "CORRECTION_HOOKS",
            {n: spying(n, h) for n, h in CORRECTION_HOOKS.items()},
        )
        aligned = await fetch(_Adapter(), "syn-subject-001")
        await analyze(aligned)
        assert all(count == 1 for count in calls.values()), calls

    async def test_correction_results_thread_into_the_fit_block(self) -> None:
        aligned = await fetch(_Adapter(), "syn-subject-001")
        sig = (await analyze(aligned))[0]
        assert [c.name for c in sig.fit.corrections] == list(CORRECTION_HOOKS)


class TestManageR3R5R10:
    async def _candidate(self, **adapter_kw):
        aligned = await fetch(_Adapter(**adapter_kw), "syn-subject-001")
        return (await analyze(aligned))[0]

    async def test_add_mid_or_strong_emits_persistence_ready_spec(self) -> None:
        sig = await self._candidate()
        out = await manage("add", sig)
        assert out.accepted and out.spec is not None
        assert out.parameterization is not None
        assert out.parameterization["form"] == "linear_standardized"
        assert out.parameterization["caveats"]  # R5: caveats present

    async def test_add_low_is_refused_ephemeral(self) -> None:
        sig = await self._candidate(values=[5.0] * 20)
        assert sig.confidence == "low"
        out = await manage("add", sig)
        assert not out.accepted and out.spec is None

    async def test_subtract_always_accepted_without_gate(self) -> None:
        sig = await self._candidate()
        out = await manage("subtract", sig)
        assert out.accepted and out.spec is None

    async def test_banned_lexicon_label_fails_the_persist_gate(self) -> None:
        """R10 — the L0-deferred persist-side claim-gate pin lands here."""
        sig = await self._candidate()
        bad = sig.model_copy(update={"label": "X improves sleep quality"})
        with pytest.raises(ClaimScopeError):
            await manage("add", bad)

    async def test_parameterization_round_trips_and_tolerates_unknown_form(self) -> None:
        """R5 (SDK side): the C4 JSONB round-trips; a future unknown ``form``
        stores+reads WITHOUT migration — the dict layer is form-agnostic and
        only the typed re-validation pins today's literal."""
        import json

        from syntropy.lhm.metrics.spec import MetricSpec

        sig = await self._candidate()
        out = await manage("add", sig)
        blob = json.dumps(out.parameterization)
        restored = json.loads(blob)
        assert MetricSpec.model_validate(restored) == out.spec  # round-trip
        future = dict(restored, form="spline_v2")  # unknown future form
        rt = json.loads(json.dumps(future))
        assert rt["form"] == "spline_v2"  # stores+reads at the JSONB layer
        with pytest.raises(Exception):
            MetricSpec.model_validate(rt)  # today's typed layer pins the literal


class TestQgRoundAdditions:
    """QG round 2026-07-10 — the review's HIGH + MEDIUM regressions pinned."""

    async def test_no_orders_returns_empty_not_indexerror(self) -> None:
        """HIGH: outcome series with zero orders is a normal early-adoption
        state — fetch returns () instead of emitting un-analyzable data."""

        class NoOrders(_Adapter):
            async def read_orders(self, user_scope):
                return []

        aligned = await fetch(NoOrders(), "syn-subject-001")
        assert aligned == ()
        assert await analyze(aligned) == ()

    async def test_naive_timestamp_from_adapter_fails_loud(self) -> None:
        from syntropy.lhm.models.mapping import MappingError

        class NaiveSeries(_Adapter):
            async def read_outcome_series(self, user_scope):
                rec = _series([1.0, 2.0])
                naive = SeriesRecord(
                    subject_ref=rec.subject_ref,
                    metric_display=rec.metric_display,
                    unit=rec.unit,
                    code=rec.code,
                    points=(
                        SeriesPoint(observed_at=datetime(2026, 1, 1), value=1.0),  # naive
                    ),
                )
                return [naive]

        with pytest.raises(MappingError, match="timezone-aware"):
            await fetch(NaiveSeries(), "syn-subject-001")

    async def test_slug_equal_series_get_distinct_metric_keys(self) -> None:
        class TwoSeries(_Adapter):
            async def read_outcome_series(self, user_scope):
                return [
                    _series([2.0] * 10 + [8.0] * 10, display="Sleep quality"),
                    _series([2.0] * 10 + [8.0] * 10, display="Sleep-quality"),
                ]

        aligned = await fetch(TwoSeries(), "syn-subject-001")
        signals = await analyze(aligned)
        keys = [s.metric_key for s in signals]
        assert len(set(keys)) == 2, f"metric_key collision: {keys}"

    async def test_non_ascii_series_name_yields_valid_metric_key(self) -> None:
        class Unicode(_Adapter):
            async def read_outcome_series(self, user_scope):
                return [_series([2.0] * 10 + [8.0] * 10, display="Énergie récupération")]

        aligned = await fetch(Unicode(), "syn-subject-001")
        sig = (await analyze(aligned))[0]
        out = await manage("add", sig)  # must NOT ValidationError at manage
        assert out.accepted
        import re

        assert re.fullmatch(r"[a-z][a-z0-9_]*", sig.metric_key), sig.metric_key

    async def test_forged_persistable_tier_on_low_fit_is_refused(self) -> None:
        """LOW→enforced: manage re-derives the tier from fit_score — a
        hand-built mid on a sub-threshold fit doesn't persist."""
        sig = await TestManageR3R5R10()._candidate(values=[5.0] * 20)  # low fit
        forged = sig.model_copy(update={"confidence": "mid"})
        out = await manage("add", forged)
        assert not out.accepted and "re-derives" in out.reason

    async def test_checkin_sentiment_series_kind_mapped(self) -> None:
        class Sentiment(_Adapter):
            async def read_outcome_series(self, user_scope):
                rec = _series([2.0] * 10 + [8.0] * 10)
                return [
                    SeriesRecord(
                        subject_ref=rec.subject_ref,
                        metric_display="Check-in sentiment",
                        unit="",
                        code="checkin-sentiment",
                        points=rec.points,
                    )
                ]

        aligned = await fetch(Sentiment(), "syn-subject-001")
        assert aligned[0].outcome.kind == "checkin_sentiment"
