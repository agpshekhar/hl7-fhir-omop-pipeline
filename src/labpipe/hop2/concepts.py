"""Fixed OMOP concepts and rules that Hop 2 applies without a vocabulary lookup.

Most of Hop 2's meaning comes from the Athena vocabularies (labpipe.hop2.mapping).
A few things don't: which OMOP vocabulary a FHIR coding system is, which concept a
FHIR gender code is, how a lab result was recorded, and which CDM table a domain
belongs in. Those are fixed by the OMOP conventions, so they live here, in one place.

Sources: OMOP CDM 5.4 conventions (ohdsi.github.io/CommonDataModel/cdm54.html),
the OMOP Gender and Type Concept vocabularies, and the FHIR R4 code system URIs.
"""

# FHIR code system URI -> OMOP vocabulary_id. A system not listed here is not
# guessed: its codes get concept 0 and are counted as unmapped.
VOCABULARIES = {
    "http://loinc.org": "LOINC",
    "http://unitsofmeasure.org": "UCUM",
    "http://snomed.info/sct": "SNOMED",
}

# Patient.gender -> PERSON.gender_concept_id (OMOP Gender vocabulary).
# FHIR "other" and "unknown" have no standard gender concept, so they get 0.
GENDER = {"male": 8507, "female": 8532}

# MEASUREMENT.measurement_type_concept_id: where the record came from. "Lab" is the
# OMOP Type Concept for a result reported by a laboratory.
LAB_TYPE_CONCEPT = 32856

# Hop 2 knows nothing about race or ethnicity: the mock LIS sends neither (PID-10 and
# PID-22 stay empty). OMOP requires both columns, so they get the "no information" 0.
NO_MATCHING_CONCEPT = 0

# The CDM event tables Hop 2 owns. A full rebuild empties exactly these, children
# before parents, and loads them again. The vocabulary tables are never touched.
EVENT_TABLES = ("measurement", "person")

# A standard concept's domain_id decides its CDM table. Phase 6 loads lab results
# only, so only the Measurement domain has a table so far.
DOMAIN_TABLES = {"Measurement": "measurement"}

# The documented default for concept 0. A code with no standard concept has no domain,
# so it cannot route itself. It goes where its FHIR resource says it belongs: a
# laboratory Observation is a measurement. The row keeps its source value, so nothing
# is lost, and the run counts it, so it is never silent.
UNMAPPED_TABLE = "measurement"


def vocabulary_for(system: str) -> str | None:
    """The OMOP vocabulary_id for a FHIR code system URI, or None if it isn't known."""
    return VOCABULARIES.get(system)


def gender_concept(gender: str | None) -> int:
    """PERSON.gender_concept_id for a FHIR Patient.gender value."""
    return GENDER.get(gender or "", NO_MATCHING_CONCEPT)


def route(domain_id: str | None, concept_id: int) -> str:
    """The CDM table for a mapped code: its domain's table, or the concept-0 default.

    A standard concept in a domain without a table yet (a lab code that maps to a
    Condition, say) is refused, not forced into MEASUREMENT: a wrong table is worse
    than a loud error.
    """
    if concept_id == NO_MATCHING_CONCEPT:
        return UNMAPPED_TABLE
    try:
        return DOMAIN_TABLES[domain_id]
    except KeyError:
        raise ValueError(
            f"concept {concept_id} is in domain {domain_id!r}, which has no CDM table yet"
        ) from None
