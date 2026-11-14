# Phase 1 ingestion

This CLI imports bounded directories of synthetic FHIR R4 collection Bundles into immutable batch snapshots. It validates the subset consumed by the projections. Full FHIR conformance and terminology validation are outside this contract.

## Lock and recovery contract

One dedicated, non-pooled Psycopg connection owns a PostgreSQL session advisory lock keyed by the bigint batch ID in this dedicated database. Its lifetime starts after durable manifest registration and ends when the connection closes. Each contender uses pg_try_advisory_lock once and returns busy immediately on contention. After acquiring the lock, read batch status again. A published batch returns the existing result. Otherwise mark any running attempt interrupted and start a new attempt in one transaction. Only the lock holder changes attempt or publication state.

Connect timeout is 5 seconds, statement timeout 30 seconds and lock timeout 5 seconds. Parse outside the publication transaction. Hold the same session lock across transactions. Publish raw and normalized rows and mark batch and attempt successful in one transaction. A write failure rolls that transaction back before recording a failure in a separate transaction. A lost or uncertain connection leaves outcomes to the next submission. A fresh connection must acquire the batch lock and reread durable status before retrying. A committed publication wins even if its client never received confirmation. PostgreSQL releases the advisory lock when the server session ends. During a network partition retries can return busy until the server detects session loss.

The application does not retry permanent input errors automatically. Resubmission verifies the manifest and uses the same batch key. Corrected input is a new snapshot.
