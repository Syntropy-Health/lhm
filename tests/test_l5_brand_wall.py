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
    _ALLOWED_COLOR_ROLES,
    _ALLOWED_REFERENCE_FRAMES,
    _BANNED_VALENCE_LEXICON,
    BANNED_ADVICE_LEXICON,
    BANNED_LEXICON,
    CLAIMS_GUARD_VERSION,
    VALENCE_GUARD_VERSION,
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


# QG 1a — the excision must terminate AT the disclaimer noun: advice
# smuggled after ANY clause punctuation (em-dash, colon, parens) or after
# the FDA verb list must still fail-close.
@pytest.mark.parametrize(
    "smuggled",
    [
        # em-dash smuggle
        "Not a recommendation — you should take 500mg of magnesium nightly.",
        # colon smuggle
        "This is not medical advice: you should take magnesium every night.",
        # parenthetical smuggle
        "This is not a diagnosis (but you should take magnesium).",
        # FDA-sentence branch + trailing advice clause after a comma
        "Not intended to treat any disease, and you should take 500 mg nightly.",
        # FDA-sentence branch + advice smuggled behind an em-dash
        "Not intended to diagnose, treat, cure, or prevent any disease — we recommend 500mg.",
    ],
)
def test_disclaimer_excision_terminates_at_the_noun(smuggled: str) -> None:
    with pytest.raises(ClaimScopeError):
        assert_narration_safe(smuggled)


# QG 1b — the negated frame must keep passing for ALL FOUR nouns × the
# medical/clinical prefixes (deleting |diagnosis|prescription must red).
@pytest.mark.parametrize("noun", ["recommendation", "advice", "diagnosis", "prescription"])
@pytest.mark.parametrize("prefix", ["", "medical ", "clinical "])
def test_negated_frame_passes_for_every_noun_and_prefix(noun: str, prefix: str) -> None:
    article = "" if noun == "advice" else "a "
    text = f"This is not {article}{prefix}{noun} — your clinician decides."
    assert_narration_safe(text)  # must not raise
    assert_claim_safe(
        "Sleep quality alongside magnesium",
        (*_TRIAD_CAVEATS, text),
        "weak",
        rx_input=False,
    )


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
        domain="sentiment_trajectory",  # a real V1 domain (QG 1c allowlist)
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


# QG 1c — the band label runs the FULL text guard (efficacy + advice
# lexicons), not just the valence lexicon.
@pytest.mark.parametrize(
    "bad_band",
    ["clinically meaningful", "you should take more", "improved vs last week"],
)
def test_band_label_runs_the_efficacy_and_advice_lexicons_too(bad_band: str) -> None:
    with pytest.raises(ClaimScopeError):
        assert_valence_safe(_neutral_rendering(band_label=bad_band))


def test_unknown_domain_is_rejected_by_the_valence_guard() -> None:
    with pytest.raises(ClaimScopeError, match="domain"):
        assert_valence_safe(_neutral_rendering(domain="vibes"))


@pytest.mark.parametrize("bad_score", [-1.0, 101.0, float("nan"), float("inf")])
def test_out_of_range_or_non_finite_score_is_rejected(bad_score: float) -> None:
    with pytest.raises(ClaimScopeError, match="score"):
        assert_valence_safe(_neutral_rendering(score=bad_score))


# QG 1d — the FULL valence lexicon is pinned red, entry by entry, and the
# parametrization provably covers the whole lexicon (meta-assert below).
@pytest.mark.parametrize("term", _BANNED_VALENCE_LEXICON)
def test_every_valence_lexicon_entry_reds_as_a_band_label(term: str) -> None:
    with pytest.raises(ClaimScopeError, match="band"):
        assert_valence_safe(_neutral_rendering(band_label=term))


def test_valence_lexicon_parametrization_covers_the_whole_lexicon() -> None:
    # The parametrize above iterates the imported lexicon itself; this pin
    # fails if the lexicon shrinks silently (coverage-by-construction).
    assert len(_BANNED_VALENCE_LEXICON) >= 21
    assert len(set(_BANNED_VALENCE_LEXICON)) == len(_BANNED_VALENCE_LEXICON)


@pytest.mark.parametrize("role", sorted(_ALLOWED_COLOR_ROLES))
def test_every_allowed_color_role_passes(role: str) -> None:
    assert_valence_safe(_neutral_rendering(color_role=role))


@pytest.mark.parametrize("frame", sorted(_ALLOWED_REFERENCE_FRAMES))
def test_every_allowed_reference_frame_passes(frame: str) -> None:
    assert_valence_safe(_neutral_rendering(reference_frame=frame))


# QG 1e — confusable folding + the imperative-dosing structural check.


@pytest.mark.parametrize(
    "evasion",
    [
        "We rеcommend magnesium",  # Cyrillic е (U+0435)
        "We recоmmend magnesium",  # Cyrillic о (U+043E)
        "We reϲommend magnesium",  # Greek lunate sigma-like ϲ (U+03F2)
        "We recοmmend magnesium",  # Greek omicron (U+03BF)
    ],
)
def test_cyrillic_greek_confusables_fold_to_latin_and_red(evasion: str) -> None:
    with pytest.raises(ClaimScopeError):
        assert_narration_safe(evasion)


@pytest.mark.parametrize(
    "dosing",
    [
        "Take 500 mg of magnesium every night.",
        "Start 5g creatine daily.",
        "Stop the 10 IU dose immediately.",
        "Consider taking 200mcg before bed.",
    ],
)
def test_imperative_dosing_is_structurally_rejected(dosing: str) -> None:
    with pytest.raises(ClaimScopeError):
        assert_narration_safe(dosing)


def test_non_imperative_dose_mention_still_passes() -> None:
    # A dose OBSERVATION is not advice — only the imperative form reds.
    assert_narration_safe(
        "You logged 500 mg of magnesium on most evenings in this window; "
        "sleep tracked alongside it — a personal association only."
    )


# QG 1f — guard-version constants (consumed by the snapshot writer's
# guard_versions provenance).


def test_guard_version_constants_exist_and_are_distinct() -> None:
    assert isinstance(CLAIMS_GUARD_VERSION, str) and CLAIMS_GUARD_VERSION
    assert isinstance(VALENCE_GUARD_VERSION, str) and VALENCE_GUARD_VERSION
    assert CLAIMS_GUARD_VERSION != VALENCE_GUARD_VERSION


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


def _card_or_none(rendering: ScoreRendering) -> dict | None:
    """The canonical RENDER caller shape: a rejected rendering yields NO card."""
    try:
        assert_valence_safe(rendering)
    except ClaimScopeError:
        return None
    return {"domain": rendering.domain, "score": rendering.score}


def test_fail_closed_asymmetry_rendering_kills_card_narration_only_kills_itself() -> None:
    """QG 1g — the two fail-closed contracts are deliberately ASYMMETRIC:

    * a rejected ``ScoreRendering`` → NO card at all (the render contract
      IS the card; there is nothing safe left to show);
    * a rejected narration → the score STILL renders, narration=None (the
      narration is an optional adornment on an already-gated score).
    """
    # Rejected rendering: the whole card is withheld — score included.
    assert _card_or_none(_neutral_rendering(band_label="poor")) is None
    # Rejected narration: the score survives; only the narration is dropped.
    card = _card_or_none(_neutral_rendering())
    assert card is not None and card["score"] == 31.0
    card["narration"] = _narration_or_none("You should take more magnesium.")
    assert card["narration"] is None
    assert card["score"] == 31.0
