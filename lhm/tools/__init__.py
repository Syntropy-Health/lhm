"""D6 tool surface (L2) — three plain async fns the app wraps as MCP tools.

App-independence (D1): these are ordinary async functions with typed inputs —
NO Reflex, NO DB, NO auth. The consuming app registers them in its own tool
registry with its auth/τ/InjectedState conventions and owns every persist.
"""

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

__all__ = [
    "AlignedData",
    "AlignedPair",
    "CandidateSignal",
    "LhmAdapter",
    "ManageOutcome",
    "analyze",
    "fetch",
    "manage",
]
