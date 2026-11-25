"""Run Hop 1 over staging: every new message becomes FHIR resources, or an error.

    staging.raw_message (status 'new')
        -> parse_oru -> LabResult -> build_bundle -> upsert each entry
        -> staging.fhir_resource, and the message marked 'parsed' (or 'error')

Each message is handled in its own transaction, like a FHIR server handling one
transaction Bundle: all of a panel's resources land together with the status change,
or none do. A message that fails is marked 'error' with the reason and never dropped,
and it doesn't stop the messages after it.
"""

from dataclasses import dataclass

import psycopg

from labpipe.hop1.parse import parse_oru
from labpipe.hop1.to_fhir import build_bundle

SELECT_NEW = "SELECT msh10, raw FROM staging.raw_message WHERE status = 'new' ORDER BY id"

# Upsert by (resource_type, id). The WHERE clause skips rows whose JSON is unchanged,
# so replaying an identical message rewrites nothing, not even updated_at.
UPSERT = """
    INSERT INTO staging.fhir_resource (resource_type, id, resource, source_msh10)
    VALUES (%s, %s, %s::jsonb, %s)
    ON CONFLICT (resource_type, id) DO UPDATE
       SET resource = EXCLUDED.resource,
           source_msh10 = EXCLUDED.source_msh10,
           updated_at = now()
     WHERE staging.fhir_resource.resource IS DISTINCT FROM EXCLUDED.resource
"""

MARK = """
    UPDATE staging.raw_message
       SET status = %s, status_detail = %s, processed_at = now()
     WHERE msh10 = %s
"""


@dataclass
class Hop1Counts:
    parsed: int = 0
    errors: int = 0
    resources: int = 0


def store_message(cur: psycopg.Cursor, msh10: str, raw: str) -> int:
    """Parse one message and upsert its resources. Return how many resources it carried."""
    result = parse_oru(raw)
    if result.msh10 != msh10:
        raise ValueError(f"MSH-10 {result.msh10!r} does not match the staged key {msh10!r}")
    bundle = build_bundle(result)
    for entry in bundle.entry:
        resource = entry.resource
        cur.execute(
            UPSERT,
            (resource.get_resource_type(), resource.id, resource.model_dump_json(), msh10),
        )
    return len(bundle.entry)


def run_hop1(conn: psycopg.Connection) -> Hop1Counts:
    """Process every message with status 'new', oldest first."""
    counts = Hop1Counts()
    with conn.cursor() as cur:
        pending = cur.execute(SELECT_NEW).fetchall()
    for msh10, raw in pending:
        try:
            with conn.transaction(), conn.cursor() as cur:
                counts.resources += store_message(cur, msh10, raw)
                cur.execute(MARK, ("parsed", None, msh10))
            counts.parsed += 1
        except Exception as error:  # noqa: BLE001 - any failure marks the message, never drops it
            with conn.transaction():
                conn.execute(MARK, ("error", f"{type(error).__name__}: {error}", msh10))
            counts.errors += 1
    return counts
