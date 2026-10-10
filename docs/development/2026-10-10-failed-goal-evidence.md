# Retaining failed GOAL planning evidence

Main005 returned HTTP 422 from `POST /v1/tasks` because planning exceeded ten
model decisions. No task was returned, so the former acceptance runner preserved
the error response but had no task ledger from which to retrieve the model rounds.
The original failed attempt remains unchanged; the new mechanism cannot recreate
its missing trace after the fact.

The host now advertises a `planningAttemptId` in a failed task-creation response
only after committing the independent planning attempt. Its authenticated read-only
endpoint is `GET /v1/planning-attempts/{id}`. If the host could not commit the audit,
`auditUnavailable=true` is explicit and no ID is advertised. An attempt is evidence
about pre-approval planning, not a task, executable plan, approval, or physical
command. Persisting it does not grant execution authority.

## Runner capture

`scripts/evaluate_long_horizon.py` follows this record only after the exact
`POST /v1/tasks` returns 422 before a task path exists. It reads the corresponding
just-saved local response file from `api.requests[-1]` and checks its manifest
SHA-256. It does **not** inspect `api.last_raw`: an HTTP error leaves that value
pointing to the preceding successful response, which could be the service catalog.
A wrong method, route, status, file path or file hash cannot select an attempt.

The attempt ID is URL-encoded as one path component and the fetched record must
match both that ID and the original natural-language request. The GET's original
response and a byte-identical `planning-attempt.json` are saved with exclusive
creation and included in the evidence manifest. No prior artifact is overwritten.

The original HTTP 422 remains the run's error and `passed` remains false. The
runner does not automatically resubmit the goal, approve a plan, create a fallback
task, or cancel a task that does not exist. Missing IDs on older hosts and explicit
persistence failures are reported as audit unavailable. A failed GET or invalid
trace is recorded separately as `planningAttemptCaptureError`; it never replaces
the original planning error. Raw returned evidence is retained even when its
integrity checks fail.

## Minimal offline checks

The helper `validate_planning_attempt` can validate the retained JSON without
contacting any service. It requires:

- `planning.attempt.v1`, matching attempt ID and original request; no root task,
  executable plan, calls or approval fields.
- Pre-task scope with an empty task ID, integer revision zero and matching robot
  identity. An unknown robot may remain explicitly empty rather than invented.
- Exact UTF-8 SHA-256 for `originalSourceTrace`, which must decode to a trace
  array; `traceAvailable` must agree with whether that array is empty.
- For retained contexts, the planning stage and same scope, exact context text
  hash, and matching scope inside the context document.
- Exact `data_json` strings/hashes and scope for retained archives, and exact
  `model_request_json` strings/hashes for model inputs. The final user message
  must contain the recorded context text. A reserialized display object is not a
  substitute for the original transport bytes.

The report includes `planningAttemptId`, `planningDecisionRounds`, and
`planningModelRounds`. The latter counts distinct rounds with verified retained
model input envelopes, not merely configured model routes or trace entries.
`planningAttemptTraceVerified` means these integrity checks passed; it does not
mean planning or the long task succeeded. An empty durable trace reports zero
rounds and `planningAttemptTraceAvailable=false`.

This helper is intentionally separate from the full successful-task offline
validator. A pre-task attempt has no child commands, task revision or physical
observations to pass that validator. The host's durability and authorization
checks have their own Go tests; runner tests use a deterministic HTTP stub and
are not a new Gazebo execution.

## Validation

`tests/install/test_long_horizon_planning_failure.py` covers the stale-last-raw
trap, URL encoding, immutable exact bytes and hashes, one original POST with no
retry/approval, unavailable/legacy audits, lookup failure, mismatched ID/request/
scope, injected executable fields, corrupted trace/context/model/archive strings,
wrong model input, an empty trace, and wrong local response provenance.
The new tests plus the existing runner and offline revalidation suites passed
**142 tests**. Ruff and the scoped diff check passed.
