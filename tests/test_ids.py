"""Tests for deterministic UUIDv5 resource IDs."""

import uuid

import pytest

from labpipe.common import ids


def test_the_same_key_always_gives_the_same_id():
    assert ids.patient_id("LABPIPE_HOSP", "100007") == ids.patient_id("LABPIPE_HOSP", "100007")


def test_ids_are_version_5_uuids_in_lowercase_text():
    value = ids.observation_id("BMP000001", "2823-3", "1")
    assert value == value.lower()
    assert uuid.UUID(value).version == 5


def test_the_ids_are_pinned():
    # If either of these changes, every ID already stored would change with it.
    assert ids.patient_id("LABPIPE_HOSP", "100007") == "a4db83b3-28e5-5049-ba1b-d9f7551f0627"
    assert ids.observation_id("BMP000001", "2951-2", "1") == "7bb6578a-d0b6-5959-8088-db6de7b631bc"


def test_resource_type_is_part_of_the_key():
    assert ids.specimen_id("BMP000001") != ids.diagnostic_report_id("BMP000001")


def test_every_part_of_the_key_matters():
    base = ids.observation_id("BMP000001", "2823-3", "1")
    assert ids.observation_id("BMP000002", "2823-3", "1") != base
    assert ids.observation_id("BMP000001", "2951-2", "1") != base
    assert ids.observation_id("BMP000001", "2823-3", "2") != base


def test_the_issuer_is_part_of_the_patient_key():
    assert ids.patient_id("LABPIPE_HOSP", "100007") != ids.patient_id("OTHER_HOSP", "100007")


def test_an_empty_key_part_is_refused():
    with pytest.raises(ValueError, match="empty part"):
        ids.observation_id("BMP000001", "2823-3", "")
