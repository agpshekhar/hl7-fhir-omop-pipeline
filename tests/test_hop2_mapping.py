"""Tests for the per-run mapping cache: codes through "Maps to" to standard concepts.

These load tests/fixtures/athena_mini, which has the awkward cases a real Athena
download has: a retired code that maps to its replacement, a mapping marked invalid,
and a code in a domain Hop 2 has no table for.
"""

from pathlib import Path

import pytest

from labpipe.common.vocab import load_vocabulary
from labpipe.hop2.mapping import UNMAPPED, Concept, build_cache

ATHENA_MINI = Path(__file__).parent / "fixtures" / "athena_mini"
LOINC, UCUM, SNOMED = "http://loinc.org", "http://unitsofmeasure.org", "http://snomed.info/sct"


@pytest.fixture
def vocab_db(staging_db):
    load_vocabulary(staging_db, ATHENA_MINI)
    return staging_db


def pair(code, unit="mmol/L", system=LOINC):
    return (system, code, UCUM, unit)


def test_a_standard_code_maps_to_itself(vocab_db):
    mapping = build_cache(vocab_db, [pair("2947-0")]).lookup(pair("2947-0"))
    assert mapping.test == Concept(900002, 900002, "Measurement")
    assert mapping.unit == Concept(8753, 8753, "Unit")
    assert mapping.mapped


def test_a_retired_code_follows_maps_to_but_keeps_its_source_concept(vocab_db):
    mapping = build_cache(vocab_db, [pair("9999-9")]).lookup(pair("9999-9"))
    assert mapping.test == Concept(900003, 900002, "Measurement")


def test_an_invalid_mapping_is_ignored(vocab_db):
    # 2339-0 has a valid "Maps to" itself and one marked invalid (D) to a SNOMED concept.
    mapping = build_cache(vocab_db, [pair("2339-0", "mg/dL")]).lookup(pair("2339-0", "mg/dL"))
    assert mapping.test == Concept(900001, 900001, "Measurement")
    assert mapping.unit.concept_id == 8840


def test_an_unknown_code_gets_concept_zero_and_is_listed(vocab_db):
    cache = build_cache(vocab_db, [pair("2947-0"), pair("0000-0")])
    assert cache.lookup(pair("0000-0")).test == UNMAPPED
    assert cache.unmapped() == [pair("0000-0")]


def test_an_unknown_unit_or_system_gets_concept_zero(vocab_db):
    odd_unit, local = pair("2947-0", "MMOL/L"), pair("K", system="urn:local:lab")
    cache = build_cache(vocab_db, [odd_unit, local])
    assert cache.lookup(odd_unit).unit == UNMAPPED
    assert cache.lookup(local).test == UNMAPPED
    assert cache.unmapped() == sorted([odd_unit, local])


def test_the_domain_comes_from_the_standard_concept(vocab_db):
    mapping = build_cache(vocab_db, [pair("80394007", system=SNOMED)]).lookup(
        pair("80394007", system=SNOMED)
    )
    assert mapping.test.domain_id == "Condition"


def test_one_entry_per_distinct_pair(vocab_db):
    cache = build_cache(vocab_db, [pair("2947-0")] * 500 + [pair("2339-0", "mg/dL")])
    assert len(cache) == 2


def test_a_pair_outside_the_cache_is_a_bug_not_a_miss(vocab_db):
    cache = build_cache(vocab_db, [pair("2947-0")])
    with pytest.raises(KeyError, match="not in the mapping cache"):
        cache.lookup(pair("2339-0"))


def test_a_code_with_two_standard_targets_is_refused(vocab_db):
    vocab_db.execute(
        "INSERT INTO cdm.concept_relationship VALUES"
        " (900002, 900004, 'Maps to', '1970-01-01', '2099-12-31', NULL)"
    )
    with pytest.raises(ValueError, match="more than one standard concept"):
        build_cache(vocab_db, [pair("2947-0")])


def test_the_committed_subset_maps_all_eight_bmp_analytes(omop_db):
    bmp = {
        "2951-2": "mmol/L",
        "2823-3": "mmol/L",
        "2075-0": "mmol/L",
        "2028-9": "mmol/L",
        "3094-0": "mg/dL",
        "2160-0": "mg/dL",
        "2345-7": "mg/dL",
        "17861-6": "mg/dL",
    }
    cache = build_cache(omop_db, [pair(code, unit) for code, unit in bmp.items()])
    assert len(cache) == 8 and cache.unmapped() == []
