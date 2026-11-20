"""Fixed translation tables from HL7 v2 codes to their FHIR R4 equivalents.

These are structural, not vocabulary lookups. HL7 v2 names a coding system with a
short mnemonic in the third component ("LN"); FHIR names the same system with a URI
("http://loinc.org"). Likewise each HL7 status or flag code has a FHIR counterpart.
The code values themselves pass through untouched: Hop 1 never decides what a code
*means*. That is Hop 2's job, through the OHDSI vocabularies.

Sources: HL7 v2.5.1 tables 0396 (coding systems), 0085 (observation result status),
0123 (result status), 0078 (abnormal flags), 0001 (administrative sex); FHIR R4
terminology pages for the matching value sets.
"""

# HL7 table 0396 mnemonic -> FHIR system URI.
CODING_SYSTEMS = {
    "LN": "http://loinc.org",
    "SCT": "http://snomed.info/sct",
    "UCUM": "http://unitsofmeasure.org",
}

# OBX-11 (HL7 table 0085) -> Observation.status.
OBSERVATION_STATUS = {
    "F": "final",
    "C": "corrected",
    "P": "preliminary",
    "X": "cancelled",
    "W": "entered-in-error",
}

# OBR-25 (HL7 table 0123) -> DiagnosticReport.status.
REPORT_STATUS = {
    "F": "final",
    "C": "corrected",
    "P": "preliminary",
    "X": "cancelled",
}

# OBX-8 (HL7 table 0078) -> Observation.interpretation, in the FHIR v3 code system.
INTERPRETATION_SYSTEM = "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation"
INTERPRETATION = {
    "N": ("N", "Normal"),
    "H": ("H", "High"),
    "L": ("L", "Low"),
}

# PID-8 (HL7 table 0001) -> Patient.gender.
GENDER = {"F": "female", "M": "male", "O": "other", "U": "unknown"}

# Fixed codings every lab resource carries.
OBSERVATION_CATEGORY = (
    "http://terminology.hl7.org/CodeSystem/observation-category",
    "laboratory",
    "Laboratory",
)
REPORT_CATEGORY = ("http://terminology.hl7.org/CodeSystem/v2-0074", "LAB", "Laboratory")
MRN_TYPE = ("http://terminology.hl7.org/CodeSystem/v2-0203", "MR", "Medical record number")


def identifier_system(kind: str, authority: str) -> str:
    """A URI naming one identifier namespace, e.g. urn:labpipe:mrn:LABPIPE_HOSP.

    FHIR identifiers are a (system, value) pair. HL7 v2 gives us the issuer by name
    (PID-3.4, OBR-3.2), so the system is built from that name.
    """
    return f"urn:labpipe:{kind}:{authority}"


def fhir_system(hl7_system: str) -> str:
    """The FHIR URI for an HL7 coding system mnemonic; unknown ones are refused, not guessed."""
    try:
        return CODING_SYSTEMS[hl7_system]
    except KeyError:
        raise ValueError(f"unknown HL7 coding system {hl7_system!r}") from None


def lookup(table: dict, code: str, what: str):
    """Look a code up in one of the tables above, with an error that names the field."""
    try:
        return table[code]
    except KeyError:
        raise ValueError(f"unknown {what} code {code!r}") from None
