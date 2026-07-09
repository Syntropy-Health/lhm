"""Clinical-data-modelling layer (A&D D2) — FHIR-backed mapping.

L0 DECISION — FHIR emit line = **R4B** (`fhir.resources.R4B`): the widest
ecosystem interop line (most deployed servers/tooling speak R4/R4B; R5
adoption is still sparse), per the A&D's "bias R4B" ratification item. The
mapping functions (``map_order`` / ``map_regimen_start`` / ``map_series``)
land in L1 and import from ``fhir.resources.R4B`` exclusively — mixing emit
lines in one payload is a contract error.
"""

#: The FHIR version every LHM-emitted resource uses (L0 decision, A&D D2).
FHIR_VERSION = "R4B"

__all__ = ["FHIR_VERSION"]
