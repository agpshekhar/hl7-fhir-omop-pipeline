"""Tests for writing messages into staging.raw_message."""

from labpipe.cli import main
from labpipe.lis.sample import load_panels
from labpipe.lis.staging import stage_panels


def count(conn):
    return conn.execute("SELECT count(*) FROM staging.raw_message").fetchone()[0]


def test_one_row_per_panel_and_a_rerun_adds_none(staging_db):
    panels = load_panels()
    assert stage_panels(staging_db, panels) == 1331
    assert count(staging_db) == 1331
    assert stage_panels(staging_db, panels) == 0
    assert count(staging_db) == 1331


def test_a_staged_message_is_stored_exactly_as_built(staging_db):
    stage_panels(staging_db, load_panels()[:1])
    msh10, raw, status = staging_db.execute(
        "SELECT msh10, raw, status FROM staging.raw_message"
    ).fetchone()
    assert msh10 == "BMP000001-1"
    assert raw.startswith("MSH|^~\\&|") and raw.endswith("\r")
    assert status == "new"


def test_lis_command_reports_new_and_skipped(fresh_db_url, monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL", fresh_db_url)
    assert main(["migrate"]) == 0
    assert main(["lis"]) == 0
    assert "staged 1331 new, skipped 0" in capsys.readouterr().out
    assert main(["lis"]) == 0
    assert "staged 0 new, skipped 1331" in capsys.readouterr().out
