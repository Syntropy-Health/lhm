# Publishing — one envelope, self-hosted or cloud

Publishing pushes a pinned metric to a syntropy-journal backend. The
contract (`lhm-publish/v1`, design 0001 D11) is ONE versioned envelope for
both personas — **the API key is the only delta** between self-hosted and
cloud. Transport ships as the opt-in extra:

```bash
pip install "lhm[publish]"   # httpx transport for HttpPublisher
```

The envelope/receipt/target models work without the extra; only
`HttpPublisher` needs it (constructing one without httpx raises an
`ImportError` naming the extra).

## `SyntropyTarget` — where, and with what credential

```python
from lhm import SyntropyTarget

self_hosted = SyntropyTarget(base_url="https://journal.my-lab.dev")
cloud = SyntropyTarget(
    base_url="https://app.syntropyhealth.bio",
    api_key="sk-demo-not-a-real-key",   # stored as a pydantic SecretStr
)
print(cloud)                            # api_key never appears in repr
print(cloud.model_dump()["api_key"])    # masked in every dump mode too
```

```text
base_url='https://app.syntropyhealth.bio' timeout_s=10.0 public_surface=False allow_insecure=False
**********
```

**Key handling.** The SDK never persists the key — you supply it per
session (environment or secret manager, 12-factor). It is held as a
`SecretStr`, masked in `repr`, `str`, and every `model_dump`, and unwrapped
in exactly one place: request-header construction (`X-Syntropy-Key`).

**The https rule.** The key rides a header, so plain `http` would send it
in cleartext. `base_url` must therefore be `https`, with two escapes:

```python
from pydantic import ValidationError

local_dev = SyntropyTarget(base_url="http://localhost:8000")  # loopback: allowed

try:
    SyntropyTarget(base_url="http://metrics.example.com", api_key="sk-demo")
except ValidationError as err:
    print("refused:", err.errors()[0]["msg"])

tunnel = SyntropyTarget(base_url="http://metrics.example.com", allow_insecure=True)
```

```text
refused: Value error, plain http would send the X-Syntropy-Key header in cleartext — use https, a loopback host (localhost / 127.0.0.1 / [::1]), or pass an explicit allow_insecure=True
```

Loopback hosts (`localhost` / `127.0.0.1` / `[::1]`) are self-hosted dev;
`allow_insecure=True` is the explicit eyes-open opt-in for anything else
(dev tunnels and the like) — the key WILL cross the wire in cleartext.

**`public_surface` semantics.** `public_surface=True` declares that the
target renders on a public/marketed surface. An Rx-input metric refuses
such targets **structurally**: the claim gate raises before any transport,
so no byte of an Rx metric can leave the process toward a public surface
(see the demo at the bottom, and [the honesty layer](honesty-layer.md)).

## The envelope and the receipt

`PublishEnvelope` is everything a backend needs to surface one metric —
additive-only within v1:

| Field | What it is |
|---|---|
| `envelope_version` | literal `"lhm-publish/v1"` |
| `spec` | the `MetricSpec` (what the backend persists as its parameterization) |
| `payload` | the `lhm-viz/v1` `VizPayload` (what the backend renders) |
| `user_scope` | your opaque subject token — email-shaped values are refused (C9) |
| `idempotency_key` | `sha256(user_scope:metric_key:canonical-spec-json)` — deterministic |
| `published_at` | tz-aware client stamp (naive datetimes are refused) |

`build_envelope(spec, payload, user_scope)` assembles one, computing the
idempotency key for you:

```python
from datetime import datetime, timezone
from lhm import InputRef, MetricSpec, OutcomeRef, VizPoint, build_envelope, to_viz_payload

SPEC = MetricSpec(
    metric_key="mag_sleep",
    label="Magnesium glycinate and sleep quality — personal trend",
    inputs=(InputRef(resource="NutritionOrder", code="supplement-order",
                     display="Magnesium glycinate"),),
    outcome=OutcomeRef(kind="journal_series", series_key="Sleep quality (self-rated)"),
    confidence="mid",
    caveats=(
        "Personal n-of-1 association from your own records — observational, not causal.",
        "Estimates carry an uncertainty interval and may not generalize.",
    ),
    rx_input=False,
)
PAYLOAD = to_viz_payload(
    SPEC, [VizPoint(ts=datetime(2026, 1, 10, tzinfo=timezone.utc), value=7.0)]
)

envelope = build_envelope(SPEC, PAYLOAD, user_scope="demo-subject-001")
print(sorted(envelope.wire_dict()))
again = build_envelope(SPEC, PAYLOAD, user_scope="demo-subject-001")
print("deterministic:", again.idempotency_key == envelope.idempotency_key)
```

```text
['envelope_version', 'idempotency_key', 'payload', 'published_at', 'spec', 'user_scope']
deterministic: True
```

The backend dedupes on `idempotency_key`, which is why retrying a publish is
always safe. `PublishReceipt` is the answer: `accepted`, `metric_key`, an
optional `dashboard_url` deep link (a tolerant reader — a non-`http(s)`
link coerces to `None` rather than failing a successful publish), and a
`reason` for refusals.

## `HttpPublisher` — POST, retry, at-least-once

`HttpPublisher(target)` POSTs the envelope to
`{base_url}/api/lhm/v1/metrics`. Semantics, in gate order:

1. **Claim gate before the wire** — `publish()` re-runs the claim-scope
   guard on the envelope's label + caveats against the target's
   `public_surface` before any transport.
2. **At-least-once retry** — transport errors and 5xx responses retry with
   capped exponential backoff (defaults: 3 attempts, 0.5 s base, 8 s cap;
   all constructor-tunable). Safe because the envelope is idempotency-keyed.
3. **Definitive answers don't retry** — a 4xx is the server's refusal and
   comes back as a refused `PublishReceipt`, not an exception.
4. **`PublishTransportError` = "unknown outcome, replay later"** — raised
   when retries are exhausted, and also when a 2xx arrives with an
   unparseable body (the server MAY have persisted, so a refused receipt
   would lie). Either way, replaying the same envelope later is safe —
   dedup on the key makes the retry a no-op if the first attempt landed.
   Error messages carry status/attempt metadata only, never payload text
   (C9); the SDK does not log at all.

Both the HTTP transport and the retry sleeper are injectable, so the whole
policy is testable with no network and no real sleeps:

```python
import asyncio
import httpx
from lhm import HttpPublisher, PublishTransportError

calls = {"n": 0}

def backend(request: httpx.Request) -> httpx.Response:
    calls["n"] += 1
    if calls["n"] < 3:  # two transient failures, then success
        return httpx.Response(503)
    assert request.url.path == "/api/lhm/v1/metrics"
    assert request.headers["X-Syntropy-Key"] == "sk-demo-not-a-real-key"
    return httpx.Response(200, json={
        "accepted": True,
        "metric_key": "mag_sleep",
        "dashboard_url": "https://app.syntropyhealth.bio/metrics/mag_sleep",
    })

slept: list[float] = []

async def recording_sleeper(delay: float) -> None:
    slept.append(delay)

async def demo() -> None:
    publisher = HttpPublisher(
        cloud,
        transport=httpx.MockTransport(backend),
        sleeper=recording_sleeper,
    )
    receipt = await publisher.publish(envelope)
    print(f"accepted={receipt.accepted} url={receipt.dashboard_url}")
    print(f"attempts: {calls['n']}, backoff slept: {slept}")

    calls["n"] = -100  # force 5xx for every remaining attempt
    try:
        await publisher.publish(envelope)
    except PublishTransportError as err:
        print(f"PublishTransportError: {err}")

asyncio.run(demo())
```

```text
accepted=True url=https://app.syntropyhealth.bio/metrics/mag_sleep
attempts: 3, backoff slept: [0.5, 1.0]
PublishTransportError: publish transport failed after 3 attempt(s) (HTTP 503) — safe to replay (idempotency-keyed)
```

Response bodies are size-capped at 1 MiB before parsing (a receipt is a few
hundred bytes; anything bigger is broken or hostile).

## `InProcessPublisher` — same gates, no HTTP

For consumers embedding LHM in the same process as their store, the
envelope goes to an injected async callback instead of the wire. The claim
gate still runs first — in-process publishing is publishing — which is why
**`public_surface` is a required keyword with no default**: the Rx/public
gate must never default open; the caller states the surface explicitly.

```python
import asyncio
from lhm import InProcessPublisher

async def store(env) -> dict:
    # your persistence here — e.g. write env.spec.model_dump() to your DB
    return {"accepted": True, "metric_key": env.spec.metric_key}

async def demo() -> None:
    publisher = InProcessPublisher(store, public_surface=False)  # REQUIRED kwarg
    receipt = await publisher.publish(envelope)
    print(f"in-process: accepted={receipt.accepted}")

asyncio.run(demo())
```

```text
in-process: accepted=True
```

The callback may return a `PublishReceipt` or a dict shaped like one. An
optional `remove_callback` backs `unpublish`; without one, `unpublish`
raises `NotImplementedError`.

## The Rx / public-surface refusal, end to end

```python
import asyncio
import httpx
from lhm import HttpPublisher, InputRef, SyntropyTarget, build_envelope, to_viz_payload
from lhm.metrics.claims import ClaimScopeError

RX_SPEC = SPEC.model_copy(update={
    "rx_input": True,
    "inputs": (InputRef(resource="MedicationRequest", code="rx-order",
                        display="Synthetic Peptide D (fixture)"),),
})
public_target = SyntropyTarget(base_url="https://public.example.org", public_surface=True)

async def demo() -> None:
    rx_payload = to_viz_payload(RX_SPEC, [])   # private render: fine
    rx_envelope = build_envelope(RX_SPEC, rx_payload, user_scope="demo-subject-001")
    publisher = HttpPublisher(public_target, transport=httpx.MockTransport(backend))
    try:
        await publisher.publish(rx_envelope)
    except ClaimScopeError as err:
        print(f"ClaimScopeError (no byte left the process): {err}")

asyncio.run(demo())
```

```text
ClaimScopeError (no byte left the process): an Rx-input metric is user-private — never a public/marketed surface (C1b)
```

## Unpublish — and the pending app route

`Publisher.unpublish(user_scope, metric_key)` removes a pinned metric.
`HttpPublisher` sends `DELETE {base_url}/api/lhm/v1/metrics/{metric_key}`;
the key is validated against the `MetricSpec` key shape and percent-escaped
before it can touch a URL (hostile keys refuse with `ValueError` before any
request exists), and a 404 answer counts as success (already gone —
idempotent delete).

**Pending route note:** the reference syntropy-journal backend currently
implements only `POST /api/lhm/v1/metrics`; the DELETE route has not landed
app-side yet. Against today's reference backend an `unpublish` therefore
gets a 404 and returns as an idempotent no-op — nothing is actually
removed. The SDK client is contract-complete; track the backend route
before relying on remote unpublish.

## One-call publication from `lhm.ask`

`AskResult.publish(target)` composes everything on this page: it walks the
tier-sorted suggestions, runs `manage` (persist gate), builds the
target-aware `VizPayload` (render gate — an Rx metric on a public target
raises here), assembles the envelope, and hands it to `HttpPublisher(target)`
(or a `publisher=` you inject). When no suggestion is persistable — all
low-tier — it returns a refused receipt, a typed no rather than an
exception.
