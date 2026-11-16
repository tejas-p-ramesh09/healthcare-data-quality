# Phase 5 Healthcare Workflow Explorer

The Explorer follows one synthetic workflow from registration through two
ingestions, two quality runs, and matching. It uses the existing FastAPI
contract and PostgreSQL-backed reports. The browser never connects to the
database. See the [Phase 4 runbook](PHASE_4.md) for backend setup, durable
recovery, input fixtures, and credential roles.

## Run locally

Use Docker Compose and the Node release pinned in
[.node-version](../frontend/.node-version). Start the Phase 4 Compose stack.
Use a new local Compose project and unused loopback ports to demonstrate a
new run when your usual database already contains the completed demo.

```sh
docker compose --profile workflow up -d --build --wait
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173/workflows` and enter the local workflow token.
Choose `demo-source-a`, `demo-source-b`, and `demo-v1`. Register the workflow,
advance each stage, then inspect Quality findings, Matching, and Provenance.
For a build-based demo, use `npm run build` then `npm run preview`. Both Vite
modes bind to 127.0.0.1 and proxy approved API paths to 127.0.0.1. Set
`WORKFLOW_API_PORT` to match the API's published port when using a different
`WORKFLOW_PORT`. Set `WORKFLOW_UI_PORT` for a different local UI port. The
browser's origin must match the literal loopback address and configured port.

## Browser and API contracts

The token stays in tab memory. Disconnect and API authorization failures
clear it, abort browser requests, and discard the session cache. A reload
needs a new connection. The backend may continue a stage after a browser
disconnect. Use Check status before trying another advance. The proxy
does not inject credentials; FastAPI still enforces bearer authentication.

The optional `X-Expected-Stage` header has the exact form
`stage_name:status:attempt_count`, for example `ingest_left:pending:0`.
Names are `ingest_left`, `ingest_right`, `validate_left`, `validate_right`,
and `match`. States are `pending`, `running`, `interrupted`, and `failed`.
The attempt count is a canonical unsigned decimal signed-bigint-range value
with no leading zeros except zero itself. Malformed or repeated headers
return `400 INVALID_EXPECTED_STAGE`. Under the existing locks the server compares
the complete tuple to the first incomplete stage. A mismatch returns
`409 STAGE_STATE_CHANGED` before any stage work or attempt write. A busy executor
may respond before the comparison. Requests without the header retain the
Phase 4 contract. The browser retries reads and leaves all mutations explicit.

Regenerate the API types from the repository root with the locked Python
environment and no database or real token.

```sh
mkdir -p work/phase5
PYTHONPATH=src uv run --locked python scripts/export_openapi.py > work/phase5/openapi.json
npm --prefix frontend run contract
```

## Reproduce checks

Run `npm --prefix frontend run lint`, `npm --prefix frontend test` and
`npm --prefix frontend run build`. For browser tests, use the same disposable
stack and export its Compose project as `DEMO_COMPOSE_PROJECT` and its token as
`WORKFLOW_TOKEN` in the test process. The test restarts that project's API.
Install Chromium with `npm --prefix frontend exec -- playwright install chromium`
and run `npm --prefix frontend run e2e`. Keep screenshots limited to connected
synthetic report screens. Set `WORKFLOW_SCREENSHOTS` to save them. Test traces,
automatic screenshots, and video are disabled. Java and retained Synthea
exports are unnecessary for the UI demo.

The browser asserts the checked-in [fixture outcomes](../fixtures/demo/expected.json).
Local verification on the approved stack ran 67 Python tests including the
retained Synthea baseline and nine frontend component/integration tests.
Type checking, the production build, lint and npm audit passed. The local
macOS execution sandbox blocked headless Chromium launch. The proxy-only
Playwright test passed locally. On the [hosted Linux browser run](https://github.com/tejas-p-ramesh09/healthcare-data-quality/actions/runs/43823594442),
both Playwright tests passed. The browser run produced these observed results:

- Five completed stages with one durable attempt per stage. A deliberately lost
  successful response recovered through status reads. A second tab's stale
  advance was rejected, and the next stage still had zero attempts.
- Zero left findings and one right missing-birth-date finding. One candidate
  was suggested by both methods. Zero left Patients and one right Patient had
  no candidate. The displayed file and entry linked to the correct batch and hash.
- API restart and browser reconnect returned the same stage records and counts.
  Authorization and cross-origin proxy rejections passed.
- Eight sampled scans using axe found zero violations across connection,
  overview, creation, execution, desktop quality, provenance, matching, and
  the 375 px quality view. Keyboard activation and route focus passed in the
  exercised views.

The browser CI job publishes sanitized screenshots of execution, matching
evidence, and the narrow quality report.

## Interpretation and limits

The quality explorer presents project-specific rules. The matching explorer
shows ranked suggestions with comparison levels and contributions. It shows
aggregate no-candidate counts and their blocking context. Source coordinates
link to verified batch and file metadata. The interface exposes neither the
raw Patient record nor demographic values. It supports one local operator,
the checked-in fixtures and profiles, and deliberate single-stage actions.
Automated accessibility checks supplement keyboard, focus and narrow-layout
checks. This demo establishes no population-level accuracy or performance
claim.
