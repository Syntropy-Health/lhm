"""LHM — Longevity Health Metrics SDK.

Chat-programmable, clinically-modelled n-of-1 personal metrics with the
honesty layer built in: every metric is a personal correlation/trend with
corrections, an uncertainty interval, and caveats — never a causal or
clinical claim (the claim-scope guard enforces this structurally).

App-independent by construction (A&D D1): no web framework, no ORM, no LLM
dep, no network in the core import path (HTTP transport is lazy under the
``lhm[publish]`` extra). The consuming app adapts its data in and renders
the pushed viz payloads.

This module is the curated front door — consumers import from ``lhm``
directly; the submodule layout is an implementation detail.
"""

from lhm.ask import DEFAULT_USER_SCOPE, AskResult, ask
from lhm.contracts.viz import VizPayload, VizPoint, export_schema, to_viz_payload
from lhm.metrics.claims import BANNED_LEXICON, ClaimScopeError, assert_claim_safe
from lhm.metrics.spec import (
    CorrectionResult,
    FitBlock,
    InputRef,
    MetricSpec,
    OutcomeRef,
)
from lhm.models.mapping import (
    MappingError,
    OrderRecord,
    RegimenStartRecord,
    SeriesPoint,
    SeriesRecord,
    map_order,
    map_regimen_start,
    map_series,
)
from lhm.nl import (
    AmbiguousIntentError,
    Interpreter,
    MetricIntent,
    ResolvedIntent,
    UnresolvableIntentError,
    resolve_intent,
)
from lhm.publish import (
    HttpPublisher,
    InProcessPublisher,
    PublishEnvelope,
    Publisher,
    PublishReceipt,
    PublishTransportError,
    SyntropyTarget,
    build_envelope,
    compute_idempotency_key,
)
from lhm.tools.pipeline import (
    AlignedData,
    AlignedPair,
    CandidateSignal,
    LhmAdapter,
    ManageOutcome,
    analyze,
    fetch,
    manage,
)

__version__ = "0.1.0"

__all__ = [
    "BANNED_LEXICON",
    "DEFAULT_USER_SCOPE",
    "AlignedData",
    "AlignedPair",
    "AmbiguousIntentError",
    "AskResult",
    "CandidateSignal",
    "ClaimScopeError",
    "CorrectionResult",
    "FitBlock",
    "HttpPublisher",
    "InProcessPublisher",
    "InputRef",
    "Interpreter",
    "LhmAdapter",
    "ManageOutcome",
    "MappingError",
    "MetricIntent",
    "MetricSpec",
    "OrderRecord",
    "OutcomeRef",
    "PublishEnvelope",
    "PublishReceipt",
    "PublishTransportError",
    "Publisher",
    "RegimenStartRecord",
    "ResolvedIntent",
    "SeriesPoint",
    "SeriesRecord",
    "SyntropyTarget",
    "UnresolvableIntentError",
    "VizPayload",
    "VizPoint",
    "__version__",
    "analyze",
    "ask",
    "assert_claim_safe",
    "build_envelope",
    "compute_idempotency_key",
    "export_schema",
    "fetch",
    "manage",
    "map_order",
    "map_regimen_start",
    "map_series",
    "resolve_intent",
    "to_viz_payload",
]
