# Errors — every public exception

LHM refuses with **typed, reasoned errors** instead of guessing. Most of
them are contracts, not accidents: catching the right type tells you which
guarantee fired. None of the SDK's error messages ever carry payload text,
notes, or identifiers (C9) — diagnostics that could be sensitive ride
exception *attributes* for in-process use only.

| Exception | Bases | Raised by |
|---|---|---|
| `LhmError` | `Exception` | never raised directly — the root every SDK refusal below mixes in |
| `ClaimScopeError` | `ValueError`, `LhmError` | `assert_claim_safe` — via `manage`, `to_viz_payload`, every publisher |
| `MappingError` | `ValueError`, `LhmError` | `map_order` / `map_regimen_start` / `map_series`, and the tz/opacity boundary checks in `fetch` and the binder |
| `UnresolvableIntentError` | `LookupError`, `LhmError` | `resolve_intent` / `syntropy.lhm.ask` |
| `AmbiguousIntentError` | `LookupError`, `LhmError` | `resolve_intent` / `syntropy.lhm.ask` |
| `PublishTransportError` | `RuntimeError`, `LhmError` | `HttpPublisher.publish` / `.unpublish` |

## `LhmError` — the root of every SDK refusal

Every public LHM exception subclasses `syntropy.lhm.LhmError` **in addition to** its
original stdlib base (multiple inheritance, additive-only):

```python
from syntropy import lhm

try:
    result = await lhm.ask(prompt, adapter=adapter, interpreter=interpreter)
    await result.publish(target)
except lhm.LhmError as refusal:
    ...  # any typed SDK refusal — binder, claim gate, mapping, transport
```

- `except LhmError` catches **any** of the five refusals in one clause —
  useful at an app boundary that just needs "the SDK said no".
- The original stdlib bases are preserved, so pre-existing
  `except ValueError` / `LookupError` / `RuntimeError` clauses keep working
  unchanged.
- `LhmError` derives from `Exception` only — catching it never absorbs
  unrelated stdlib `ValueError`s / `LookupError`s.

## `ClaimScopeError`

**When:** a metric's user-facing text (label + caveats) or tier/surface
combination violates the claim-scope contract: a banned clinical-efficacy
stem, a missing association or uncertainty marker, no caveats at all, an
Rx-input metric at the `strong` tier, or an Rx-input metric aimed at a
`public_surface=True` target.

**Why:** the [honesty layer](../guide/honesty-layer.md) is fail-closed — a
dishonest metric must be unrepresentable, not merely discouraged.

**What to do:** fix the *source* of the text (reword the label, restore the
caveat markers, keep Rx metrics private). Do **not** auto-rewrite text to
pass the gate, and do not persist/render/transport after catching it. Note
the one deliberate hard-raise: `AskResult.publish` lets this propagate
instead of returning a refused receipt, because `ask`-produced suggestions
are claim-safe by construction — reaching the gate there means a caller
hand-built or mutated a candidate (a programming error, not a runtime
condition).

## `MappingError`

**When:** a record can't cross the boundary — a naive (tz-less) `datetime`,
an email-shaped `subject_ref`, an empty display, a series point with
neither value nor note, or an injected `sentiment_fn` returning a
non-numeric.

**Why:** fail loud at the boundary with the offending field *named*, rather
than a bare `TypeError`/KeyError deep in the pipeline.

**What to do:** fix the adapter — attach timezones at parse time and keep
subject tokens opaque (see [Adapters](../guide/adapters.md)).

## `UnresolvableIntentError`

**When:** an intent query matched **no** adapter record (whole-token
matching — substrings never count).

**Why:** the NL layer never invents data (D10) — a hallucinated or
misheard name must refuse, not bind the nearest real record.

**What to do:** surface it as a user-facing "I don't see that in your data".
Attributes: `kind` (`"input"` or `"outcome"`), `query`, and `candidates`
(the displays that *were* available — in-process only; don't log them
alongside user identifiers).

## `AmbiguousIntentError`

**When:** an intent query matched **more than one distinct** record
("magnesium" against both a glycinate and a citrate).

**Why:** silently merging distinct substances/series would make the metric's
label misrepresent the data — refusal beats guessing.

**What to do:** ask the user to narrow the query. Attributes: `kind`,
`query`, `matched_displays` (the distinct displays — same C9 handling rule;
they are deliberately absent from the message itself).

## `PublishTransportError`

**When:** `HttpPublisher` exhausted its retries (transport errors / 5xx), a
2xx response body was oversized or unparseable, or `unpublish` got a
non-404 error status.

**Why:** it means **outcome unknown** — the server may or may not have
persisted. A refused receipt would claim knowledge the client doesn't have.

**What to do:** replay the same envelope later — always safe, because the
envelope is idempotency-keyed (the backend dedupes). A definitive server
"no" (4xx) is *not* this error; it comes back as a refused
`PublishReceipt` with a `reason`. See
[Publishing](../guide/publish.md#httppublisher--post-retry-at-least-once).

## Standard-library types you'll also meet

- **`pydantic.ValidationError`** — constructing any model with bad data:
  a non-`https` `SyntropyTarget` without loopback/`allow_insecure`, a
  too-short `MetricIntent` query, an email-shaped envelope `user_scope`, a
  naive `published_at`, an oversized `MetricSpec` field.
- **`ValueError`** — `HttpPublisher.unpublish` on a `metric_key` that fails
  the key shape (refused before any URL is built), and
  `HttpPublisher(max_attempts=0)`.
- **`ImportError`** — constructing `HttpPublisher` without the transport
  extra; the message names the fix (`pip install "syntropy-lhm[publish]"`).
- **`NotImplementedError`** — `InProcessPublisher.unpublish` when no
  `remove_callback` was injected.
