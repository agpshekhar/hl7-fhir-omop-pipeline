"""Give every FHIR resource a stable OMOP ID, kept in staging.crosswalk.

OMOP keys are integers; FHIR ids are UUIDs. Hop 2 empties and reloads the CDM event
tables on every run, so the integer can't live only in the CDM: it would change each
time, and every saved query, cohort or lineage link that names a measurement_id
would break. The crosswalk lives in staging and is never emptied, so:

- a FHIR resource seen before keeps the OMOP ID it got the first time;
- a new one gets the next number from the crosswalk's identity column;
- the crosswalk is also the lineage link: measurement_id -> FHIR id -> source MSH-10.

New ids are inserted in UUID order, so two fresh databases fed the same resources
number them identically.
"""

from uuid import UUID

import psycopg

INSERT_NEW = """
    INSERT INTO staging.crosswalk (fhir_resource_type, fhir_id, omop_table)
    SELECT %s, fhir_id, %s
      FROM unnest(%s::uuid[]) AS new(fhir_id)
     ORDER BY fhir_id
    ON CONFLICT (fhir_resource_type, fhir_id) DO NOTHING
"""

SELECT_IDS = """
    SELECT fhir_id, omop_table, omop_id
      FROM staging.crosswalk
     WHERE fhir_resource_type = %s
       AND fhir_id = ANY(%s::uuid[])
"""


def assign_ids(
    cur: psycopg.Cursor, fhir_resource_type: str, omop_table: str, fhir_ids: list[UUID]
) -> dict[UUID, int]:
    """FHIR id -> OMOP ID for every id given, adding the ones the crosswalk hasn't seen.

    A resource already recorded against a different OMOP table is refused: its ID
    belongs to that table, and reusing it elsewhere would break lineage.
    """
    ordered = sorted(set(fhir_ids))
    cur.execute(INSERT_NEW, (fhir_resource_type, omop_table, ordered))
    ids = {}
    for fhir_id, table, omop_id in cur.execute(SELECT_IDS, (fhir_resource_type, ordered)):
        if table != omop_table:
            raise ValueError(
                f"{fhir_resource_type}/{fhir_id} is already in the crosswalk for {table!r},"
                f" not {omop_table!r}"
            )
        ids[fhir_id] = omop_id
    return ids
