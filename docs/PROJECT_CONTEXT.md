# Project context

Healthcare records exchanged across systems can be incomplete, inconsistent, duplicated, or conflicting. Integration engineers need to trace a finding to the source record and rule. Data quality analysts need to review findings and possible duplicates with enough evidence to reason about them.

The goal is an end-to-end portfolio project that demonstrates backend and data engineering, applied ML evaluation, automated testing, reliability, and deployment. The owner is building it individually and should be able to explain every significant design and implementation choice.

## Proposed architecture

1. Generate a controlled synthetic dataset with Synthea and export HL7 FHIR R4 records.
2. Ingest synthetic records through a Python service. Evaluate FastAPI for the API and PostgreSQL for source JSONB and normalized relational tables.
3. Validate the selected FHIR structure separately from project-specific quality rules. Keep provenance from normalized records and findings back to ingested sources.
4. Inject known errors into dataset copies and record the mutation parameters and expected findings for evaluation.
5. Generate candidate patient pairs with blocking, rank them with similarity scores, and present evidence for human review. Evaluate a simple baseline before considering a learned model.
6. Expose findings and run metadata in reports. Evaluate React and TypeScript for the review interface, Docker for packaging, and GitHub Actions for CI and approved deployment workflows.

The initial scope is batch ingestion, a selected subset of FHIR resources, data quality findings, duplicate candidates, and audit reports. Real patient data, clinical use, and automatic record merging are out of scope. See the [decision log](DECISIONS.md) for tradeoffs and open choices.

## Roadmap

1. Demonstrate Synthea FHIR R4 generation and detection of one controlled defect.
2. Implement repeatable ingestion, source retention, normalization, and traceability.
3. Expand quality rules and evaluate them against labeled synthetic mutations.
4. Implement and evaluate a patient matching baseline, including candidate recall and precision and recall at selected thresholds.
5. Build report and review workflows that expose the evidence behind each finding.
6. Add production-style packaging, CI, logging, monitoring, measured performance tests, and an approved cloud deployment.

Each milestone begins with a plan and acceptance criteria approved by the project owner. See [current progress](PROGRESS.md).
