# Healthcare Data Quality & Interoperability Platform

An individual software engineering project that explores how integration engineers and data quality analysts can find and investigate problems in exchanged healthcare records. All patient data used by the project will be synthetic.

The proposed workflow ingests Synthea-generated HL7 FHIR R4 records, preserves the source data, validates and normalizes selected resources, flags quality issues and possible duplicate patients, and produces auditable reports.

Phase 0 locally verified repeatable patient IDs and birth dates across two small Synthea FHIR R4 runs and detected a controlled missing birth date. See the runbook for the commands, evidence, and limits.

- [Project context and roadmap](docs/PROJECT_CONTEXT.md)
- [Progress and next actions](docs/PROGRESS.md)
- [Architectural decisions and open questions](docs/DECISIONS.md)
- [Phase 0 runbook and results](docs/PHASE_0.md)
- [Phase 1 ingestion runbook and evidence](docs/PHASE_1.md)
- [Phase 2 quality rules and evaluation](docs/PHASE_2.md)
- [Phase 3 Patient matching and evaluation](docs/PHASE_3.md)
