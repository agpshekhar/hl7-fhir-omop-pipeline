"""Parse one ORU^R01 message into a LabResult.

This is the only module in the pipeline that knows HL7 v2 field numbers. It also
holds the time zone rule, in one function, so no other code ever handles HL7 times.
"""

import re
from datetime import UTC, date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from hl7apy.consts import VALIDATION_LEVEL
from hl7apy.parser import parse_message

from labpipe.hop1.model import Coded, LabObservation, LabPatient, LabResult

# The lab's documented local zone. Used only when a time arrives without an offset.
LAB_TIMEZONE = ZoneInfo("America/Chicago")

_DTM = re.compile(r"(\d{14})([+-])(\d{2})(\d{2})|(\d{14})")

_UNESCAPES = {"F": "|", "S": "^", "T": "&", "R": "~", "E": "\\"}


def to_instant(text: str) -> datetime:
    """The time zone rule: an HL7 date/time becomes an aware UTC datetime.

    "19841030024248-0600" -> 1984-10-30 08:42:48+00:00

    With an offset, the offset is the truth. Without one, the time is read as lab
    local time (America/Chicago). Anything else, such as a time cut short to
    minutes, is refused rather than guessed.
    """
    match = _DTM.fullmatch(text)
    if not match:
        raise ValueError(f"not an HL7 date/time to the second: {text!r}")
    digits, sign, hours, minutes, no_offset = match.groups()
    if no_offset:
        local = datetime.strptime(no_offset, "%Y%m%d%H%M%S").replace(tzinfo=LAB_TIMEZONE)
    else:
        offset = timedelta(hours=int(hours), minutes=int(minutes))
        zone = timezone(offset if sign == "+" else -offset)
        local = datetime.strptime(digits, "%Y%m%d%H%M%S").replace(tzinfo=zone)
    return local.astimezone(UTC)


def to_date(text: str) -> date:
    """An HL7 date (YYYYMMDD), such as a birth date. Dates have no time zone."""
    return datetime.strptime(text, "%Y%m%d").date()


def unescape(text: str) -> str:
    r"""Undo HL7 escaping: \T\ becomes &, \E\ becomes \, and so on.

    hl7apy hands back field values still escaped, so the parser undoes it here, the
    mirror image of escape() in labpipe.lis.oru. One pass, left to right, so an
    escaped backslash can never start a second escape sequence.
    """
    return re.sub(r"\\([FSTRE])\\", lambda m: _UNESCAPES[m.group(1)], text)


def _value(element) -> str:
    """The unescaped text of a field or component ("" when it is empty)."""
    return unescape(element.to_er7()) if element is not None else ""


def _coded(field) -> Coded:
    """A CE/CWE field: code^display^system."""
    parts = field.to_er7().split("^")
    parts += [""] * (3 - len(parts))
    return Coded(code=unescape(parts[0]), display=unescape(parts[1]), system=unescape(parts[2]))


def parse_oru(raw: str) -> LabResult:
    """Parse and strictly validate one ORU^R01 v2.5.1 message.

    hl7apy checks the message against the standard's structure (segment order,
    required fields, data types) and raises on any violation. find_groups=True
    rebuilds HL7's nesting, so the code below can say "the PID of this patient
    result" instead of counting segments.
    """
    message = parse_message(raw, validation_level=VALIDATION_LEVEL.STRICT, find_groups=True)
    msh = message.msh
    result = message.oru_r01_patient_result
    pid = result.oru_r01_patient.pid
    pv1 = result.oru_r01_patient.oru_r01_visit.pv1
    order = result.oru_r01_order_observation
    obr = order.obr
    spm = order.oru_r01_specimen.spm

    observations = tuple(
        LabObservation(
            set_id=int(obx.obx_1.to_er7()),
            value_type=obx.obx_2.to_er7(),
            test=_coded(obx.obx_3),
            sub_id=obx.obx_4.to_er7(),
            value=_value(obx.obx_5),
            unit=_coded(obx.obx_6),
            reference_range=_value(obx.obx_7),
            abnormal_flag=obx.obx_8.to_er7(),
            status=obx.obx_11.to_er7(),
            observed_at=to_instant(obx.obx_14.to_er7()),
            analyzed_at=to_instant(obx.obx_19.to_er7()),
        )
        for obx in (group.obx for group in order.oru_r01_observation)
    )

    deathdate = pid.pid_29.to_er7()
    return LabResult(
        msh10=_value(msh.msh_10),
        sending_application=_value(msh.msh_3),
        accession=_value(obr.obr_3.ei_1),
        filler=_value(obr.obr_3.ei_2),
        visit_number=_value(pv1.pv1_19),
        panel=_coded(obr.obr_4),
        collected_at=to_instant(obr.obr_7.to_er7()),
        resulted_at=to_instant(obr.obr_22.to_er7()),
        report_status=obr.obr_25.to_er7(),
        specimen_type=_coded(spm.spm_4),
        patient=LabPatient(
            assigning_authority=_value(pid.pid_3.cx_4),
            mrn=_value(pid.pid_3.cx_1),
            family=_value(pid.pid_5.xpn_1),
            given=_value(pid.pid_5.xpn_2),
            birthdate=to_date(pid.pid_7.to_er7()),
            sex=pid.pid_8.to_er7(),
            deathdate=to_date(deathdate) if deathdate else None,
        ),
        observations=observations,
    )
