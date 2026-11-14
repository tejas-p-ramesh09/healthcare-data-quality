# Healthcare Data Quality & Interoperability Platform

An individual software engineering project that explores how integration engineers and data quality analysts can find and investigate problems in exchanged healthcare records. All patient data used by the project will be synthetic.

The proposed workflow ingests Synthea-generated HL7 FHIR R4 records, preserves the source data, validates and normalizes selected resources, flags quality issues and possible duplicate patients, and produces auditable reports.

The project is at the documentation stage. The first technical milestone will test whether a small synthetic FHIR R4 dataset can be generated reproducibly and whether one controlled defect can be detected by a documented rule.

- [Project context and roadmap](docs/PROJECT_CONTEXT.md)
- [Progress and next actions](docs/PROGRESS.md)
- [Architectural decisions and open questions](docs/DECISIONS.md)
