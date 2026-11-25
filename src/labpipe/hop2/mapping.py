"""The per-run mapping cache: (test code, unit) -> standard concepts, in one query.

A FHIR Observation names its test as (system, code), e.g. ("http://loinc.org",
"2823-3"), and its unit as a UCUM code, e.g. "mmol/L". OMOP wants integers: the
*source* concept (the code exactly as sent) and the *standard* concept it "Maps to"
(the one analyses use). For LOINC lab tests and UCUM units the two are usually the
same concept, but a retired or local code maps to a different one, so Hop 2 always
follows "Maps to" and never assumes.

Looking each result up separately would run one query per row (10,648 for the sample,
millions in a real feed) for the same 16 answers. Instead, Hop 2 collects the distinct
(code, unit) pairs once, resolves them all in one query, and keeps the answers in a
dict for the run. Every row after that is a dictionary lookup.

A pair the vocabulary can't resolve still gets an entry, with concept 0. That keeps
lookups total (no KeyError mid-run) and lets the run count every miss.
"""

from collections.abc import Iterable
from dataclasses import dataclass

import psycopg

from labpipe.hop2.concepts import NO_MATCHING_CONCEPT, vocabulary_for

# For each (vocabulary_id, concept_code) asked about: the source concept, and the
# standard concept it maps to with that concept's domain. Only valid relationships
# to valid standard concepts count. LEFT JOINs keep a code that exists without a
# usable mapping (standard_id is NULL), and a code that isn't in the vocabulary at all
# simply returns no row.
RESOLVE = """
    SELECT src.vocabulary_id, src.concept_code, src.concept_id,
           std.concept_id, std.domain_id
      FROM unnest(%s::text[], %s::text[]) AS wanted(vocabulary_id, concept_code)
      JOIN cdm.concept src
        ON src.vocabulary_id = wanted.vocabulary_id
       AND src.concept_code = wanted.concept_code
      LEFT JOIN cdm.concept_relationship rel
        ON rel.concept_id_1 = src.concept_id
       AND rel.relationship_id = 'Maps to'
       AND rel.invalid_reason IS NULL
      LEFT JOIN cdm.concept std
        ON std.concept_id = rel.concept_id_2
       AND std.standard_concept = 'S'
       AND std.invalid_reason IS NULL
"""


@dataclass(frozen=True)
class Concept:
    """One code resolved: the source concept as sent and the standard concept it maps to."""

    source_concept_id: int
    concept_id: int
    domain_id: str | None


UNMAPPED = Concept(NO_MATCHING_CONCEPT, NO_MATCHING_CONCEPT, None)


@dataclass(frozen=True)
class Mapping:
    """Everything MEASUREMENT needs for one (test code, unit) pair."""

    test: Concept
    unit: Concept

    @property
    def mapped(self) -> bool:
        """True when both the test and the unit reached a standard concept."""
        return NO_MATCHING_CONCEPT not in (self.test.concept_id, self.unit.concept_id)


# A pair as Hop 2 sees it on an Observation: (code system, code, unit system, unit code).
Pair = tuple[str, str, str, str]


class MappingCache:
    """The answers for one run. Build it with build_cache; read it with lookup."""

    def __init__(self, entries: dict[Pair, Mapping]):
        self._entries = entries

    def __len__(self) -> int:
        return len(self._entries)

    def lookup(self, pair: Pair) -> Mapping:
        """The mapping for a pair the cache was built with. Anything else is a bug."""
        try:
            return self._entries[pair]
        except KeyError:
            raise KeyError(f"{pair} was not in the mapping cache built for this run") from None

    def unmapped(self) -> list[Pair]:
        """The pairs that didn't reach a standard concept, in a stable order."""
        return sorted(pair for pair, mapping in self._entries.items() if not mapping.mapped)


def resolve(
    conn: psycopg.Connection, codes: set[tuple[str, str]]
) -> dict[tuple[str, str], Concept]:
    """(vocabulary_id, concept_code) -> Concept for each code, in one round trip.

    A code that maps to more than one standard concept is refused. That's legal in
    OMOP (a combination code can map to two), but never right for a single lab result.
    """
    wanted = sorted(codes)
    with conn.cursor() as cur:
        rows = cur.execute(RESOLVE, ([v for v, _ in wanted], [c for _, c in wanted])).fetchall()

    sources: dict[tuple[str, str], int] = {}
    targets: dict[tuple[str, str], set[tuple[int, str]]] = {}
    for vocabulary_id, code, source_id, standard_id, domain_id in rows:
        key = (vocabulary_id, code)
        sources[key] = source_id
        targets.setdefault(key, set())
        if standard_id is not None:  # NULL: no valid "Maps to" to a valid standard concept
            targets[key].add((standard_id, domain_id))

    found = {}
    for key, source_id in sources.items():
        if len(targets[key]) > 1:
            raise ValueError(f"{key[0]} {key[1]} maps to more than one standard concept")
        if targets[key]:
            ((standard_id, domain_id),) = targets[key]
            found[key] = Concept(source_id, standard_id, domain_id)
        else:
            found[key] = Concept(source_id, NO_MATCHING_CONCEPT, None)
    return found


def build_cache(conn: psycopg.Connection, pairs: Iterable[Pair]) -> MappingCache:
    """Resolve every distinct pair once and return the cache for this run."""
    pairs = set(pairs)
    codes = set()
    for code_system, code, unit_system, unit in pairs:
        for system, value in ((code_system, code), (unit_system, unit)):
            vocabulary_id = vocabulary_for(system)
            if vocabulary_id and value:
                codes.add((vocabulary_id, value))
    concepts = resolve(conn, codes)

    def concept_for(system: str, value: str) -> Concept:
        return concepts.get((vocabulary_for(system), value), UNMAPPED)

    return MappingCache(
        {
            pair: Mapping(test=concept_for(pair[0], pair[1]), unit=concept_for(pair[2], pair[3]))
            for pair in pairs
        }
    )
