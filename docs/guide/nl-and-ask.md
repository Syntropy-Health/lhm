# Natural language & `lhm.ask` — sugar, never load-bearing

The NL layer's design rule (D10): **the LLM narrows language; the SDK binds
data.** Every NL capability has a hand-constructible typed equivalent, the
SDK ships **no LLM dependency**, and anything the language layer can't
resolve against your *actual* records is a typed refusal — never a guess.

```
prompt ──Interpreter (your LLM)──▶ MetricIntent ──resolve_intent (deterministic)──▶ ResolvedIntent ──▶ fetch → analyze
```

## `MetricIntent` — the structured reading of the question

```python
from lhm import MetricIntent

intent = MetricIntent(
    input_query="magnesium glycinate",   # what the user consumes
    outcome_query="sleep quality",       # what they observe
    window_days=90,                      # trailing analysis window
    raw_prompt="How does my magnesium…", # provenance — in-process only
)
```

LLM-output-shaped, but a plain frozen pydantic model you can build by hand —
skipping NL entirely is fully supported. Two contracts live on the model:

- **`raw_prompt` never leaves the process (C9):** it is excluded from every
  serialization *and* from `repr`, so dumping or logging an intent cannot
  leak the user's free-text question.
- **Queries must be substantive:** each query needs at least one token of
  3+ characters — tiny queries maximize the binder's false-positive surface,
  so the model refuses them at construction:

```python
from pydantic import ValidationError

try:
    MetricIntent(input_query="d", outcome_query="sleep quality")
except ValidationError as err:
    print("refused:", err.errors()[0]["msg"])
```

```text
refused: Value error, query needs at least one token of 3+ characters — too short to bind safely
```

## `Interpreter` — the BYO-LLM seam

One async method, defined as a `typing.Protocol` — implement it with any
LLM (or none):

```python
from typing import Protocol

class Interpreter(Protocol):
    async def interpret(self, prompt: str) -> MetricIntent: ...
```

An implementation must ONLY narrow language into a `MetricIntent`. It must
not attempt data binding — that is the SDK's deterministic job, which is
exactly what makes a hallucinated column structurally incapable of becoming
a metric.

## The binder — deterministic matching, typed refusals

`resolve_intent(intent, adapter, user_scope=…)` resolves the intent's
queries against the adapter's **actual records**:

- `input_query` must match at least one order (display text or curated
  code); matching regimen starts ride along (none is fine).
- `outcome_query` must match at least one outcome series; matched series are
  windowed to the intent's trailing `window_days` (anchored at each series'
  latest point).

**Matching is whole-token subset containment, nothing fuzzier.** The query's
casefolded token set must be a subset of the display's: `"sleep quality"`
binds `"Sleep quality (self-rated)"`; `"iron"` does **not** bind
`"Environment score"`, even though it is a raw substring of it.

**Why guessing is banned:** binding the *wrong real record* is strictly
worse than a refusal — the metric's label would silently misrepresent which
data it was computed from, and every downstream honesty guarantee (tiers,
caveats, the claim gate) would be truthfully describing the wrong series. So
near-misses refuse (`UnresolvableIntentError`), and a query matching more
than one **distinct** record refuses too (`AmbiguousIntentError`) rather
than merging distinct substances/series under one metric. Same-display
repeats (re-purchases of one product) are one substance and bind fine.

Both refusals keep diagnostics off the message: the available/matched
displays ride the exception's `candidates` / `matched_displays` attributes
for in-process use only — don't log them alongside user identifiers (C9).

## End to end: `lhm.ask` with a stub interpreter

`lhm.ask` is a convenience composition of the public stages — interpret →
bind → `fetch` → `analyze` — plus one-call publication. Here with an
in-memory adapter and a zero-LLM interpreter stub:

```python
import asyncio
import re
from datetime import datetime, timezone

import lhm
from lhm import (
    AmbiguousIntentError,
    MetricIntent,
    OrderRecord,
    RegimenStartRecord,
    SeriesPoint,
    SeriesRecord,
    UnresolvableIntentError,
    resolve_intent,
)


def day(d: int) -> datetime:
    return datetime(2026, 1, d, 8, 0, tzinfo=timezone.utc)


class MemoryAdapter:
    """In-memory demo data — one supplement, one journal series."""

    def __init__(self, orders, starts, series):
        self._orders, self._starts, self._series = orders, starts, series

    async def read_orders(self, user_scope):
        return self._orders

    async def read_regimen_starts(self, user_scope):
        return self._starts

    async def read_outcome_series(self, user_scope):
        return self._series


ADAPTER = MemoryAdapter(
    orders=[OrderRecord("demo-subject-001", "Magnesium glycinate", day(10))],
    starts=[RegimenStartRecord("demo-subject-001", "Magnesium glycinate", day(10))],
    series=[
        SeriesRecord(
            "demo-subject-001",
            "Sleep quality (self-rated)",
            points=tuple(
                SeriesPoint(observed_at=day(d), value=v)
                for d, v in zip(range(3, 17), [5, 4, 5, 6, 5, 4, 5, 7, 8, 7, 8, 9, 8, 8])
            ),
        )
    ],
)


class RegexInterpreter:
    """A zero-LLM stand-in: parses 'how does my X relate to my Y?'.

    A real implementation calls YOUR LLM with a structured-output prompt and
    validates the reply into a MetricIntent. The SDK never sees the LLM.
    """

    _SHAPE = re.compile(r"how does my (.+?) relate to my (.+?)\??$", re.IGNORECASE)

    async def interpret(self, prompt: str) -> MetricIntent:
        m = self._SHAPE.match(prompt.strip())
        if m is None:
            raise ValueError("prompt does not match the demo shape")
        return MetricIntent(
            input_query=m.group(1),
            outcome_query=m.group(2),
            window_days=90,
            raw_prompt=prompt,  # provenance only — excluded from dumps and repr (C9)
        )


async def main() -> None:
    result = await lhm.ask(
        "How does my magnesium glycinate relate to my sleep quality?",
        adapter=ADAPTER,
        interpreter=RegexInterpreter(),
        user_scope="demo-subject-001",
    )
    print(result.intent)  # note: raw_prompt is absent from the repr (C9)
    best = result.suggestions[0]
    print(f"best: {best.label!r} — tier {best.confidence}")

    # refusal 1 — the intent names data the adapter does not have
    try:
        await lhm.ask(
            "How does my creatine relate to my sleep quality?",
            adapter=ADAPTER,
            interpreter=RegexInterpreter(),
            user_scope="demo-subject-001",
        )
    except UnresolvableIntentError as err:
        print(f"UnresolvableIntentError: {err}")

    # refusal 2 — one query, two DISTINCT substances
    two_mags = MemoryAdapter(
        orders=[
            OrderRecord("demo-subject-001", "Magnesium glycinate", day(10)),
            OrderRecord("demo-subject-001", "Magnesium citrate", day(12)),
        ],
        starts=[],
        series=ADAPTER._series,
    )
    try:
        await resolve_intent(
            MetricIntent(input_query="magnesium", outcome_query="sleep quality"),
            two_mags,
            user_scope="demo-subject-001",
        )
    except AmbiguousIntentError as err:
        print(f"AmbiguousIntentError: {err}")
        print(f"  distinct matches: {err.matched_displays}")


asyncio.run(main())
```

Output:

```text
input_query='magnesium glycinate' outcome_query='sleep quality' window_days=90
best: 'Magnesium glycinate ↔ Sleep quality (self-rated) (association)' — tier strong
UnresolvableIntentError: intent input query 'creatine' matched no adapter record — refusing to guess (1 candidate(s) available); the NL layer never invents data (design 0001 D10)
AmbiguousIntentError: intent input query 'magnesium' matched 2 distinct records — refusing to merge distinct data under one metric; narrow the query (the displays ride the .matched_displays attribute, never this message)
  distinct matches: ('Magnesium glycinate', 'Magnesium citrate')
```

The `AskResult` carries `intent`, `resolved`, `aligned`, tier-sorted
`suggestions` (best first: tier, then fit score), and `user_scope`; its
`await result.publish(target)` runs the best persistable suggestion through
the full gate chain — see [Publishing](publish.md).

Handling refusals in a chat surface is straightforward: catch the two typed
errors and ask the user to narrow ("which magnesium?") or confirm the data
exists — the exception attributes carry exactly what a follow-up question
needs.
