"""``lhm.ask`` — the README one-liner (design 0001 §1).

One natural-language question in; a claim-safe, tiered, uncertainty-carrying
personal metric out; one call to put it on a dashboard::

    result = await lhm.ask(
        "How does my magnesium intake relate to my sleep quality?",
        adapter=my_adapter,          # your data (D7-style adapter)
        interpreter=my_interpreter,  # your LLM (BYO — design 0001 §3)
    )
    result.suggestions               # tiered candidates (low / mid / strong)
    await result.publish(target)     # → your syntropy-journal dashboard

A convenience COMPOSITION, not a magic box (D10): interpret → deterministic
bind → fetch → analyze, each stage a public, hand-callable surface. The LLM
only narrows language; unresolvable intents refuse (typed), and publishing
runs the full manage/render/transport gate chain.
"""

from __future__ import annotations

from dataclasses import dataclass

from syntropy.lhm.contracts.viz import VizPoint, to_viz_payload
from syntropy.lhm.nl.binder import ResolvedIntent, resolve_intent
from syntropy.lhm.nl.intent import Interpreter, MetricIntent
from syntropy.lhm.publish.envelope import PublishReceipt, build_envelope
from syntropy.lhm.publish.publisher import HttpPublisher, Publisher
from syntropy.lhm.publish.target import SyntropyTarget
from syntropy.lhm.tools.pipeline import (
    AlignedData,
    CandidateSignal,
    LhmAdapter,
    analyze,
    fetch,
    manage,
)

#: Default opaque subject token for single-user/self-hosted use (U1) — an
#: adapter serving one person needs no scoping; multi-user callers pass
#: their own opaque token (C9: never an email or name).
DEFAULT_USER_SCOPE = "local-subject"

_TIER_RANK = {"strong": 2, "mid": 1, "low": 0}


@dataclass(frozen=True)
class AskResult:
    """What :func:`ask` hands back: suggestions + one-call publication."""

    intent: MetricIntent
    resolved: ResolvedIntent
    aligned: tuple[AlignedData, ...]
    #: Tiered candidate metrics, best-first (tier, then fit score).
    suggestions: tuple[CandidateSignal, ...]
    user_scope: str

    async def publish(
        self,
        target: SyntropyTarget,
        *,
        publisher: Publisher | None = None,
    ) -> PublishReceipt:
        """Publish the best persistable suggestion to ``target``.

        The full gate chain runs in order: ``manage`` (persist gate, C4
        tier rule) → ``to_viz_payload`` (render gate, target-aware — an
        Rx metric on a ``public_surface`` target raises here, before any
        transport) → the publisher's own pre-wire gate. When NO suggestion
        is persistable (all low-tier) a refused receipt comes back — a
        typed no, not an exception.

        ``publisher`` overrides the default :class:`HttpPublisher(target)`
        (e.g. an :class:`InProcessPublisher`, or an HttpPublisher with an
        injected transport in tests).

        DELIBERATE SDK contract — :class:`lhm.metrics.claims.ClaimScopeError`
        propagates (hard-raises) rather than returning a refused receipt:
        suggestions produced by :func:`ask` are claim-safe by construction,
        so a banned-lexicon candidate reaching this method means a caller
        hand-built or mutated one — a PROGRAMMING error, not a runtime
        condition. (The journals app route, by contrast, answers external
        callers and maps the same gate to a refused receipt.)
        """
        for candidate in self.suggestions:
            outcome = await manage("add", candidate)
            if not outcome.accepted or outcome.spec is None:
                continue
            data = next((a for a in self.aligned if a.outcome == candidate.outcome), None)
            series = (
                tuple(VizPoint(ts=p.at, value=p.outcome_value) for p in data.pairs)
                if data is not None
                else ()
            )
            payload = to_viz_payload(
                outcome.spec,
                series,
                regimen_start=data.regimen_start if data is not None else None,
                public_surface=target.public_surface,
            )
            envelope = build_envelope(outcome.spec, payload, self.user_scope)
            active = publisher or HttpPublisher(target)
            return await active.publish(envelope)
        return PublishReceipt(
            accepted=False,
            metric_key=self.suggestions[0].metric_key if self.suggestions else "",
            reason="no persistable suggestion (C4 — only mid/strong tiers publish)",
        )


async def ask(
    prompt: str,
    *,
    adapter: LhmAdapter,
    interpreter: Interpreter,
    user_scope: str = DEFAULT_USER_SCOPE,
) -> AskResult:
    """Interpret → bind → fetch → analyze (design 0001 §1).

    Raises :class:`lhm.nl.UnresolvableIntentError` when the interpreter's
    intent names data the adapter does not actually have (D10 — a
    hallucinated column cannot become a metric), and
    :class:`lhm.nl.AmbiguousIntentError` when a query spans more than one
    distinct record (the binder never merges distinct substances/series).
    """
    intent = await interpreter.interpret(prompt)
    resolved = await resolve_intent(intent, adapter, user_scope=user_scope)
    aligned = await fetch(resolved.as_adapter(), user_scope)
    suggestions = tuple(
        sorted(
            await analyze(aligned),
            key=lambda c: (-_TIER_RANK[c.confidence], -c.fit.fit_score, c.metric_key),
        )
    )
    return AskResult(
        intent=intent,
        resolved=resolved,
        aligned=aligned,
        suggestions=suggestions,
        user_scope=user_scope,
    )


__all__ = ["DEFAULT_USER_SCOPE", "AskResult", "ask"]
