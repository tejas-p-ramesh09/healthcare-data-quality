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
