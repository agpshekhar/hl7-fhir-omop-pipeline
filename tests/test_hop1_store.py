"""Tests for running Hop 1 over staging: counts, reruns, errors and lineage."""

import csv
import shutil
from pathlib import Path

import pytest

from labpipe.cli import main
from labpipe.hop1.store import run_hop1
from labpipe.lis.sample import load_panels
from labpipe.lis.staging import stage_panels

SAMPLE = Path(__file__).resolve().parent.parent / "data" / "sample"


@pytest.fixture(scope="module")
def panels():
    return load_panels()


def fhir_counts(conn):
    rows = conn.execute(
        "SELECT resource_type, count(*) FROM staging.fhir_resource GROUP BY 1"
    ).fetchall()
    return dict(rows)


def snapshot(conn):
    return conn.execute(
        "SELECT resource_type, id, resource, source_msh10, updated_at"
        " FROM staging.fhir_resource ORDER BY 1, 2"
    ).fetchall()


def test_ten_messages_make_one_patient_per_mrn_and_eight_observations_each(staging_db, panels):
    first_ten = panels[:10]
    stage_panels(staging_db, first_ten)
    counts = run_hop1(staging_db)
    assert (counts.parsed, counts.errors, counts.resources) == (10, 0, 110)
    assert fhir_counts(staging_db) == {
        "Patient": len({p.patient.mrn for p in first_ten}),
        "Specimen": 10,
        "DiagnosticReport": 10,
        "Observation": 80,
    }
    statuses = staging_db.execute(
        "SELECT status, count(*) FROM staging.raw_message GROUP BY 1"
    ).fetchall()
    assert statuses == [("parsed", 10)]


def test_a_rerun_changes_nothing(staging_db, panels):
    stage_panels(staging_db, panels[:10])
    run_hop1(staging_db)
    before = snapshot(staging_db)
    counts = run_hop1(staging_db)
    assert (counts.parsed, counts.errors) == (0, 0)
    assert snapshot(staging_db) == before


def test_reprocessing_the_same_messages_rewrites_nothing(staging_db, panels):
    # Even when every message is forced back to 'new', identical JSON is not rewritten,
    # so updated_at stays put. This is what a replayed feed will rely on.
    stage_panels(staging_db, panels[:1])
    run_hop1(staging_db)
    before = snapshot(staging_db)
    staging_db.execute("UPDATE staging.raw_message SET status = 'new'")
    run_hop1(staging_db)
    assert snapshot(staging_db) == before


def test_a_bad_message_is_marked_error_and_the_rest_still_load(staging_db, panels):
    stage_panels(staging_db, panels[:3])
    staging_db.execute(
        "UPDATE staging.raw_message SET raw = replace(raw, '|NM|', '|XX|')"
        " WHERE msh10 = 'BMP000002-1'"
    )
    counts = run_hop1(staging_db)
    assert (counts.parsed, counts.errors) == (2, 1)
    status, detail = staging_db.execute(
        "SELECT status, status_detail FROM staging.raw_message WHERE msh10 = 'BMP000002-1'"
    ).fetchone()
    assert status == "error" and detail
    # Nothing from the failed message was half-written.
    assert fhir_counts(staging_db)["DiagnosticReport"] == 2


def test_trailing_zeros_survive_in_postgres(staging_db, panels):
    stage_panels(staging_db, panels)
    staging_db.execute(
        "UPDATE staging.raw_message SET status = 'parsed' WHERE msh10 <> 'BMP000001-1'"
    )
    run_hop1(staging_db)
    value = staging_db.execute(
        "SELECT resource->'valueQuantity'->>'value' FROM staging.fhir_resource"
        " WHERE resource->'code'->'coding'->0->>'code' = '2951-2'"
    ).fetchone()[0]
    assert value == "139.9"


def test_the_whole_sample_meets_the_done_when(staging_db, panels):
    stage_panels(staging_db, panels)
    counts = run_hop1(staging_db)
    assert (counts.parsed, counts.errors) == (1331, 0)

    # Observation count equals OBX count.
    obx = staging_db.execute(
        r"SELECT sum(array_length(regexp_split_to_array(raw, '\rOBX\|'), 1) - 1)"
        " FROM staging.raw_message"
    ).fetchone()[0]
    assert obx == 10648
    assert fhir_counts(staging_db) == {
        "Patient": 12,
        "Specimen": 1331,
        "DiagnosticReport": 1331,
        "Observation": 10648,
    }

    # Lineage: every resource names its source message, in meta.source and in the
    # column, the two agree, and the message exists in staging.
    orphans = staging_db.execute(
        """
        SELECT count(*)
          FROM staging.fhir_resource f
          LEFT JOIN staging.raw_message m ON m.msh10 = f.source_msh10
         WHERE m.msh10 IS NULL
            OR f.resource->'meta'->>'source' IS DISTINCT FROM 'LABPIPE_LIS#' || f.source_msh10
        """
    ).fetchone()[0]
    assert orphans == 0

    # A rerun finds nothing to do.
    assert run_hop1(staging_db).parsed == 0


def small_sample(directory: Path, panels: int) -> Path:
    """A copy of the sample cut to its first few panels, to keep the CLI test quick."""
    directory.mkdir()
    shutil.copy(SAMPLE / "patients.csv", directory)
    with (SAMPLE / "bmp_panels.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))[:panels]
    keep = {(r["patient_id"], r["collected_at"]) for r in rows}
    for name, keyed in (("bmp_panels.csv", rows), ("bmp_results.csv", None)):
        with (SAMPLE / name).open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames
            data = keyed or [r for r in reader if (r["patient_id"], r["collected_at"]) in keep]
        with (directory / name).open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            writer.writerows(data)
    return directory


def test_hop1_command_and_run_all(fresh_db_url, monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    monkeypatch.setenv("LABPIPE_SAMPLE_DIR", str(small_sample(tmp_path / "sample", 5)))
    assert main(["migrate"]) == 0
    assert main(["vocab", "load"]) == 0
    assert main(["run", "--all"]) == 0
    out = capsys.readouterr().out
    assert "staged 5 new" in out
    assert "hop1: 5 messages parsed, 0 errors" in out
    assert "upserted 55 FHIR resources" in out
    assert "hop2: loaded 1 persons and 40 measurements" in out
    assert main(["hop1"]) == 0
    assert "hop1: 0 messages parsed, 0 errors" in capsys.readouterr().out
