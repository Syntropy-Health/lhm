"""L1 — D2 mapping functions + synthetic fixtures (A&D D2/D3, R4B line).

Covers: the four-resource mapping contract, the C9 PHI boundary (note text
never serialized), the D3 sentiment injection seam, fail-loud validation,
fixture determinism, and the R4B-exclusivity pin.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fhir.resources.R4B.medicationrequest import MedicationRequest
from fhir.resources.R4B.medicationstatement import MedicationStatement
from fhir.resources.R4B.nutritionorder import NutritionOrder
from fhir.resources.R4B.observation import Observation

from syntropy.lhm.fixtures import (
    SYNTHETIC_EPOCH,
    synthetic_order,
    synthetic_regimen_start,
    synthetic_series,
    synthetic_subject_ref,
)
from syntropy.lhm.models import FHIR_VERSION
from syntropy.lhm.models.mapping import (
    LHM_CODE_SYSTEM,
    SENTIMENT_CODE,
    UCUM_SYSTEM,
    MappingError,
    OrderRecord,
    RegimenStartRecord,
    SeriesPoint,
    SeriesRecord,
    map_order,
    map_regimen_start,
    map_series,
)

_AWARE = datetime(2026, 2, 1, 12, 0, tzinfo=timezone.utc)


class TestMapOrder:
    def test_supplement_order_is_nutrition_order(self) -> None:
        res = map_order(synthetic_order(1))
        assert isinstance(res, NutritionOrder)
        assert res.status == "completed" and res.intent == "order"
        assert res.patient.reference.startswith("Patient/syn-subject-")
        # QG fix 2026-07-09: the product rides NutritionOrder.supplement
        # (oralDiet models a therapeutic-diet CLASS, not a product).
        supp = res.supplement[0]
        assert supp.type.coding[0].system == LHM_CODE_SYSTEM
        assert supp.type.coding[0].code == "supplement-order"
        assert supp.productName == res.supplement[0].type.text or supp.productName

    def test_rx_order_is_medication_request(self) -> None:
        res = map_order(synthetic_order(2, is_rx=True))
        assert isinstance(res, MedicationRequest)
        assert res.status == "completed" and res.intent == "order"
        assert res.medicationCodeableConcept.coding[0].code == "rx-order"

    def test_naive_timestamp_fails_loud(self) -> None:
        rec = OrderRecord(
            subject_ref="syn-subject-001",
            item_display="Synthetic Compound A",
            occurred_at=datetime(2026, 2, 1),  # naive
        )
        with pytest.raises(MappingError, match="timezone-aware"):
            map_order(rec)

    def test_empty_subject_fails_loud(self) -> None:
        rec = OrderRecord(subject_ref="  ", item_display="X", occurred_at=_AWARE)
        with pytest.raises(MappingError, match="subject_ref"):
            map_order(rec)


class TestMapRegimenStart:
    def test_start_marker_shape(self) -> None:
        res = map_regimen_start(synthetic_regimen_start(3))
        assert isinstance(res, MedicationStatement)
        assert res.status == "active"
        assert res.effectiveDateTime >= SYNTHETIC_EPOCH
        dose = res.dosage[0].doseAndRate[0].doseQuantity
        assert dose.system == UCUM_SYSTEM and dose.value > 0

    def test_doseless_regimen_has_no_dosage(self) -> None:
        rec = RegimenStartRecord(
            subject_ref=synthetic_subject_ref(5),
            substance_display="Synthetic Compound B",
            started_at=_AWARE,
        )
        assert map_regimen_start(rec).dosage is None


class TestMapSeries:
    def test_numeric_points_become_ucum_observations(self) -> None:
        obs = map_series(synthetic_series(4, with_notes=False))
        assert obs and all(isinstance(o, Observation) for o in obs)
        assert len(obs) == 14  # one per numeric point, no sentiment
        q = obs[0].valueQuantity
        assert q.system == UCUM_SYSTEM and q.code == "/min"

    def test_unitless_series_omits_ucum_system(self) -> None:
        rec = SeriesRecord(
            subject_ref=synthetic_subject_ref(1),
            metric_display="Synthetic energy rating",
            unit="",
            points=(SeriesPoint(observed_at=_AWARE, value=7.0),),
        )
        q = map_series(rec)[0].valueQuantity
        assert q.system is None and q.unit is None and q.value == 7.0

    def test_sentiment_fn_injection_emits_sentiment_observations(self) -> None:
        obs = map_series(synthetic_series(4), sentiment_fn=lambda s: -0.25)
        sentiment = [o for o in obs if o.code.coding[0].code == SENTIMENT_CODE]
        assert len(sentiment) == 3  # every 5th of 14 daily points carries a note
        assert all(o.valueQuantity.value == -0.25 for o in sentiment)

    def test_no_sentiment_fn_skips_note_points(self) -> None:
        obs = map_series(synthetic_series(4))  # notes present, no fn
        assert all(o.code.coding[0].code != SENTIMENT_CODE for o in obs)

    def test_note_text_never_serialized(self) -> None:
        """C9 PHI boundary: the raw note must not appear ANYWHERE in the
        serialized resources — only the injected classifier's float does."""
        rec = synthetic_series(7)
        notes = [p.note for p in rec.points if p.note]
        assert notes, "fixture must carry notes for this test to bite"
        obs = map_series(rec, sentiment_fn=lambda s: 0.9)
        blob = json.dumps([o.model_dump(exclude_none=True) for o in obs], default=str)
        for note in notes:
            assert note not in blob

    def test_empty_point_fails_loud(self) -> None:
        rec = SeriesRecord(
            subject_ref=synthetic_subject_ref(1),
            metric_display="Synthetic energy rating",
            points=(SeriesPoint(observed_at=_AWARE),),
        )
        with pytest.raises(MappingError, match="neither value nor note"):
            map_series(rec)


class TestFixtureDeterminism:
    def test_same_seed_same_records(self) -> None:
        assert synthetic_order(9) == synthetic_order(9)
        assert synthetic_regimen_start(9) == synthetic_regimen_start(9)
        assert synthetic_series(9) == synthetic_series(9)

    def test_different_seed_different_records(self) -> None:
        assert synthetic_order(1) != synthetic_order(2)

    def test_timestamps_are_epoch_derived_and_aware(self) -> None:
        rec = synthetic_series(2, n=5)
        for p in rec.points:
            assert p.observed_at.tzinfo is not None
            assert SYNTHETIC_EPOCH <= p.observed_at <= SYNTHETIC_EPOCH + timedelta(days=365)


class TestR4BExclusivity:
    def test_mapping_imports_r4b_only(self) -> None:
        """The emit line is R4B exclusively (lhm.models.FHIR_VERSION) —
        mixing lines in one payload is a contract error."""
        from pathlib import Path

        import syntropy.lhm.models.mapping as mapping_mod

        src = Path(mapping_mod.__file__).read_text()
        assert FHIR_VERSION == "R4B"
        assert "from fhir.resources.R4B." in src
        # No non-R4B fhir.resources import (the default line is R5).
        for line in src.splitlines():
            if "from fhir.resources" in line:
                assert ".R4B." in line, f"non-R4B fhir import: {line.strip()}"


class TestReviewRoundAdditions:
    """QG round 2026-07-09 — coverage the test review found missing."""

    def test_sentiment_fn_receives_each_note_verbatim_exactly_once(self) -> None:
        """D3 seam positive case: the classifier gets the exact note text,
        once per note point, and is the ONLY consumer of it."""
        rec = synthetic_series(4)
        expected_notes = [p.note for p in rec.points if p.note]
        seen: list[str] = []

        def spy(note: str) -> float:
            seen.append(note)
            return 0.1 * len(seen)

        obs = map_series(rec, sentiment_fn=spy)
        assert seen == expected_notes
        scores = [o.valueQuantity.value for o in obs if o.code.coding[0].code == SENTIMENT_CODE]
        assert [float(s) for s in scores] == [pytest.approx(0.1 * (i + 1)) for i in range(3)]

    def test_note_leak_sentinel_across_fields_and_fragments(self) -> None:
        """Stronger C9 pin: a rare sentinel note must not appear in the
        serialized output — verbatim, prefix-fragment, or reversed."""
        sentinel = "ZQXJKVWPY-lhm-c9-sentinel-7f3a9b"
        rec = SeriesRecord(
            subject_ref=synthetic_subject_ref(1),
            metric_display="Synthetic energy rating",
            points=(SeriesPoint(observed_at=_AWARE, value=5.0, note=sentinel),),
        )
        obs = map_series(rec, sentiment_fn=lambda s: 0.5)
        blob = json.dumps([o.model_dump(exclude_none=True) for o in obs], default=str)
        assert sentinel not in blob
        assert sentinel[:12] not in blob  # partial-copy regression
        assert sentinel[::-1] not in blob  # trivially-transformed leak

    def test_unknown_code_key_fails_open(self) -> None:
        """CURATED_CODES is a display-curation table, NOT an emission gate."""
        rec = OrderRecord(
            subject_ref=synthetic_subject_ref(1),
            item_display="Custom App Metric",
            occurred_at=_AWARE,
            code="app-custom-key",
        )
        res = map_order(rec)
        coding = res.supplement[0].type.coding[0]
        assert coding.code == "app-custom-key"
        assert coding.display == "Custom App Metric"  # text fallback

    def test_default_code_fallbacks_when_code_empty(self) -> None:
        supp = map_order(
            OrderRecord(subject_ref="syn-s", item_display="X", occurred_at=_AWARE, code="")
        )
        assert supp.supplement[0].type.coding[0].code == "supplement-order"
        rx = map_order(
            OrderRecord(
                subject_ref="syn-s", item_display="X", occurred_at=_AWARE, is_rx=True, code=""
            )
        )
        assert rx.medicationCodeableConcept.coding[0].code == "rx-order"
        obs = map_series(
            SeriesRecord(
                subject_ref="syn-s",
                metric_display="M",
                code="",
                points=(SeriesPoint(observed_at=_AWARE, value=1.0),),
            )
        )
        assert obs[0].code.coding[0].code == "journal-metric"

    @pytest.mark.parametrize(
        "build",
        [
            lambda: map_order(
                OrderRecord(subject_ref="syn-s", item_display="  ", occurred_at=_AWARE)
            ),
            lambda: map_regimen_start(
                RegimenStartRecord(subject_ref="syn-s", substance_display="", started_at=_AWARE)
            ),
            lambda: map_series(
                SeriesRecord(
                    subject_ref="syn-s",
                    metric_display="",
                    points=(SeriesPoint(observed_at=_AWARE, value=1.0),),
                )
            ),
        ],
    )
    def test_empty_display_fails_loud_in_all_three(self, build) -> None:
        with pytest.raises(MappingError, match="display text"):
            build()

    def test_email_shaped_subject_ref_rejected(self) -> None:
        """C9 opacity tightening: an email-shaped subject token is a PHI
        leak at the source."""
        rec = OrderRecord(subject_ref="buyer@example.com", item_display="X", occurred_at=_AWARE)
        with pytest.raises(MappingError, match="opaque"):
            map_order(rec)

    def test_sentiment_int_return_coerces_and_bad_return_is_mapping_error(self) -> None:
        rec = SeriesRecord(
            subject_ref="syn-s",
            metric_display="M",
            points=(SeriesPoint(observed_at=_AWARE, note="synthetic note"),),
        )
        obs = map_series(rec, sentiment_fn=lambda s: 1)  # int → 1.0
        assert float(obs[0].valueQuantity.value) == 1.0
        with pytest.raises(MappingError, match="non-numeric"):
            map_series(rec, sentiment_fn=lambda s: "bad")

    def test_unitless_dose_omits_ucum_system(self) -> None:
        rec = RegimenStartRecord(
            subject_ref="syn-s",
            substance_display="Synthetic Compound A",
            started_at=_AWARE,
            dose_value=2.0,
            dose_unit="",
        )
        dose = map_regimen_start(rec).dosage[0].doseAndRate[0].doseQuantity
        assert dose.value == 2.0 and dose.system is None and dose.unit is None

    def test_note_only_point_emits_exactly_one_sentiment_observation(self) -> None:
        rec = SeriesRecord(
            subject_ref="syn-s",
            metric_display="M",
            points=(SeriesPoint(observed_at=_AWARE, note="synthetic note"),),
        )
        obs = map_series(rec, sentiment_fn=lambda s: 0.2)
        assert len(obs) == 1
        assert obs[0].code.coding[0].code == SENTIMENT_CODE

    def test_empty_series_maps_to_empty_list(self) -> None:
        rec = SeriesRecord(subject_ref="syn-s", metric_display="M", points=())
        assert map_series(rec) == []

    def test_point_with_value_and_note_emits_two_observations(self) -> None:
        # The synthetic default: 14 daily points, every 5th also carries a
        # note → 14 numeric + 3 sentiment = 17 total (pins the fixture
        # arithmetic the earlier per-kind counts relied on implicitly).
        obs = map_series(synthetic_series(4), sentiment_fn=lambda s: 0.5)
        assert len(obs) == 17
