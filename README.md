# hl7-fhir-omop-pipeline

**An interoperability bridge from hospital lab messaging to research-ready data.**

A lab result starts life as an HL7 v2 message, is shared through FHIR APIs, and is analyzed in OMOP research databases. Each hop crosses a different standard, and each is a place where data quietly breaks: duplicate messages, corrections that arrive out of order, codes that never get mapped. This pipeline carries synthetic basic metabolic panel (BMP) results across all three standards, through an interface engine, and proves with automated tests that every result arrives intact.

It also improves on how lab-to-research pipelines usually work: every OMOP value can be traced back to its original HL7 message in one command, bad data is caught at ingest and the checks are scored against a known answer key, unmapped codes go to a review queue instead of silently becoming concept 0, new results reach OMOP within seconds instead of the next nightly batch, and analysts get a query-ready table with one row per panel.

> **Status:** Phase 7 of 16, in progress. See the [roadmap](#roadmap).
>
> **All data is synthetic,** generated with [Synthea](https://synthetichealth.github.io/synthea/). No real patient data is used anywhere in this project.

## How it works

1. **Mock LIS.** A mock laboratory information system builds one HL7 v2.5.1 `ORU^R01` message per BMP panel from a seeded Synthea sample and sends it over MLLP, the way a real lab system does.
2. **BridgeLink channel.** An interface engine channel validates each message. It also rejects values outside physiologic limits taken from a cited public source (a potassium of 45 mmol/L is a decimal error, not a patient). Good messages get an ACK (`AA`) and are written to a Postgres staging table. Bad messages get a NAK (`AE` or `AR`) with the reason and go to an error queue.
3. **Hop 1: structure (HL7 v2 to FHIR R4).** Each staged message is parsed into a canonical `LabResult` model, then built into one FHIR transaction bundle per panel (Patient, Specimen, DiagnosticReport, Observations). Resource IDs are UUIDv5 values derived from business keys, so a resend or replay never creates a duplicate. Hop 1 changes only the shape of the data. It never looks up a vocabulary or touches OMOP.
4. **Quality flags.** Before anything reaches OMOP, each result is checked for problems that are suspicious but possible: a missing or invalid unit, an abnormal flag that disagrees with the reference range, collection after resulting, a missing patient or specimen. Each one is written to a quality flag table with a reason code, so it is caught at the door rather than by a researcher months later.
5. **Hop 2: meaning (FHIR to OMOP CDM 5.4).** Hop 2 reads only the FHIR output. It maps codes through the OHDSI Athena vocabularies (LOINC for tests, UCUM for units), follows "Maps to" relationships, and lets each standard concept's domain decide the OMOP table. It loads PERSON and MEASUREMENT, keeps source values and source concept IDs, and records every FHIR ID in a crosswalk with a stable OMOP ID.
6. **Proof.** A round-trip test checks every result against the Synthea source (same value, unit and collection time, nothing lost, nothing invented). A replay test feeds the pipeline a shuffled, duplicated feed with early corrections and asserts the OMOP tables match a clean run exactly.

Around the two hops, four features make the output easier to trust and use:

- **Lineage.** Every FHIR resource records the MSH-10 of the message that produced it, and the crosswalk links each FHIR ID to its OMOP row. `labpipe trace <measurement_id>` shows one value three ways: the OMOP row, the FHIR Observation and the original OBX segment, with every duplicate and correction that touched it.
- **Mapping cache and review queue.** Hop 2 builds one small lookup table per run (source code and unit to standard concept, domain and unit concept) instead of joining the full vocabulary for every result. A code that misses goes to an unmapped-code queue with suggested matches, so a person reviews each new code once.
- **Incremental loading.** Besides the full rebuild, Hop 1 and Hop 2 can pick up only new messages and upsert by stable ID, so a result reaches OMOP seconds after it arrives. The replay test proves the incremental path gives exactly the same OMOP tables as a full rebuild.
- **Query-ready lab view.** A materialized view holds one row per BMP panel with one column per analyte, beside the standard MEASUREMENT table, so common questions need no self-joins.

```
Seeded Synthea BMP sample
  → Mock LIS sends ORU^R01 over MLLP
  → BridgeLink channel: validate, plausibility limits, ACK or NAK (rejects to the error queue)
  → Postgres staging: every raw message kept, unique on MSH-10
  → Hop 1 (structure): parse to LabResult → FHIR R4 bundle per panel, UUIDv5 IDs, MSH-10 in meta.source
  → Quality flags: suspicious results flagged with a reason before loading
  → Hop 2 (meaning): mapping cache (misses to the review queue) → route by concept domain → OMOP CDM 5.4
     (full rebuild, or incremental with the same result)
  → Query-ready BMP view, lineage trace, dashboard
  → Tests and run report: round trip, replay determinism, quality scorecard, latency
```

<!-- TODO: Add the diagram to docs/architecture.md and embed it here (Phase 13). -->

## Quickstart

You need Docker (Docker Desktop on a Mac) and Git.

```bash
git clone https://github.com/agpshekhar/hl7-fhir-omop-pipeline.git
cd hl7-fhir-omop-pipeline
cp .env.example .env
docker compose up --build -d
docker compose ps -a     # db is healthy; migrate, vocab and pipeline exited with code 0
```

Compose starts Postgres, waits for it to be healthy, then runs each job in order: `migrate` (creates the staging and OMOP tables), `vocab` (loads vocabularies) and `pipeline` (runs the hops). Every job is safe to rerun. On a fresh database the pipeline job loads 12 persons and 10,468 measurements into OMOP

To work on the code outside Docker:

```bash
uv sync
uv run pytest -q
```

A minimal LOINC/UCUM vocabulary subset ships with the repo in `db/vocab_subset/`, so the default stack runs on any clone. A full OHDSI Athena bundle can be mounted into `data/vocab/` (gitignored) if you want it.

## Worked example

One potassium result from panel BMP000001, as it moves through the pipeline.

**HL7 v2 (the OBX segment the mock LIS sends):**

```
OBX|2|NM|2823-3^Potassium [Moles/volume] in Serum or Plasma^LN|1|3.9|mmol/L^mmol/L^UCUM|3.7-5.2|N|||F|||19841030024248-0600
```

**FHIR R4 (the Observation Hop 1 builds, shortened):**

```json
{
  "resourceType": "Observation",
  "id": "0f889cc3-ee88-519f-99bc-9f94b8b3993e",
  "meta": {"source": "LABPIPE_LIS#BMP000001-1"},
  "code": {"coding": [{"system": "http://loinc.org", "code": "2823-3"}]},
  "effectiveDateTime": "1984-10-30T08:42:48Z",
  "valueQuantity": {"value": 3.9, "unit": "mmol/L", "system": "http://unitsofmeasure.org", "code": "mmol/L"},
  "referenceRange": [{"low": {"value": 3.7}, "high": {"value": 5.2}, "text": "3.7-5.2"}]
}
```

**OMOP CDM 5.4 (the MEASUREMENT row Hop 2 loads):**

| Column | Value |
| --- | --- |
| `measurement_concept_id` | 3023103 (Potassium [Moles/volume] in Serum or Plasma) |
| `measurement_source_value` / `measurement_source_concept_id` | `2823-3` / 3023103 |
| `measurement_datetime` | 1984-10-30 08:42:48 (UTC) |
| `value_as_number` / `value_source_value` | 3.9 / `3.9` |
| `unit_concept_id` / `unit_source_value` | 8753 (millimole per liter) / `mmol/L` |
| `range_low` / `range_high` | 3.7 / 5.2 |
| `measurement_type_concept_id` | 32856 (Lab) |

The local time `02:42:48-0600` becomes the UTC instant `08:42:48Z` in Hop 1, and stays UTC in OMOP. The value is carried as an exact decimal from end to end. The crosswalk links the MEASUREMENT row to the Observation's UUID, whose `meta.source` names the message, so the row traces back to the OBX above.

## Tech stack

| Layer | Tool |
| --- | --- |
| Language | Python, with a `labpipe` command-line tool that runs every job |
| Synthetic patients | Synthea (seeded BMP sample committed to the repo) |
| HL7 v2 messaging | hl7apy, MLLP |
| Interface engine | BridgeLink (open-source Mirth Connect fork) |
| FHIR models | fhir.resources (R4B, targeting FHIR R4) |
| Database | Postgres 16 (staging, error queue, quality flags, crosswalk, unmapped-code queue, OMOP), with `pg_trgm` for code suggestions |
| Target data model | OMOP CDM 5.4 with OHDSI Athena vocabularies (LOINC, UCUM) |
| Containers | Docker Compose, one command |
| CI | GitHub Actions, running the same Compose stack |
| Dashboard | Streamlit |
| FHIR server (stretch goal) | Blaze |

## Data quality approach

<!-- TODO: Link the tests and the run report once they exist (Phase 12). -->

- **Fix what's provable, flag what isn't.** Local codes and unit spellings are mapped through vocabularies. A missing unit or an unmapped code is flagged and counted, never guessed.
- **Ground truth.** Synthea is the source of truth, so the round-trip test checks every result end to end, counting in both directions.
- **Messy feeds on purpose.** The mock LIS injects realistic problems (local codes, unit variants, missing units, censored values, hemolyzed specimens, duplicates, corrections, out-of-order arrivals, malformed messages) and logs each one as an answer key.
- **Corrections and ordering.** A correction updates the same business key in place, and the newest result time wins, so a late original is ignored.
- **Check at the door, not after the load.** Every result is checked before Hop 2 loads it: collection before resulting, abnormal flag versus reference range, a valid UCUM unit, a present patient and specimen, and a value inside physiologic limits taken from a cited public source. Impossible values get a NAK; suspicious ones are flagged with a reason. Clinical rules such as critical values and delta checks are left to a separate rule simulator.
- **Score the checks.** Because the mess injection log is a complete answer key, the run report grades the ingest checks the way a lab grades a test: planted defects caught (recall) and clean results wrongly flagged (false alarms), per defect type.
- **No silent concept 0.** A code that doesn't map is queued for review with suggested matches and counted in the run report.
- **Replay determinism.** A shuffled, duplicated feed with early corrections must produce exactly the same OMOP output as a clean one.

## Design decisions and trade-offs

<!-- TODO: One ADR per decision in docs/adr/. -->

Planned decision records:

- Two independent hops, with Hop 2 reading only FHIR
- BridgeLink channel in front of staging, with code mapping kept in Hop 2
- Postgres staging as a durable queue instead of a message broker
- A canonical `LabResult` model inside Hop 1
- Deterministic UUIDv5 IDs from business keys, with upsert semantics
- Corrections and ordering: the newest result time wins
- OMOP destination chosen by concept domain, not by resource type
- Foreign keys kept on, made deferrable, one transaction per load
- Hop 2 loading: full rebuild as the reference, incremental upserts as the default, proven equal by the replay test
- Quality checks at ingest, scored against the mess injection answer key
- Lineage carried on every record (MSH-10 in `meta.source`, FHIR ID in the crosswalk)
- A per-run mapping cache with an unmapped-code review queue
- A query-ready wide BMP view beside the standard MEASUREMENT table
- FHIR R4B library targeting R4, and the US Core trade-off for local codes
- A committed minimal vocabulary subset, with full Athena optional
- hl7apy for strict parsing, revisited if it rejects too much messy input

## Roadmap

The work runs in 16 phases, each closed when its done-when check passes. Gate reviews follow Phases 6, 8, 12 and 16.

**Milestone 1: walking skeleton (Phases 1–6)**

- [x] Phase 1: Athena vocabulary request, `labpipe` CLI skeleton, LICENSE and SOURCES.md, pre-commit with ruff and gitleaks
- [x] Phase 2: app image, Postgres and migrations (staging, error queue, crosswalk, OMOP 5.4, deferrable FKs), CI runs Compose
- [x] Phase 3: seeded Synthea BMP extract and vocabulary subset
- [x] Phase 4: mock LIS writing ORU^R01 messages into staging
- [x] Phase 5: Hop 1, HL7 v2 to FHIR R4 with UUIDv5 IDs, every resource stamped with its source MSH-10
- [x] Phase 6: Hop 2, FHIR to OMOP, with the mapping cache, the round-trip test and a lineage test. **Gate: Milestone 1**

**Milestone 2: interface engine and messy data (Phases 7–12)**

- [ ] Phase 7: BridgeLink in Compose, MLLP channel with ACK
- [ ] Phase 8: validation, plausibility limits, NAK and error queue, interface spec, tag v0.1. **Gate: v0.1**
- [ ] Phase 9: mess injection log, local codes and unit variants, unmapped-code review queue
- [ ] Phase 10: missing units, censored values, hemolysis, corrections and ordering, quality flags at ingest
- [ ] Phase 11: replay determinism test, incremental Hop 1 and Hop 2 proven equal to a full rebuild
- [ ] Phase 12: run report (HTML and JSON) with the quality scorecard and latency, and ADRs. **Gate: Milestone 2**

**Milestone 3: ship it (Phases 13–16)**

- [ ] Phase 13: Streamlit dashboard with the lineage trace page, query-ready BMP view and query benchmark, architecture, mapping and interface specs
- [ ] Phase 14: final README, Makefile, badges, mypy, fresh-clone test
- [ ] Phase 15: demo video and final read-through of the docs (Blaze only if ahead)
- [ ] Phase 16: tag v1.0. **Gate: v1.0**

## Out of scope

The ASTM analyzer and middleware layer, the order loop (ORM/OML), ADT and patient merges, SMART on FHIR apps, and machine learning on OMOP.

## At production scale

<!-- TODO: Expand once Milestone 2 is done. -->

What a production version would add: highly available MLLP listeners, monitoring and alerting on the error queue, PHI security controls, transformation tooling such as dbt, workflow orchestration, and cloud deployment.

## Demo

<!-- TODO: Link the demo video (Phase 15). -->

_Coming in Phase 15._
