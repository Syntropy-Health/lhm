"""``lhm.errors`` — the SDK-wide exception root (D2-6, decision-of-record).

:class:`LhmError` is MIXED IN to every public LHM exception alongside its
original stdlib base (``class UnresolvableIntentError(LookupError,
LhmError)`` etc.), so:

- ``except LhmError`` catches ANY typed SDK refusal in one clause;
- every pre-existing ``except ValueError`` / ``LookupError`` /
  ``RuntimeError`` clause keeps working unchanged (additive-only).

Dependency-free by design — every SDK module may import it without cycles.
See ``docs/reference/errors.md`` for the full per-type reference.
"""

from __future__ import annotations


class LhmError(Exception):
    """Root of every typed LHM refusal.

    All five public SDK exceptions (:class:`~lhm.metrics.claims.ClaimScopeError`,
    :class:`~lhm.models.mapping.MappingError`,
    :class:`~lhm.publish.publisher.PublishTransportError`,
    :class:`~lhm.nl.binder.UnresolvableIntentError`,
    :class:`~lhm.nl.binder.AmbiguousIntentError`) subclass it IN ADDITION to
    their original stdlib bases. Catch it to handle "the SDK refused" as one
    category without enumerating the types.
    """


__all__ = ["LhmError"]
