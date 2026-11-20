"""Tests for the ORU^R01 builder: the golden message, escaping and structure."""

from pathlib import Path

import pytest
from hl7apy.consts import VALIDATION_LEVEL
from hl7apy.parser import parse_message

from labpipe.lis.oru import build_oru, escape, segment
from labpipe.lis.sample import load_panels

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "oru_bmp000001.hl7"


@pytest.fixture(scope="module")
def panels():
    return load_panels()


def test_first_panel_matches_the_golden_message(panels):
    # The golden file keeps one segment per line so it is easy to read and review;
    # a real message separates segments with \r.
    expected = GOLDEN.read_text(encoding="utf-8").replace("\n", "\r")
    assert build_oru(panels[0]) == expected


def test_segments_end_with_carriage_returns_only(panels):
    message = build_oru(panels[0])
    assert "\n" not in message
    assert message.endswith("\r")
    names = [s[:3] for s in message.split("\r") if s]
    assert names == ["MSH", "PID", "PV1", "ORC", "OBR"] + ["OBX"] * 8 + ["SPM"]


@pytest.mark.parametrize(
    ("raw", "escaped"),
    [
        ("Smith & Jones", "Smith \\T\\ Jones"),
        ("a|b", "a\\F\\b"),
        ("a^b~c", "a\\S\\b\\R\\c"),
        ("C:\\temp", "C:\\E\\temp"),
        ("D'Amore443", "D'Amore443"),
    ],
)
def test_escape(raw, escaped):
    assert escape(raw) == escaped


def test_segment_numbers_fields_like_the_standard():
    assert segment("PV1", {1: "1", 3: "X"}) == "PV1|1||X"
    assert segment("MSH", {2: "^~\\&", 3: "APP"}) == "MSH|^~\\&|APP"


def test_every_message_passes_strict_hl7apy_validation(panels):
    for panel in panels[:50]:
        message = parse_message(
            build_oru(panel), validation_level=VALIDATION_LEVEL.STRICT, find_groups=True
        )
        assert message.validate()
