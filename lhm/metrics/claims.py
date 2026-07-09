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

import re
import unicodedata
from collections.abc import Iterable

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
    r"\b(" + "|".join(re.escape(w) for w in BANNED_LEXICON) + r")\w*",
    re.IGNORECASE,
)

# Recognized NEGATED-DISCLAIMER phrasing (QGR L0 code-F2): the standard FDA
# structure-function disclaimer ("… not intended to diagnose, treat, cure, or
# prevent any disease") is the canonical COMPLIANT caveat — banning its
# vocabulary would fail-closed the exact text compliance wants. Sentences
# matching this negated frame are excised BEFORE the denylist scan; the
# banned check still applies to everything else in the caveat.
_DISCLAIMER_RE = re.compile(
    r"not\s+(?:intended|meant)\s+to\s+[^.;]*?"
    r"(?:diagnose|treat|cure|prevent|mitigate)[^.;]*",
    re.IGNORECASE,
)


class ClaimScopeError(ValueError):
    """A metric's text violates the claim-scope contract (R10)."""


def _strip_disclaimers(text: str) -> str:
    """Remove recognized negated-disclaimer sentences before the denylist scan."""
    return _DISCLAIMER_RE.sub("", text)


def _fold(text: str) -> str:
    """NFKC-normalize + casefold + strip zero-width/format chars (LHM-SEC-01).

    Defense-in-depth for the denylist layer: homoglyph-adjacent compatibility
    forms and zero-width insertions (``im\u200bprove``) fold back to matchable
    ASCII before the banned-lexicon / marker checks run. The structural triad
    remains the primary control.
    """
    normalized = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in normalized if unicodedata.category(ch) not in ("Cf", "Mn")
    ).casefold()


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


__all__ = ["BANNED_LEXICON", "ClaimScopeError", "assert_claim_safe"]
