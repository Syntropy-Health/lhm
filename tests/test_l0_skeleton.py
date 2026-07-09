"""LHM L0 gate tests — package boundary, shapes, claim guard, viz contract.

Runs from the app workspace (``uv run pytest LHM/tests``) AND standalone in a
clean env (the R7 install test reuses it). No app imports, no DB, no network.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from lhm.contracts import viz as viz_mod
from lhm.metrics.claims import BANNED_LEXICON, ClaimScopeError, assert_claim_safe
from lhm.metrics.spec import FitBlock, InputRef, MetricSpec, OutcomeRef
from pydantic import ValidationError

import lhm

_FIXTURES = Path(viz_mod.__file__).parent / "fixtures"

_SAFE_CAVEATS = (
    "Personal n-of-1 association from your own records — observational, not causal.",
    "Estimates carry an uncertainty interval and may not generalize.",
)


def _spec(**over) -> MetricSpec:
    base = dict(
        metric_key="omega3_sleep",
        label="Omega-3 intake and sleep duration — personal trend",
        inputs=(InputRef(resource="NutritionOrder", code="omega-3", display="Omega-3"),),
        outcome=OutcomeRef(kind="journal_series", series_key="sleep_duration"),
        confidence="mid",
        caveats=_SAFE_CAVEATS,
        rx_input=False,
    )
    base.update(over)
    return MetricSpec(**base)


# --------------------------------------------------------------------------- #
# D1 — app-independence (the no-app-imports HARD gate)
# --------------------------------------------------------------------------- #
class TestNoAppImports:
    def test_lhm_pulls_no_app_frameworks(self) -> None:
        """Importing every lhm module must not load Reflex/SQLModel/the app.

        Run in a SUBPROCESS so the assertion sees a clean interpreter (the
        surrounding test session may itself have app modules loaded).
        """
        code = (
            "import sys\n"
            "import lhm, lhm.metrics.spec, lhm.metrics.claims, lhm.contracts.viz, lhm.models\n"
            "banned = {'reflex', 'sqlmodel', 'syntropy_journals', 'sqlalchemy'}\n"
            "loaded = {m.split('.')[0] for m in sys.modules}\n"
            "hit = banned & loaded\n"
            "assert not hit, f'lhm imported app frameworks: {hit}'\n"
            "print('clean')\n"
        )
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False, env=env
        )
        assert out.returncode == 0, out.stderr
        assert "clean" in out.stdout


# --------------------------------------------------------------------------- #
# D4 — MetricSpec shapes
# --------------------------------------------------------------------------- #
class TestMetricSpec:
    def test_valid_spec_constructs_and_is_frozen(self) -> None:
        spec = _spec()
        assert spec.form == "linear_standardized"
        with pytest.raises(ValidationError):
            spec.metric_key = "other"  # type: ignore[misc]

    def test_caveats_required(self) -> None:
        with pytest.raises(ValidationError):
            _spec(caveats=())

    def test_inputs_required(self) -> None:
        with pytest.raises(ValidationError):
            _spec(inputs=())

    def test_metric_key_slug_pattern(self) -> None:
        with pytest.raises(ValidationError):
            _spec(metric_key="Not A Slug")

    def test_fit_block_reserves_the_v2_method_slot(self) -> None:
        fit = FitBlock(posterior_mean=0.3, posterior_ci=(0.1, 0.5), n_effective=40, fit_score=0.7)
        assert fit.method == "stub"  # ADR-0002: adam/grid re-enter in v2
        with pytest.raises(ValidationError):
            FitBlock(
                posterior_mean=0.3,
                posterior_ci=(0.1, 0.5),
                n_effective=40,
                fit_score=0.7,
                method="bogus",
            )

    def test_extra_fields_forbidden_on_spec(self) -> None:
        with pytest.raises(ValidationError):
            _spec(unknown_field=1)


# --------------------------------------------------------------------------- #
# R10 — claim-scope guard
# --------------------------------------------------------------------------- #
class TestClaimScopeGuard:
    @pytest.mark.parametrize("word", BANNED_LEXICON)
    def test_banned_lexicon_fails(self, word: str) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe(f"Omega-3 {word}s sleep", _SAFE_CAVEATS, "mid", False)

    def test_banned_word_inside_caveat_fails(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe(
                "Omega-3 and sleep — personal trend",
                (_SAFE_CAVEATS[0], "This clinically validates the effect."),
                "mid",
                False,
            )

    def test_missing_association_marker_fails(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("x", ("Estimates carry an uncertainty interval.",), "mid", False)

    def test_missing_uncertainty_marker_fails(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("x", ("A personal n-of-1 association.",), "mid", False)

    def test_safe_text_passes(self) -> None:
        assert_claim_safe("Omega-3 and sleep — personal trend", _SAFE_CAVEATS, "mid", False)

    def test_rx_strong_fails(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("x — personal trend", _SAFE_CAVEATS, "strong", True)

    def test_rx_public_surface_fails(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("x — personal trend", _SAFE_CAVEATS, "mid", True, public_surface=True)

    def test_rx_private_mid_passes(self) -> None:
        assert_claim_safe("x — personal trend", _SAFE_CAVEATS, "mid", True)


# --------------------------------------------------------------------------- #
# D5 — the lhm-viz/v1 contract
# --------------------------------------------------------------------------- #
class TestVizContract:
    def test_export_matches_committed_schema_byte_for_byte(self) -> None:
        assert viz_mod.export_schema() == viz_mod.committed_schema(), (
            "the committed lhm-viz.v1.schema.json drifted from the model — "
            "regenerate with `python -m lhm.contracts.viz` ONLY for an "
            "intentional additive change (breaking → mint v2)"
        )

    def test_valid_fixtures_accepted(self) -> None:
        for f in sorted((_FIXTURES / "valid").glob("*.json")):
            payload = lhm.VizPayload.model_validate(json.loads(f.read_text()))
            assert payload.contract == "lhm-viz/v1", f.name

    def test_invalid_fixtures_rejected(self) -> None:
        for f in sorted((_FIXTURES / "invalid").glob("*.json")):
            with pytest.raises(ValidationError):
                lhm.VizPayload.model_validate(json.loads(f.read_text()))

    def test_tolerant_reader_ignores_unknown_fields(self) -> None:
        data = json.loads((_FIXTURES / "valid" / "no-regimen-extra-field.json").read_text())
        payload = lhm.VizPayload.model_validate(data)
        assert not hasattr(payload, "future_additive_field")

    def test_to_viz_payload_is_the_render_gate(self) -> None:
        """QGR D5/F8: the payload builder re-runs the claim guard (fail-closed)."""
        spec = _spec(label="Omega-3 improves sleep")
        with pytest.raises(ClaimScopeError):
            viz_mod.to_viz_payload(spec, [])

    def test_to_viz_payload_carries_rx_and_regimen(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        spec = _spec(rx_input=True)
        payload = viz_mod.to_viz_payload(
            spec,
            [viz_mod.VizPoint(ts=start, value=7.4)],
            regimen_start=start,
        )
        assert payload.rx_input is True
        assert payload.regimen_start == start

    def test_rx_payload_never_builds_for_public_surface(self) -> None:
        spec = _spec(rx_input=True)
        with pytest.raises(ClaimScopeError):
            viz_mod.to_viz_payload(spec, [], public_surface=True)


# --------------------------------------------------------------------------- #
# D2 — the L0 FHIR decisions
# --------------------------------------------------------------------------- #
class TestFhirDecisions:
    def test_emit_line_is_r4b(self) -> None:
        from lhm.models import FHIR_VERSION

        assert FHIR_VERSION == "R4B"

    def test_pinned_fhir_lib_imports_r4b(self) -> None:
        """The pinned dep actually provides the chosen emit line."""
        from fhir.resources.R4B.observation import Observation  # noqa: F401
