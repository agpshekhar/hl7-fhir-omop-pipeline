"""LabResult: the canonical model in the middle of Hop 1.

Hop 1 never goes straight from HL7 segments to FHIR resources. It first parses each
message into these plain dataclasses, then builds FHIR from them:

    raw ORU^R01 text --parse--> LabResult --to_fhir--> transaction Bundle

The middle step keeps the two standards apart. The parser is the only code that knows
HL7 field numbers; the FHIR builder is the only code that knows FHIR. A different
input (MLLP from BridgeLink in Phase 7, a messy feed in Phase 9) changes only the
parser, and the Part 2 rule simulator can read LabResults without knowing FHIR at all.

Everything here is structure, not meaning: codes and units are carried exactly as
the lab sent them, and no vocabulary is consulted. Times are aware UTC datetimes,
because the time zone rule has already been applied by the parser.
"""

from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class Coded:
    """One HL7 coded element (CE/CWE): code, display text and the coding system name."""

    code: str
    display: str
    system: str  # as HL7 names it: "LN", "SCT", "UCUM"


@dataclass(frozen=True)
class LabPatient:
    """PID."""

    assigning_authority: str  # PID-3.4
    mrn: str  # PID-3.1
    family: str  # PID-5.1
    given: str  # PID-5.2
    birthdate: date  # PID-7
    sex: str  # PID-8, the HL7 code: "F", "M", "O", "U"
    deathdate: date | None  # PID-29 (date only in this feed)


@dataclass(frozen=True)
class LabObservation:
    """One OBX: one analyte's result."""

    set_id: int  # OBX-1, the position in the panel
    value_type: str  # OBX-2, "NM" for numeric
    test: Coded  # OBX-3
    sub_id: str  # OBX-4
    value: str  # OBX-5, text exactly as sent ("14.0" stays "14.0")
    unit: Coded  # OBX-6
    reference_range: str  # OBX-7, as sent ("136-144")
    abnormal_flag: str  # OBX-8: "L", "H", "N" (or empty)
    status: str  # OBX-11: "F" final, "C" corrected, ...
    observed_at: datetime  # OBX-14, the collection instant (UTC)
    analyzed_at: datetime  # OBX-19, the result instant (UTC)


@dataclass(frozen=True)
class LabResult:
    """One ORU^R01 message for one panel: everything Hop 1 needs to build FHIR."""

    msh10: str  # message control ID, the lineage key
    sending_application: str  # MSH-3
    accession: str  # OBR-3.1, the filler order number
    filler: str  # OBR-3.2, who assigned the accession
    visit_number: str  # PV1-19
    panel: Coded  # OBR-4, the ordered test
    collected_at: datetime  # OBR-7 (UTC)
    resulted_at: datetime  # OBR-22 (UTC)
    report_status: str  # OBR-25
    specimen_type: Coded  # SPM-4
    patient: LabPatient
    observations: tuple[LabObservation, ...]  # in OBX-1 order
