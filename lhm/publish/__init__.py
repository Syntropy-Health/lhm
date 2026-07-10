"""``lhm.publish`` — the publisher seam (L4, design 0001 §5, D11).

One versioned envelope (``lhm-publish/v1``) for self-hosted and cloud — the
API key is the only delta. Claim-gated before transport, idempotency-keyed
(at-least-once safe), Rx-tier refuses public targets structurally.

Importing this package never imports httpx — the HTTP transport is lazy
under the ``lhm[publish]`` extra; the envelope/receipt/target models work
without it.
"""

from lhm.publish.envelope import (
    ENVELOPE_VERSION,
    PublishEnvelope,
    PublishReceipt,
    build_envelope,
    compute_idempotency_key,
)
from lhm.publish.publisher import (
    HttpPublisher,
    InProcessPublisher,
    Publisher,
    PublishTransportError,
)
from lhm.publish.target import API_KEY_HEADER, PUBLISH_PATH, SyntropyTarget

__all__ = [
    "API_KEY_HEADER",
    "ENVELOPE_VERSION",
    "PUBLISH_PATH",
    "HttpPublisher",
    "InProcessPublisher",
    "PublishEnvelope",
    "PublishReceipt",
    "PublishTransportError",
    "Publisher",
    "SyntropyTarget",
    "build_envelope",
    "compute_idempotency_key",
]
