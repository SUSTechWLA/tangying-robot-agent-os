# Model cost and recipe integration validation — 2026-10-28

Scope: 10 consecutive planning scenarios on a loopback HTTP fixture and reopenable SQLite, plus one separate recovery/System action-loop accounting-failure test. No proposal was approved or executed. There was no external LLM, ROS 2, Gazebo, real robot, physical motion or measured commercial billing. Provider usage numbers are scripted fixture values.

Baseline commit: a98c0681d524a6db7036a7a5bbe121df6f8e239d. Baseline and final test sources differ only to check the new typed accounting error; both preserve exact source files and SHA-256 manifests. The separate loop baseline was run after the planner/sentinel fix but before the Loop fatal branch; see commands.json.

The final targeted run has an unchanged source set before/after execution. Full package race tests also passed for actionloop, capabilityagent and recoveryexec. Raw commands, outputs, SHA-256 values, cases and complete planning traces are retained. SQLite databases are deliberately outside the suggested Git archive; MANIFEST.json records their paths, sizes and hashes.

| Scenario | HTTP before→after | ModelCall traces before→after | Fresh reads after | Valid/missing usage fields after |
|---|---:|---:|---:|---:|
| off-initial | 2→2 | 2→2 | 1 | 1/1 |
| off-repeat | 2→2 | 2→2 | 1 | 1/1 |
| hint-initial | 2→2 | 2→2 | 1 | 1/1 |
| hint-repeat | 2→2 | 2→2 | 1 | 1/1 |
| validated-initial | 2→2 | 2→2 | 1 | 1/1 |
| validated-restart-repeat | 0→0 | 0→0 | 1 | 0/0 |
| validated-state-changed | 1→1 | 1→1 | 1 | 1/0 |
| validated-cancel-read | 0→0 | 0→0 | 1 | 0/0 |
| off-cancel-read | 1→1 | 4→1 | 1 | 0/1 |
| off-ledger-result-failure | 1→1 | 3→1 | 0 | 1/0 |

The before/after HTTP total is 13 in both planning campaigns. The change eliminates spurious client attempts, not four paid requests: the canceled-read baseline had four ModelCall traces but the server received only one HTTP request. Cancellation now returns context.Canceled with its original observed round. A result-ledger failure now retains its first accepted HTTP response and known usage, returns ErrModelUsageLog, and does not retry a platform failure as malformed model output.

The separate recovery/System Loop baseline received one HTTP request, retained two refused rounds and returned nil error; after the fix it retains one round, returns the typed ledger error, and calls no tools. Normal malformed-response retry behavior is unchanged.

Exact validated reuse after SQLite reopen makes no new model request, refreshes one Provider read, and returns RECIPE_HIT. A changed map returns RECIPE_REVALIDATION_MISS and makes one new model request using the refreshed read. Hint/off repeat paths each issue two fixture requests. Plan and trace JSON round-trip byte hashes were checked after SQLite reopening; all scenarios dispatch zero physical writes.

The final usage ledger contains 13 starts, 12 results and one unfinished call from deliberate result persistence failure. That unfinished call is not zero-cost: its returned fixture usage is preserved in the planning trace, while the independent ledger cannot claim a complete result. The usage report leaves unknown token fields null and counts only reported fields. known_prompt_tokens in per-case summaries is a sum over observed provider fields, not total billed usage; use usage_valid/usage_missing/model_calls alongside it. Do not interpret missing usage as zero.

Packaging note: copied Go sources use .go.txt to keep this evidence directory outside Go package discovery. Remove only the final .txt suffix when reconstructing sources; content hashes are unchanged.
