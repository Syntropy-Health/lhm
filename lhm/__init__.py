"""LHM — Longevity Health Metrics SDK.

Chat-programmable, clinically-modelled n-of-1 personal metrics with the
honesty layer built in: every metric is a personal correlation/trend with
corrections, an uncertainty interval, and caveats — never a causal or
clinical claim (the claim-scope guard enforces this structurally).

App-independent by construction (A&D D1): no web framework, no ORM, no I/O.
The consuming app adapts its data in and renders the pushed viz payloads.
"""

from lhm.contracts.viz import VizPayload, VizPoint, export_schema, to_viz_payload
from lhm.metrics.claims import BANNED_LEXICON, ClaimScopeError, assert_claim_safe
from lhm.metrics.spec import (
    CorrectionResult,
    FitBlock,
    InputRef,
    MetricSpec,
    OutcomeRef,
)

__version__ = "0.1.0"

__all__ = [
    "BANNED_LEXICON",
    "ClaimScopeError",
    "CorrectionResult",
    "FitBlock",
    "InputRef",
    "MetricSpec",
    "OutcomeRef",
    "VizPayload",
    "VizPoint",
    "__version__",
    "assert_claim_safe",
    "export_schema",
    "to_viz_payload",
]
