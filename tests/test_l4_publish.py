"""L4 — the publish seam: envelope, targets, publishers (design 0001 §5, D11).

Pins:

- The ``lhm-publish/v1`` envelope: version pin, opaque non-email
  ``user_scope`` (C9), tz-aware ``published_at``, spec dump + viz payload.
- Envelope↔route COMPATIBILITY: the SDK envelope's JSON validates against
  the app route's actual request model (imported — drift is structurally
  impossible). App-coupled tests skip when the app isn't importable
  (standalone SDK runs stay clean).
- Idempotency recipe equality with the app store (documented recipe
  replicated, never imported into SDK code; equality pinned here).
- The gate stays in front of the wire (§5.3): banned lexicon and
  Rx-on-public-target refuse BEFORE any transport — the transport mock is
  never called.
- At-least-once retry with capped backoff — injected sleeper, mocked
  transport, no network, no real sleeps.
- Transport is extra-gated: ``lhm.publish`` imports without httpx; the
  missing-dep error names ``pip install "lhm[publish]"``.
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from lhm.contracts.viz import VizPayload, VizPoint
from lhm.metrics.claims import ClaimScopeError
from lhm.metrics.spec import InputRef, MetricSpec, OutcomeRef
from lhm.publish import (
    HttpPublisher,
    InProcessPublisher,
    PublishEnvelope,
    PublishReceipt,
    PublishTransportError,
    SyntropyTarget,
    build_envelope,
    compute_idempotency_key,
)

# The app-coupled contract tests below import the journals route/store; the
# app's config layer runs its LLM dependency contract at import — silence it
# the same way the app's own test bootstrap does (honored only under pytest).
os.environ.setdefault("_LLM_DEP_CONTRACT_SKIP_FOR_PYTEST", "1")

SCOPE = "opaque-subject-042"
STAMP = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)

CLAIM_SAFE_CAVEATS = (
    "Personal n-of-1 association from your own records — observational, not causal.",
    "Estimates carry an uncertainty interval and may not generalize.",
)


def _spec(
    *,
    label: str = "Magnesium glycinate ↔ Sleep quality (association)",
    confidence: str = "mid",
    rx_input: bool = False,
) -> MetricSpec:
    return MetricSpec(
        metric_key="sig_sleep_quality_a1b2c3",
        label=label,
        inputs=(
            InputRef(
                resource="MedicationRequest" if rx_input else "NutritionOrder",
                code="rx-order" if rx_input else "supplement-order",
                display="Magnesium glycinate",
            ),
        ),
        outcome=OutcomeRef(kind="journal_series", series_key="Sleep quality"),
        confidence=confidence,  # type: ignore[arg-type]
        caveats=CLAIM_SAFE_CAVEATS,
        rx_input=rx_input,
    )


def _payload(spec: MetricSpec) -> VizPayload:
    return VizPayload(
        metric_key=spec.metric_key,
        label=spec.label,
        series=(VizPoint(ts=STAMP, value=5.0),),
        confidence=spec.confidence,
        caveats=spec.caveats,
        rx_input=spec.rx_input,
    )


def _envelope(spec: MetricSpec | None = None) -> PublishEnvelope:
    spec = spec or _spec()
    return build_envelope(spec, _payload(spec), SCOPE, published_at=STAMP)


def _target(**kw) -> SyntropyTarget:
    return SyntropyTarget(base_url="https://journal.example.org", **kw)


# --------------------------------------------------------------------------- #
# The envelope models (httpx-free)
# --------------------------------------------------------------------------- #


def test_envelope_wire_shape_pins_version_and_tz():
    wire = _envelope().wire_dict()
    assert wire["envelope_version"] == "lhm-publish/v1"
    assert wire["user_scope"] == SCOPE
    assert wire["published_at"].endswith("Z") or "+" in wire["published_at"]
    assert isinstance(wire["payload"], dict)
    assert wire["payload"]["contract"] == "lhm-viz/v1"
    assert wire["spec"]["metric_key"] == "sig_sleep_quality_a1b2c3"
    assert len(wire["idempotency_key"]) == 64  # sha256 hex


def test_envelope_refuses_naive_published_at():
    spec = _spec()
    with pytest.raises(ValidationError, match="timezone-aware"):
        build_envelope(spec, _payload(spec), SCOPE, published_at=datetime(2026, 7, 10, 12, 0))


def test_envelope_refuses_email_shaped_user_scope():
    spec = _spec()
    with pytest.raises(ValidationError, match="opaque"):
        build_envelope(spec, _payload(spec), "person@example.org", published_at=STAMP)


def test_receipt_defaults():
    receipt = PublishReceipt(accepted=True, metric_key="sig_x")
    assert receipt.dashboard_url is None
    assert receipt.reason == ""


def test_idempotency_key_is_deterministic_and_scope_sensitive():
    spec = _spec()
    param = spec.model_dump(mode="json")
    a = compute_idempotency_key(SCOPE, spec.metric_key, param)
    b = compute_idempotency_key(SCOPE, spec.metric_key, param)
    other = compute_idempotency_key("another-scope", spec.metric_key, param)
    assert a == b
    assert a != other
    assert _envelope(spec).idempotency_key == a


def test_target_never_reprs_the_api_key():
    target = _target(api_key="sk-super-secret")
    assert "sk-super-secret" not in repr(target)
    assert target.headers() == {"X-Syntropy-Key": "sk-super-secret"}
    assert _target().headers() == {}


def test_publish_module_import_does_not_import_httpx():
    """Core import path stays transport-free (D12) — checked in a clean process."""
    code = "import sys; import lhm.publish; assert 'httpx' not in sys.modules, 'httpx leaked'"
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_missing_httpx_error_names_the_publish_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "httpx", None)
    with pytest.raises(ImportError, match=r'pip install "lhm\[publish\]"'):
        HttpPublisher(_target())


# --------------------------------------------------------------------------- #
# Envelope ↔ route compatibility + idempotency recipe equality (app-coupled)
# --------------------------------------------------------------------------- #


def _import_app_module(modname: str):
    """Import an app module; skip on a genuinely-standalone SDK checkout.

    In-repo (design 0002: LHM lives inside the journals repo through the
    split), the app root is LHM's parent — put it on sys.path when pytest's
    rootdir insertion hasn't already (an LHM-only invocation).
    """
    try:
        return importlib.import_module(modname)
    except ModuleNotFoundError:
        app_root = Path(__file__).resolve().parents[2]
        if (app_root / "syntropy_journals").is_dir():
            if str(app_root) not in sys.path:
                sys.path.insert(0, str(app_root))
            try:
                return importlib.import_module(modname)
            except ModuleNotFoundError:
                pass
        pytest.skip(f"{modname} not importable (standalone SDK run)")


def test_sdk_envelope_round_trips_through_the_route_request_model():
    """lhm-publish/v1 drift is structurally impossible: the route's actual
    pydantic request model must validate the SDK envelope's JSON."""
    route = _import_app_module("syntropy_journals.app.api.routes.lhm_metrics")
    envelope = _envelope()
    parsed = route.PublishEnvelopeIn.model_validate(envelope.wire_dict())
    assert parsed.envelope_version == envelope.envelope_version
    assert parsed.spec == envelope.spec
    assert parsed.user_scope == envelope.user_scope
    assert parsed.idempotency_key == envelope.idempotency_key
    assert parsed.published_at == envelope.published_at
    assert parsed.payload["metric_key"] == envelope.payload.metric_key


def test_idempotency_recipe_matches_the_app_store():
    """The SDK replicates (never imports) the store's key recipe — pinned."""
    store = _import_app_module("syntropy_journals.app.integrations.lhm.store")
    spec = _spec()
    param = spec.model_dump(mode="json")
    assert compute_idempotency_key(SCOPE, spec.metric_key, param) == (
        store.compute_idempotency_key(SCOPE, spec.metric_key, param)
    )


# --------------------------------------------------------------------------- #
# The gate stays in front of the wire (§5.3)
# --------------------------------------------------------------------------- #


def _mock_transport(responses):
    """A counting httpx.MockTransport; ``responses`` yields per call."""
    httpx = pytest.importorskip("httpx")
    calls: list = []

    def handler(request):
        calls.append(request)
        result = responses[min(len(calls), len(responses)) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    return httpx.MockTransport(handler), calls


def _accepted_response():
    httpx = pytest.importorskip("httpx")

    return httpx.Response(
        200, json={"accepted": True, "metric_key": "sig_sleep_quality_a1b2c3", "reason": "pinned"}
    )


async def test_banned_lexicon_never_reaches_the_wire():
    transport, calls = _mock_transport([_accepted_response()])
    publisher = HttpPublisher(_target(), transport=transport)
    bad = _envelope(_spec(label="Magnesium improves sleep quality"))
    with pytest.raises(ClaimScopeError, match="banned clinical-efficacy term"):
        await publisher.publish(bad)
    assert calls == []  # nothing crossed the transport


async def test_rx_metric_refuses_public_target_structurally():
    transport, calls = _mock_transport([_accepted_response()])
    publisher = HttpPublisher(_target(public_surface=True), transport=transport)
    rx = _envelope(_spec(rx_input=True))
    with pytest.raises(ClaimScopeError, match="user-private"):
        await publisher.publish(rx)
    assert calls == []


async def test_rx_metric_publishes_to_private_target():
    transport, calls = _mock_transport([_accepted_response()])
    publisher = HttpPublisher(_target(), transport=transport)
    receipt = await publisher.publish(_envelope(_spec(rx_input=True)))
    assert receipt.accepted
    assert len(calls) == 1


async def test_inprocess_publisher_gates_then_hands_off():
    seen: list[PublishEnvelope] = []

    async def callback(envelope: PublishEnvelope):
        seen.append(envelope)
        return {"accepted": True, "metric_key": envelope.spec.metric_key, "reason": "pinned"}

    publisher = InProcessPublisher(callback)
    receipt = await publisher.publish(_envelope())
    assert receipt.accepted
    assert len(seen) == 1

    with pytest.raises(ClaimScopeError):
        await publisher.publish(_envelope(_spec(label="This treats insomnia")))
    assert len(seen) == 1  # the gated envelope never reached the callback


# --------------------------------------------------------------------------- #
# Retry / backoff (mocked transport, injected sleeper — no sleeps, no net)
# --------------------------------------------------------------------------- #


def _sleeper():
    sleeps: list[float] = []

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return sleep, sleeps


async def test_retries_5xx_then_succeeds_with_backoff():
    httpx = pytest.importorskip("httpx")

    transport, calls = _mock_transport(
        [httpx.Response(503), httpx.Response(503), _accepted_response()]
    )
    sleep, sleeps = _sleeper()
    publisher = HttpPublisher(_target(), transport=transport, sleeper=sleep, max_attempts=5)
    receipt = await publisher.publish(_envelope())
    assert receipt.accepted
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]  # exponential from the base, no sleep after success


async def test_retries_transport_errors_and_caps_backoff():
    httpx = pytest.importorskip("httpx")

    transport, calls = _mock_transport([httpx.ConnectError("synthetic connection failure")])
    sleep, sleeps = _sleeper()
    publisher = HttpPublisher(
        _target(),
        transport=transport,
        sleeper=sleep,
        max_attempts=6,
        backoff_cap_s=2.0,
    )
    with pytest.raises(PublishTransportError, match="after 6 attempt"):
        await publisher.publish(_envelope())
    assert len(calls) == 6
    assert sleeps == [0.5, 1.0, 2.0, 2.0, 2.0]  # capped, one fewer sleep than attempts


async def test_4xx_is_definitive_no_retry_refused_receipt():
    httpx = pytest.importorskip("httpx")

    transport, calls = _mock_transport([httpx.Response(401, json={"detail": "invalid key"})])
    sleep, sleeps = _sleeper()
    publisher = HttpPublisher(_target(), transport=transport, sleeper=sleep, max_attempts=5)
    receipt = await publisher.publish(_envelope())
    assert not receipt.accepted
    assert "HTTP 401" in receipt.reason
    assert len(calls) == 1
    assert sleeps == []


async def test_exhausted_5xx_raises_transport_error():
    httpx = pytest.importorskip("httpx")

    transport, calls = _mock_transport([httpx.Response(500)])
    sleep, _ = _sleeper()
    publisher = HttpPublisher(_target(), transport=transport, sleeper=sleep, max_attempts=2)
    with pytest.raises(PublishTransportError, match="HTTP 500"):
        await publisher.publish(_envelope())
    assert len(calls) == 2


async def test_api_key_rides_the_header():
    pytest.importorskip("httpx")

    transport, calls = _mock_transport([_accepted_response(), _accepted_response()])
    keyed = HttpPublisher(_target(api_key="sk-cloud-key"), transport=transport)
    await keyed.publish(_envelope())
    assert calls[0].headers["X-Syntropy-Key"] == "sk-cloud-key"

    unkeyed = HttpPublisher(_target(), transport=transport)
    await unkeyed.publish(_envelope())
    assert "X-Syntropy-Key" not in calls[1].headers
