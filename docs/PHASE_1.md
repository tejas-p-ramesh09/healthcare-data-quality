# Phase 1 ingestion

This CLI imports bounded directories of synthetic FHIR R4 collection Bundles into immutable batch snapshots. It validates the subset consumed by the projections. Full FHIR conformance and terminology validation are outside this contract.

## Lock and recovery contract

One dedicated, non-pooled Psycopg connection owns a PostgreSQL session advisory lock keyed by the bigint batch ID in this dedicated database. Its lifetime starts after durable manifest registration and ends when the connection closes. Each contender uses pg_try_advisory_lock once and returns busy immediately on contention. After acquiring the lock, read batch status again. A published batch returns the existing result. Otherwise mark any running attempt interrupted and start a new attempt in one transaction. Only the lock holder changes attempt or publication state.

Connect timeout is 5 seconds, statement timeout 30 seconds and lock timeout 5 seconds. Parse outside the publication transaction. Hold the same session lock across transactions. Publish raw and normalized rows and mark batch and attempt successful in one transaction. A write failure rolls that transaction back before recording a failure in a separate transaction. A lost or uncertain connection leaves outcomes to the next submission. A fresh connection must acquire the batch lock and reread durable status before retrying. A committed publication wins even if its client never received confirmation. PostgreSQL releases the advisory lock when the server session ends. During a network partition retries can return busy until the server detects session loss.

The application does not retry permanent input errors automatically. Resubmission verifies the manifest and uses the same batch key. Corrected input is a new snapshot.

## Setup and commands

From the repository root, use Python 3.13, uv 0.11.19 and Docker Compose. Copy `.env.example` to the ignored `.env` and replace the example passwords. Use distinct passwords for PostgreSQL administration and the ingestion role. URL-encode passwords in connection strings as needed. Export the variables using your shell before running the CLI.

```sh
uv sync --locked --python 3.13
docker compose up -d --wait
export PYTHONPATH=src
uv run --locked python -m healthcare_dq.cli migrate
docker compose exec postgres psql -U postgres -d healthcare
```

In psql, run `\password ingest` and enter the password used in `DATABASE_URL`. The migration creates the login and grants read/insert privileges and narrow status updates. It does not grant update/delete on published resources or schema creation. Exit psql before using the CLI.

```sh
uv run --locked python -m healthcare_dq.cli ingest data/generated/phase0/run-01/baseline/fhir --source phase0
uv run --locked python -m healthcare_dq.cli status BATCH_ID
uv run --locked python -m healthcare_dq.cli retry data/generated/phase0/run-01/baseline/fhir --source phase0 --batch-id BATCH_ID
```

Replace `BATCH_ID` with the returned ID. Read the JSON result from stdout and the structured summary with duration, counts, and replay status from stderr. Exit codes are 0 for success or a status lookup in progress, 1 for failure and 3 for batch lock contention. For connection errors, consult durable status or safely submit the same manifest again. Validation reports the first blocking problem with a file basename and JSON Pointer where available. Use synthetic fixture filenames that do not carry personal information.

For database tests, create a separate empty database initialized with the same PostgreSQL version and point `TEST_DATABASE_URL` at it using an owner login. The tests use unique source keys and intentionally terminate only their own test connection. Set `SYNTHEA_BASELINE` to the retained baseline directory for the additional end-to-end test. Hosted CI uses small synthetic fixtures and does not need Java or the Synthea export.

```sh
uv run --locked python -m unittest discover -s tests -t . -v
```

## Data contract and tradeoffs

Accept top-level collection Bundles from non-recursive JSON directories. A manifest hashes sorted filename, byte length and SHA-256 triples. The same source and contract version reuse the same immutable batch. Changed bytes, a renamed file, or a different source create a separate snapshot. Parsed JSONB preserves resource content, including unsupported resource types, while file and entry positions and file digests preserve provenance. Exact original bytes remain in the source directory. Resource IDs and links are scoped to the batch.

A `urn:uuid:` reference must match the exact `fullUrl` in the containing Bundle. A supported unversioned relative reference such as `Patient/p1` may resolve across Bundles in the batch. Forward links work because identity indexes are built before projection. Reject duplicate supported logical IDs and duplicate aliases within a Bundle, missing required subjects, ambiguous links, wrong target types, and encounter links to another patient. This contract does not fetch absolute links or resolve versioned, contained, or identifier-only references. Provider, medication-resource and other links outside the selected projection remain in raw JSON.

Patient birth dates retain their original text precision. Encounters, observations, conditions and medication requests retain selected codes, scalar values and times. Codeable concepts retain their coding arrays in JSONB. Other values, components and alternate date forms remain available in raw JSON. PostgreSQL enforces same-batch patient and encounter references and same-patient encounter links. Date strings remain text in this initial projection. The sample timeline query orders the observed Synthea time strings and does not establish chronological correctness for arbitrary offsets or partial dates.

Limits are 512 files, 16 MiB per file, 256 MiB of input bytes, JSON depth 64, and 150,000 entries. Validation allocates decoded objects and currently buffers the batch. Input size limits bound the workload but are not process-memory quotas. A bounded batch-level transaction simplifies recovery at the cost of rejecting the entire batch when a supported record cannot be published.

## Verified correctness

The retained baseline manifest `e5cfc1631f52f4e98c7185de578409b1ab44b110d5c58725a6b861c222667d12` published 7 files, 3,099 raw entries, and 1,678 normalized records: 5 Patients, 197 Encounters, 1,196 Observations, 154 Conditions, and 126 MedicationRequests. The other 1,421 entries remained raw only. Exact replay returned the same batch, and publication also succeeded as the restricted ingestion role.

Database tests forced a constraint failure after the inserts, terminated a connection during publication, simulated loss of the success response after commit, and synchronized competing clients. Failed publication and session termination left zero committed resource rows. Retry recovered the interrupted attempt and returned the existing snapshot after a lost success response. Contention returned busy for the second writer, and replay preserved the counts.

## Benchmark evidence

Generate the 25- and 100-patient requests before timing, using the pinned JAR and seeds from the [Phase 0 runbook](PHASE_0.md) and [benchmark configuration](../config/synthea/phase1-benchmark.json). Use the same Java command with `-p 25` or `-p 100` and a new output directory. Use the retained Phase 0 export for size 5. For each trial create a new database, point both connection URLs at it, and run:

```sh
PYTHONPATH=src uv run --locked python scripts/benchmark_ingestion.py PATH_TO_FHIR_DIRECTORY
```

The harness applies migrations before timing, measures discovery through committed result, then measures a same-manifest replay. Generation, migration, and plan capture are outside the timed ingestion. It emits resource counts, manifest, phase times, throughput, process high-water RSS, database size, and two `EXPLAIN (ANALYZE, BUFFERS)` plans. Database size includes PostgreSQL catalog overhead. Retain per-trial reports in an ignored directory.

Observed on macOS 26.6.2 ARM64 with Python 3.13.13, PostgreSQL 17.9 in Docker 29.5.3, and Docker reporting 10 CPUs and 8,321,515,520 bytes of memory. Five fresh database trials per completed dataset, with a fresh Python process and the same input files. The database container and host filesystem cache were shared across trials. Ingestion code was at `bd23829` with the benchmark harness later committed in `2fc4e30`.

| Patients | Raw / normalized | Ingest seconds, median (range) | Replay median | Entries/s median | Peak RSS median | DB bytes |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 | 3,099 / 1,678 | 0.411 (0.407–0.428) | 10.7 ms | 7,544 | 84.5 MiB | 15,095,475 |
| 25 | 14,685 / 7,580 | 1.945 (1.921–1.971) | 25.9 ms | 7,550 | 227.3 MiB | 38,540,979 |

The 25-patient manifest was `c61d6bf82e79c7cb2e3fd3d8a17988edd29a82b97653225365e7b2c49d11bc4d`. Parse and reference resolution took a median 0.235 s and 1.217 s, compared with 0.158 s and 0.682 s for publication. The first trial's timeline plan had a top-level Sort (0.047 ms and 0.053 ms). The provenance lookup had a top-level Nested Loop (0.016 ms and 0.026 ms). These are observed local samples, with no SLA implied.

The 100-patient request exported 111 Patient records, including 11 deceased, in 113 files. Independent inspection found 87,834 raw entries and 49,640 supported entries in 227,762,650 bytes. The largest Bundle was 19,873,139 bytes, above the 16 MiB file limit. Discovery rejected the dataset before it reached PostgreSQL. No ingestion rate is reported at this size, and no records were removed to make it fit. Before supporting this workload, evaluate streaming or a larger bounded input contract against observed memory use.
