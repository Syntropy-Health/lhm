"""Publishers — the transport half of the publish seam (design 0001 §5.2-5.3).

Two shipped implementations of the :class:`Publisher` Protocol:

- :class:`HttpPublisher` — POSTs the envelope to
  ``{base_url}/api/lhm/v1/metrics`` (self-hosted journals or the hosted
  cloud; the API key header is the only delta — D11). Transport is httpx,
  imported LAZILY under the ``lhm[publish]`` extra: the envelope/receipt
  models never need it.
- :class:`InProcessPublisher` — the U2 path: no HTTP; hands the envelope to
  an injected app callback (e.g. the journals MCP ``manage_metrics``
  wrapper).

**The gate stays in front of the wire (§5.3):** every ``publish()`` re-runs
``assert_claim_safe`` on the envelope's label + caveats BEFORE any transport
(R10 gates persist AND render — publishing is both), and an Rx-input metric
refuses ``public_surface=True`` targets structurally (the gate raises; no
byte ever leaves the process).

At-least-once delivery: transient failures (transport errors, 5xx) retry
with capped exponential backoff — safe because the envelope is
idempotency-keyed. The sleeper is injectable (tests never sleep). C9: no
payload text ever appears in errors or logs — the SDK does not log at all.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable, Protocol, runtime_checkable
from urllib.parse import quote

from lhm.metrics.claims import assert_claim_safe
from lhm.metrics.spec import METRIC_KEY_MAX_LENGTH, METRIC_KEY_PATTERN
from lhm.publish.envelope import PublishEnvelope, PublishReceipt
from lhm.publish.target import SyntropyTarget

_MISSING_HTTPX_MSG = (
    "the HTTP publisher needs httpx, which ships with the publish extra — "
    'install it with: pip install "lhm[publish]"'
)

#: Cap on any response body the publisher will parse (SEC-5): a receipt is
#: a few hundred bytes; anything bigger is broken or hostile.
MAX_RESPONSE_BYTES = 1024 * 1024  # 1 MiB

_METRIC_KEY_RE = re.compile(METRIC_KEY_PATTERN)


def _validate_metric_key(metric_key: str) -> str:
    """Refuse any metric_key that doesn't match the MetricSpec shape (SEC-1).

    The key is interpolated into a URL path — a traversal ("../…") or
    query-smuggling ("k?x=") key must never produce a request. The raw
    value is deliberately absent from the error message (hostile input).
    """
    if len(metric_key) > METRIC_KEY_MAX_LENGTH or not _METRIC_KEY_RE.fullmatch(metric_key):
        raise ValueError(
            f"metric_key must match {METRIC_KEY_PATTERN} "
            f"(<= {METRIC_KEY_MAX_LENGTH} chars) — refusing to build a URL from it"
        )
    return metric_key


def _require_httpx() -> Any:
    """Lazy httpx import — core stays transport-free (D12 extras tiers)."""
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised via sys.modules stub
        raise ImportError(_MISSING_HTTPX_MSG) from exc
    return httpx


class PublishTransportError(RuntimeError):
    """Transport exhausted its retries — the caller may replay later.

    Replays are safe: the envelope carries a deterministic idempotency key
    (at-least-once by construction). The message never carries payload text
    (C9) — status/attempt metadata only.
    """


def _gate_envelope(envelope: PublishEnvelope, *, public_surface: bool) -> None:
    """R10 before ANY transport — claim-violating text never reaches the wire."""
    spec = envelope.spec
    assert_claim_safe(
        spec.label,
        spec.caveats,
        spec.confidence,
        spec.rx_input,
        public_surface=public_surface,
    )


@runtime_checkable
class Publisher(Protocol):
    """The publisher seam (design 0001 §5.2)."""

    async def publish(self, envelope: PublishEnvelope) -> PublishReceipt: ...

    async def unpublish(self, user_scope: str, metric_key: str) -> None: ...


class HttpPublisher:
    """POST the lhm-publish/v1 envelope to a syntropy-journal backend.

    ``transport`` and ``sleeper`` are injection points for tests (an
    ``httpx.MockTransport`` and a recording no-op respectively) — no real
    network, no real sleeps required to exercise the retry policy.
    """

    def __init__(
        self,
        target: SyntropyTarget,
        *,
        transport: Any | None = None,
        sleeper: Callable[[float], Awaitable[None]] | None = None,
        max_attempts: int = 3,
        backoff_base_s: float = 0.5,
        backoff_cap_s: float = 8.0,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        self._httpx = _require_httpx()
        self._target = target
        self._transport = transport
        self._sleeper = sleeper or asyncio.sleep
        self._max_attempts = max_attempts
        self._backoff_base_s = backoff_base_s
        self._backoff_cap_s = backoff_cap_s

    async def publish(self, envelope: PublishEnvelope) -> PublishReceipt:
        """Gate → POST (with retry) → receipt.

        Non-retryable server refusals (4xx) come back as a refused
        :class:`PublishReceipt`; retryable exhaustion raises
        :class:`PublishTransportError`.
        """
        _gate_envelope(envelope, public_surface=self._target.public_surface)
        response = await self._send("POST", self._target.publish_url(), envelope.wire_dict())
        if response.status_code >= 400:
            return PublishReceipt(
                accepted=False,
                metric_key=envelope.spec.metric_key,
                reason=f"target refused with HTTP {response.status_code}",
            )
        return self._parse_receipt(response)

    def _parse_receipt(self, response: Any) -> PublishReceipt:
        """Parse a 2xx receipt body — malformed success is a TRANSPORT error.

        The server answered 2xx, so it MAY have persisted: a refused receipt
        would lie. :class:`PublishTransportError` keeps the at-least-once /
        safe-to-replay semantics (the envelope is idempotency-keyed). The
        body is size-capped (SEC-5) before any parse.
        """
        declared = response.headers.get("Content-Length", "")
        if declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
            raise PublishTransportError(
                f"publish response declares {declared} bytes — over the "
                f"{MAX_RESPONSE_BYTES}-byte cap; refusing to parse (safe to replay)"
            )
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise PublishTransportError(
                f"publish response body exceeds the {MAX_RESPONSE_BYTES}-byte cap — "
                "refusing to parse (safe to replay)"
            )
        try:
            data = response.json()
        except ValueError as exc:  # json.JSONDecodeError is a ValueError
            raise PublishTransportError(
                "publish returned 2xx but the receipt body is not valid JSON — "
                "the server MAY have persisted; safe to replay (idempotency-keyed)"
            ) from exc
        return PublishReceipt.model_validate(data)

    async def unpublish(self, user_scope: str, metric_key: str) -> None:
        """DELETE the pinned metric — idempotent: a 404 is already-gone = success.

        ``metric_key`` is validated against the MetricSpec key shape AND
        percent-escaped before it touches the URL (SEC-1) — a hostile key
        refuses with :class:`ValueError` before any request is built.
        """
        key = _validate_metric_key(metric_key)
        url = f"{self._target.publish_url()}/{quote(key, safe='')}"
        response = await self._send("DELETE", url, {"user_scope": user_scope})
        if response.status_code == 404:
            return  # idempotent delete: the metric is already gone
        if response.status_code >= 400:
            raise PublishTransportError(
                f"unpublish refused with HTTP {response.status_code} for {metric_key!r}"
            )

    async def _send(self, method: str, url: str, body: dict[str, Any]) -> Any:
        """At-least-once request with capped exponential backoff.

        Retries transport errors and 5xx; returns the first definitive
        response (2xx/3xx/4xx). Raises :class:`PublishTransportError` when
        every attempt was retryable.
        """
        httpx = self._httpx
        delay = self._backoff_base_s
        last_status: int | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                async with httpx.AsyncClient(
                    timeout=self._target.timeout_s,
                    transport=self._transport,
                ) as client:
                    response = await client.request(
                        method, url, json=body, headers=self._target.headers()
                    )
            except httpx.TransportError:
                last_status = None
            else:
                if response.status_code < 500:
                    return response
                last_status = response.status_code
            if attempt < self._max_attempts:
                await self._sleeper(delay)
                delay = min(delay * 2, self._backoff_cap_s)
        detail = f"HTTP {last_status}" if last_status is not None else "transport error"
        raise PublishTransportError(
            f"publish transport failed after {self._max_attempts} attempt(s) ({detail}) — "
            "safe to replay (idempotency-keyed)"
        )


class InProcessPublisher:
    """The U2 path: no HTTP — the envelope goes to an injected app callback.

    The callback owns persistence (e.g. the journals ``manage_metrics``
    wrapper) and answers with a :class:`PublishReceipt` (or a dict shaped
    like one). The R10 gate still runs BEFORE the callback: in-process
    publishing is publishing.

    ``public_surface`` is a REQUIRED keyword (SEC-3): the Rx/public gate
    must never default open — the caller states the surface explicitly.
    """

    def __init__(
        self,
        callback: Callable[[PublishEnvelope], Awaitable[PublishReceipt | dict[str, Any]]],
        *,
        public_surface: bool,
        remove_callback: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self._callback = callback
        self._remove_callback = remove_callback
        self._public_surface = public_surface

    async def publish(self, envelope: PublishEnvelope) -> PublishReceipt:
        _gate_envelope(envelope, public_surface=self._public_surface)
        result = await self._callback(envelope)
        if isinstance(result, PublishReceipt):
            return result
        return PublishReceipt.model_validate(result)

    async def unpublish(self, user_scope: str, metric_key: str) -> None:
        if self._remove_callback is None:
            raise NotImplementedError("this InProcessPublisher has no remove_callback")
        await self._remove_callback(user_scope, metric_key)


__all__ = [
    "MAX_RESPONSE_BYTES",
    "HttpPublisher",
    "InProcessPublisher",
    "PublishTransportError",
    "Publisher",
]
