# Task 15 report: portable frontend-bound acceptance pack

## Outcome

Task 15 replaces the developer-machine-only round3 assumption with a tracked, offline-revalidatable acceptance pack. The authenticated envelope now binds the complete nine-role frontend/visual closure and requires both a controlled-browser page-assets inventory and runner/server response corroboration. The retained anchor and round3 pack come from a fresh controlled Edge episode; no legacy browser JSON was migrated or fabricated.

Fresh episode identity:

- run: `ade8b7d6ad3848049a29071afb294571`
- task: `task-4383c9f733c3aae726d0922c`
- summary: `passed: true`, 20/20 checks true
- browser inventory: 48 unique same-origin URLs, including all nine required roles and no unexpected/external request
- frontend build digest: `dd571e8d52aa22e780c873415253c3cdba734c636a7b785f03a6e999aa3229fe`

## TDD and security evidence

RED was observed before implementation:

- authenticated receiver did not persist the browser network envelope;
- v2 complete-closure evidence was rejected;
- clean archive had no tracked round3 files;
- the external-browser generated `/favicon.ico` probe was initially rejected, while the adversarial `debug.js` request remained a required rejection case.

GREEN adds:

- exact roles for document, CSS, `webgl_scene.js`, `world_view.js`, `app.js`, manifest, scene GLB, robot GLB and binding;
- exact source path, served path, byte count and SHA-256 in a deterministic frontend build identity;
- same-origin/no-redirect/status/nonce/bytes/hash verification for runner/server corroboration;
- exact cross-checking of every browser-uploaded lifecycle record against the independently runner-written corroboration file;
- browser page-assets inventory coverage, uniqueness and origin checks;
- fail-closed omission, byte tamper, unexpected same-origin, external-origin and frontend-build tamper tests;
- an exact allow-list for runtime API/health requests and the browser-generated same-origin `/favicon.ico` without query or fragment;
- explicit candidate failure diagnostics without exposing the capture bearer;
- a clean git-archive regression that runs the default pinned Make target offline.

The 0600 capture session and bearer remained private. The final POST was accepted once with HTTP 201, the candidate self-validation exited zero, and independent promotion revalidated the candidate before atomically replacing the tracked anchor.

## Browser and visual evidence

The controlled Edge viewport was 1404x794. All five PNGs are fresh, substantial and bound to the final episode:

- `overview`: revision 6181, `WORLD LIVE / VISUAL LIVE`
- `robot-1`: revision 6223, `WORLD LIVE / VISUAL LIVE`
- `robot-2`: revision 6279, `WORLD LIVE / VISUAL LIVE`
- `handoff-final`: revision 6335, `WORLD LIVE / VISUAL LIVE`
- `fallback`: revision 6405, `WORLD LIVE / VISUAL DEGRADED`

Signed interaction/performance evidence:

- first interaction: 3348 ms
- refresh recovery: 297 ms
- actual display rAF: 29.7516 FPS (reported only; not claimed as 50 FPS display)
- steady renderer submission capacity: 100.3680 FPS
- render mean/median/p90/p95/max: 9.9633 / 6.5 / 24.7 / 27.0 / 31.2 ms
- steady render sample count: 300

## Verification

Fresh final verification passed:

- visual-twin protocol/adversarial suite: 132 passed, including missing/tampered corroboration and clean git-archive offline revalidation;
- default pinned entry point: `make robocasa-acceptance` revalidated round3;
- Go: `go test ./...` passed;
- Web: clean `npm ci`, build and 100/100 tests passed;
- deterministic bundle: two rebuilds and the pre-build file all matched `f6c89f4b920220bc81f186f3dcc33246a3505ecea2665574f351e5ed20ed6473`;
- source/test/bundle JavaScript syntax checks and Python compilation passed; and
- tracked-pack count, secret scan, `git diff --check` and final worktree state were checked before commit.
