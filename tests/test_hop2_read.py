"""Tests for reading Hop 1's FHIR output back out of staging."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

import pytest

from labpipe.hop1.store import run_hop1
from labpipe.hop2.read import load_resources, reference_id, utc_wall_time
from labpipe.lis.sample import load_panels
from labpipe.lis.staging import stage_panels

PATIENT = "a4db83b3-28e5-5049-ba1b-d9f7551f0627"


def test_reference_id_reads_the_uuid():
    assert reference_id(f"Patient/{PATIENT}", "Patient") == UUID(PATIENT)


@pytest.mark.parametrize("reference", [f"Specimen/{PATIENT}", "Patient/", PATIENT])
def test_reference_id_refuses_the_wrong_type_or_shape(reference):
    with pytest.raises(ValueError):
        reference_id(reference, "Patient")


def test_utc_wall_time_drops_the_zone_only_after_converting():
    assert utc_wall_time("1984-10-30T08:42:48Z") == datetime(1984, 10, 30, 8, 42, 48)
    assert utc_wall_time("1984-10-30T03:42:48-05:00") == datetime(1984, 10, 30, 8, 42, 48)


def test_utc_wall_time_refuses_an_instant_without_a_zone():
    with pytest.raises(ValueError, match="no time zone"):
        utc_wall_time("1984-10-30T08:42:48")


def test_load_resources_keeps_decimals_and_orders_by_id(staging_db):
    stage_panels(staging_db, load_panels()[:2])
    run_hop1(staging_db)
    observations = load_resources(staging_db, "Observation")
    assert len(observations) == 16
    assert [o.id for o in observations] == sorted(o.id for o in observations)
    values = [o.resource["valueQuantity"]["value"] for o in observations]
    assert all(isinstance(v, Decimal) for v in values)
    sodium = next(
        o
        for o in observations
        if o.resource["code"]["coding"][0]["code"] == "2951-2" and o.source_msh10 == "BMP000001-1"
    )
    assert sodium.resource["valueQuantity"]["value"] == Decimal("139.9")
