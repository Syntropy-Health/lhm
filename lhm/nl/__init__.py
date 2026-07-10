"""``lhm.nl`` — the NL seam (L4, design 0001 §3, D10).

NL is sugar, never load-bearing: :class:`MetricIntent` is hand-constructible,
:class:`Interpreter` is BYO-LLM (a Protocol — no LLM dep in the SDK, ever),
and :func:`resolve_intent` binds intents to real adapter records
deterministically (typed refusal on anything unresolvable).
"""

from lhm.nl.binder import ResolvedIntent, UnresolvableIntentError, resolve_intent
from lhm.nl.intent import Interpreter, MetricIntent

__all__ = [
    "Interpreter",
    "MetricIntent",
    "ResolvedIntent",
    "UnresolvableIntentError",
    "resolve_intent",
]
