"""Tests for turning FHIR resources into OMOP rows, and the golden OMOP rows."""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

from labpipe.hop1.store import run_hop1
from labpipe.hop2.load import run_hop2
from labpipe.hop2.mapping import Concept, Mapping
from labpipe.hop2.read import FhirRow
from labpipe.hop2.to_omop import (
    MEASUREMENT_COLUMNS,
    PERSON_COLUMNS,
    measurement_row,
    pair_of,
    person_row,
)
from labpipe.lis.sample import load_panels
from labpipe.lis.staging import stage_panels

GOLDEN = Path(__file__).parent / "fixtures" / "golden"
POTASSIUM = Mapping(test=Concept(3023103, 3023103, "Measurement"), unit=Concept(8753, 8753, "Unit"))


@pytest.fixture(scope="module")
def bundle():
    text = (GOLDEN / "fhir_bmp000001.json").read_text(encoding="utf-8")
    return json.loads(text, parse_float=Decimal)


def fhir_row(bundle, kind, index=0):
    resource = [e["resource"] for e in bundle["entry"] if e["resource"]["resourceType"] == kind][
        index
    ]
    return FhirRow(id=UUID(resource["id"]), resource=resource, source_msh10="BMP000001-1")


def test_person_row_from_the_golden_patient(bundle):
    row = person_row(fhir_row(bundle, "Patient"), person_id=7)
    assert list(row) == list(PERSON_COLUMNS)
    assert row["person_id"] == 7
    assert row["gender_concept_id"] == 8532
    assert (row["year_of_birth"], row["month_of_birth"], row["day_of_birth"]) == (1916, 11, 14)
    assert row["person_source_value"] == "100007"
    assert row["gender_source_value"] == "female"
    assert (row["race_concept_id"], row["ethnicity_concept_id"]) == (0, 0)


def test_a_patient_without_an_mrn_is_refused(bundle):
    patient = fhir_row(bundle, "Patient")
    patient.resource["identifier"] = []
    with pytest.raises(ValueError, match="no MRN"):
        person_row(patient, person_id=1)


def test_pair_of_reads_code_and_unit(bundle):
    potassium = fhir_row(bundle, "Observation", 1).resource
    assert pair_of(potassium) == (
        "http://loinc.org",
        "2823-3",
        "http://unitsofmeasure.org",
        "mmol/L",
    )


def test_measurement_row_from_the_golden_potassium(bundle):
    row = measurement_row(fhir_row(bundle, "Observation", 1), 42, 7, POTASSIUM)
    assert list(row) == list(MEASUREMENT_COLUMNS)
    assert row["measurement_id"] == 42 and row["person_id"] == 7
    assert row["measurement_concept_id"] == 3023103
    assert row["measurement_source_value"] == "2823-3"
    assert row["measurement_source_concept_id"] == 3023103
    assert row["measurement_type_concept_id"] == 32856
    assert row["measurement_datetime"] == datetime(1984, 10, 30, 8, 42, 48)
    assert row["measurement_date"] == date(1984, 10, 30)
    assert row["value_as_number"] == Decimal("3.9")
    assert row["value_source_value"] == "3.9"
    assert (row["unit_concept_id"], row["unit_source_value"]) == (8753, "mmol/L")
    assert row["unit_source_concept_id"] == 8753
    assert (row["range_low"], row["range_high"]) == (Decimal("3.7"), Decimal("5.2"))


def test_trailing_zeros_survive_into_value_source_value(bundle):
    obs = fhir_row(bundle, "Observation", 1)
    obs.resource["valueQuantity"]["value"] = Decimal("4.0")
    row = measurement_row(obs, 1, 1, POTASSIUM)
    assert row["value_source_value"] == "4.0"


def test_an_unmapped_code_keeps_its_source_value(bundle):
    unmapped = Mapping(test=Concept(0, 0, None), unit=Concept(0, 0, None))
    row = measurement_row(fhir_row(bundle, "Observation", 1), 1, 1, unmapped)
    assert row["measurement_concept_id"] == 0
    assert row["measurement_source_value"] == "2823-3"
    assert row["unit_source_value"] == "mmol/L"


def omop_rows(conn) -> dict:
    """Every column of every PERSON and MEASUREMENT row, as JSON-ready text."""

    def as_json(value):
        return value if value is None or isinstance(value, int) else str(value)

    tables = {}
    for table, key in (("person", "person_id"), ("measurement", "measurement_id")):
        with conn.cursor() as cur:
            cur.execute(f"SELECT * FROM cdm.{table} ORDER BY {key}")
            names = [c.name for c in cur.description]
            tables[table] = [
                {n: as_json(v) for n, v in zip(names, row, strict=True)} for row in cur
            ]
    return tables


def test_golden_omop_rows_match_the_reviewed_file(omop_db):
    stage_panels(omop_db, load_panels()[:1])
    run_hop1(omop_db)
    run_hop2(omop_db)
    expected = json.loads((GOLDEN / "omop_bmp000001.json").read_text(encoding="utf-8"))
    assert omop_rows(omop_db) == expected
