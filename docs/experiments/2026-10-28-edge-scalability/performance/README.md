# Edge long-task journal performance evidence

Date: 2026-10-28. Host: darwin/arm64, Apple M5. All measurements use the existing scripted in-process campaign fixture. No LLM, Gazebo, flight controller or physical robot was tested here.

## What changed

The parent audit path now calls Service.RecordEvent when available. SQLite.AppendTaskEvent updates only UpdatedAt, reserves MAX(sequence)+1 after taking a writer lock, and strictly inserts one event in the same transaction. It does not read prior event payloads or replace approval/state/plan/revision. Existing AppendEvent remains compatible. Completion reports load each child journal once per invocation and reload it on the next invocation; authority reads remain fresh.

The previous append path loaded all events and UPSERTed all old events each time. The 32-step CPU baseline attributed 0.22 s cumulative to Service.AppendEvent, 0.21 s to SQLite.Get (0.19 s loadEvents) and 0.04 s to SQLite.Update. Profiles are short and noisy; operation-count regressions and fixed-history benchmarks supply stronger scaling evidence than CPU percentages alone.

## Measured result

In three interleaved executions of saved before/after test binaries, median initial 32-step Host execution was 803.859 ms before and 245.779 ms after (3.27x in this fixture). Before range 785.163–807.731 ms; after 245.041–249.948 ms. Every run reported 32 physical starts, zero restart writes and zero restart plans. This is a local software measurement, not a robot latency promise.

The fixed-history benchmark retains 32/128/512 historical 1 KiB payloads and measures one append, resetting the new row outside the timer. At 512 rows full-task append measured 6.067 ms/op, 3.174 MB/op, versus append-only 0.106 ms/op, about 2.3 KB/op. Timing is never a test assertion.

## Regression coverage

- SQLite immutable-history trigger proves the hot path never rewrites an old event; authority fields survive, and data survives close/reopen.
- 32 concurrent appends through independent Service locks and two connection pools retain every event with distinct contiguous sequences.
- Serialization failure and injected insertion failure roll back UpdatedAt and sequence, and emit no observer notification; committed observer can reenter Service.RecordEvent.
- Legacy repository/parent-port fallback retains event identity and notifications.
- Completion report loads a child once per invocation, reloads on retries, and fails delivery when the journal is unavailable, with no physical replay.
- Race tests: tasks, middleware/sqlite and internal/edgeagent excluding Campaign passed. Selected campaign race checks also passed: 32 steps (2.89 s subtest), 96 steps following the selected 32-step task (25.45 s), database close/reopen, unknown outcome after 24 confirmed steps, and plan/step/read budget boundaries. Total selected campaign package 38.403 s. Unknown stays pending, not resumable, with zero restart writes/plans.

## Limits and provenance

The earlier 96-step timeout was a larger multi-task campaign, so this selected 32→96 run is not a matched before/after comparison. Authority reads and child aggregate serialization still grow with history; this change does not turn every task operation into O(1). Cross-service full-task read/modify/Update concurrency remains a separate legacy limitation; the new append-only path does not use it. No checkpoints, event history, verification or unknown reconciliation rules were removed. Retry reports still deliberately redeliver audit facts.

Raw evidence: before.jsonl, after.jsonl, paired-*-*.log, paired-results.json, campaign-race.jsonl, append-benchmark.txt, regressions-race.txt, before/after.cpu, before/after-profile.txt. commands.txt records exact invocations. Saved binaries allow rerunning the same compiled fixtures. before-source-sha256.json and after-source-sha256.json identify touched source snapshots; the latter includes the final comment-only clarification after timing. Full Go JSON output chunks must be concatenated by test before parsing a campaign JSON log line. Raw Go Time fields use local +08:00; scenario timestamps inside records are UTC.

Additional bounded regression: the selected 128-step race fixture passed with 128 physical starts and zero restart writes/plans. See summary.json and campaign-128-race.jsonl for exact duration. This starts a fresh campaign and is not a matched speedup comparison. A temporary build failure during another agent's actionloop edit is preserved separately as campaign-128-build-interrupted.jsonl.
