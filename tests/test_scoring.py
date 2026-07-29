"""HD-4 — ``[lhm-scoring]`` scoring engine + stub-fails-readiness (A&D D-D, §2.4, §3.3, R3).

Covers:
- guarded import: the extra missing → ``SCORING_AVAILABLE is False`` and the
  module still imports (subprocess-poisoned so this interpreter stays clean);
- the four accepted v1 estimators — known fixture → known score, method
  provenance recorded, typed-input-only signatures (no ``str`` corpus param);
- property-style bounds (seeded-random fixtures — hypothesis is not a repo
  dep, so a deterministic seeded sweep stands in): every score ∈
  [score_min, score_max]; engineered out-of-range raw statistic → clipped
  AND ``params["clipped"] is True``, never raw;
- the analyze seam: extra present → ``FitBlock.method`` records the real
  estimator name; extra absent → the deterministic stub with ``"stub"``;
- **stub-fails-readiness**: ``is_ready`` is False for ANY stub-method score
  regardless of every other conjunct (the R5b fail-closed rule), and every
  conjunct is falsified independently.
"""

from __future__ import annotations

import inspect
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest

from syntropy.lhm.metrics import scoring
from syntropy.lhm.metrics.scoring import (
    DEFAULT_N_MIN,
    MIN_DISTINCT_DAYS,
    N_MIN,
    SCORE_MAX,
    SCORE_MIN,
    V1_DOMAINS,
    DomainScore,
    is_ready,
    score_consistency,
    score_domain,
    score_nutrition_regularity,
    score_sentiment_trajectory,
    score_symptom_load,
)

AS_OF = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)


def _days_ago(n: float) -> datetime:
    return AS_OF - timedelta(days=n)


# --------------------------------------------------------------------------- #
# Guarded import — the extra-absent degradation contract
# --------------------------------------------------------------------------- #


class TestGuardedImport:
    def test_extra_present_in_this_env(self) -> None:
        """The suite env installs the extra — the real-estimator path runs."""
        assert scoring.SCORING_AVAILABLE is True

    def test_poisoned_statsmodels_degrades_not_crashes(self) -> None:
        """statsmodels poisoned out of sys.modules → import still succeeds,
        SCORING_AVAILABLE is False, and score_domain returns method='stub'.

        Subprocess-isolated so this interpreter's module state stays clean.
        """
        code = (
            "import sys; sys.modules['statsmodels'] = None;"
            "import syntropy.lhm.metrics.scoring as s;"
            "assert s.SCORING_AVAILABLE is False, 'flag did not degrade';"
            "from datetime import datetime, timezone;"
            "d = s.score_domain('consistency',"
            " [(datetime(2026, 7, 20, tzinfo=timezone.utc), 1.0)],"
            " as_of=datetime(2026, 7, 28, tzinfo=timezone.utc));"
            "assert d.method == 'stub', d.method;"
            "assert s.SCORE_MIN <= d.score <= s.SCORE_MAX;"
            "print('DEGRADED-OK')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, f"stderr: {out.stderr}"
        assert "DEGRADED-OK" in out.stdout


# --------------------------------------------------------------------------- #
# The four accepted v1 estimators (A&D §3.3)
# --------------------------------------------------------------------------- #


class TestConsistency:
    def test_known_fixture_binomial_wilson(self) -> None:
        # 21 adherence events in the 28d window: 18 taken, 3 skipped.
        samples = [(_days_ago(i + 1), 1.0 if i < 18 else 0.0) for i in range(21)]
        d = score_consistency(samples, as_of=AS_OF)
        assert d.domain == "consistency"
        assert d.method == "binomial_wilson"
        assert d.score == pytest.approx(100.0 * 18 / 21)
        assert d.n_effective == 21
        # Wilson interval matches statsmodels' own computation.
        from statsmodels.stats.proportion import proportion_confint

        lo, hi = proportion_confint(18, 21, alpha=0.05, method="wilson")
        assert d.interval is not None
        assert d.interval[0] == pytest.approx(100.0 * lo)
        assert d.interval[1] == pytest.approx(100.0 * hi)
        assert d.params["distinct_days"] == 21

    def test_outside_window_samples_are_excluded(self) -> None:
        samples = [(_days_ago(1), 1.0), (_days_ago(60), 0.0)]
        d = score_consistency(samples, as_of=AS_OF)
        assert d.n_effective == 1
        assert d.score == pytest.approx(100.0)

    def test_no_samples_scores_zero_and_never_ready(self) -> None:
        d = score_consistency([], as_of=AS_OF)
        assert d.n_effective == 0
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_naive_timestamp_rejected(self) -> None:
        with pytest.raises(ValueError, match="aware"):
            score_consistency([(datetime(2026, 7, 20), 1.0)], as_of=AS_OF)


class TestSentimentTrajectory:
    def test_flat_series_is_neutral_50(self) -> None:
        samples = [(_days_ago(27 - i), 0.2) for i in range(28)]
        d = score_sentiment_trajectory(samples, as_of=AS_OF)
        assert d.method == "robust_z_theil_sen"
        assert d.score == pytest.approx(50.0)

    def test_rising_series_scores_above_neutral(self) -> None:
        samples = [(_days_ago(27 - i), -0.5 + i * (1.0 / 27)) for i in range(28)]
        d = score_sentiment_trajectory(samples, as_of=AS_OF)
        assert d.score > 50.0
        assert d.score <= SCORE_MAX

    def test_falling_series_scores_below_neutral(self) -> None:
        samples = [(_days_ago(27 - i), 0.5 - i * (1.0 / 27)) for i in range(28)]
        d = score_sentiment_trajectory(samples, as_of=AS_OF)
        assert d.score < 50.0
        assert d.score >= SCORE_MIN


class TestNutritionRegularity:
    def test_perfectly_regular_logging_scores_100(self) -> None:
        # Two logs on EACH of the 28 calendar dates ending at as_of
        # (the estimator's day grid) → daily-count CV 0 → inverted 100.
        samples = [
            (AS_OF - timedelta(days=day, hours=h), 1.0) for day in range(28) for h in (1, 11)
        ]
        d = score_nutrition_regularity(samples, as_of=AS_OF)
        assert d.method == "inverse_cv"
        assert d.score == pytest.approx(100.0)
        assert d.n_effective == 56

    def test_sparse_burst_logging_scores_low_with_clip_flag(self) -> None:
        # 10 logs on ONE day, nothing else in the window → CV >> 1 → clipped.
        samples = [(_days_ago(14) + timedelta(hours=h), 1.0) for h in range(10)]
        d = score_nutrition_regularity(samples, as_of=AS_OF)
        assert d.score == pytest.approx(0.0)
        assert d.params["clipped"] is True


class TestSymptomLoad:
    def test_all_mild_scores_high(self) -> None:
        samples = [(_days_ago(i + 1), 1.0) for i in range(14)]
        d = score_symptom_load(samples, as_of=AS_OF)
        assert d.method == "ew_mean_inverted"
        assert d.score == pytest.approx(100.0)

    def test_all_severe_scores_zero(self) -> None:
        samples = [(_days_ago(i + 1), 3.0) for i in range(14)]
        d = score_symptom_load(samples, as_of=AS_OF)
        assert d.score == pytest.approx(0.0)

    def test_out_of_ordinal_range_input_is_clipped_and_flagged(self) -> None:
        samples = [(_days_ago(i + 1), 9.0) for i in range(14)]
        d = score_symptom_load(samples, as_of=AS_OF)
        assert d.score == pytest.approx(0.0)  # clipped, never raw-negative
        assert d.params["clipped"] is True

    def test_no_symptoms_reported_scores_max(self) -> None:
        d = score_symptom_load([], as_of=AS_OF)
        assert d.score == pytest.approx(SCORE_MAX)
        assert d.n_effective == 0


class TestTypedInputOnlyApi:
    def test_no_estimator_takes_a_str_corpus_param(self) -> None:
        """R3: typed samples in, DomainScore out — no free-text parameter."""
        for fn in (
            score_consistency,
            score_sentiment_trajectory,
            score_nutrition_regularity,
            score_symptom_load,
        ):
            for name, p in inspect.signature(fn).parameters.items():
                assert p.annotation is not str and "str" != str(p.annotation), (
                    f"{fn.__name__}({name}: str) — a str param invites raw text "
                    "into the stats path (A&D §2.3: never raw text)"
                )

    def test_score_domain_rejects_unknown_domain(self) -> None:
        with pytest.raises(KeyError):
            score_domain("vibes", [], as_of=AS_OF)

    def test_v1_domain_set_is_exactly_the_accepted_four(self) -> None:
        assert set(V1_DOMAINS) == {
            "consistency",
            "sentiment_trajectory",
            "nutrition_regularity",
            "symptom_load",
        }


# --------------------------------------------------------------------------- #
# QG cluster 2 — degenerate/non-finite guards + window/day-grid agreement
# --------------------------------------------------------------------------- #


class TestNonFiniteAndDegenerateGuards:
    @pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_sample_values_are_rejected_loudly(self, bad: float) -> None:
        """QG 2b: a nan/inf sample must never flow into an estimator —
        pre-fix a nan sample saturated the logistic to 99.97 ready=True."""
        for fn in (
            score_consistency,
            score_sentiment_trajectory,
            score_nutrition_regularity,
            score_symptom_load,
        ):
            with pytest.raises(ValueError, match="finite"):
                fn([(_days_ago(1), bad), (_days_ago(2), 1.0)], as_of=AS_OF)

    def test_identical_timestamps_degenerate_not_max(self) -> None:
        """QG 2a: Theil–Sen over identical timestamps yields a nan slope;
        pre-fix ``min(8.0, nan) == 8.0`` scored 99.97. Post-fix: neutral
        50 + ``params['degenerate']`` and NEVER ready."""
        t = _days_ago(3)
        samples = [(t, float(v)) for v in range(10)]
        d = score_sentiment_trajectory(samples, as_of=AS_OF)
        assert d.score == pytest.approx(50.0)
        assert d.params.get("degenerate") is True
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_mad_zero_is_degenerate_neutral_not_saturated(self) -> None:
        """QG 2c: MAD==0 must not fall back to a 1e-9 epsilon divisor —
        pre-fix a 0.0001 wiggle over a flat baseline scored 99.97."""
        samples = [(_days_ago(27 - i), 0.5) for i in range(27)] + [(_days_ago(0), 0.5001)]
        d = score_sentiment_trajectory(samples, as_of=AS_OF)
        assert d.score == pytest.approx(50.0)
        assert d.params.get("degenerate") is True
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_window_and_day_grid_agree_no_off_by_one(self) -> None:
        """QG 2g: the estimator day-grid covers ``window_days`` calendar
        dates — a sample whose calendar date falls OUTSIDE that grid must
        not survive ``_window`` (pre-fix: 29 dates kept vs 28 counted)."""
        samples = [(AS_OF - timedelta(days=28), 1.0), (_days_ago(1), 1.0)]
        d = score_nutrition_regularity(samples, as_of=AS_OF)
        assert d.n_effective == 1

    def test_earliest_grid_date_is_kept_regardless_of_time_of_day(self) -> None:
        """The date-grid boundary is calendar-date based: a sample 27 days
        and 11 hours ago still lands on the oldest grid date and counts."""
        samples = [(AS_OF - timedelta(days=27, hours=11), 1.0), (_days_ago(1), 1.0)]
        d = score_nutrition_regularity(samples, as_of=AS_OF)
        assert d.n_effective == 2


class TestBrokenExtraDegradesToStub:
    def test_importable_but_broken_extra_falls_back_to_stub(self, tmp_path) -> None:
        """QG 2e: ``find_spec`` proves importABILITY, not import success.
        A broken statsmodels/scipy on the path must degrade to the
        recorded stub, never crash the scorer."""
        for pkg in ("statsmodels", "scipy"):
            (tmp_path / pkg).mkdir()
            (tmp_path / pkg / "__init__.py").write_text("raise ImportError('broken wheel')\n")
        code = (
            f"import sys; sys.path.insert(0, {str(tmp_path)!r});"
            "import syntropy.lhm.metrics.scoring as s;"
            "assert s.SCORING_AVAILABLE is True, 'probe should see the broken pkg';"
            "from datetime import datetime, timezone, timedelta;"
            "as_of = datetime(2026, 7, 28, tzinfo=timezone.utc);"
            "d = s.score_domain('consistency',"
            " [(as_of - timedelta(days=1), 1.0)], as_of=as_of);"
            "assert d.method == 'stub', d.method;"
            "d2 = s.score_domain('sentiment_trajectory',"
            " [(as_of - timedelta(days=i), float(i % 3)) for i in range(1, 10)],"
            " as_of=as_of);"
            "assert d2.method == 'stub', d2.method;"
            "print('BROKEN-EXTRA-OK')"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
        )
        assert out.returncode == 0, f"stderr: {out.stderr}"
        assert "BROKEN-EXTRA-OK" in out.stdout


# --------------------------------------------------------------------------- #
# QG 2h — end-to-end readiness + real-estimator value pins
# --------------------------------------------------------------------------- #


class TestEndToEndReadiness:
    def test_all_four_domains_reach_ready_from_20_days_of_plausible_samples(self) -> None:
        """distinct_days threads through EVERY estimator and a plausible
        20-day series clears every readiness conjunct for all 4 domains."""
        fixtures: dict[str, list] = {
            "consistency": [(_days_ago(i), 1.0 if i % 5 else 0.0) for i in range(1, 21)],
            "sentiment_trajectory": [
                (_days_ago(i), 0.1 * (20 - i) + (0.05 if i % 2 else -0.05)) for i in range(1, 21)
            ],
            "nutrition_regularity": [(_days_ago(i), 1.0) for i in range(1, 21)],
            "symptom_load": [(_days_ago(i), float(1 + (i % 3))) for i in range(1, 21)],
        }
        for domain, samples in fixtures.items():
            d = score_domain(domain, samples, as_of=AS_OF)
            assert d.method != "stub", (domain, d.method)
            assert d.params["distinct_days"] == 20, (domain, d.params)
            assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is True, (
                domain,
                d,
            )


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    vx = sum((a - mx) ** 2 for a in xs)
    vy = sum((b - my) ** 2 for b in ys)
    return cov / (vx**0.5 * vy**0.5)


def _noisy_aligned(*, flip: bool = False, constant_outcome: bool = False):
    from syntropy.lhm.metrics.spec import InputRef, OutcomeRef
    from syntropy.lhm.tools.pipeline import AlignedData, AlignedPair

    rng = random.Random(7)
    day0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pairs = []
    for i in range(30):
        active = 0.0 if i < 15 else 1.0
        if constant_outcome:
            y = 3.0
        else:
            y = 1.0 + active * 1.5 + rng.uniform(-0.5, 0.5)
            if flip:
                y = -y
        pairs.append(AlignedPair(at=day0 + timedelta(days=i), input_active=active, outcome_value=y))
    return AlignedData(
        user_scope="user_x",
        inputs=(InputRef(resource="NutritionOrder", code="supplement-order", display="Mg"),),
        outcome=OutcomeRef(kind="journal_series", series_key="Sleep quality"),
        regimen_start=day0 + timedelta(days=15),
        pairs=tuple(pairs),
        rx_input=False,
    )


class TestRealEstimatorValuePins:
    async def test_posterior_mean_is_pearson_r_on_the_fixture(self) -> None:
        from syntropy.lhm.tools.pipeline import analyze

        data = _noisy_aligned()
        (candidate,) = await analyze([data])
        assert candidate.fit.method == "ols_standardized"
        xs = [p.input_active for p in data.pairs]
        ys = [p.outcome_value for p in data.pairs]
        r = _pearson(xs, ys)
        assert candidate.fit.posterior_mean == pytest.approx(r, rel=1e-9)
        assert -1.0 <= candidate.fit.posterior_mean <= 1.0

    async def test_sign_flip_flips_the_posterior_mean(self) -> None:
        from syntropy.lhm.tools.pipeline import analyze

        (pos,) = await analyze([_noisy_aligned()])
        (neg,) = await analyze([_noisy_aligned(flip=True)])
        assert neg.fit.posterior_mean == pytest.approx(-pos.fit.posterior_mean, rel=1e-9)

    async def test_constant_outcome_var_zero_degrades_to_stub(self) -> None:
        from syntropy.lhm.tools.pipeline import analyze

        (candidate,) = await analyze([_noisy_aligned(constant_outcome=True)])
        assert candidate.fit.method == "stub"

    async def test_real_ci_derives_from_model_conf_int_not_fabricated(self) -> None:
        """QG 2f: on the real path the posterior_ci is the standardized
        OLS conf_int, not the stub's ±half-width fabrication."""
        import statsmodels.api as sm

        from syntropy.lhm.tools.pipeline import analyze

        data = _noisy_aligned()
        (candidate,) = await analyze([data])
        xs = [p.input_active for p in data.pairs]
        ys = [p.outcome_value for p in data.pairs]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        vx = sum((a - mx) ** 2 for a in xs)
        vy = sum((b - my) ** 2 for b in ys)
        scale = (vx**0.5) / (vy**0.5)
        model = sm.OLS(ys, sm.add_constant(xs)).fit()
        lo, hi = (float(c) for c in model.conf_int()[1])
        expected = (
            max(-1.0, min(1.0, lo * scale)),
            max(-1.0, min(1.0, hi * scale)),
        )
        assert candidate.fit.posterior_ci == pytest.approx(expected, rel=1e-9)

    async def test_provisional_stub_caveat_only_on_the_stub_path(self, monkeypatch) -> None:
        """QG 2f: the 'stub estimate / provisional interval' caveat must not
        ride a REAL-estimator candidate; it stays on the stub path."""
        import syntropy.lhm.tools.pipeline as pipeline

        (real,) = await pipeline.analyze([_noisy_aligned()])
        assert real.fit.method == "ols_standardized"
        assert not any("stub" in c.lower() for c in real.caveats), real.caveats
        assert any("interval" in c.lower() for c in real.caveats)

        monkeypatch.setattr(pipeline.scoring, "SCORING_AVAILABLE", False)
        (stub,) = await pipeline.analyze([_noisy_aligned()])
        assert stub.fit.method == "stub"
        assert any("stub" in c.lower() for c in stub.caveats), stub.caveats


# --------------------------------------------------------------------------- #
# Property-style bounds (seeded deterministic sweep — no hypothesis dep)
# --------------------------------------------------------------------------- #


class TestScoreBounds:
    def test_every_generated_fixture_scores_in_declared_range(self) -> None:
        rng = random.Random(20260728)
        for case in range(50):
            n = rng.randint(0, 60)
            for domain in V1_DOMAINS:
                lo, hi = {
                    "consistency": (0.0, 1.0),
                    "sentiment_trajectory": (-3.0, 3.0),
                    "nutrition_regularity": (0.5, 5.0),
                    "symptom_load": (0.0, 12.0),  # deliberately exceeds ordinal 3
                }[domain]
                samples = [(_days_ago(rng.uniform(0, 40)), rng.uniform(lo, hi)) for _ in range(n)]
                d = score_domain(domain, samples, as_of=AS_OF)
                assert d.score_min == SCORE_MIN and d.score_max == SCORE_MAX
                assert SCORE_MIN <= d.score <= SCORE_MAX, (
                    f"case {case} {domain}: {d.score} out of range (params={d.params})"
                )
                if d.interval is not None:
                    assert d.interval[0] <= d.interval[1]


# --------------------------------------------------------------------------- #
# The analyze seam — real estimator name vs stub (pipeline.py)
# --------------------------------------------------------------------------- #


def _aligned_fixture():
    from syntropy.lhm.metrics.spec import InputRef, OutcomeRef
    from syntropy.lhm.tools.pipeline import AlignedData, AlignedPair

    day0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
    pairs = tuple(
        AlignedPair(
            at=day0 + timedelta(days=i),
            input_active=0.0 if i < 5 else 1.0,
            outcome_value=1.0 if i < 5 else 2.0,
        )
        for i in range(10)
    )
    return AlignedData(
        user_scope="user_x",
        inputs=(InputRef(resource="NutritionOrder", code="supplement-order", display="Mg"),),
        outcome=OutcomeRef(kind="journal_series", series_key="Sleep quality"),
        regimen_start=day0 + timedelta(days=5),
        pairs=pairs,
        rx_input=False,
    )


class TestAnalyzeSeam:
    async def test_extra_present_records_the_real_estimator_name(self) -> None:
        from syntropy.lhm.tools.pipeline import analyze

        (candidate,) = await analyze([_aligned_fixture()])
        assert candidate.fit.method == "ols_standardized"

    async def test_extra_absent_runs_the_stub_and_records_it(self, monkeypatch) -> None:
        import syntropy.lhm.tools.pipeline as pipeline

        monkeypatch.setattr(pipeline.scoring, "SCORING_AVAILABLE", False)
        (candidate,) = await analyze_via(pipeline)
        assert candidate.fit.method == "stub"

    async def test_degenerate_contrast_falls_back_to_stub_honestly(self) -> None:
        """One empty activity group → no real fit exists; record 'stub'."""
        from syntropy.lhm.metrics.spec import InputRef, OutcomeRef
        from syntropy.lhm.tools.pipeline import AlignedData, AlignedPair, analyze

        day0 = datetime(2026, 6, 1, tzinfo=timezone.utc)
        data = AlignedData(
            user_scope="user_x",
            inputs=(InputRef(resource="NutritionOrder", code="supplement-order"),),
            outcome=OutcomeRef(kind="journal_series", series_key="s"),
            regimen_start=day0,
            pairs=tuple(
                AlignedPair(at=day0 + timedelta(days=i), input_active=1.0, outcome_value=1.0)
                for i in range(4)
            ),
            rx_input=False,
        )
        (candidate,) = await analyze([data])
        assert candidate.fit.method == "stub"


async def analyze_via(pipeline_module):
    return await pipeline_module.analyze([_aligned_fixture()])


# --------------------------------------------------------------------------- #
# Readiness — stub fails CLOSED (R5b; consumed by HD-7)
# --------------------------------------------------------------------------- #


def _ready_score(**overrides) -> DomainScore:
    kwargs = dict(
        domain="consistency",
        score=80.0,
        score_min=SCORE_MIN,
        score_max=SCORE_MAX,
        n_effective=DEFAULT_N_MIN + 10,
        interval=(70.0, 90.0),
        method="binomial_wilson",
        params={"distinct_days": MIN_DISTINCT_DAYS + 7},
    )
    kwargs.update(overrides)
    return DomainScore(**kwargs)


class TestStubFailsReadiness:
    def test_fully_qualified_real_score_is_ready(self) -> None:
        assert is_ready(_ready_score(), claim_gate_passed=True, valence_gate_passed=True)

    def test_a_stub_score_NEVER_passes_readiness(self) -> None:
        """The fail-closed pin: method='stub' → not ready, regardless of
        every other conjunct being maximally satisfied."""
        stub = _ready_score(method="stub", n_effective=10_000, params={"distinct_days": 10_000})
        assert is_ready(stub, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_scoring_unavailable_fails_readiness(self, monkeypatch) -> None:
        monkeypatch.setattr(scoring, "SCORING_AVAILABLE", False)
        assert is_ready(_ready_score(), claim_gate_passed=True, valence_gate_passed=True) is False

    def test_insufficient_n_effective_fails(self) -> None:
        d = _ready_score(n_effective=N_MIN["consistency"] - 1)
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_insufficient_distinct_days_fails(self) -> None:
        d = _ready_score(params={"distinct_days": MIN_DISTINCT_DAYS - 1})
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_missing_distinct_days_fails_closed(self) -> None:
        d = _ready_score(params={})
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_out_of_range_score_fails(self) -> None:
        d = _ready_score(score=101.0)
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_claim_gate_not_passed_fails(self) -> None:
        assert is_ready(_ready_score(), claim_gate_passed=False, valence_gate_passed=True) is False

    def test_valence_gate_not_passed_fails(self) -> None:
        assert is_ready(_ready_score(), claim_gate_passed=True, valence_gate_passed=False) is False

    def test_gates_default_to_not_passed(self) -> None:
        """Omitting the gate flags can never make a score ready (fail-closed)."""
        assert is_ready(_ready_score()) is False

    def test_unknown_domain_fails_closed(self) -> None:
        d = _ready_score(domain="vibes")
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    # -- QG 2d: the new conjuncts, each falsified independently ------------- #

    def test_degenerate_flag_fails_readiness(self) -> None:
        d = _ready_score(params={"distinct_days": MIN_DISTINCT_DAYS + 7, "degenerate": True})
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_clipped_flag_fails_readiness(self) -> None:
        """A clipped score means the raw statistic left the declared range —
        that number is not surfaceable (QG 2d decision: clipped blocks)."""
        d = _ready_score(params={"distinct_days": MIN_DISTINCT_DAYS + 7, "clipped": True})
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_below_range_score_fails(self) -> None:
        """The LOWER bound is pinned too (pre-fix only 101.0 was tested)."""
        d = _ready_score(score=-1.0)
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_spoofed_self_declared_bounds_fail(self) -> None:
        """QG 2d: bounds are checked against the MODULE constants — a score
        carrying its own widened score_min/score_max cannot self-ratify."""
        d = _ready_score(score=-1.0, score_min=-50.0)
        assert is_ready(d, claim_gate_passed=True, valence_gate_passed=True) is False
        d2 = _ready_score(score=150.0, score_max=200.0)
        assert is_ready(d2, claim_gate_passed=True, valence_gate_passed=True) is False

    def test_domain_override_branch(self) -> None:
        """The ``domain=`` override is the lookup key: a valid override
        rescues an oddly-labelled score; an invalid override fails closed."""
        odd = _ready_score(domain="not_a_domain")
        assert (
            is_ready(odd, "consistency", claim_gate_passed=True, valence_gate_passed=True) is True
        )
        assert (
            is_ready(_ready_score(), "vibes", claim_gate_passed=True, valence_gate_passed=True)
            is False
        )
