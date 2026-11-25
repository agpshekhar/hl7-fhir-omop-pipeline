"""Gate M1: the whole sample through LIS, Hop 1 and Hop 2, checked from every angle.

The pipeline runs once for the module (about half a minute), then each test reads the
result. Tests here must not change the database, except by rerunning Hop 2, which
must change nothing.

- Done-when for Hop 2: no concept 0, stable OMOP IDs, every Observation loaded.
- Lineage: every MEASUREMENT row traces to exactly one staged message, and to the
  OBX segment inside it that carries the same code and value.
- Round trip: OMOP agrees with the Synthea source on value, unit and instant of
  collection, counted in both directions, so nothing is lost and nothing is invented.
"""

import csv
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
import pytest

from labpipe.common.migrate import apply_migrations
from labpipe.common.vocab import load_vocabulary
from labpipe.hop1.store import run_hop1
from labpipe.hop2.load import run_hop2
from labpipe.lis.sample import load_panels, load_patients
from labpipe.lis.staging import stage_panels

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"


@pytest.fixture(scope="module")
def db(module_db_url):
    with psycopg.connect(module_db_url, autocommit=True) as conn:
        apply_migrations(conn, ROOT / "db" / "migrations")
        load_vocabulary(conn, ROOT / "db" / "vocab_subset")
        stage_panels(conn, load_panels())
        assert run_hop1(conn).errors == 0
        run_hop2(conn)
        yield conn


def measurements(conn):
    return conn.execute("SELECT * FROM cdm.measurement ORDER BY measurement_id").fetchall()


# --- Hop 2 done-when ---------------------------------------------------------------


def test_every_observation_becomes_one_measurement(db):
    observations = db.execute(
        "SELECT count(*) FROM staging.fhir_resource WHERE resource_type = 'Observation'"
    ).fetchone()[0]
    assert observations == 10648
    assert db.execute("SELECT count(*) FROM cdm.measurement").fetchone()[0] == observations
    assert db.execute("SELECT count(*) FROM cdm.person").fetchone()[0] == 12


def test_no_bmp_row_has_concept_zero(db):
    zeros = db.execute(
        "SELECT count(*) FROM cdm.measurement"
        " WHERE 0 IN (measurement_concept_id, unit_concept_id,"
        "             measurement_source_concept_id, unit_source_concept_id)"
    ).fetchone()[0]
    assert zeros == 0
    assert (
        db.execute("SELECT count(DISTINCT measurement_concept_id) FROM cdm.measurement").fetchone()[
            0
        ]
        == 8
    )


def test_omop_ids_are_identical_across_two_runs(db):
    before = measurements(db)
    persons = db.execute("SELECT * FROM cdm.person ORDER BY person_id").fetchall()
    run_hop2(db)
    assert measurements(db) == before
    assert db.execute("SELECT * FROM cdm.person ORDER BY person_id").fetchall() == persons


# --- Lineage -------------------------------------------------------------------------

LINEAGE = """
    SELECT m.measurement_id, m.measurement_source_value, m.value_source_value,
           count(r.msh10) AS messages, min(r.raw) AS raw
      FROM cdm.measurement m
      LEFT JOIN staging.crosswalk x
             ON x.omop_table = 'measurement' AND x.omop_id = m.measurement_id
      LEFT JOIN staging.fhir_resource f
             ON f.resource_type = x.fhir_resource_type AND f.id = x.fhir_id
      LEFT JOIN staging.raw_message r ON r.msh10 = f.source_msh10
     GROUP BY m.measurement_id, m.measurement_source_value, m.value_source_value
"""


def test_every_measurement_traces_to_exactly_one_staged_message(db):
    rows = db.execute(LINEAGE).fetchall()
    assert len(rows) == 10648
    assert Counter(messages for _, _, _, messages, _ in rows) == {1: 10648}


def test_the_traced_message_holds_the_same_code_and_value(db):
    for measurement_id, code, value, _, raw in db.execute(LINEAGE):
        obx = [s.split("|") for s in raw.split("\r") if s.startswith("OBX|")]
        matches = [f for f in obx if f[3].split("^")[0] == code and f[5] == value]
        assert len(matches) == 1, f"measurement {measurement_id}: no OBX with {code} = {value}"


# --- Round trip against Synthea --------------------------------------------------------

Key = tuple[str, datetime, str]  # (MRN, collection instant in UTC, LOINC)


def synthea_results(directory: Path = SAMPLE) -> list[tuple[Key, Decimal, str]]:
    """Every BMP result in the Synthea extract, read straight from the CSV.

    Only the MRN comes from labpipe: Synthea has no MRN, and the LIS assigns them.
    Values, units and times are the raw source text.
    """
    mrn = {pid: p.mrn for pid, p in load_patients(directory).items()}
    with (directory / "bmp_results.csv").open(newline="", encoding="utf-8") as f:
        return [
            (
                (
                    mrn[row["patient_id"]],
                    datetime.fromisoformat(row["collected_at"])
                    .astimezone(UTC)
                    .replace(tzinfo=None),
                    row["loinc"],
                ),
                Decimal(row["value"]),
                row["unit"],
            )
            for row in csv.DictReader(f)
        ]


def omop_results(conn) -> list[tuple[Key, Decimal, str]]:
    rows = conn.execute(
        """
        SELECT p.person_source_value, m.measurement_datetime, m.measurement_source_value,
               m.value_as_number, m.unit_source_value
          FROM cdm.measurement m JOIN cdm.person p USING (person_id)
        """
    ).fetchall()
    return [((mrn, taken, code), value, unit) for mrn, taken, code, value, unit in rows]


def compare(source, loaded) -> list[str]:
    """Every disagreement between two result lists, counted in both directions."""
    problems = []
    for side, rows in (("source", source), ("OMOP", loaded)):
        for key, n in Counter(key for key, _, _ in rows).items():
            if n > 1:
                problems.append(f"{side} has {n} rows for {key}")
    by_key_source = {key: (value, unit) for key, value, unit in source}
    by_key_loaded = {key: (value, unit) for key, value, unit in loaded}
    for key in by_key_source.keys() - by_key_loaded.keys():
        problems.append(f"missing from OMOP: {key}")
    for key in by_key_loaded.keys() - by_key_source.keys():
        problems.append(f"not in the source: {key}")
    for key in by_key_source.keys() & by_key_loaded.keys():
        if by_key_source[key] != by_key_loaded[key]:
            problems.append(f"{key}: source {by_key_source[key]} != OMOP {by_key_loaded[key]}")
    return sorted(problems)


def test_round_trip_matches_the_synthea_source(db):
    source, loaded = synthea_results(), omop_results(db)
    assert len(source) == len(loaded) == 10648
    assert compare(source, loaded) == []


def test_breaking_one_value_makes_the_round_trip_fail(db):
    loaded = omop_results(db)
    key, value, unit = loaded[0]
    loaded[0] = (key, value + Decimal("0.1"), unit)
    problems = compare(synthea_results(), loaded)
    assert len(problems) == 1 and str(value + Decimal("0.1")) in problems[0]


def test_a_lost_or_invented_row_makes_the_round_trip_fail(db):
    loaded = omop_results(db)
    problems = compare(synthea_results(), loaded[1:])
    assert problems == [f"missing from OMOP: {loaded[0][0]}"]
    problems = compare(synthea_results(), [*loaded, loaded[0]])
    assert problems == [f"OMOP has 2 rows for {loaded[0][0]}"]
