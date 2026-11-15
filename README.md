# Healthcare Data Quality & Interoperability Platform

An individual software engineering project that explores how integration engineers and data quality analysts can find and investigate problems in exchanged healthcare records. All patient data used by the project will be synthetic.

The platform ingests synthetic FHIR R4 Bundles into PostgreSQL, runs versioned quality policies, and recommends possible duplicate patients with auditable evidence.

Start with the [local API demo](docs/PHASE_4.md). It uses checked-in synthetic fixtures and a fresh database without a Java or Synthea dependency.

- [Project context and roadmap](docs/PROJECT_CONTEXT.md)
- [Progress and next actions](docs/PROGRESS.md)
- [Architectural decisions and open questions](docs/DECISIONS.md)
- [Phase 0 runbook and results](docs/PHASE_0.md)
- [Phase 1 ingestion runbook and evidence](docs/PHASE_1.md)
- [Phase 2 quality rules and evaluation](docs/PHASE_2.md)
- [Phase 3 Patient matching and evaluation](docs/PHASE_3.md)
