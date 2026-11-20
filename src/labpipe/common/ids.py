"""Deterministic FHIR resource IDs (UUIDv5) from business keys.

A UUIDv5 is a hash of a namespace and a name: the same two inputs always give the same
UUID, on any machine, in any run. Hop 1 uses the real-world identifiers a lab owns as
the name (the MRN and its issuer, the accession number, the test code), so a resent or
replayed message produces the same IDs, and an upsert on those IDs never duplicates.

Each key starts with its resource type, so a Specimen and a DiagnosticReport that share
an accession number still get different IDs. The parts are joined with "|", which
cannot appear inside an HL7 field value (it is the field separator).

Never change NAMESPACE or a key's layout once data exists: every ID would change with it.
"""

import uuid

# uuid5(NAMESPACE_URL, "https://github.com/agpshekhar/hl7-fhir-omop-pipeline"),
# written out so the value can never drift.
NAMESPACE = uuid.UUID("de65e58d-9b9a-587d-9165-aed459490679")


def stable_id(resource_type: str, *key: str) -> str:
    """The UUIDv5 for one business key, as the lowercase text FHIR uses for an id."""
    if not key or any(not part for part in key):
        raise ValueError(f"{resource_type} key has an empty part: {key!r}")
    return str(uuid.uuid5(NAMESPACE, "|".join((resource_type, *key))))


def patient_id(assigning_authority: str, mrn: str) -> str:
    """An MRN means something only together with who issued it (PID-3.4 + PID-3.1)."""
    return stable_id("Patient", assigning_authority, mrn)


def specimen_id(accession: str) -> str:
    """One specimen per accession: the number on the tube label (OBR-3.1)."""
    return stable_id("Specimen", accession)


def diagnostic_report_id(accession: str) -> str:
    """One report per ordered panel, so the accession is the key (OBR-3.1)."""
    return stable_id("DiagnosticReport", accession)


def observation_id(accession: str, loinc: str, sub_id: str) -> str:
    """One result: accession (OBR-3.1) + test code (OBX-3.1) + sub-ID (OBX-4).

    The message control ID (MSH-10) is deliberately not part of the key. A correction
    arrives in a new message with a new MSH-10 but must update the same Observation.
    """
    return stable_id("Observation", accession, loinc, sub_id)
