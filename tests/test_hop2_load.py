"""Tests for running Hop 2: full rebuild, one transaction, stable IDs, the CLI."""

import psycopg
import pytest

from labpipe.cli import main
from labpipe.hop1.store import run_hop1
from labpipe.hop2 import load
from labpipe.hop2.load import run_hop2
from labpipe.lis.sample import load_panels
from labpipe.lis.staging import stage_panels


@pytest.fixture(scope="module")
def panels():
    return load_panels()


def cdm_counts(conn):
    return conn.execute(
        "SELECT (SELECT count(*) FROM cdm.person), (SELECT count(*) FROM cdm.measurement)"
    ).fetchone()


def test_three_panels_load_persons_and_measurements(omop_db, panels):
    stage_panels(omop_db, panels[:3])
    run_hop1(omop_db)
    counts = run_hop2(omop_db)
    persons = len({p.patient.mrn for p in panels[:3]})
    assert (counts.persons, counts.measurements) == (persons, 24)
    assert (counts.cache_entries, counts.unmapped) == (8, [])
    assert cdm_counts(omop_db) == (persons, 24)


def test_new_messages_keep_the_old_ids_and_add_new_ones(omop_db, panels):
    stage_panels(omop_db, panels[:3])
    run_hop1(omop_db)
    run_hop2(omop_db)
    before = dict(omop_db.execute("SELECT measurement_id, value_as_number FROM cdm.measurement"))
    stage_panels(omop_db, panels[3:6])
    run_hop1(omop_db)
    run_hop2(omop_db)
    after = dict(omop_db.execute("SELECT measurement_id, value_as_number FROM cdm.measurement"))
    assert len(after) == 48
    assert {k: after[k] for k in before} == before


def test_a_failed_run_leaves_the_previous_cdm_in_place(omop_db, panels, monkeypatch):
    stage_panels(omop_db, panels[:2])
    run_hop1(omop_db)
    run_hop2(omop_db)
    before = cdm_counts(omop_db)

    def broken(*args):
        raise RuntimeError("simulated failure halfway through")

    monkeypatch.setattr(load, "measurement_row", broken)
    with pytest.raises(RuntimeError):
        run_hop2(omop_db)
    assert cdm_counts(omop_db) == before


def test_hop2_never_reads_the_hl7_messages(omop_db, panels):
    # Hop 2 reads staging.fhir_resource only: blanking the raw HL7 changes nothing.
    stage_panels(omop_db, panels[:2])
    run_hop1(omop_db)
    run_hop2(omop_db)
    before = omop_db.execute("SELECT * FROM cdm.measurement ORDER BY 1").fetchall()
    omop_db.execute("UPDATE staging.raw_message SET raw = ''")
    run_hop2(omop_db)
    assert omop_db.execute("SELECT * FROM cdm.measurement ORDER BY 1").fetchall() == before


def test_hop2_command_fails_loudly_on_an_unmapped_code(fresh_db_url, monkeypatch, capsys, panels):
    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    assert main(["migrate"]) == 0
    assert main(["vocab", "load"]) == 0
    with psycopg.connect(fresh_db_url) as conn:
        stage_panels(conn, panels[:1])
        run_hop1(conn)
        conn.execute(
            "UPDATE staging.fhir_resource"
            " SET resource = jsonb_set(resource, '{code,coding,0,code}', '\"0000-0\"')"
            " WHERE resource->'code'->'coding'->0->>'code' = '2823-3'"
        )
    capsys.readouterr()
    assert main(["hop2"]) == 1
    captured = capsys.readouterr()
    assert "hop2: loaded 1 persons and 8 measurements" in captured.out
    assert "unmapped: http://loinc.org 0000-0 with unit 'mmol/L'" in captured.err
