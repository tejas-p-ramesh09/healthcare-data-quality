# Architectural decisions

## Confirmed project requirements

- Use only synthetic patient data. Preserve reproducibility with controlled datasets, fixed seeds, recorded tool versions, and documented experiment configurations.
- Obtain owner approval for each milestone plan and its acceptance criteria before implementation. Obtain explicit approval before pushing, merging, deploying, or making destructive changes.
- Verify important behavior with automated tests, including failures and edge cases. Base accuracy and performance claims on recorded measurements.
- Keep architecture proportionate to an individual project. Introduce microservices, Kubernetes, Kafka, or Airflow only when a concrete requirement justifies them.

## Adopted milestone workflow

An approved plan can authorize the GitHub actions it explicitly lists for that milestone. This includes issue reuse or creation, feature branch creation, incremental commits, feature branch pushes, PR creation or updates, and CI verification. Verification of the repository root, GitHub account, remote, and branch precedes remote operations. PR validation uses read-only GitHub Actions permissions unless a milestone explicitly requires and approves additional access.

Merging, deployment, branch deletion, history rewriting, destructive actions, and repository protection changes require separate explicit approval. See the [project instructions](../AGENTS.md) for the full workflow.

## Phase 1 decisions

Use a Python batch CLI, PostgreSQL JSONB source retention, and batch-scoped relational projections. Immutable snapshots trade storage for explicit provenance and safe replay. A PostgreSQL session advisory lock and one publication transaction protect each batch. The [Phase 1 runbook](PHASE_1.md) defines the supported reference subset, recovery, and measured limits.

## Phase 2 decisions

Quality policies run after ingestion on immutable batches. Use versioned Python rules, atomic PostgreSQL findings, and independent mutation labels and controls. Keep untouched baseline findings separate from scored outcomes. These checks cover the documented project rules. See the [Phase 2 runbook](PHASE_2.md).

## Phase 3 decisions

Match distinct published synthetic Patient batches with versioned demographic evidence, deterministic multi-pass blocking and exact and fitted statistical baselines. Keep recommendations separate from source data. The [Phase 3 runbook](PHASE_3.md) records fit and selection isolation, the locked evaluation, and transactional publication.

## Phase 4 decisions

Use a fixture-only FastAPI interface and an explicit one-stage advance operation. A PostgreSQL workflow records pinned input identities and reconciles completed underlying results after interruption. Migration, execution, and reporting credentials are separate. See the [Phase 4 runbook](PHASE_4.md) for the local trust boundary and operational results.

## Phase 5 decisions

Use a React and TypeScript frontend with a loopback Vite proxy and an in-memory
bearer session. FastAPI owns the API contract and workflow state. Browser
advances compare a stage snapshot under the existing execution locks.
Quality, matching, and provenance views explain published evidence without
fetching patient payloads. See the [Phase 5 runbook](PHASE_5.md).

## Provisional technical decisions

| Area | Starting proposal | Tradeoff or evidence needed |
| --- | --- | --- |
| Synthetic source and exchange format | Evaluate Synthea with HL7 FHIR R4 exports. | Verify the chosen generator version, export configuration, and repeatability in the feasibility milestone. |
| Backend | Start with a modular Python application and consider FastAPI at the API milestone. | A single application simplifies development and deployment. Keep ingestion, validation, and matching boundaries clear so each can be tested independently. |
| Storage | Evaluate PostgreSQL with JSONB source records and normalized relational tables. | Source records support audit and replay. Relational tables support queries and constraints. Storing both adds schema and consistency work. Define byte-level retention separately if needed. |
| Validation | Track FHIR structure and project quality rules separately. | This helps reviewers tell a data exchange conformance issue from a project policy finding. Select the resource and profile coverage after inspecting the synthetic input. |
| Patient matching | Start with blocking and interpretable similarity scores. Select features and thresholds using labeled synthetic cases. | Blocking reduces pair comparisons and can miss true matches. Establish its recall before comparing scoring methods or adding a learned model. |
| Reporting and delivery | Consider React and TypeScript, Docker, GitHub Actions, and a simple cloud deployment. | Select the UI scope, hosting, and monitoring after the core flow works and deployment requirements are clear. |

See the [project context](PROJECT_CONTEXT.md) for the proposed workflow.
