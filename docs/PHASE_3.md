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
