"""The Interpreter seam — language → :class:`MetricIntent` (design 0001 §3).

**App-independence holds: the SDK ships NO LLM dependency (D10).** The
Interpreter is a :class:`typing.Protocol` the consumer implements — the
Shrine agent backs it with its own provider chain, a self-hoster backs it
with any LLM, or skips NL entirely and constructs :class:`MetricIntent` by
hand. The NL layer is sugar, never load-bearing: every shape here is a plain
pydantic model.

The LLM only narrows LANGUAGE. Binding the intent to concrete adapter data
is deterministic SDK code (:mod:`lhm.nl.binder`) — a hallucinated column can
never silently become a metric.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: A query must carry at least one token this long — single-character
#: queries maximize the binder's false-positive surface (QG CODE-5).
_MIN_QUERY_TOKEN_LEN = 3

_QUERY_TOKEN_RE = re.compile(r"[a-z0-9]+")


class MetricIntent(BaseModel):
    """The structured reading of the user's question (design 0001 §3).

    LLM-OUTPUT-SHAPED, but constructible by hand — the NL layer is optional
    sugar (D10). ``raw_prompt`` is provenance only and NEVER leaves the
    process (C9): it is excluded from every serialization and repr, so an
    intent dump can't leak the user's free-text question downstream.
    """

    model_config = ConfigDict(frozen=True)

    input_query: str = Field(min_length=1)  #: what the user consumes ("magnesium")
    outcome_query: str = Field(min_length=1)  #: what they observe ("sleep quality")
    window_days: int = Field(default=90, ge=1)  #: analysis window
    #: Provenance — in-process only (C9): excluded from dumps AND repr.
    raw_prompt: str = Field(default="", exclude=True, repr=False)

    @field_validator("input_query", "outcome_query")
    @classmethod
    def _substantive_query(cls, v: str) -> str:
        """Floor pinned at the model: ≥1 token of ≥3 chars per query.

        Tiny queries ("d") maximize the binder's false-positive surface —
        with whole-token matching they mostly self-solve, but the model is
        where the contract lives (hand-built intents get it too).
        """
        tokens = _QUERY_TOKEN_RE.findall(v.casefold())
        if not any(len(t) >= _MIN_QUERY_TOKEN_LEN for t in tokens):
            raise ValueError(
                f"query needs at least one token of {_MIN_QUERY_TOKEN_LEN}+ characters "
                "— too short to bind safely"
            )
        return v


@runtime_checkable
class Interpreter(Protocol):
    """BYO-LLM seam (design 0001 §3): one async method, no SDK LLM dep.

    Implementations narrow a natural-language prompt into a
    :class:`MetricIntent`. They must NOT attempt data binding — that is the
    SDK's deterministic job (:func:`lhm.nl.binder.resolve_intent`).
    """

    async def interpret(self, prompt: str) -> MetricIntent: ...


__all__ = ["Interpreter", "MetricIntent"]
