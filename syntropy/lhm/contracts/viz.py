"""The LHM→journals viz-push contract — ``lhm-viz/v1`` (A&D D5, amendment A3).

"Push" is a **library call, not a network hop**: after a metric is pinned the
consuming app calls :func:`to_viz_payload` and hands the returned
:class:`VizPayload` to its own render surface. LHM owns the payload SHAPE
(this module + the committed JSON schema shipped as package data); the app
owns the render. A future remote deployment reuses the same schema over MCP
unchanged.

Discipline (mirrors the monorepo component-descriptor contract):

* the committed ``lhm-viz.v1.schema.json`` is the single source of truth —
  :func:`export_schema` must reproduce it byte-for-byte (contract-tested);
* additive-only within v1; breaking changes ship ``lhm-viz/v2``;
* consumers are TOLERANT READERS (unknown fields ignored — pydantic default);
* ``rx_input`` travels WITH the payload so the render gate re-checks the C1b
  rules independently of upstream (A&D F8);
* fixtures under ``contracts/fixtures/{valid,invalid}/`` are the pact-lite
  suite every consumer adapter must run.
"""

from __future__ import annotations

import json
from datetime import datetime  # noqa: TC003 - pydantic needs the runtime type
from importlib import resources
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from syntropy.lhm.metrics.claims import assert_claim_safe
from syntropy.lhm.metrics.spec import Confidence, MetricSpec  # noqa: TC001 - runtime use

CONTRACT_ID = "lhm-viz/v1"
SCHEMA_FILENAME = "lhm-viz.v1.schema.json"
_SCHEMA_META_ID = "https://contracts.syntropyhealth.bio/lhm-viz/v1.schema.json"


class VizPoint(BaseModel):
    """One (timestamp, value) sample of the rendered trend."""

    model_config = ConfigDict(frozen=True)

    ts: datetime
    value: float


class VizPayload(BaseModel):
    """The versioned wire shape the app's dashboard renders (A&D D5)."""

    model_config = ConfigDict(frozen=True)  # extra: pydantic default ignore → tolerant reader

    contract: Literal["lhm-viz/v1"] = CONTRACT_ID
    metric_key: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    label: str = Field(min_length=1)
    series: tuple[VizPoint, ...]
    #: The R1 regimen start-date marker (rendered as a rule); None when the
    #: fetch produced no start date.
    regimen_start: datetime | None = None
    confidence: Confidence
    caveats: tuple[str, ...] = Field(min_length=1)
    #: C1b tier bit — travels WITH the payload so the render gate re-checks
    #: the Rx rules independently (A&D F8).
    rx_input: bool
    render_hint: Literal["trend_card"] = "trend_card"


def to_viz_payload(
    spec: MetricSpec,
    series: tuple[VizPoint, ...] | list[VizPoint],
    regimen_start: datetime | None = None,
    *,
    public_surface: bool = False,
) -> VizPayload:
    """Build the render payload for a pinned metric — the R10 RENDER gate.

    Re-runs :func:`assert_claim_safe` (fail-closed) so a payload can never be
    constructed from claim-violating text, regardless of what upstream did.
    ``regimen_start`` comes from the R1 fetch result (A&D F9).
    """
    assert_claim_safe(
        spec.label,
        spec.caveats,
        spec.confidence,
        spec.rx_input,
        public_surface=public_surface,
    )
    return VizPayload(
        metric_key=spec.metric_key,
        label=spec.label,
        series=tuple(series),
        regimen_start=regimen_start,
        confidence=spec.confidence,
        caveats=spec.caveats,
        rx_input=spec.rx_input,
    )


def export_schema() -> str:
    """The canonical JSON-schema text for ``lhm-viz/v1``.

    Deterministic (sorted keys, 2-space indent, trailing newline) so the
    committed ``lhm-viz.v1.schema.json`` package-data file can be verified
    byte-for-byte in the contract test — regenerate the file with
    ``python -m syntropy.lhm.contracts.viz`` after any INTENTIONAL additive change.

    PYDANTIC COUPLING (QGR L0 code-F7): the byte-match also pins the exact
    ``model_json_schema()`` output of the LOCKED pydantic version — a pydantic
    minor upgrade can red the contract test with NO intentional schema change.
    When that happens: diff the regenerated schema; if the delta is purely
    representational (const/enum encoding), regenerate + commit WITH the
    pydantic bump and note it; a SEMANTIC delta means mint v2.
    """
    schema = VizPayload.model_json_schema()
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": _SCHEMA_META_ID,
        **schema,
    }
    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def committed_schema() -> str:
    """The schema text shipped as package data (what consumers validate against)."""
    return (
        resources.files("syntropy.lhm.contracts")
        .joinpath(SCHEMA_FILENAME)
        .read_text(encoding="utf-8")
    )


if __name__ == "__main__":  # pragma: no cover - regeneration helper
    import pathlib

    target = pathlib.Path(__file__).with_name(SCHEMA_FILENAME)
    target.write_text(export_schema(), encoding="utf-8")
    print(f"wrote {target}")


__all__ = [
    "CONTRACT_ID",
    "SCHEMA_FILENAME",
    "VizPayload",
    "VizPoint",
    "committed_schema",
    "export_schema",
    "to_viz_payload",
]
