# Cloud-to-edge execution context v1

`execution.context.v1` is a bounded execution basis, not shared conversation history. It carries task/revision identity, the claim's task aggregate version and coordinator commit version, command/step/index, robot and scope, the approved intent/plan digest, catalog revision, resource fencing, and explicit world authority with evidence sequence baselines. The digest detects mixed content and corruption. It is not a signature, approval, live observation, or substitute for claim renewal.

The coordinator stores the basis with `INTENT_CLAIMED`, aggregate state and its checkpoint in one existing event-store transaction. Events also carry `contextBasisJSON` as an exact JSON string: generic event projections may round uint64 values, so audit verification uses this string or the typed checkpoint. Existing ready-queue/outbox entries remain wakeups only; the worker obtains a current claim before dispatch. Ready outbox IDs include revision and step to avoid collisions across replans. New claims receive v1 context. A legacy RUNNING claim without context cannot be renewed or executed by the new worker; it needs reconciliation. No context is invented from old chat or a partially known running action.

The worker requires its existing `ExecutionStore` to also implement the atomic `eventlog.Store` contract; the deployed SQLite store already does. A per-task/robot CAS checkpoint records these phases:

| Durable phase | Restart behavior |
| --- | --- |
| No record | Validate current approved task, claim, revision, plan digest and scope; atomically persist ACCEPTED before execution. A failed read or CAS dispatches nothing. |
| ACCEPTED | An attempt may have dispatched work. A duplicate or restart stops for reconciliation; it never automatically reruns the physical graph. |
| EXECUTED | Successful execution and a completion outbox were committed together. Replay only the cloud completion report, with exact claim identity. |
| COMPLETED | Physical execution and cloud completion are closed. Duplicate delivery does no work; a lost outbox acknowledgement is safe to repeat. |

The checkpoint rejects lower coordinator commit versions, lower task revisions, conflicting digests at the same version, and a new handoff while earlier work is unclosed. Workers sharing the same robot journal compete on the database CAS; only one may accept a command. Separate databases are not a distributed lock. Runtime command idempotency and fencing remain necessary, and co-located workers for one robot must share `EDGE_EXECUTION_DB`.

Before each legacy Runtime invocation, including after potentially slow policy inference, the worker checks the current task content and approval, its durable accepted basis, the live coordinator claim, connected robot/adapter/catalog, and physical readiness/resource fencing. Capability services retain their existing catalog/operation ownership checks and add the same task/context/claim gate. Stop/status paths retain their narrowly scoped ownership-aware behavior after lease loss.

A pending revision may advance the task aggregate while the active immutable task remains identical. This is allowed when the execution digest and task revision match; an older aggregate snapshot is rejected. Dynamic JSON numeric arguments must survive the repository/HTTP float64 boundary without changing their decimal value and must remain within the safe integer magnitude. Use strings for larger business identifiers; nonportable arguments fail closed instead of hashing a rounded command. Typed protocol uint64 values, including UnixNano fencing tokens and observation sequences, retain their full precision and are not subject to that dynamic-argument restriction.

If a cloud completion also activates a new task revision and its reply is lost, an old EXECUTED outbox sees a revision mismatch and stops for review. The current cloud client has no authoritative historical claim-read method, so the worker does not infer completion or erase this discrepancy. Likewise, a failed EXECUTED checkpoint leaves ACCEPTED rather than manufacturing success.

Tests cover synthetic Runtime execution, concurrent workers with one physical dispatch, SQLite close/reopen at all three checkpoints, read/commit/ack failures, stale and mixed contexts, mutation during policy inference, coordinator restart, and JSON precision. They do not represent physical robot acceptance.
