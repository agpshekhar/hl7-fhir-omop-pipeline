"""Write ORU^R01 messages into staging.raw_message.

raw_message is unique on msh10, so inserting with ON CONFLICT DO NOTHING makes the
job safe to rerun: a message that is already staged is skipped, never duplicated or
overwritten. This is the same rule the interface engine will follow from Phase 7.
"""

import psycopg

from labpipe.lis.oru import build_oru, message_control_id
from labpipe.lis.sample import Panel

INSERT_SQL = """
    INSERT INTO staging.raw_message (msh10, message_type, raw)
    VALUES (%s, 'ORU^R01', %s)
    ON CONFLICT (msh10) DO NOTHING
"""


def stage_panels(conn: psycopg.Connection, panels: list[Panel]) -> int:
    """Build and insert one message per panel, in one transaction. Return how many were new."""
    rows = [(message_control_id(p), build_oru(p)) for p in panels]
    with conn.transaction(), conn.cursor() as cur:
        before = cur.execute("SELECT count(*) FROM staging.raw_message").fetchone()[0]
        cur.executemany(INSERT_SQL, rows)
        after = cur.execute("SELECT count(*) FROM staging.raw_message").fetchone()[0]
    return after - before
