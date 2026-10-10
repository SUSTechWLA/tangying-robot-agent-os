# Durable rejected planning attempts

A GOAL rejection happens before a task exists. Its model inputs therefore cannot
be recovered through a task ledger. The Local Agent now wraps the configured
GOAL planner at both initial startup and model-setting replacement. If planning
fails, the wrapper retains the returned diagnostic trace in the existing durable
event store before returning the original error classification.

Each rejection has an independent random `planning-attempt-<128-bit hex>` ID.
One atomic commit writes its version-1 aggregate state and a
`PLANNING_ATTEMPT_REJECTED` domain event under aggregate type `planning_attempt`.
There is no task, task revision, executable plan, approval, outbox dispatch, or
execution checkpoint. An accepted plan passes through without an attempt record.
A nil development planner retains the existing parser fallback.

The trace is stored once as the exact `originalSourceTrace` JSON string, with a
SHA-256 of its UTF-8 bytes. Nested context `model_request_json` and artifact
`data_json` strings retain their own exact transport/source bytes and hashes.
The ordinary structured display objects may have passed through generic JSON
decoders; they are not substitutes for these authoritative strings. Credentials
sent in HTTP authorization headers are not part of the retained model request.

The DTO has schema `planning.attempt.v1`, `id`, `createdAt`, `request`, `error`,
`source`, `scope`, `robotId`, `catalogRevision`, `originalSourceTrace`,
`sourceTraceSHA256`, and `traceAvailable`. Scope reflects the actual pre-task
state: an empty task ID, revision zero, and the robot identity known to the
planner. Unknown robot/catalog values stay empty. A rejected planner with no
retained trace records `originalSourceTrace="[]"` and `traceAvailable=false`;
it does not claim to contain model rounds. Conflicting non-pre-task trace scope
is refused rather than relabeled.

Only after the atomic commit succeeds does the existing failure response include
`planningAttemptId`. A persistence failure instead includes
`auditUnavailable=true` and no ID. The original error remains reachable through
`errors.Is`/`errors.As`, and its HTTP classification remains unchanged: unsupported
or clarification-required requests return 422. The legacy mapping request route
uses the same response helper. Failed planning does not trigger a retry or task
creation.

The record commit has a five-second detached deadline so cancellation of the
model request can itself be audited. Only journal work is detached, never the
planner or a robot operation. Commits from the shared archive instance are
serialized through a cancellable gate within that deadline, avoiding concurrent
SQLite deferred-transaction upgrades by this recorder. Other database/storage
failures remain explicit audit-unavailable results.

`GET /v1/planning-attempts/{id}` requires the console session and a same-origin
request, and returns `Cache-Control: private, no-store`. There is no list or write
API. Malformed/unknown IDs return 404; unavailable storage returns 503; a damaged
record, source checksum, trace shape or scope returns 500 rather than verified
evidence. Records remain available after SQLite close/reopen and Local Agent
restart, using the new process's console session.

The real-model regression sends an HTTP planning request to a deterministic
model fixture, rejects it, closes/reopens real SQLite, and retrieves the attempt
through the console handler. It compares the exact HTTP model body and nested
SHA values, checks credentials are absent, and verifies zero task creation and
zero provider calls. Additional regressions cover generic JSON numeric rounding
of `9007199254740993`, canceled requests, unavailable storage, bounded gate waits,
an uncertain commit response after the row was written, eight concurrent
independent records, invalid scope, cross-site reads, missing
IDs and corrupted evidence. These tests exercise persistence and HTTP contracts;
they are not a successful physical Gazebo task.

Historical attempts that failed before this feature existed, including main005,
still lack their original model trace. The feature cannot reconstruct that
evidence. Runner collection and offline validation are documented in
[failed GOAL evidence](2026-10-10-failed-goal-evidence.md).
