"""Build one HL7 v2.5.1 ORU^R01 message per BMP panel.

The message follows the conventions of the HL7 Laboratory Results Interface (LRI):
one OBR for the panel, one OBX per analyte, LOINC codes for the tests and UCUM for
the units. It does not claim full LRI conformance (no MSH-21 profile identifier).

Segments, in order: MSH, PID, PV1, ORC, OBR, OBX x 8, SPM.
Each segment ends with a carriage return (\\r), as HL7 v2 requires.
"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from labpipe.lis.reference_ranges import RANGES, abnormal_flag
from labpipe.lis.sample import PANEL_LOINC, PANEL_NAME, Panel

SEGMENT_END = "\r"
ENCODING_CHARACTERS = "^~\\&"  # component, repetition, escape, subcomponent

# Synthea's patients live in Kansas. The lab sends local time with its UTC offset,
# the way a real LIS does; Hop 1 turns it back into an instant.
LAB_TIMEZONE = ZoneInfo("America/Chicago")

SENDING_APPLICATION = "LABPIPE_LIS"
SENDING_FACILITY = "LABPIPE_LAB"
RECEIVING_APPLICATION = "LABPIPE"
RECEIVING_FACILITY = "LABPIPE_HOSP"
ASSIGNING_AUTHORITY = "LABPIPE_HOSP"  # who issues the MRN (PID-3.4)

SERUM_SPECIMEN = ("119364003", "Serum specimen", "SCT")  # SNOMED CT, for SPM-4


def escape(text: str) -> str:
    """Escape the five HL7 delimiter characters inside a data value.

    The backslash goes first; otherwise the backslashes added for the other four
    would be escaped a second time.
    """
    return (
        text.replace("\\", "\\E\\")
        .replace("|", "\\F\\")
        .replace("^", "\\S\\")
        .replace("&", "\\T\\")
        .replace("~", "\\R\\")
    )


def components(*parts: str) -> str:
    """Join escaped parts with ^, dropping empty trailing components."""
    return "^".join(escape(p) for p in parts).rstrip("^")


def segment(name: str, fields: dict[int, str]) -> str:
    """Build one segment from {field number: value}, numbered as the HL7 standard numbers them.

    Values must already be escaped (use components()). Fields not given are left empty,
    and nothing is written after the last given field. MSH is special: MSH-1 is the
    field separator itself, so its first written field is MSH-2.
    """
    first = 2 if name == "MSH" else 1
    values = [fields.get(n, "") for n in range(first, max(fields) + 1)]
    return "|".join([name, *values])


def hl7_datetime(moment: datetime) -> str:
    """An aware datetime as HL7 DTM in lab local time with offset: 19841030024248-0600."""
    return moment.astimezone(LAB_TIMEZONE).strftime("%Y%m%d%H%M%S%z")


def hl7_date(day: date) -> str:
    return day.strftime("%Y%m%d")


def message_control_id(panel: Panel) -> str:
    """MSH-10. The first message for an accession is -1; a correction will be -2, and so on."""
    return f"{panel.accession}-1"


def build_oru(panel: Panel) -> str:
    """The complete ORU^R01 message for one panel, as a string."""
    patient = panel.patient
    collected = hl7_datetime(panel.collected_at)
    resulted = hl7_datetime(panel.resulted_at)
    filler_order = components(panel.accession, SENDING_FACILITY)

    segments = [
        segment(
            "MSH",
            {
                2: ENCODING_CHARACTERS,
                3: SENDING_APPLICATION,
                4: SENDING_FACILITY,
                5: RECEIVING_APPLICATION,
                6: RECEIVING_FACILITY,
                7: resulted,
                9: components("ORU", "R01", "ORU_R01"),
                10: escape(message_control_id(panel)),
                11: "P",
                12: "2.5.1",
                18: "UNICODE UTF-8",
            },
        ),
        segment(
            "PID",
            {
                1: "1",
                3: components(patient.mrn, "", "", ASSIGNING_AUTHORITY, "MR"),
                5: components(patient.family, patient.given),
                7: hl7_date(patient.birthdate),
                8: patient.gender,
                29: hl7_date(patient.deathdate) if patient.deathdate else "",
                30: "Y" if patient.deathdate else "",
            },
        ),
        segment("PV1", {1: "1", 2: "O", 19: escape(panel.encounter_id)}),
        segment("ORC", {1: "RE", 3: filler_order}),
        segment(
            "OBR",
            {
                1: "1",
                3: filler_order,
                4: components(PANEL_LOINC, PANEL_NAME, "LN"),
                7: collected,
                22: resulted,
                25: "F",
            },
        ),
    ]
    for set_id, result in enumerate(panel.results, start=1):
        segments.append(
            segment(
                "OBX",
                {
                    1: str(set_id),
                    2: "NM",
                    3: components(result.loinc, result.name, "LN"),
                    4: "1",
                    5: escape(result.value),
                    6: components(result.unit, result.unit, "UCUM"),
                    7: escape(RANGES[result.loinc].text),
                    8: abnormal_flag(result.loinc, result.value, result.unit),
                    11: "F",
                    14: collected,
                    19: resulted,
                },
            )
        )
    segments.append(segment("SPM", {1: "1", 4: components(*SERUM_SPECIMEN), 17: collected}))

    return "".join(s + SEGMENT_END for s in segments)
