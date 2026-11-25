"""Run Hop 2: rebuild PERSON and MEASUREMENT from staging.fhir_resource.

    staging.fhir_resource (Patient, Observation)
        -> mapping cache (one query) -> route by domain -> crosswalk IDs
        -> cdm.person, cdm.measurement

Hop 2 is a full rebuild, in one transaction: empty the event tables, load them again,
commit. A reader sees the old CDM or the new one, never half of each, and a run that
fails anywhere rolls back to the old one. Because the crosswalk survives, the rebuilt
rows get the same IDs as before.

Specimen and DiagnosticReport are not loaded yet. A BMP's meaning lives in its eight
Observations; the panel itself has no OMOP row in Phase 6.
"""

from dataclasses import dataclass, field

import psycopg
from psycopg import sql

from labpipe.hop2 import concepts
from labpipe.hop2.crosswalk import assign_ids
from labpipe.hop2.mapping import Pair, build_cache
from labpipe.hop2.read import load_resources, reference_id
from labpipe.hop2.to_omop import (
    MEASUREMENT_COLUMNS,
    PERSON_COLUMNS,
    measurement_row,
    pair_of,
    person_row,
)


@dataclass
class Hop2Counts:
    persons: int = 0
    measurements: int = 0
    cache_entries: int = 0
    unmapped: list[Pair] = field(default_factory=list)


def copy_rows(cur: psycopg.Cursor, table: str, columns: tuple[str, ...], rows: list[dict]):
    """COPY rows into cdm.<table>: one streamed command instead of one INSERT per row."""
    query = sql.SQL("COPY cdm.{} ({}) FROM STDIN").format(
        sql.Identifier(table), sql.SQL(", ").join(map(sql.Identifier, columns))
    )
    with cur.copy(query) as copy:
        for row in rows:
            copy.write_row(tuple(row[c] for c in columns))


def run_hop2(conn: psycopg.Connection) -> Hop2Counts:
    """Rebuild the CDM event tables from the staged FHIR resources."""
    counts = Hop2Counts()
    with conn.transaction(), conn.cursor() as cur:
        for table in concepts.EVENT_TABLES:
            cur.execute(sql.SQL("DELETE FROM cdm.{}").format(sql.Identifier(table)))

        patients = load_resources(conn, "Patient")
        observations = load_resources(conn, "Observation")

        # 1. One query resolves every distinct (code, unit) pair for the whole run.
        cache = build_cache(conn, (pair_of(o.resource) for o in observations))
        counts.cache_entries = len(cache)
        counts.unmapped = cache.unmapped()

        # 2. Route each Observation by its standard concept's domain.
        routed = {}
        for obs in observations:
            mapping = cache.lookup(pair_of(obs.resource))
            table = concepts.route(mapping.test.domain_id, mapping.test.concept_id)
            routed.setdefault(table, []).append((obs, mapping))
        measurements = routed.pop("measurement", [])
        if routed:  # a table route() knows but this loader doesn't write yet
            raise ValueError(f"no loader for CDM tables {sorted(routed)}")

        # 3. Stable OMOP IDs from the crosswalk.
        person_ids = assign_ids(cur, "Patient", "person", [p.id for p in patients])
        measurement_ids = assign_ids(
            cur, "Observation", "measurement", [o.id for o, _ in measurements]
        )

        # 4. Build and COPY the rows.
        persons = [person_row(p, person_ids[p.id]) for p in patients]
        rows = [
            measurement_row(
                obs,
                measurement_ids[obs.id],
                person_ids[reference_id(obs.resource["subject"]["reference"], "Patient")],
                mapping,
            )
            for obs, mapping in measurements
        ]
        copy_rows(cur, "person", PERSON_COLUMNS, persons)
        copy_rows(cur, "measurement", MEASUREMENT_COLUMNS, rows)
        counts.persons, counts.measurements = len(persons), len(rows)

        # Check every deferred foreign key here, so a bad concept or person ID fails
        # this run with a clear error instead of at some later commit.
        cur.execute("SET CONSTRAINTS ALL IMMEDIATE")
    return counts
