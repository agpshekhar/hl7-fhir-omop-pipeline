"""Tests for the crosswalk: stable OMOP IDs for FHIR resources."""

import uuid

import pytest

from labpipe.hop2.crosswalk import assign_ids

A, B, C = (uuid.UUID(int=n) for n in (1, 2, 3))


def test_new_ids_are_numbered_in_uuid_order(staging_db):
    with staging_db.cursor() as cur:
        ids = assign_ids(cur, "Observation", "measurement", [C, A, B])
    assert ids[A] < ids[B] < ids[C]


def test_a_resource_keeps_its_id_on_every_later_run(staging_db):
    with staging_db.cursor() as cur:
        first = assign_ids(cur, "Observation", "measurement", [A, B])
        second = assign_ids(cur, "Observation", "measurement", [B, C, A])
    assert second[A] == first[A] and second[B] == first[B]
    assert second[C] not in first.values()


def test_duplicates_in_one_call_get_one_id(staging_db):
    with staging_db.cursor() as cur:
        ids = assign_ids(cur, "Patient", "person", [A, A, A])
    assert list(ids) == [A]
    assert staging_db.execute("SELECT count(*) FROM staging.crosswalk").fetchone()[0] == 1


def test_the_same_uuid_under_two_resource_types_gets_two_ids(staging_db):
    with staging_db.cursor() as cur:
        person = assign_ids(cur, "Patient", "person", [A])
        measurement = assign_ids(cur, "Observation", "measurement", [A])
    assert person[A] != measurement[A]


def test_a_resource_cannot_move_to_another_table(staging_db):
    with staging_db.cursor() as cur:
        assign_ids(cur, "Observation", "measurement", [A])
        with pytest.raises(ValueError, match="already in the crosswalk for 'measurement'"):
            assign_ids(cur, "Observation", "observation", [A])


def test_ids_survive_emptying_the_cdm(staging_db):
    # The crosswalk is in staging, so the rebuild that empties cdm.* never touches it.
    with staging_db.cursor() as cur:
        first = assign_ids(cur, "Observation", "measurement", [A])
        cur.execute("DELETE FROM cdm.measurement")
        assert assign_ids(cur, "Observation", "measurement", [A]) == first
