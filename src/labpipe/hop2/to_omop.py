"""Turn one FHIR resource into one OMOP CDM 5.4 row. Pure functions: no database.

Each builder takes the parsed resource, the OMOP IDs the crosswalk gave it, and (for
a measurement) its entry from the mapping cache. It returns a dict keyed by column
name, in the column order of the table, so the loader can COPY it straight in.

OMOP keeps two views of every coded value, and Hop 2 fills both:
- *_concept_id: the standard concept, what analyses filter on;
- *_source_value and *_source_concept_id: exactly what the source sent, so a mapping
  can be audited, or redone when the vocabulary changes.
"""

from labpipe.hop2 import concepts
from labpipe.hop2.mapping import Mapping, Pair
from labpipe.hop2.read import FhirRow, utc_wall_time

PERSON_COLUMNS = (
    "person_id",
    "gender_concept_id",
    "year_of_birth",
    "month_of_birth",
    "day_of_birth",
    "birth_datetime",
    "race_concept_id",
    "ethnicity_concept_id",
    "person_source_value",
    "gender_source_value",
)

MEASUREMENT_COLUMNS = (
    "measurement_id",
    "person_id",
    "measurement_concept_id",
    "measurement_date",
    "measurement_datetime",
    "measurement_type_concept_id",
    "value_as_number",
    "unit_concept_id",
    "range_low",
    "range_high",
    "measurement_source_value",
    "measurement_source_concept_id",
    "unit_source_value",
    "unit_source_concept_id",
    "value_source_value",
)

MRN_TYPE_CODE = "MR"


def mrn(patient: dict) -> str:
    """The medical record number: the identifier whose type is MR (HL7 table 0203)."""
    for identifier in patient.get("identifier", []):
        codings = identifier.get("type", {}).get("coding", [])
        if any(c.get("code") == MRN_TYPE_CODE for c in codings):
            return identifier["value"]
    raise ValueError(f"Patient/{patient['id']} has no MRN identifier")


def person_row(row: FhirRow, person_id: int) -> dict:
    """One PERSON row from a FHIR Patient."""
    patient = row.resource
    year, month, day = (int(part) for part in patient["birthDate"].split("-"))
    return {
        "person_id": person_id,
        "gender_concept_id": concepts.gender_concept(patient.get("gender")),
        "year_of_birth": year,
        "month_of_birth": month,
        "day_of_birth": day,
        "birth_datetime": None,  # FHIR has only a birth date; OMOP says leave the time empty
        "race_concept_id": concepts.NO_MATCHING_CONCEPT,
        "ethnicity_concept_id": concepts.NO_MATCHING_CONCEPT,
        "person_source_value": mrn(patient),
        "gender_source_value": patient.get("gender"),
    }


def pair_of(observation: dict) -> Pair:
    """The mapping cache key for an Observation: (code system, code, unit system, unit)."""
    (coding,) = observation["code"]["coding"]  # Hop 1 sends exactly one coding
    quantity = observation["valueQuantity"]
    return (coding["system"], coding["code"], quantity.get("system", ""), quantity.get("code", ""))


def measurement_row(row: FhirRow, measurement_id: int, person_id: int, mapping: Mapping) -> dict:
    """One MEASUREMENT row from a FHIR laboratory Observation and its cache entry."""
    observation = row.resource
    _, code, _, unit = pair_of(observation)
    quantity = observation["valueQuantity"]
    taken = utc_wall_time(observation["effectiveDateTime"])
    ranges = observation.get("referenceRange", [])
    low = ranges[0].get("low", {}).get("value") if ranges else None
    high = ranges[0].get("high", {}).get("value") if ranges else None
    return {
        "measurement_id": measurement_id,
        "person_id": person_id,
        "measurement_concept_id": mapping.test.concept_id,
        "measurement_date": taken.date(),
        "measurement_datetime": taken,
        "measurement_type_concept_id": concepts.LAB_TYPE_CONCEPT,
        "value_as_number": quantity["value"],
        "unit_concept_id": mapping.unit.concept_id,
        "range_low": low,
        "range_high": high,
        "measurement_source_value": code,
        "measurement_source_concept_id": mapping.test.source_concept_id,
        "unit_source_value": unit or None,
        "unit_source_concept_id": mapping.unit.source_concept_id,
        "value_source_value": str(quantity["value"]),
    }
