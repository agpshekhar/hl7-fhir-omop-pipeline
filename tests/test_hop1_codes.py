"""Tests for the fixed HL7-to-FHIR code tables."""

import pytest

from labpipe.hop1 import codes


def test_hl7_coding_systems_become_fhir_uris():
    assert codes.fhir_system("LN") == "http://loinc.org"
    assert codes.fhir_system("UCUM") == "http://unitsofmeasure.org"
    assert codes.fhir_system("SCT") == "http://snomed.info/sct"


def test_an_unknown_coding_system_is_refused_not_guessed():
    with pytest.raises(ValueError, match="unknown HL7 coding system 'L'"):
        codes.fhir_system("L")


def test_lookup_names_the_field_in_its_error():
    assert codes.lookup(codes.OBSERVATION_STATUS, "F", "OBX-11 status") == "final"
    with pytest.raises(ValueError, match="unknown OBX-11 status code 'Z'"):
        codes.lookup(codes.OBSERVATION_STATUS, "Z", "OBX-11 status")


def test_identifier_systems_are_built_from_the_issuer():
    assert codes.identifier_system("mrn", "LABPIPE_HOSP") == "urn:labpipe:mrn:LABPIPE_HOSP"
