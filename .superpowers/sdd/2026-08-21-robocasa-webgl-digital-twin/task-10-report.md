# Task 10 Report: reproducible authenticated RoboCasa acceptance workflow

## Outcome

Task 10 hardens the retained acceptance boundary and separates capture from trust:

- one authenticated receiver session accepts exactly one valid POST atomically;
- the runner writes a fail-closed summary before producing the final Ed25519 attestation;
- the final attestation covers `summary.json`, the capture-envelope hash, and all 27 retained evidence files;
- the ephemeral private key remains only in a temporary directory and is destroyed immediately after finalization;
- candidate creation, candidate audit/promotion, and pinned retained revalidation are explicit, separate CLI and Make workflows;
- the default `make robocasa-acceptance` starts no stack and only revalidates pinned round3.

## Atomic receiver and repository uploader

The receiver now validates path, bearer, and bounded content length, then reserves the single-use session under a lock before reading the body. Eight simultaneous valid bearer POSTs produce exactly one HTTP 201 and seven HTTP 409 responses. A malformed reserved request releases the reservation only when the receiver-owned file fingerprint set is unchanged; any partial file commit leaves the receiver closed.

`scripts/upload_robocasa_browser_capture.py` reads only a current-user-owned regular 0600 session file, requires an exact `http://127.0.0.1/.../v1/capture` endpoint, checks payload run/nonce/task identity, disables proxies and redirects, performs one POST with a bounded timeout, and never prints the bearer.

## Final attestation and pinned round3

Receive/write creates a signed capture envelope but retains the same ephemeral Ed25519 private key. Runner finalization writes `summary.json`, then signs a canonical `acceptance-attestation.json` containing:

- `summarySha256`;
- `captureEnvelopeSha256`;
- the canonical SHA-256 manifest of every retained evidence file;
- run, episode nonce, task, public key, and public-key fingerprint.

Retained validation checks the tracked anchor, attestation bytes, Ed25519 signatures, same public key across envelope/attestation/anchor, exact file manifest, summary/envelope hashes, `summary.passed`, every summary check, and a fresh semantic reconstruction from the retained source artifacts. Summary edits, public-key replacement, file edits, extra files, or missing files fail closed.

The local round3 pack was finalized and promoted through the new validation path. The tracked v2 anchor pins:

- run `0d31e835cde04a9fb84e4c0312c71366`;
- task `task-72371174c045f93f29706ec0`;
- public-key fingerprint `0fdd4aa1e7f937947266470830d17bba7eef612489edae081ad2b63c24cf09ff`;
- summary SHA-256 `59145f66ca22951584bdb78c0d23155c63465eb692a61bd1d5ffeba6db3eac64`;
- capture-envelope SHA-256 `f18b517ecb065d6294bc8c2d1559b8b8c67a58649dd04ee0e368a04adf6c5451`;
- final-attestation SHA-256 `f35dff85b51350d7c3c596909c6d49ff3c6faf9eb94a1b3717058732edd44a9f`.

No private-key file exists in the retained pack.

## Candidate, promotion, and revalidation workflow

- `--candidate` starts a fresh stack, purges its candidate directory, requires a finite browser wait greater than zero and at most 600 seconds (Make default 300), writes only an untrusted candidate anchor, finalizes, and self-revalidates before returning success.
- `--promote-anchor` starts no stack. It revalidates the candidate signature, every retained artifact, and the recomputed passing summary before atomically replacing the tracked anchor.
- `--revalidate` starts no stack. It validates only the selected retained pack against the selected trusted anchor.
- `make robocasa-acceptance-candidate`, `make robocasa-acceptance-promote`, and `make robocasa-acceptance` expose those three distinct operations. No default path builds a summary with a zero browser wait.

## Performance ruling

The committed plan and operations docs now formally distinguish submission capacity from displayed cadence:

- controlled full-quality display rAF: 33.8038 FPS;
- signed full-quality steady renderer submission capacity: 107.9176 FPS;
- automated controlled-browser gate: submission capacity at least 50 FPS, with actual display cadence and tail latency retained;
- separate real-hardware/browser gate: visible, unthrottled display rAF at least 50 FPS.

The automated result does not claim display rAF at least 50. Its explicit cost is that renderer submission capacity can overestimate visible smoothness when GPU completion, compositor, automation, or display scheduling is the bottleneck.

## TDD evidence

Observed RED before implementation:

- eight concurrent authenticated POSTs returned multiple HTTP 201 responses;
- final-attestation, retained-validation, and workflow CLI APIs did not exist;
- the repository uploader module did not exist;
- candidate/promote Make targets did not exist and the default target created a new summary with zero browser wait.

Observed GREEN after minimal implementation and refactoring:

- atomic/malformed receiver, key lifecycle, uploader, summary tamper, anchor/public-key, candidate/promotion/revalidation, zero-timeout, and Make-boundary focus: 10 passed;
- complete `tests/e2e/test_robocasa_visual_twin.py`: 75 passed.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`: 75 passed in 37.50 s.
- focused adversarial/workflow selection: 10 passed.
- `go test ./...`: all packages passed.
- `cd web && npm ci && npm test`: 97 passed.
- `make robocasa-acceptance`: pinned round3 revalidated successfully without starting a stack.
- Python compile check for runner, uploader, and focused tests: passed.
- `git diff --check`: passed.

The first Web run correctly exposed that Task 9 cleanup had removed `web/node_modules`; five tests could not resolve `three`. After `npm ci`, all 97 tests passed. The generated dependency directory was moved out of the worktree to `/tmp/tangying-node-modules.dpcFUA/node_modules` after verification.

## Scope

Changes are limited to the Task 10 runner, uploader, acceptance tests, tracked anchor, Make workflow, README, RoboCasa handoff operations guide, formal plan, and this report. No Go or Web production file changed.
