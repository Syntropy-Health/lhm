"""D-G (shrinelongevity-health-dashboard A&D §2.6) — brand-wall extension.

One guard module (``syntropy.lhm.metrics.claims``), three surfaces:

1. **Text** — ``BANNED_ADVICE_LEXICON`` extends the efficacy denylist with
   advice/diagnosis framing, COUPLED with the matched ``_DISCLAIMER_RE``
   extension so the compliant "not a recommendation — your clinician
   decides" phrasing still passes (the C1a coupling: extending the
   lexicon alone would fail-closed the exact copy compliance wants).
2. **Valence** — ``assert_valence_safe(ScoreRendering)``: structural check
   over the render contract (band label, color role, reference frame).
   Only neutral-magnitude + personal-trajectory framings pass.
3. **Narration** — ``assert_narration_safe(text)``: the extended text
   guard applied to free narration (no caveat-triad requirement —
   narration is prose, not a metric). Fail-closed: on rejection the
   caller renders the score WITHOUT narration (``narration=None``).

Tests are R6's sealed-able set: red/green lexicon pairs, the matched-
disclaimer regression pins, valence structural cases, and the narration
fail-closed caller path.
"""

from __future__ import annotations

import pytest

from syntropy.lhm.metrics.claims import (
    BANNED_ADVICE_LEXICON,
    BANNED_LEXICON,
    ClaimScopeError,
    ScoreRendering,
    assert_claim_safe,
    assert_narration_safe,
    assert_valence_safe,
)

# Caveats carrying the R10 triad (association + uncertainty markers) so
# metric-text cases isolate the LEXICON behavior under test.
_TRIAD_CAVEATS = (
    "Personal n-of-1 association only.",
    "Uncertainty interval shown; not causal.",
)


# --------------------------------------------------------------------------- #
# 1. Extended lexicon — red (R6a: red-before-green pairs)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "bad_text",
    [
        "We recommend magnesium",
        "This diagnoses poor sleep",
        "You should take magnesium before bed",
        "We advise a higher dose",
        "Follow this advice for better sleep",
        "Ask about prescribing options",
        "Your pattern suggests you are at risk",
    ],
)
def test_advice_framing_raises_in_narration(bad_text: str) -> None:
    with pytest.raises(ClaimScopeError):
        assert_narration_safe(bad_text)


@pytest.mark.parametrize(
    "bad_label",
    [
        "We recommend magnesium",
        "This diagnoses poor sleep",
        "You should take magnesium",
    ],
)
def test_advice_framing_raises_in_metric_text(bad_label: str) -> None:
    with pytest.raises(ClaimScopeError, match="banned"):
        assert_claim_safe(bad_label, _TRIAD_CAVEATS, "weak", rx_input=False)


def test_zero_width_evasion_still_caught() -> None:
    # LHM-SEC-01 fold applies to the extended lexicon too.
    with pytest.raises(ClaimScopeError):
        assert_narration_safe("we rec​ommend magnesium")


# --------------------------------------------------------------------------- #
# 2. Extended lexicon — green + the matched-disclaimer pins (R6b)
# --------------------------------------------------------------------------- #

# Regression-pinned string from the A&D (verbatim).
_COMPLIANT_DISCLAIMER = "This is not a recommendation — your clinician decides."
_FDA_DISCLAIMER = "This product is not intended to diagnose, treat, cure, or prevent any disease."


def test_compliant_disclaimer_passes_narration_guard_verbatim() -> None:
    assert_narration_safe(_COMPLIANT_DISCLAIMER)  # must not raise


def test_compliant_disclaimer_passes_as_metric_caveat() -> None:
    assert_claim_safe(
        "Sleep quality alongside magnesium",
        (*_TRIAD_CAVEATS, _COMPLIANT_DISCLAIMER),
        "weak",
        rx_input=False,
    )


def test_fda_disclaimer_still_passes_with_diagnos_banned() -> None:
    # Pre-extension pin: "diagnose" is now a banned stem, but the negated
    # FDA structure-function frame must keep passing (C1a coupling).
    assert_narration_safe(_FDA_DISCLAIMER)
    assert_claim_safe(
        "Sleep quality alongside magnesium",
        (*_TRIAD_CAVEATS, _FDA_DISCLAIMER),
        "weak",
        rx_input=False,
    )


def test_disclaimer_excision_does_not_shield_trailing_advice() -> None:
    # The negated frame is excised only up to the clause boundary — a
    # smuggled advice clause after a comma still fail-closes.
    with pytest.raises(ClaimScopeError):
        assert_narration_safe("This is not a recommendation, but you should take magnesium.")


def test_consult_is_not_banned() -> None:
    # Routing to a clinician IS the product (A&D D-G #1).
    assert_narration_safe("Consult your clinician about these observations.")
    assert "consult" not in BANNED_LEXICON
    assert "consult" not in BANNED_ADVICE_LEXICON


def test_neutral_association_narration_passes() -> None:
    assert_narration_safe(
        "Your sleep series moved alongside magnesium intake in this window; "
        "this is a personal association, not a causal finding."
    )


# --------------------------------------------------------------------------- #
# 3. Valence — structural render-contract checks (R6c)
# --------------------------------------------------------------------------- #


def _neutral_rendering(**overrides) -> ScoreRendering:
    base = dict(
        domain="sleep",
        score=31.0,
        band_label="lower than your recent range",
        color_role="neutral",
        reference_frame="personal-trajectory",
    )
    base.update(overrides)
    return ScoreRendering(**base)


def test_neutral_personal_trajectory_rendering_of_low_score_passes() -> None:
    assert_valence_safe(_neutral_rendering())  # must not raise


def test_neutral_magnitude_frame_passes() -> None:
    assert_valence_safe(_neutral_rendering(reference_frame="neutral-magnitude"))


def test_bandless_rendering_passes() -> None:
    assert_valence_safe(_neutral_rendering(band_label=None))


@pytest.mark.parametrize(
    "bad_band",
    ["poor", "good", "excellent", "unhealthy", "at-risk", "abnormal", "optimal"],
)
def test_qualitative_verdict_band_labels_raise(bad_band: str) -> None:
    with pytest.raises(ClaimScopeError, match="band"):
        assert_valence_safe(_neutral_rendering(band_label=bad_band))


@pytest.mark.parametrize("bad_color", ["danger", "red", "warning", "success", "green"])
def test_diagnostic_color_semantics_raise(bad_color: str) -> None:
    with pytest.raises(ClaimScopeError, match="color"):
        assert_valence_safe(_neutral_rendering(color_role=bad_color))


@pytest.mark.parametrize("bad_frame", ["population", "reference-range", "clinical-normal"])
def test_population_reference_verdict_framing_raises(bad_frame: str) -> None:
    with pytest.raises(ClaimScopeError, match="frame"):
        assert_valence_safe(_neutral_rendering(reference_frame=bad_frame))


# --------------------------------------------------------------------------- #
# 4. Narration fail-closed contract (R6d) — score WITHOUT narration
# --------------------------------------------------------------------------- #


def _narration_or_none(text: str) -> str | None:
    """The canonical caller shape: fail-closed narration, fail-soft surface."""
    try:
        assert_narration_safe(text)
    except ClaimScopeError:
        return None
    return text


def test_rejected_narration_yields_score_without_narration() -> None:
    payload = {
        "domain": "sleep",
        "score": 31.0,
        "narration": _narration_or_none("You should take more magnesium."),
    }
    assert payload["score"] == 31.0  # the score still renders
    assert payload["narration"] is None  # the narration does not


def test_safe_narration_passes_through_unchanged() -> None:
    text = "Sleep tracked alongside magnesium here; personal association only."
    assert _narration_or_none(text) == text
