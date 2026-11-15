# Phase 3 Patient matching

## Execution contract

Matching evaluates two distinct published snapshots in order. One dedicated
non-pooled Psycopg session owns an advisory lock with two integer keys
`(303, matching_run.id)`. The separate two-key namespace does not collide with
the existing single-bigint locks. Run IDs use positive PostgreSQL integers.
Connect, statement, and lock timeouts are 5, 30, and 5 seconds. Register
durably, attempt the advisory lock once, and return busy on contention.
The caller closes the connection after each invocation. The lock owner
rechecks durable status and reconciles abandoned running attempts. Publication
of all outputs and success occurs in one transaction. Retry after a lost
session checks durable status before starting a new attempt. Server detection
of disconnection can delay lock release. Matching outputs are recommendations.
Source records and prior quality evidence are retained.

## Reproduction and identity isolation

Use the Phase 1 environment and migrations. Synthea v4.0.0 and its verified
SHA-256 are documented in [Phase 0](PHASE_0.md). Use the same pinned Java and
exporter settings with the populations and seeds in the [experiment configuration](../config/matching/experiment-v1.json).
Add `-o false --exporter.fhir.included_resources=Patient --exporter.hospital.fhir.export=false --exporter.practitioner.fhir.export=false`
and use a new output path. The exporter still emitted Encounters, Organizations,
and Practitioners. The generator keeps the original bytes and writes separate
Patient-only collection Bundles of at most 25 entries. The 320-source export fit
the Phase 1 limits (320 files, 35,428,402 bytes total, largest 1,171,344).
All 320 Patient IDs and normalized name/date fingerprints were distinct. Every
parent had a usable name, birth date, phone, street and ZIP. Fingerprint overlap
with the retained 5- and 25-patient exports was zero.

```sh
export PYTHONPATH=src
python -m healthcare_dq.matching.experiment PATH_TO_SOURCE_FHIR NEW_COHORT_DIRECTORY
mkdir -p work/phase3
python scripts/evaluate_matching.py fit NEW_COHORT_DIRECTORY work/phase3/fit.json
python scripts/evaluate_matching.py test NEW_COHORT_DIRECTORY work/phase3 --fit-sha256 RECORDED_FIT_SHA256
```

Publish the selected left and right directories with the existing ingestion
command under distinct synthetic source keys. Then run
`python -m healthcare_dq.matching.cli run LEFT_BATCH RIGHT_BATCH work/phase3/fit.json`.
Use `status`, `pairs`, and `no-candidates` with the returned run ID. Both
52-record final sides were ingested and the persisted match run produced 47
candidates. Batch-scoped foreign keys enforce endpoint provenance. Runtime
permissions prevent result updates and deletes, and triggers reject inserts and
attempt edits after completion. Migration 006 limits runtime registration to
identity and configuration columns. A database trigger requires the registered
initial state. Completion requires the recorded candidate count to equal the
number of persisted candidates, exactly one completed attempt, no running
attempt, and two recommendations per candidate. The foreign keys and unique
method keys imply a corresponding exact and statistical score for each
candidate. Zero candidates may complete with a completed attempt and zero
outputs. Model validation checks both methods, their policies, and finite
weights and thresholds before registration.

Splits were fixed by seeded parent groups before scoring. Fit used 192 people
(116 matched, 76 unmatched), and selection and test each used 64 people
(40 matched, 24 unmatched). No identity crosses a split. Shared-phone
hard-negative groups stayed together, with 38, 12 and 12 labeled pairs
respectively. The final split had 6–7 matched examples per perturbation family.
Distinct FHIR IDs are used on each side. Identifiers, truth IDs and metadata are
excluded from features. Only the offline evaluator reads truth files. A second
build reproduced the entire frozen cohort byte for byte. Record the fit artifact SHA-256 after selection and before final scoring. The test command requires the separately recorded value and rejects a mismatch. The locked fit artifact
SHA-256 was `164747533708fa307de6d040bb3b14e11f2e332b1ddd665f6ee4033d243e295d`.

## Comparisons and decisions

Normalization uses Unicode NFKC and case folding, whitespace/token cleanup,
recognized alternate names, calendar-checked partial dates, ten-digit US phone
features and basic street/ZIP normalization. A plus sign is accepted only in a leading +1 prefix. Four blocking passes use full DOB
with given or family prefix, birth year plus ZIP and given initial, or phone.
Missing keys emit nothing. Limits are 5,000 patients per side, 10,000 pair
emissions per block and 100,000 unique pairs. Exceeding a limit fails the run.

The exact baseline requires full birth-date and given/family name agreement.
The statistical fit uses all 141 fit candidates (102 true pairs, 39 nonmatches)
and add-one smoothing across exact, close, conflict and missing. Sparse levels
and correlated features such as street and ZIP limit interpretation. The score
is a sum of log likelihood ratios and is not a calibrated probability. The
selection sweep prioritizes precision, then recall, then fewer reviews, with
at least five suggestions and at most 20 review pairs for the selected operating
point. If none qualify, the review-only fallback disables suggestions even on
unseen inputs. On the selection split it admits whole groups of tied scores from
the top down until the next group would exceed 20 reviews. Lower-scoring pairs
remain scored and are marked unlikely. Review volume on the final split is a
measurement, since its distribution may differ from selection. The chosen exact and
statistical policies both had equal low/high thresholds (1 and 5.42067691)
and zero tie margin. Competing candidates at or above the suggestion threshold
can still go to review. Statistical matching was selected on the selection split.

## Locked evaluation

The [original aggregate result](evidence/phase3-evaluation-v1.json) contains
per-family counts, candidate-only measures, unmatched outcomes and the model.
On the final 52 × 52 universe, blocking emitted 120 hits and deduplicated them
to 47 comparisons, reducing pairs by 98.26%. It retained 35/40 true pairs.
Five of six combined-variation positives were blocked out. Both methods saw
the same candidates. All 12 shared-phone hard negatives were scored without
false suggestions. There were 13 no-candidate records on each side and zero
review dispositions at the selected thresholds.

| Method | TP | FP | FN | TN | Precision | Recall | F1 | Candidate recall after blocking |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Exact | 15 | 0 | 25 | 2,664 | 1.000 | 0.375 | 0.545 | 0.429 |
| Statistical | 35 | 0 | 5 | 2,664 | 1.000 | 0.875 | 0.933 | 1.000 |
| Exhaustive exact | 15 | 0 | 25 | 2,664 | 1.000 | 0.375 | 0.545 | 0.375 |

Both false-match denominators give zero on this cohort. Miss rates were
62.5% and 12.5%. Neither method suggested a link for an unmatched identity.
Examples of exact misses include surname changes and partial birth dates.
The statistical scorer recovered all true pairs it received but had no evidence
for blocked-out pairs. Hard negatives represent one controlled category
(shared phone), and parent attributes were unusually complete before mutation.
Scores describe this small synthetic experiment. They do not establish clinical
identity accuracy, calibration, or robustness to independent source errors.

## Benchmarks

Generate Patient-only benchmark sides using the experiment command with
`--benchmark-size 25`, `100`, or `500`. Use the retained 25-patient export,
the 320-patient source, and the independently seeded 500-patient source,
respectively. Import each side under fresh source keys per trial. Run
`PYTHONPATH=src python scripts/benchmark_matching.py LEFT_BATCH RIGHT_BATCH work/phase3/fit.json`.
Five trials per size used macOS ARM64, Python 3.13.13 and Docker PostgreSQL 17.9.
The timer excludes generation, ingestion, fitting and evaluation. It includes
connection, read, normalization, blocking, comparisons, decisions and commit.
Replay was timed separately. RSS is Python process high-water memory.

| Per side | Comparisons | Reduction | Median seconds (range) | Comparisons/s, scoring only | Median RSS | Median replay |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 25 | 21 | 96.64% | 0.033 (0.030–0.037) | 103,704 | 41.2 MiB | 6.4 ms |
| 100 | 84 | 99.16% | 0.041 (0.036–0.055) | 179,391 | 41.7 MiB | 6.1 ms |
| 500 | 423 | 99.83% | 0.105 (0.102–0.106) | 171,684 | 44.3 MiB | 6.5 ms |

The [trial evidence](evidence/phase3-benchmark-v1.json) includes individual
stage timings and emissions. At 500 per side, median read, blocking, score,
decision and publication times were 35.2, 2.4, 2.5, 0.7 and 41.9 ms.
These corpora are sparse and do not establish behavior at the configured
candidate limits. Accuracy is measured on the separate frozen cohort.
