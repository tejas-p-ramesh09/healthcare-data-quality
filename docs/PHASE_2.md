# Phase 2 quality validation

Phase 1 ingestion integrity determines whether a snapshot can be published. Phase 2
evaluates published snapshots with nonblocking project policies. These checks cover
a selected structural and temporal subset. Full FHIR profile, terminology, and
clinical validation are outside this scope.

## Frozen rule contract

Each rule below is v1, severity warning. A finding is a review signal.
Absent or null means missing. For string fields, whitespace-only is also missing.
Rule output distinguishes pass, finding, not applicable, and unable to assess.

| Rule | Predicate and applicability |
| --- | --- |
| PATIENT_BIRTH_DATE_MISSING | On Patient, flag a missing birthDate. Any nonblank string passes this completeness rule, including partial dates. |
| OBSERVATION_CODE_NO_USABLE_CODING | On Observation, flag a code object without a coding array containing an object with nonblank string system and code. Text alone is insufficient for this analytics policy. |
| OBSERVATION_AFTER_ENCOUNTER_END | On Observation with resolved Encounter, compare effectiveDateTime with period.end plus 24 hours by default. Strictly greater triggers review. Missing link is not applicable. Missing or uncomparable timestamps are unable to assess. This may be valid post-visit activity. |
| CONDITION_ABATEMENT_BEFORE_ONSET | On Condition, flag a comparable abatementDateTime strictly earlier than onsetDateTime. Missing, partial, or alternate representations are unable to assess. |
| MEDICATION_ORDER_AUTHORED_ON_MISSING | On MedicationRequest with intent order, flag a missing authoredOn. Other intents are not applicable. This is a project timeline policy. |

Temporal comparisons accept full date-times with seconds and explicit UTC offsets,
including Z and up to six fractional digits. Compare instants, not strings.
Partial timestamps, leap seconds, and unsupported precision are unable to assess.
Resources outside a rule's type are excluded from its denominator. Unexpected rule
exceptions fail the run. The ruleset configuration and engine revision distinguish
runs when behavior or tolerance changes.

## Locking and recovery contract

One dedicated, non-pooled PostgreSQL session claims a registered run using the
single-bigint advisory key `-run_id - 1`, distinct from positive ingestion IDs
and migration key -1. Run IDs are positive and below bigint maximum. Attempt the
lock once and return busy on contention. Close the connection on every exit.
Recheck persisted run status after acquisition. Only the owner changes attempts.
A successful run replays its result. Otherwise reconcile a prior running attempt
as interrupted and register a new attempt in one transaction.

Use Phase 1 connection, statement and lock timeouts (5 s, 30 s, 5 s). Read the
published snapshot and evaluate outside the findings-write transaction. Commit all
findings, outcome counts and final status together. On known failure record a
sanitized error after rollback. On lost connection or uncertain commit, a new
session acquires the lock and checks durable status before retrying. A server
session can retain a lock until it detects disconnection. Batch data stays immutable.

## Reproduction

Use the Phase 1 setup and apply migration 003 with the existing migration command.
The quality CLI uses the same `DATABASE_URL` and restricted ingestion role.
Replace the placeholders below with the published batch IDs and returned run IDs.

```sh
export PYTHONPATH=src
uv run --locked python -m healthcare_dq.quality.cli validate BASELINE_BATCH_ID
uv run --locked python -m healthcare_dq.quality.cli findings BASELINE_RUN_ID
uv run --locked python -m healthcare_dq.quality.cli mutate data/generated/phase1/synthea-25/fhir data/generated/phase2/evaluation-v1
uv run --locked python -m healthcare_dq.cli ingest data/generated/phase2/evaluation-v1/fhir --source phase2-variant
uv run --locked python -m healthcare_dq.quality.cli validate VARIANT_BATCH_ID
uv run --locked python -m healthcare_dq.quality.cli evaluate \
  data/generated/phase2/evaluation-v1/manifest.json BASELINE_RUN_ID VARIANT_RUN_ID \
  --manifest-sha256 e6e4d3eb455f002338c4f7885907b30629d418b5051d8cd63b5c07559a482d9e
```

Record the manifest file checksum at freeze time. Evaluation rejects a mismatched
manifest hash, different ruleset or engine identity, an unfinished validation,
or runs whose ingestion manifests do not match the experiment. Semantic findings
are identified by source coordinates, rule version and JSON Pointer. Validation
records a behavior digest of the executable rule, engine, repository and service
modules. For the observed run that digest was
`fb862ac80c86099ce4b5bae1b53b152ae56d07a28c98f8c7bec747629df62337`.

## Untouched baselines

Before mutation, the Phase 1 five- and 25-patient manifests matched their recorded
hashes. They had 7 and 19 findings, respectively, all encounter timing review
signals. No baseline result was adjudicated as a clinical defect. The 19 timing
signals on the evaluation input are outside the labeled cohort and remain
indeterminate for clinical purposes. Some activity can occur after an encounter.
The phase does not estimate false positives in unlabeled data.

| Rule | 5-patient pass / finding / unable | 25-patient pass / finding / unable |
| --- | ---: | ---: |
| Birth date | 5 / 0 / 0 | 25 / 0 / 0 |
| Observation coding | 1,196 / 0 / 0 | 5,431 / 0 / 0 |
| Encounter timing | 1,189 / 7 / 0 | 5,412 / 19 / 0 |
| Condition timing | 124 / 0 / 30 | 485 / 0 / 187 |
| Medication authored time | 126 / 0 / 0 | 490 / 0 / 0 |

All in-type not-applicable counts were zero in these inputs. Condition
comparisons without two comparable timestamps are explicitly unassessable.

## Frozen synthetic evaluation

The 25-patient manifest was
`c61d6bf82e79c7cb2e3fd3d8a17988edd29a82b97653225365e7b2c49d11bc4d`.
Independent preconditions gave 25, 5,431, 5,412, 485 and 490 eligible cases
in the table order above. Using seed 202703, reserve five disjoint positive
targets and five unchanged controls per rule independently of detector output.
Delete birthDate, empty the coding array, set effective time to encounter end
plus grace plus one minute, set abatement one minute before onset, or delete
authoredOn, respectively. The two generated manifests and variant hashes were
identical, and the original source checksum was unchanged. The variant manifest
was `83ab4739d83f70c7cc2594af698b80d28105d2ae907ba84c5862ac607cf175f6`.
Variant ingestion produced 14,685 raw and 7,580 normalized records.

The original [evaluation summary](evidence/phase2-evaluation-v1.json) preserves
the first scored outcome. Each rule and its corresponding category recovered
five of five injected defects. Every category had TP=5, TN=5, FP=0 and FN=0.
On the 50 labeled rule-target cases, micro precision, recall and F1 were all
1.0 (25 TP, 25 TN, no FP or FN). The variant had 44 findings, of which 19
were unchanged baseline findings and 25 matched injected defects. There were
no newly unscored findings. Zero labeled false positives means none of these
25 selected controls triggered their assigned rule. It does not establish
clinical accuracy, the error rate over the full batch, or FHIR conformance.
The cohort is synthetic, single-reviewer, and constructed from explicit
preconditions. Its operators are direct violations, so results are a
regression baseline. Boundary cases and alternate representations are tested
separately in hand-authored fixtures.

## Validation benchmark

Five fresh validation trials per size on previously published batches used
macOS 26.6.2 ARM64, CPython 3.13.13 and Docker PostgreSQL 17.9. Docker reported
10 CPUs and 8,321,515,520 bytes of memory. Each trial used a separate published
batch identity and fresh Python process with a shared database and filesystem
cache. Input discovery, ingestion and Synthea generation were outside timing.
From a checkout, import each benchmark snapshot under a fresh source key and run:

```sh
PYTHONPATH=src uv run --locked python scripts/benchmark_quality.py BATCH_ID
```

| Dataset | Rows scanned | Findings | Median seconds (range) | Median rows/s | Peak RSS median | Replay median |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 5 patients | 1,678 | 7 | 0.165 (0.161–0.166) | 10,172 | 52.2 MiB | 6.1 ms |
| 25 patients | 7,580 | 19 | 2.640 (2.616–2.781) | 2,871 | 92.1 MiB | 6.4 ms |

Median read/context, rules, and publication times were 0.145 s, 0.002 s and
0.006 s at size 5, and 2.586 s, 0.011 s and 0.018 s at size 25. Reading the
batch dominated these measurements. Analyze the query plan and consider indexing
or reducing raw payload reads before making throughput claims at larger sizes.
The benchmark measured process peak RSS, not database memory. The existing
Phase 1 input size limits still apply. No new capacity or latency SLA is claimed.

### Evaluation report compatibility

Variant classification compares all frozen labels with variant findings. A
positive still present in the variant is a true positive even when the same
finding already existed in the baseline. Injected-defect recovery credits only
positive keys present in the variant and absent from the baseline.

In new reports, `recovery_baseline_overlapping_positives` lists all positive
keys already in the baseline. Each `injected_recovery` entry retains
`injected` as the number of labeled positives, adds `baseline_overlapping`
and `eligible_for_recovery`, and counts only new detections in `detected`.
Use `eligible_for_recovery` as the denominator for a recovery rate. A zero
denominator means recovery is undefined. The historical v1 summary retains
its original schema and values. Rescoring its original manifest reproduces
those counts because none of its positive keys overlapped baseline findings.

Regenerating the v1 variant with the corrected generator preserves the labels
and variant bytes for the retained inputs. New manifests record the changed
operator source checksum and therefore have a different manifest file SHA-256.
Use the new manifest's checksum when scoring a regenerated variant, or use the
original frozen manifest and checksum above to reproduce the historical result.
