"""Tests for Hop 2's fixed OMOP concepts and the domain router."""

from pathlib import Path

import pytest

from labpipe.hop2 import concepts

SUBSET = Path(__file__).resolve().parent.parent / "db" / "vocab_subset" / "CONCEPT.csv"


def test_known_fhir_systems_name_their_omop_vocabulary():
    assert concepts.vocabulary_for("http://loinc.org") == "LOINC"
    assert concepts.vocabulary_for("http://unitsofmeasure.org") == "UCUM"


def test_an_unknown_system_is_not_guessed():
    assert concepts.vocabulary_for("urn:local:lab-codes") is None


def test_gender_maps_to_the_omop_gender_concepts():
    assert concepts.gender_concept("female") == 8532
    assert concepts.gender_concept("male") == 8507


@pytest.mark.parametrize("gender", ["other", "unknown", None])
def test_genders_without_a_standard_concept_get_zero(gender):
    assert concepts.gender_concept(gender) == 0


def test_a_measurement_concept_routes_to_measurement():
    assert concepts.route("Measurement", 3023103) == "measurement"


def test_concept_zero_takes_the_documented_default():
    assert concepts.route(None, 0) == concepts.UNMAPPED_TABLE == "measurement"


def test_a_domain_without_a_table_is_refused():
    with pytest.raises(ValueError, match="domain 'Condition'"):
        concepts.route("Condition", 900005)


def test_event_tables_empty_children_before_parents():
    # measurement references person, so it must come first in a rebuild.
    assert concepts.EVENT_TABLES.index("measurement") < concepts.EVENT_TABLES.index("person")


def test_fixed_concepts_exist_in_the_committed_vocabulary_subset():
    concept_ids = {
        int(line.split("\t", 1)[0]) for line in SUBSET.read_text(encoding="utf-8").splitlines()[1:]
    }
    fixed = {concepts.LAB_TYPE_CONCEPT, concepts.NO_MATCHING_CONCEPT, *concepts.GENDER.values()}
    assert fixed <= concept_ids
