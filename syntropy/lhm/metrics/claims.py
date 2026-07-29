"""Claim-scope guard — the C1/C1b enforcer (PVR R10; A&D D4).

Every emitted metric's user-facing TEXT (``label`` + every ``caveat``) is
gated here before it may persist (R3) or render (R6). The guard is part of
the SDK on purpose: the honesty layer is the OSS differentiator, not an
app afterthought.

Three checks, all structural:

1. **Banned lexicon** — clinical-efficacy vocabulary never appears in a
   metric's text: personal n-of-1 trends are correlations, not treatment
   claims (structure-function language only).
2. **The correlation/uncertainty triad** — the caveats must EXPLICITLY carry
   (a) an association/correlation framing marker and (b) an uncertainty
   marker; a metric with no caveats never validates (``MetricSpec`` already
   requires ≥1).
3. **The Rx tier (C1b)** — an ``rx_input`` metric is stricter: never a
   ``strong`` tier (v0.1), never on a public surface.

Fail-CLOSED: violations raise ``ClaimScopeError`` — callers must not
persist/render on failure (and must not "fix" text automatically).

Known accepted edge (QGR code-F6): the ``\\b(word)\\w*`` stems over-match a
few benign forms ("reverse chronological", "treaty"). Deliberate: for a
claims guard, a rare false REJECTION beats a missed efficacy claim; the
negated-disclaimer exemption above covers the common compliant phrasing.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

from syntropy.lhm.errors import LhmError
from syntropy.lhm.metrics.scoring import SCORE_MAX, SCORE_MIN, V1_DOMAINS

#: Guard-version stamps (QG 1f) — persisted into every snapshot row's
#: ``guard_versions`` provenance so a stored row records exactly which
#: guard generation gated it (C1 retroactive re-gate).
CLAIMS_GUARD_VERSION = "claims-v3"
VALENCE_GUARD_VERSION = "valence-v2"

#: Clinical-efficacy vocabulary that must never appear in metric text
#: (PVR R10 lexicon — mirrors the app's evidence-section claim-scope test).
BANNED_LEXICON: tuple[str, ...] = (
    "improve",
    "treat",
    "cure",
    "prevent",
    "reverse",
    "efficacy",
    "clinical",  # \w* also catches "clinically", "clinical-grade" etc.
)

#: Advice/diagnosis framing — the D-G brand-wall extension (health-dashboard
#: A&D §2.6 #1). Personal n-of-1 observations are never advice, a diagnosis,
#: or a prescription. ``consult`` is deliberately NOT banned: routing the
#: user to a clinician IS the product. Coupled with the matched
#: ``_DISCLAIMER_RE`` extension below so the compliant negated frame
#: ("not a recommendation — your clinician decides") still passes (C1a).
BANNED_ADVICE_LEXICON: tuple[str, ...] = (
    "recommend",  # \w* catches "recommendation", "recommended", ...
    "diagnos",  # diagnose / diagnosis / diagnostic
    "prescrib",  # prescribe / prescribing / prescribed
    "advis",  # advise / advisable / advising
    "advice",
    "should take",
    "you should",
    "at risk",
)

#: Association-framing markers — at least one caveat must carry one.
_ASSOCIATION_MARKERS: tuple[str, ...] = (
    "associat",  # associated / association
    "correlat",  # correlation / correlated
    "observational",
    "n-of-1",
    "n=1",
)

#: Uncertainty markers — at least one caveat must carry one.
_UNCERTAINTY_MARKERS: tuple[str, ...] = (
    "uncertain",
    "interval",
    "not causal",
    "no causal",
    "may not",
    "cannot establish",
)

_BANNED_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in (*BANNED_LEXICON, *BANNED_ADVICE_LEXICON)) + r")\w*",
    re.IGNORECASE,
)

# Recognized NEGATED-DISCLAIMER phrasing (QGR L0 code-F2): the standard FDA
# structure-function disclaimer ("… not intended to diagnose, treat, cure, or
# prevent any disease") is the canonical COMPLIANT caveat — banning its
# vocabulary would fail-closed the exact text compliance wants. Sentences
# matching this negated frame are excised BEFORE the denylist scan; the
# banned check still applies to everything else in the caveat.
#
# D-G extension (C1a coupling): the second branch recognizes the negated
# ADVICE frame ("this is not a recommendation — your clinician decides",
# "not medical advice") that the BANNED_ADVICE_LEXICON would otherwise
# fail-close.
#
# QG 1a hardening: BOTH branches terminate AT the disclaimer noun/verb
# itself — no trailing catch-alls. The FDA branch consumes only the
# comma/or/and-joined chain of NEGATED verbs ("diagnose, treat, cure, or
# prevent"); the advice branch stops at the noun. Everything after the
# excision (em-dash, colon, paren, comma clauses included) stays in the
# text and is re-scanned by the denylist — a smuggled advice clause
# ("not a recommendation — you should take 500mg") can no longer hide
# inside the excision window.
_FDA_NEGATED_VERBS = r"(?:diagnose|treat|cure|prevent|mitigate)"
_DISCLAIMER_RE = re.compile(
    r"(?:"
    r"not\s+(?:intended|meant)\s+to\s+[^.;,:()—–-]*?"
    + _FDA_NEGATED_VERBS
    + r"(?:(?:\s*,\s*(?:or\s+|and\s+)?|\s+(?:or|and)\s+)"
    + _FDA_NEGATED_VERBS
    + r")*"
    r"|not\s+(?:a\s+|an\s+)?(?:medical\s+|clinical\s+)?"
    r"(?:recommendation|advice|diagnosis|prescription)"
    r")",
    re.IGNORECASE,
)

#: QG 1e — imperative-dosing structural check: a take/start/stop verb with
#: a dose quantity ("Take 500 mg …") is advice by construction, even when
#: no lexicon word appears. Observational past-tense mentions ("you logged
#: 500 mg") deliberately do not match.
_IMPERATIVE_DOSE_RE = re.compile(
    r"\b(?:take|takes|taking|start|starts|starting|stop|stops|stopping)\b"
    r"[^.;:!?]{0,80}?"
    r"\b\d+(?:\.\d+)?\s?(?:mg|mcg|g|iu|ml)\b",
    re.IGNORECASE,
)


class ClaimScopeError(ValueError, LhmError):
    """A metric's text violates the claim-scope contract (R10)."""


def _strip_disclaimers(text: str) -> str:
    """Remove recognized negated-disclaimer sentences before the denylist scan."""
    return _DISCLAIMER_RE.sub("", text)


#: QG 1e \u2014 targeted Cyrillic/Greek\u2192Latin confusable fold. NFKC does NOT
#: unify cross-script lookalikes ("We r\u0435commend" with Cyrillic \u0435 survives
#: normalization), so the ~30 highest-risk lowercase lookalike codepoints
#: fold to their Latin twins before the denylist scan. Over-matching on
#: genuine Cyrillic/Greek prose is an accepted fail-closed trade.
_CONFUSABLE_FOLD = str.maketrans(
    {
        # Cyrillic lowercase \u2192 Latin lookalikes
        "\u0430": "a",  # \u0430
        "\u0432": "b",  # \u0432
        "\u0435": "e",  # \u0435
        "\u0451": "e",  # \u0451
        "\u043a": "k",  # \u043a
        "\u043c": "m",  # \u043c
        "\u043d": "h",  # \u043d
        "\u043e": "o",  # \u043e
        "\u0440": "p",  # \u0440
        "\u0441": "c",  # \u0441
        "\u0442": "t",  # \u0442
        "\u0443": "y",  # \u0443
        "\u0445": "x",  # \u0445
        "\u0455": "s",  # \u0455
        "\u0456": "i",  # \u0456
        "\u0458": "j",  # \u0458
        "\u04bb": "h",  # \u04bb
        "\u051b": "q",  # \u051b
        "\u051d": "w",  # \u051d
        # Greek lowercase \u2192 Latin lookalikes
        "\u03b1": "a",  # \u03b1
        "\u03b5": "e",  # \u03b5
        "\u03b9": "i",  # \u03b9
        "\u03ba": "k",  # \u03ba
        "\u03bd": "v",  # \u03bd
        "\u03bf": "o",  # \u03bf
        "\u03c1": "p",  # \u03c1
        "\u03c4": "t",  # \u03c4
        "\u03c5": "u",  # \u03c5
        "\u03c7": "x",  # \u03c7
        "\u03f2": "c",  # \u03f2 (lunate sigma)
    }
)


def _fold(text: str) -> str:
    """NFKC-normalize + casefold + strip zero-width/format chars (LHM-SEC-01).

    Defense-in-depth for the denylist layer: homoglyph-adjacent compatibility
    forms and zero-width insertions (``im\u200bprove``) fold back to matchable
    ASCII before the banned-lexicon / marker checks run, and the targeted
    Cyrillic/Greek confusable table (QG 1e) folds cross-script lookalikes
    NFKC leaves alone. The structural triad remains the primary control.
    """
    # Fold confusables BEFORE NFKC too: NFKC rewrites some lookalikes into
    # non-lookalike letters (e.g. lunate sigma ϲ → σ) which would dodge the
    # post-normalization table.
    prefolded = text.casefold().translate(_CONFUSABLE_FOLD)
    normalized = unicodedata.normalize("NFKC", prefolded)
    stripped = "".join(
        ch for ch in normalized if unicodedata.category(ch) not in ("Cf", "Mn")
    ).casefold()
    return stripped.translate(_CONFUSABLE_FOLD)


def _contains_any(texts: Iterable[str], markers: tuple[str, ...]) -> bool:
    low = _fold(" ".join(texts))
    return any(m in low for m in markers)


def assert_claim_safe(
    label: str,
    caveats: tuple[str, ...],
    confidence: str,
    rx_input: bool,
    *,
    public_surface: bool = False,
) -> None:
    """Gate a metric's user-facing text; raise ``ClaimScopeError`` on violation.

    Call sites (both mandatory): the ``manage`` tool before it emits a
    persistence-ready spec (R3) and the app's render adapter before a card
    renders (R6 — the ``VizPayload`` path re-checks via
    ``lhm.contracts.viz.to_viz_payload``).
    """
    texts = (label, *caveats)

    hit = next(
        (m.group(0) for t in texts if (m := _BANNED_RE.search(_strip_disclaimers(_fold(t))))),
        None,
    )
    if hit is not None:
        raise ClaimScopeError(
            f"banned clinical-efficacy term {hit!r} in metric text — personal "
            "n-of-1 trends use structure-function association language only (R10)"
        )

    dose_hit = next(
        (
            m.group(0)
            for t in texts
            if (m := _IMPERATIVE_DOSE_RE.search(_strip_disclaimers(_fold(t))))
        ),
        None,
    )
    if dose_hit is not None:
        raise ClaimScopeError(
            f"imperative dosing instruction {dose_hit!r} in metric text — "
            "dose directives are advice by construction (QG 1e structural check)"
        )

    if not caveats:
        raise ClaimScopeError("a metric must carry at least one caveat (R10 triad)")
    if not _contains_any(caveats, _ASSOCIATION_MARKERS):
        raise ClaimScopeError(
            "caveats must carry an association/correlation framing marker "
            "(e.g. 'personal association', 'observational', 'n-of-1') — R10 triad"
        )
    if not _contains_any(caveats, _UNCERTAINTY_MARKERS):
        raise ClaimScopeError(
            "caveats must carry an uncertainty marker (e.g. an interval note, "
            "'not causal') — R10 triad"
        )

    if rx_input and confidence == "strong":
        raise ClaimScopeError("an Rx-input metric never surfaces the 'strong' tier in v0.1 (C1b)")
    if rx_input and public_surface:
        raise ClaimScopeError(
            "an Rx-input metric is user-private — never a public/marketed surface (C1b)"
        )


# --------------------------------------------------------------------------- #
# D-G #2 — valence guard (health-dashboard A&D §2.6): the render contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScoreRendering:
    """The structural render contract a domain score surfaces through (D-G #2).

    Every field the valence guard checks is HERE, by construction — a
    surface that wants a band label, a color, or a reference frame must
    route it through this contract, where ``assert_valence_safe`` can
    reject verdict framing before anything renders.

    * ``band_label`` — optional short label for the score's band. Must be
      neutral-magnitude / personal-trajectory copy ("lower than your
      recent range"), never a qualitative verdict ("poor", "at-risk").
    * ``color_role`` — semantic color slot, NOT a hex value. Allowlisted
      to non-diagnostic roles; "danger"/"red"/traffic-light semantics are
      structurally rejected.
    * ``reference_frame`` — what the score is framed against. Only the
      user's own trajectory or a neutral magnitude scale pass; population
      reference-range verdict framing is rejected.
    """

    domain: str
    score: float
    band_label: str | None = None
    color_role: str = "neutral"
    reference_frame: str = "personal-trajectory"


#: Qualitative-verdict vocabulary banned from band labels (word-stem match).
_BANNED_VALENCE_LEXICON: tuple[str, ...] = (
    "good",
    "bad",
    "poor",
    "excellent",
    "great",
    "terrible",
    "worst",
    "best",
    "ideal",
    "optimal",
    "suboptimal",  # \b anchors the stem match, so this needs its own entry
    "healthy",
    "unhealthy",
    "normal",  # also catches "normalized"? accepted over-match (fail-closed)
    "abnormal",
    "risk",  # catches "at risk", "at-risk", "risky"
    "danger",
    "warning",
    "alarm",
    "concern",
    "critical",
    "fail",
)

_BANNED_VALENCE_RE = re.compile(
    r"\b(" + "|".join(re.escape(w) for w in _BANNED_VALENCE_LEXICON) + r")\w*",
    re.IGNORECASE,
)

#: Non-diagnostic color roles (allowlist — fail-closed: anything else raises).
_ALLOWED_COLOR_ROLES: frozenset[str] = frozenset({"neutral", "muted", "accent", "informational"})

#: Permitted score framings (allowlist): the user's own trajectory or a
#: neutral magnitude scale. Population reference-range verdicts never pass.
_ALLOWED_REFERENCE_FRAMES: frozenset[str] = frozenset({"personal-trajectory", "neutral-magnitude"})


def assert_valence_safe(rendering: ScoreRendering) -> None:
    """Gate a score's render contract; raise ``ClaimScopeError`` on violation.

    Structural checks (D-G #2): no qualitative-verdict band labels, no
    diagnostic color semantics, no population-reference verdict framing.
    QG 1c: the domain must be a declared V1 domain, the score must be
    finite and inside the declared [SCORE_MIN, SCORE_MAX] range, and the
    band label runs the FULL text guard (efficacy + advice lexicons +
    imperative dosing) on top of the valence lexicon.
    Fail-CLOSED: callers must not render a rejected ``ScoreRendering``
    (and must not "fix" it automatically).
    """
    if rendering.domain not in V1_DOMAINS:
        raise ClaimScopeError(
            f"unknown scoring domain {rendering.domain!r} — the render "
            f"contract accepts only the declared V1 domains {V1_DOMAINS} "
            "(D-G valence, QG 1c)"
        )

    if not math.isfinite(rendering.score) or not (SCORE_MIN <= rendering.score <= SCORE_MAX):
        raise ClaimScopeError(
            f"score {rendering.score!r} is outside the declared "
            f"[{SCORE_MIN}, {SCORE_MAX}] range (or non-finite) — never "
            "render an out-of-range score (D-G valence, QG 1c)"
        )

    if rendering.band_label is not None:
        folded = _fold(rendering.band_label)
        hit = _BANNED_VALENCE_RE.search(folded)
        if hit is not None:
            raise ClaimScopeError(
                f"qualitative-verdict band label {rendering.band_label!r} "
                f"(term {hit.group(0)!r}) — bands use neutral-magnitude / "
                "personal-trajectory copy only (D-G valence)"
            )
        stripped = _strip_disclaimers(folded)
        text_hit = _BANNED_RE.search(stripped) or _IMPERATIVE_DOSE_RE.search(stripped)
        if text_hit is not None:
            raise ClaimScopeError(
                f"band label {rendering.band_label!r} carries banned "
                f"efficacy/advice text {text_hit.group(0)!r} — the band "
                "label runs the full text guard too (QG 1c)"
            )

    if rendering.color_role not in _ALLOWED_COLOR_ROLES:
        raise ClaimScopeError(
            f"diagnostic color role {rendering.color_role!r} — allowed "
            f"non-diagnostic roles: {sorted(_ALLOWED_COLOR_ROLES)} (D-G valence)"
        )

    if rendering.reference_frame not in _ALLOWED_REFERENCE_FRAMES:
        raise ClaimScopeError(
            f"reference frame {rendering.reference_frame!r} is a "
            "population/verdict framing — only "
            f"{sorted(_ALLOWED_REFERENCE_FRAMES)} pass (D-G valence)"
        )


# --------------------------------------------------------------------------- #
# D-G #3 — narration guard (health-dashboard A&D §2.6)
# --------------------------------------------------------------------------- #


def assert_narration_safe(text: str) -> None:
    """Gate free LLM narration; raise ``ClaimScopeError`` on violation.

    The extended text guard (efficacy + advice/diagnosis lexicons, with
    the matched negated-disclaimer excision) applied to free narration.
    No caveat-triad requirement — narration is prose, not a metric; the
    triad stays mandatory for metric text (``assert_claim_safe``).

    Fail-closed contract: every LLM narration passes here before ANY
    surface. On rejection the caller renders the score WITHOUT narration
    (``narration=None``) — never a "fixed" narration, never the raw one.
    """
    stripped = _strip_disclaimers(_fold(text))
    hit = _BANNED_RE.search(stripped)
    if hit is not None:
        raise ClaimScopeError(
            f"banned advice/efficacy term {hit.group(0)!r} in narration — "
            "render the score without narration (D-G fail-closed)"
        )
    dose_hit = _IMPERATIVE_DOSE_RE.search(stripped)
    if dose_hit is not None:
        raise ClaimScopeError(
            f"imperative dosing instruction {dose_hit.group(0)!r} in "
            "narration — render the score without narration (QG 1e "
            "structural check, D-G fail-closed)"
        )


__all__ = [
    "BANNED_ADVICE_LEXICON",
    "BANNED_LEXICON",
    "CLAIMS_GUARD_VERSION",
    "VALENCE_GUARD_VERSION",
    "ClaimScopeError",
    "ScoreRendering",
    "assert_claim_safe",
    "assert_narration_safe",
    "assert_valence_safe",
]
