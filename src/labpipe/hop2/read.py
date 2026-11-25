"""Read Hop 1's FHIR output back out of staging, exactly as it was written.

Hop 2 reads staging.fhir_resource and nothing else: never the HL7 message, never the
Synthea sample. Three details matter when reading it:

- Numbers stay Decimal. psycopg turns a jsonb column into Python with json.loads,
  which makes 139.9 a float (139.900000000000005684...). Selecting resource::text and
  parsing with parse_float=Decimal keeps the exact digits Hop 1 stored, trailing
  zeros included ("14.0" stays 14.0).
- Rows come back in a fixed order (by id), so everything built from them is in the
  same order on every run.
- FHIR instants are UTC text ("1984-10-30T08:42:48Z"). OMOP datetime columns are
  TIMESTAMP without a time zone, so they are stored as UTC wall time, with the
  offset removed only after converting to UTC.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import psycopg

SELECT_RESOURCES = """
    SELECT id, resource::text, source_msh10
      FROM staging.fhir_resource
     WHERE resource_type = %s
     ORDER BY id
"""


@dataclass(frozen=True)
class FhirRow:
    """One row of staging.fhir_resource, with the resource parsed."""

    id: UUID
    resource: dict
    source_msh10: str


def load_resources(conn: psycopg.Connection, resource_type: str) -> list[FhirRow]:
    """Every staged resource of one type, ordered by id, numbers as Decimal."""
    with conn.cursor() as cur:
        rows = cur.execute(SELECT_RESOURCES, (resource_type,)).fetchall()
    return [
        FhirRow(id=row_id, resource=json.loads(text, parse_float=Decimal), source_msh10=msh10)
        for row_id, text, msh10 in rows
    ]


def reference_id(reference: str, resource_type: str) -> UUID:
    """The id inside a literal reference: "Patient/<uuid>" -> UUID('<uuid>').

    The type is checked, so a Specimen reference can never be read as a Patient.
    """
    kind, _, value = reference.partition("/")
    if kind != resource_type or not value:
        raise ValueError(f"expected a {resource_type} reference, got {reference!r}")
    return UUID(value)


def utc_wall_time(instant: str) -> datetime:
    """A FHIR instant as a naive datetime in UTC, ready for an OMOP TIMESTAMP column."""
    moment = datetime.fromisoformat(instant)
    if moment.tzinfo is None:
        raise ValueError(f"instant {instant!r} has no time zone")
    return moment.astimezone(UTC).replace(tzinfo=None)
