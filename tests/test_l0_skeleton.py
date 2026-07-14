"""LHM L0 gate tests — package boundary, shapes, claim guard, viz contract.

Runs from the app workspace (``uv run pytest LHM/tests``) AND standalone in a
clean env (the R7 install test reuses it). No app imports, no DB, no network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from syntropy import lhm
from syntropy.lhm.contracts import viz as viz_mod
from syntropy.lhm.metrics.claims import BANNED_LEXICON, ClaimScopeError, assert_claim_safe
from syntropy.lhm.metrics.spec import FitBlock, InputRef, MetricSpec, OutcomeRef

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
            "import syntropy.lhm, syntropy.lhm.metrics.spec, syntropy.lhm.metrics.claims, "
            "syntropy.lhm.contracts.viz, syntropy.lhm.models\n"
            "banned = {'reflex', 'sqlmodel', 'syntropy_journals', 'sqlalchemy'}\n"
            "loaded = {m.split('.')[0] for m in sys.modules}\n"
            "hit = banned & loaded\n"
            "assert not hit, f'lhm imported app frameworks: {hit}'\n"
            "print('clean')\n"
        )
        # No forced PYTHONPATH (scorer residual): the subprocess exercises
        # the INSTALLED package, same as TestAppIndependenceAllowlist.
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
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

    # -- persist→render size caps (additive lhm-publish/v1 tightening) ------

    def test_label_length_capped_at_200(self) -> None:
        assert _spec(label="x" * 200).label == "x" * 200
        with pytest.raises(ValidationError):
            _spec(label="x" * 201)

    def test_metric_key_length_capped_at_64(self) -> None:
        assert len(_spec(metric_key="a" * 64).metric_key) == 64
        with pytest.raises(ValidationError):
            _spec(metric_key="a" * 65)

    def test_caveats_count_capped_at_8(self) -> None:
        assert len(_spec(caveats=tuple(f"caveat number {i}" for i in range(8))).caveats) == 8
        with pytest.raises(ValidationError):
            _spec(caveats=tuple(f"caveat number {i}" for i in range(9)))

    def test_caveat_item_length_capped_at_500(self) -> None:
        assert _spec(caveats=("y" * 500,)).caveats == ("y" * 500,)
        with pytest.raises(ValidationError):
            _spec(caveats=("y" * 501,))


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
            "regenerate with `python -m syntropy.lhm.contracts.viz` ONLY for an "
            "intentional additive change (breaking → mint v2)"
        )

    def test_valid_fixtures_accepted(self) -> None:
        files = sorted((_FIXTURES / "valid").glob("*.json"))
        # Count FLOOR (QGR test-F1): an empty/unpackaged fixture dir must RED
        # this test, never pass vacuously.
        assert len(files) >= 2, f"valid fixture set missing/unpackaged: {_FIXTURES}"
        payloads = [lhm.VizPayload.model_validate(json.loads(f.read_text())) for f in files]
        assert all(p.contract == "lhm-viz/v1" for p in payloads)
        # Both regimen_start shapes covered by the pact set.
        assert any(p.regimen_start is not None for p in payloads)
        assert any(p.regimen_start is None for p in payloads)

    def test_invalid_fixtures_rejected(self) -> None:
        files = sorted((_FIXTURES / "invalid").glob("*.json"))
        assert len(files) >= 4, f"invalid fixture set missing/unpackaged: {_FIXTURES}"
        for f in files:
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
        from syntropy.lhm.models import FHIR_VERSION

        assert FHIR_VERSION == "R4B"

    def test_pinned_fhir_lib_imports_r4b(self) -> None:
        """The pinned dep actually provides the chosen emit line."""
        from fhir.resources.R4B.observation import Observation  # noqa: F401


class TestClaimGuardUnicodeHardening:
    """LHM-SEC-01: the denylist folds NFKC + strips zero-width chars."""

    def test_zero_width_insertion_caught(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("Omega-3 im​proves sleep", _SAFE_CAVEATS, "mid", False)

    def test_fullwidth_compat_form_caught(self) -> None:
        # Fullwidth Latin folds to ASCII under NFKC.
        word = "ｉｍｐｒｏｖｅ"  # ｉｍｐｒｏｖｅ
        with pytest.raises(ClaimScopeError):
            assert_claim_safe(f"Omega-3 {word}s sleep", _SAFE_CAVEATS, "mid", False)

    def test_markers_fold_too(self) -> None:
        # An association marker written with a zero-width char still counts.
        assert_claim_safe(
            "Omega-3 and sleep — personal trend",
            ("Personal n-of-1 asso​ciation; uncertainty interval applies.",),
            "mid",
            False,
        )


class TestGuardMatrixCompletion:
    """QGR test-F3/F7/F8/F9: the remaining guard/spec arms."""

    def test_non_rx_public_surface_passes(self) -> None:
        # The 4th Rx matrix arm: public surfaces are fine for NON-Rx metrics.
        assert_claim_safe(
            "Omega-3 and sleep — personal trend",
            _SAFE_CAVEATS,
            "strong",
            False,
            public_surface=True,
        )

    def test_guard_rejects_empty_caveats_directly(self) -> None:
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("x — personal trend", (), "mid", False)

    def test_benign_words_not_false_flagged(self) -> None:
        # \b boundaries: secure/measure/reverence must NOT trip the stems.
        assert_claim_safe(
            "A secure measure of reverence — personal trend",
            _SAFE_CAVEATS,
            "mid",
            False,
        )

    def test_fda_disclaimer_caveat_is_exempt(self) -> None:
        """QGR code-F2: the canonical compliant disclaimer must PASS."""
        assert_claim_safe(
            "Omega-3 and sleep — personal trend",
            (
                *_SAFE_CAVEATS,
                "This product is not intended to diagnose, treat, cure, or prevent any disease.",
            ),
            "mid",
            False,
        )

    def test_bare_clinical_adjective_flagged(self) -> None:
        """QGR code-F5: 'clinical' (not just 'clinically') trips the lexicon."""
        with pytest.raises(ClaimScopeError):
            assert_claim_safe("Clinical-grade omega-3 trend", _SAFE_CAVEATS, "mid", False)

    def test_label_min_length(self) -> None:
        with pytest.raises(ValidationError):
            _spec(label="")


class TestFitBlockConstraints:
    """QGR test-F4: numeric/arity/method-policy pins."""

    def test_negative_n_effective_rejected(self) -> None:
        with pytest.raises(ValidationError):
            FitBlock(posterior_mean=0.3, posterior_ci=(0.1, 0.5), n_effective=-1, fit_score=0.7)

    def test_ci_arity_enforced(self) -> None:
        with pytest.raises(ValidationError):
            FitBlock(posterior_mean=0.3, posterior_ci=(0.1,), n_effective=1, fit_score=0.7)

    def test_adam_grid_accepted_as_reserved_slots(self) -> None:
        """ADR-0002 policy: 'adam'/'grid' VALIDATE today (the reserved v2
        vocabulary) but nothing produces them in v0.1 — acceptance is the
        deliberate contract so v2 estimators need no schema change."""
        for m in ("adam", "grid"):
            fit = FitBlock(
                posterior_mean=0.3,
                posterior_ci=(0.1, 0.5),
                n_effective=1,
                fit_score=0.7,
                method=m,
            )
            assert fit.method == m


class TestPayloadFieldValidation:
    """QGR test-F5: VizPayload/VizPoint field constraints beyond fixtures."""

    def _base(self) -> dict:
        return {
            "contract": "lhm-viz/v1",
            "metric_key": "ok_metric",
            "label": "A personal trend",
            "series": [],
            "confidence": "mid",
            "caveats": ["Observational n-of-1 association; uncertainty interval applies."],
            "rx_input": False,
        }

    def test_bad_metric_key_slug_rejected(self) -> None:
        data = self._base() | {"metric_key": "Not A Slug"}
        with pytest.raises(ValidationError):
            lhm.VizPayload.model_validate(data)

    def test_empty_label_rejected(self) -> None:
        data = self._base() | {"label": ""}
        with pytest.raises(ValidationError):
            lhm.VizPayload.model_validate(data)

    def test_malformed_series_element_rejected(self) -> None:
        data = self._base() | {"series": [{"ts": "not-a-datetime", "value": "NaN-ish"}]}
        with pytest.raises(ValidationError):
            lhm.VizPayload.model_validate(data)

    def test_series_element_missing_value_rejected(self) -> None:
        data = self._base() | {"series": [{"ts": "2026-01-01T00:00:00Z"}]}
        with pytest.raises(ValidationError):
            lhm.VizPayload.model_validate(data)


class TestSchemaRegenHelper:
    """QGR test-F10: the regen flow is deterministic + idempotent."""

    def test_export_is_deterministic(self) -> None:
        assert viz_mod.export_schema() == viz_mod.export_schema()

    def test_regen_roundtrip(self, tmp_path: Path) -> None:
        target = tmp_path / "schema.json"
        target.write_text(viz_mod.export_schema(), encoding="utf-8")
        assert target.read_text(encoding="utf-8") == viz_mod.export_schema()


class TestAppIndependenceAllowlist:
    """QGR test-F6/design-F5: allowlist INVERSION over every lhm submodule."""

    def test_all_lhm_modules_within_allowlist(self) -> None:
        code = (
            "import pkgutil, sys, importlib\n"
            "import syntropy.lhm as lhm\n"
            "for m in pkgutil.walk_packages(lhm.__path__, prefix='syntropy.lhm.'):\n"
            "    importlib.import_module(m.name)\n"
            "allow = set(sys.stdlib_module_names) | {\n"
            "    'syntropy', 'pydantic', 'pydantic_core', 'annotated_types',\n"
            "    'typing_extensions', 'fhir', 'fhir_core', '_virtualenv',\n"
            "    'typing_inspection',\n"
            # L1 note: fhir_core soft-imports PyYAML when present (a
            # try/except optional feature; PyYAML is only fhir_core's
            # test/dev extra, NOT an lhm runtime dep). In the shared app
            # venv PyYAML exists, so 'yaml' (+ its C ext's
            # 'cython_runtime') appears in sys.modules once the L1
            # mapping layer imports fhir.resources. Allowed as fhir_core
            # optional soft-deps — app frameworks stay banned via
            # TestNoAppImports.
            "    'yaml', 'cython_runtime',\n"
            "}\n"
            "loaded = {m.split('.')[0] for m in sys.modules if not m.startswith('_')}\n"
            "extra = loaded - allow\n"
            "assert not extra, f'lhm pulled modules outside the allowlist: {extra}'\n"
            "print('allowlist clean')\n"
        )
        out = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, check=False
        )
        assert out.returncode == 0, out.stderr
        assert "allowlist clean" in out.stdout


class TestInstallIntegrity:
    """QGR test-F2/code-F4: package-data resolves via importlib.resources —
    the API installed consumers use — not __file__-relative paths."""

    def test_schema_loads_via_package_resources(self) -> None:
        from importlib import resources

        text = (
            resources.files("syntropy.lhm.contracts").joinpath("lhm-viz.v1.schema.json").read_text()
        )
        assert '"lhm-viz/v1"' in text

    def test_fixtures_load_via_package_resources(self) -> None:
        from importlib import resources

        root = resources.files("syntropy.lhm.contracts").joinpath("fixtures")
        valid = [p.name for p in root.joinpath("valid").iterdir() if p.name.endswith(".json")]
        assert len(valid) >= 2, "fixtures missing from package data"
