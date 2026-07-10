"""The ``lhm-publish/v1`` envelope — the data model that crosses the wire.

Design 0001 §5.1 (D11): ONE versioned envelope for self-hosted and cloud —
``MetricSpec`` (the C4 parameterization source) + ``VizPayload`` (the
lhm-viz/v1 render contract) + an OPAQUE subject token + a deterministic
idempotency key + a tz-aware client stamp. Additive-only within v1.

The consuming backend persists ``spec``, renders from ``payload``, and
dedupes on ``idempotency_key`` — publishing is at-least-once safe by
construction. :func:`compute_idempotency_key` replicates the app store's
documented recipe (``sha256(user_scope + ':' + metric_key + ':' +
canonical-spec-json)`` with sorted-keys compact JSON) WITHOUT importing the
app; equality is pinned by test.

These models work without httpx installed — transport is a separate,
extra-gated concern (:mod:`lhm.publish.publisher`).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from lhm.contracts.viz import VizPayload  # noqa: TC001 - runtime pydantic field type
from lhm.metrics.spec import MetricSpec  # noqa: TC001 - runtime pydantic field type

ENVELOPE_VERSION = "lhm-publish/v1"


def compute_idempotency_key(user_scope: str, metric_key: str, parameterization: dict) -> str:
    """``sha256(user_scope:metric_key:canonical-spec-json)`` (design §5.1).

    MUST stay recipe-identical to the app store's
    ``compute_idempotency_key`` (``syntropy_journals…lhm/store.py``) so a
    client-stamped key and the server-computed key agree — the equality is
    contract-tested against the app implementation, never imported.
    """
    canonical = json.dumps(parameterization, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{user_scope}:{metric_key}:{canonical}".encode()).hexdigest()


class PublishEnvelope(BaseModel):
    """Everything a syntropy-journal backend needs to surface one metric.

    Versioned like the viz contract: additive-only within v1 (D11).
    ``user_scope`` is the OPAQUE subject token (C9 — never PII; email-shaped
    scopes are refused at construction, mirroring the route + mapping
    guards). ``published_at`` must be tz-aware.
    """

    model_config = ConfigDict(frozen=True)

    envelope_version: Literal["lhm-publish/v1"] = ENVELOPE_VERSION
    spec: MetricSpec
    payload: VizPayload
    user_scope: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    published_at: datetime

    @field_validator("published_at")
    @classmethod
    def _tz_aware(cls, v: datetime) -> datetime:
        if v.tzinfo is None or v.tzinfo.utcoffset(v) is None:
            raise ValueError("published_at must be timezone-aware")
        return v

    @field_validator("user_scope")
    @classmethod
    def _opaque_scope(cls, v: str) -> str:
        if "@" in v:
            raise ValueError("user_scope must be an opaque token, not an email (C9)")
        return v

    def wire_dict(self) -> dict[str, Any]:
        """The JSON-ready dict the route accepts (``payload`` as plain dict)."""
        return self.model_dump(mode="json")


class PublishReceipt(BaseModel):
    """What a publish answers with (design 0001 §5.2) — tolerant reader.

    ``dashboard_url`` is server-controlled text handed to consumers as a
    clickable link — only ``http(s)`` URLs survive; anything else (e.g. a
    ``javascript:`` URI) coerces to ``None``. Fail-soft by design: the
    publish itself succeeded, so a bad link never fails the receipt.
    """

    model_config = ConfigDict(frozen=True)

    accepted: bool
    metric_key: str
    dashboard_url: str | None = None  #: deep link to the rendered card
    reason: str = ""

    @field_validator("dashboard_url")
    @classmethod
    def _http_links_only(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return v if urlsplit(v).scheme.casefold() in ("http", "https") else None


def build_envelope(
    spec: MetricSpec,
    payload: VizPayload,
    user_scope: str,
    published_at: datetime | None = None,
) -> PublishEnvelope:
    """Assemble a v1 envelope, computing the idempotency key from the spec."""
    return PublishEnvelope(
        spec=spec,
        payload=payload,
        user_scope=user_scope,
        idempotency_key=compute_idempotency_key(
            user_scope, spec.metric_key, spec.model_dump(mode="json")
        ),
        published_at=published_at or datetime.now(timezone.utc),
    )


__all__ = [
    "ENVELOPE_VERSION",
    "PublishEnvelope",
    "PublishReceipt",
    "build_envelope",
    "compute_idempotency_key",
]
