"""Tests for building FHIR resources and the transaction Bundle from a LabResult."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from labpipe.common import ids
from labpipe.hop1.parse import parse_oru
from labpipe.hop1.to_fhir import build_bundle

GOLDEN = Path(__file__).parent / "fixtures" / "golden"


@pytest.fixture(scope="module")
def result():
    raw = (GOLDEN / "oru_bmp000001.hl7").read_text(encoding="utf-8").replace("\n", "\r")
    return parse_oru(raw)


@pytest.fixture(scope="module")
def bundle(result):
    return json.loads(build_bundle(result).model_dump_json())


def resources(bundle, kind):
    return [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind]


def test_golden_bundle_matches_the_reviewed_file(bundle):
    expected = json.loads((GOLDEN / "fhir_bmp000001.json").read_text(encoding="utf-8"))
    assert bundle == expected


def test_one_transaction_with_eleven_entries(bundle):
    assert bundle["type"] == "transaction"
    kinds = [e["resource"]["resourceType"] for e in bundle["entry"]]
    assert kinds == ["Patient", "Specimen", "DiagnosticReport"] + ["Observation"] * 8


def test_every_entry_is_a_put_to_its_own_id(bundle):
    for entry in bundle["entry"]:
        resource = entry["resource"]
        assert entry["request"] == {
            "method": "PUT",
            "url": f"{resource['resourceType']}/{resource['id']}",
        }
        assert entry["fullUrl"] == f"urn:uuid:{resource['id']}"


def test_every_resource_carries_its_source_message(bundle):
    assert {e["resource"]["meta"]["source"] for e in bundle["entry"]} == {"LABPIPE_LIS#BMP000001-1"}


def test_ids_come_from_business_keys(bundle):
    (patient,) = resources(bundle, "Patient")
    assert patient["id"] == ids.patient_id("LABPIPE_HOSP", "100007")
    potassium = resources(bundle, "Observation")[1]
    assert potassium["id"] == ids.observation_id("BMP000001", "2823-3", "1")


def test_potassium_by_hand(bundle):
    # Written from the OBX segment, not from the code: OBX|2|NM|2823-3^...|1|3.9|mmol/L...
    potassium = resources(bundle, "Observation")[1]
    assert potassium["status"] == "final"
    assert potassium["code"]["coding"][0] == {
        "system": "http://loinc.org",
        "code": "2823-3",
        "display": "Potassium [Moles/volume] in Serum or Plasma",
    }
    assert potassium["valueQuantity"] == {
        "value": 3.9,
        "unit": "mmol/L",
        "system": "http://unitsofmeasure.org",
        "code": "mmol/L",
    }
    assert potassium["effectiveDateTime"] == "1984-10-30T08:42:48Z"
    assert potassium["issued"] == "1984-10-30T09:27:48Z"
    assert potassium["referenceRange"][0]["text"] == "3.7-5.2"
    assert potassium["interpretation"][0]["coding"][0]["code"] == "N"


def test_references_point_inside_the_bundle(bundle):
    (patient,) = resources(bundle, "Patient")
    (specimen,) = resources(bundle, "Specimen")
    (report,) = resources(bundle, "DiagnosticReport")
    observations = resources(bundle, "Observation")
    assert report["subject"]["reference"] == f"Patient/{patient['id']}"
    assert report["specimen"] == [{"reference": f"Specimen/{specimen['id']}"}]
    assert report["result"] == [{"reference": f"Observation/{o['id']}"} for o in observations]
    for o in observations:
        assert o["subject"]["reference"] == f"Patient/{patient['id']}"
        assert o["specimen"]["reference"] == f"Specimen/{specimen['id']}"


def test_trailing_zeros_survive(result):
    # Synthea writes values such as "14.0"; the FHIR JSON must keep the zero.
    sodium = replace(result.observations[0], value="14.0")
    text = build_bundle(replace(result, observations=(sodium,))).model_dump_json()
    assert '"valueQuantity":{"value":14.0,' in text


def test_building_twice_gives_identical_json(result):
    assert build_bundle(result).model_dump_json() == build_bundle(result).model_dump_json()
