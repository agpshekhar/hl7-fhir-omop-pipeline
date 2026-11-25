"""Tests for parsing ORU^R01 into LabResult, and for the time zone rule."""

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from labpipe.hop1.parse import parse_oru, to_instant, unescape
from labpipe.lis.oru import build_oru, escape
from labpipe.lis.sample import load_panels

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "oru_bmp000001.hl7"


@pytest.fixture(scope="module")
def golden():
    return parse_oru(GOLDEN.read_text(encoding="utf-8").replace("\n", "\r"))


# --- the time zone rule ---------------------------------------------------------------


def test_an_offset_is_applied_to_get_the_instant():
    assert to_instant("19841030024248-0600") == datetime(1984, 10, 30, 8, 42, 48, tzinfo=UTC)


def test_summer_and_winter_offsets_both_land_on_utc():
    assert to_instant("20160715100000-0500") == datetime(2016, 7, 15, 15, 0, 0, tzinfo=UTC)
    assert to_instant("20160115100000-0600") == datetime(2016, 1, 15, 16, 0, 0, tzinfo=UTC)


def test_a_time_without_an_offset_is_read_as_lab_local_time():
    # 15 July is daylight time in Chicago (-0500); 15 January is standard time (-0600).
    assert to_instant("20160715100000") == datetime(2016, 7, 15, 15, 0, 0, tzinfo=UTC)
    assert to_instant("20160115100000") == datetime(2016, 1, 15, 16, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text", ["198410300242-0600", "1984-10-30T02:42:48", "", "19841030024248Z"]
)
def test_anything_else_is_refused(text):
    with pytest.raises(ValueError, match="not an HL7 date/time"):
        to_instant(text)


# --- unescaping -----------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Smith & Jones", "a|b^c~d", "back\\slash", "\\T\\ literally"])
def test_unescape_undoes_escape(text):
    assert unescape(escape(text)) == text


# --- the golden message ---------------------------------------------------------------


def test_golden_header_and_order(golden):
    assert golden.msh10 == "BMP000001-1"
    assert golden.sending_application == "LABPIPE_LIS"
    assert (golden.accession, golden.filler) == ("BMP000001", "LABPIPE_LAB")
    assert golden.panel.code == "24321-2"
    assert golden.report_status == "F"


def test_golden_times_are_utc_instants(golden):
    assert golden.collected_at == datetime(1984, 10, 30, 8, 42, 48, tzinfo=UTC)
    assert golden.resulted_at == datetime(1984, 10, 30, 9, 27, 48, tzinfo=UTC)


def test_golden_patient(golden):
    p = golden.patient
    assert (p.assigning_authority, p.mrn) == ("LABPIPE_HOSP", "100007")
    assert (p.family, p.given, p.sex) == ("Ruecker817", "Birgit199", "F")
    assert p.birthdate == date(1916, 11, 14)
    assert p.deathdate == date(1994, 10, 7)


def test_golden_observations(golden):
    assert [o.set_id for o in golden.observations] == list(range(1, 9))
    potassium = golden.observations[1]
    assert potassium.test.code == "2823-3" and potassium.test.system == "LN"
    assert (potassium.value, potassium.unit.code) == ("3.9", "mmol/L")
    assert (potassium.reference_range, potassium.abnormal_flag) == ("3.7-5.2", "N")
    assert potassium.sub_id == "1"


# --- the whole sample -----------------------------------------------------------------


def test_sample_messages_parse_back_to_their_panels():
    # Every tenth panel: 134 messages across all 12 patients and both DST offsets.
    # (The Hop 1 store test parses all 1,331; parsing is about 30 ms a message.)
    for panel in load_panels()[::10]:
        result = parse_oru(build_oru(panel))
        assert result.accession == panel.accession
        assert result.patient.mrn == panel.patient.mrn
        assert result.collected_at == panel.collected_at
        assert result.resulted_at == panel.resulted_at
        assert [(o.test.code, o.value, o.unit.code) for o in result.observations] == [
            (r.loinc, r.value, r.unit) for r in panel.results
        ]


def test_a_broken_message_is_refused():
    raw = GOLDEN.read_text(encoding="utf-8").replace("\n", "\r")
    without_obr = "\r".join(s for s in raw.split("\r") if not s.startswith("OBR"))
    with pytest.raises(Exception):  # noqa: B017 - hl7apy raises several error types
        parse_oru(without_obr)
