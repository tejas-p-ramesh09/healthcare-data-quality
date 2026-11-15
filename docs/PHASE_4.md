# Phase 4 — Local REST API and durable workflow

The FastAPI service connects the existing ingestion, quality, and matching
services. It accepts catalog keys for checked-in synthetic fixtures. The demo
requires neither Java nor Synthea. Phase 2 rules are project policies, and
Phase 3 scores are ranking signals. The API serves review evidence and never
updates or adjudicates patient identities.

## Start the demo

Install Docker with Compose and Python 3.13 for the standard-library demo
client. Start from the repository root. Create a new local environment file
without overwriting an existing one:

```sh
(
  umask 077
  set -C
  for name in POSTGRES_PASSWORD INGEST_PASSWORD WORKFLOW_EXEC_PASSWORD WORKFLOW_REPORT_PASSWORD WORKFLOW_TOKEN; do
    printf '%s=%s\n' "$name" "$(openssl rand -hex 24)"
  done > .env
)
docker compose --profile workflow up -d --build --wait
set -a
. ./.env
set +a
python3 scripts/demo_workflow.py --port "${WORKFLOW_PORT:-8000}"
```

Use a separate Compose project and free ports for a fresh demo if an existing
installation uses the defaults. Set `POSTGRES_LOCAL_PORT` and `WORKFLOW_PORT`
in the environment file. Preserve an existing PostgreSQL volume's admin password
when upgrading it. The one-shot migration container applies the checksummed SQL
and sets the three local role passwords. The API container is non-root and
read-only, receives restricted runtime DSNs only, and publishes on loopback.

For host execution, apply the same migrations and role setup with
`MIGRATION_DATABASE_URL` and the role passwords, using
`PYTHONPATH=src uv run --locked python scripts/bootstrap_workflow.py`. Set
`WORKFLOW_EXEC_DATABASE_URL` and `WORKFLOW_REPORT_DATABASE_URL` to the local
database with their respective role logins. Run
`PYTHONPATH=src uv run --locked python -m healthcare_dq.api.server`.
The host entry point listens on 127.0.0.1:8000. Only the Compose container sets
`WORKFLOW_CONTAINER=1`. Liveness is process-only. Readiness checks both roles,
migration 011, and the catalog. Runtime connections also validate their PostgreSQL
identities and privileges before serving requests. The reporting login must have
the approved views and no other application data access. Both logins reject
unexpected role memberships, database ownership, and elevated role attributes.
An invalid connection returns a sanitized service-unavailable result. API startup
does not apply migrations.

## HTTP use

Open `http://127.0.0.1:8000/docs` for the OpenAPI interface and provide the
local bearer token. Example registration:

```sh
curl -sS http://127.0.0.1:8000/v1/workflows \
  -H "Authorization: Bearer $WORKFLOW_TOKEN" \
  -H 'Idempotency-Key: local-demo-v1' -H 'Content-Type: application/json' \
  -d '{"left_fixture":"demo-source-a","right_fixture":"demo-source-b","profile":"demo-v1"}'
```

Use the returned `Location` path in a GET for durable status or POST to its
`/advance` suffix. Advance processes one stage synchronously. In order, the
stages ingest the left and right sources, validate each, then match. Left and
right must use distinct source namespaces. The existing CLI can still
compare distinct batches of the same source.

The workflow's `quality-findings?side=left`, `recommendations?method=exact`,
and `batches/left/files` paths expose results with provenance. List routes
accept a limit from 1–100 (default 25) and a cursor bound to that list and
filter. Completed-empty lists return 200 and an empty array. Unknown workflows
return 404. A known unfinished, interrupted, or failed parent stage returns a
distinct 409 error. An earlier completed stage remains reportable when matching
fails. Errors include a request ID and stable code. Structured server events
carry safe route/stage identifiers, timing, and outcome. Payloads, credentials,
absolute paths, and patient demographics are excluded.

The catalog verifies the file manifests and parses the same bytes whose
ruleset and model checksums were verified. Request bodies are capped at 16 KiB.
There is no upload, arbitrary path, URL, model, or DSN input. The existing
ingestion and matching input limits still apply. The API is single-user and
local only. Public access would require separately designed authentication,
authorization, TLS, secret rotation, and operations controls.

## Durable advancement and recovery

One dedicated PostgreSQL session holds the global execution slot (404, 0)
then the workflow lock (404, workflow ID). The underlying stage uses its own
session and existing lock namespace. The global slot bounds stage execution
while the owner connection is live. If that connection alone fails, the
underlying stage lock and execution key still prevent duplicate publication
while the stage connection is finishing. No in-memory work queue or automatic
dispatcher is used.

Submission atomically creates the workflow, five steps, and key binding. A
replayed request with the same key returns the same workflow, and different
keys for the same resolved execution converge. An executor commits expected
stage identity and attempt before invoking the stage. A completed result and
workflow link commit separately. If the process stops between them, the next
advance can attach the result by key without invoking the stage again. Migration
011 also captures the exact underlying attempt in its claim transaction. A recovery
request checks its committed failure before looking for reusable success. It records
that failure and returns the new status. A subsequent advance can retry only if
the failure allows it. Three committed retryable failures exhaust the budget.
Without evidence of a terminal outcome, recovery records interruption. Attempts
created before migration 011 lack this correlation, so a pre-upgrade interrupted
attempt cannot be retroactively attributed to a specific underlying failure.
Interruptions and capacity contention are accounted for separately. An unavailable pinned revision blocks execution
without substituting new behavior. A running status can remain visible until
the next executor reconciles it. Clients should poll after a lost response
before advancing again.

Migrations 007–009 preserve earlier migration bytes. Migration 007 checks
historical output and attempt consistency then adds registration, transition
and publication guards. Migration 008 records workflow identities, attempts,
and validated links. Migration 009 grants a dedicated executor login the
guarded stage and workflow operations and gives the reporting login vetted
views only. Report credentials cannot read unrestricted raw JSONB or mutate
stage results. Migration credentials stay in the one-shot process.

Migration 010 recomputes the canonical manifest from registered file labels,
sizes, and SHA-256 values before execution and publication, seals file rows,
and checks persisted resource identities, dispositions, indexes, and counts.
The database checks consistency with registered metadata. The ingestion service
hashes the actual bytes and validates the Bundle, references, and projections.
PostgreSQL does not receive the original file bytes and cannot attest to their
contents independently. The quality guard requires a published input, the
versioned five-rule contract and digest, outcome totals covering the applicable
resource populations, and matching persisted finding counts. Rule predicates
continue to run in the quality engine. A completed run with zero findings remains
valid.

Migration 011 requires the journal and stage states to agree at commit. The
executor closes an attempt and links its result in one transaction. Terminal
attempts are immutable. Attempt changes lock the stage row with `NOWAIT`, so
competing changes fail promptly instead of waiting on a reverse lock order.
The runner holds its own workflow-scoped advisory lock (406, workflow ID) until
its connection closes. An owner recovering the same workflow must acquire that
lock for the recovery transaction before inspecting or closing the prior attempt.

## Corrective verification

The complete PostgreSQL suite passed 65 local tests, including the retained
Synthea baseline. It exercises fresh migrations, upgrades from versions 006 and
009, direct SQL under the executor role, and elevated reporting credentials.
The live-socket recovery test stops the API after a durable permanent failure
and after each of three retryable failures. Restart preserves the exact error
codes and attempt links, reaches the expected retry limit, and leaves no
published or partially written batch for those failures. The checks below
record the original Phase 4 demonstration.

## Observed verification

The complete local Docker build and startup against a new named database
volume took 10.15 s on macOS ARM64 with the dependency build cache warm.
The fresh HTTP run took 0.230 s including report checks. Its five advance
requests took 21.8, 19.2, 18.5, 16.5, and 20.0 ms. Submission replay took
7.9 ms. After restarting the API, replay returned the same workflow and all
five stage links, with zero advance calls. That replay request took 9.2 ms.
These are single local demo observations, not a production SLA.

The two fixture manifests matched their frozen expectations. The left quality
list was empty. The right contained one missing birth-date finding. Both
methods suggested the one candidate pair with match evidence. One right-side
Patient had no candidate. The live-socket test killed the Uvicorn process
before stage commit and after stage commit but before linking. The workflow
recovered both times and published one left batch. A separate live client
disconnect allowed the already running stage to finish in the tested server.
Recovery relies on durable state regardless of that behavior.

Migration 007 found no incompatible rows in 47 published ingestion batches
and 13 completed quality runs on the retained local database. Automated
upgrade tests create representative Phase 3 data under migrations 001–006
before applying 007–009. The hosted workflow also provisions a fresh Compose
database and exercises the API over HTTP with separate database logins.
