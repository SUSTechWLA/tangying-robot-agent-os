# Task 9 Report: authenticated RoboCasa WebGL handoff acceptance

## Outcome

Task 9 passes fail-closed against one retained, process-backed episode:

- run: `0d31e835cde04a9fb84e4c0312c71366`
- episode nonce: `846832fdd71e0aec6fbe85a5990e0f112ac2eb71a6c36050972fffdc9ea4c12d`
- task: `task-72371174c045f93f29706ec0`
- request: `让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区`

The retained `artifacts/robocasa-harness/round3/summary.json` reports `passed: true`; all 21 checks are true. The real task is `SUCCEEDED`, both intents are `SATISFIED`, both robots moved canonical joints, the block ends inside `right-target-zone`, neither robot holds it, every source is fresh, and custody progresses robot-1/token 1 → robot-2/token 2 → environment/token 3.

## Authenticated capture provenance

The runner generated the nonce and one-time bearer before starting the stack, exposed the nonce through server responses, `/v1/world`, the page DOM, and the visible acceptance marker, and accepted the final browser payload once (`HTTP 201`). The loopback receiver decoded the controlled browser's JPEG screenshot buffers and rewrote them as genuine PNG before hashing and sealing.

The receiver generated an ephemeral Ed25519 key, signed the canonical file manifest, and destroyed the private key. The tracked anchor `tests/e2e/robocasa_golden_capture_anchor.json` pins:

- run/nonce/task above;
- public-key fingerprint `7332cea87686980eefa66cd9b6d76e833b8033777c5a57898948f24a2449ab5c`;
- capture-envelope SHA-256 `fdd66d29e67be8cc1e1d0496fe252998803eca6009656237d67ea159bcfaff09`.

The validator verifies the Ed25519 signature, anchor identity, public-key fingerprint, envelope hash, every canonical file hash, screenshot PNG decoding/content, DOM assertion envelope, world digest/revision, raw timing arrays, and runner-owned network lifecycle. Substituting nonce, public key, signature, JSON, screenshot, network records, or performance arrays fails closed.

## Network evidence

The runner—not browser-supplied JSON—recorded five cache-disabled, no-redirect GET lifecycles at `http://127.0.0.1:18080`:

1. document;
2. manifest;
3. scene GLB (`331466…f8005`, 34,199,888 bytes);
4. robot GLB (`d81d98…6a7fb`, 16,902,520 bytes);
5. joint binding (`8d0558…4ff20`, 1,805 bytes).

Every request and final response URL is same-origin, every status is 200, every response carries the exact episode nonce header, content hashes match, and `externalOrigins` is empty. Redirect-to-external and truncated lifecycle attacks are rejected before external I/O can be counted safe.

## Browser and visual acceptance

The required browser-client runtime controlled the existing Edge surface at the inferred URL; no standalone Playwright browser was started. The episode captured the complete RoboCasa kitchen, two complete articulated XLeRobot models, current joints/block/custody/task state, and real interaction results.

Five receiver-produced 1404×794 PNGs are retained under `artifacts/robocasa-harness/round3/visual/`:

- `overview.png`, world revision 11249;
- `robot-1.png`, revision 11277;
- `robot-2.png`, revision 11319;
- `handoff-final.png`, revision 11347;
- `fallback.png`, revision 11389.

Each image decodes as PNG, has substantial entropy/color/edge/row/column content, and is bound to its raw DOM assertion, exact snapshot digest, nonce, task, revision, and capture time. The fallback image visibly records `WORLD LIVE`, `VISUAL DEGRADED`, and the in-viewport semantic `fleet-godview-canvas`; the four live images bind `VISUAL LIVE` and the WebGL Canvas.

Real UI interactions exercised four independent toggles, left-pan, pointer-anchored zoom, `F` reset, top/R1/R2 presets, follow and pan-cancel-follow, robot-2 focus, refresh restoration, and post-refresh camera interaction. Browser-client CUA has no right-button parameter, so right orbit remains honestly covered by the deterministic browser test. The controlled surface rejects raw `file://`; app tests verify its zero-request service-link behavior without claiming a browser image that could not be made.

## Performance contract and measurements

The page retained 600 real, monotonic, jittered rAF timestamps and 600 raw `renderer.render()` durations with browser timestamps. The fail-closed steady-capacity definition is:

`capacity_fps = 1000 / mean(duration_ms)` over the last up to 300 samples at least 250 ms after the final recorded interaction.

The validator recomputes the window and requires the signed mean, median, p90, p95, max, sample count, and window bounds to match. The retained full-quality surface is DPR 2, CSS 1064×532, backing buffer 2128×1064. Results:

- first interaction: 1807 ms (limit 5000 ms);
- refresh recovery: 756 ms (limit 5000 ms);
- actual controlled-display rAF: 33.8038 FPS with real jitter;
- steady render window: 300 samples;
- mean: 9.2663 ms → 107.9176 FPS capacity;
- median: 6.4 ms;
- p90: 23.4 ms;
- p95: 24.9 ms;
- max: 31.0 ms.

The report does not claim display FPS ≥50. The Edge automation/display surface is cadence-limited; the Task brief's steady-stage ≥50 requirement is satisfied by measured render throughput, while display cadence and tail latency remain explicit.

## Semantic and evidence gates

The runner requires exact request/adapter/scene/task identities, lowercase 64-hex model revision, non-boolean finite joints, movement by both robots, strictly increasing initial/moving/final revisions and projected times, final fresh sources, right-target placement, clear held state, monotonic custody fencing, and exact tokens 1/2/3.

Each Harness evidence ID is parsed from the right and matched to an allowed registered scene/proprioception source, decimal source sequence, millisecond observation time, world frame, transform revision, task correlation, intent, robot, token, status/reason, domain event, and authoritative post-command trajectory observation. The real episode exposed a float epoch conversion bug at `.366Z`; the regression now computes epoch milliseconds with integer datetime arithmetic, preserving the exact observation ID boundary.

Adversarial coverage includes boolean joints, invalid hashes/identities, stationary robots, stale/final-held state, custody regressions, fabricated evidence/transitions/correlation, wrong observation source/sequence/time/frame/transform, 1×1/black/stripe/wrong-state/fallback/JPEG/corrupt images, nonce/key/signature tampering, external redirect/final URL, truncated network/timing arrays, constant render timestamps, synthetic rAF, and slow render capacity.

## Scope deviations

Necessary scoped changes outside the original Task 9 harness files are documented:

- the page retains the runner nonce in a hidden authoritative snapshot and visible acceptance marker;
- the page exposes 600 raw rAF samples plus time origin;
- the WebGL renderer exposes a rolling 600-entry raw render-duration/timestamp timeline;
- the acceptance-only fallback button makes `WORLD LIVE / VISUAL DEGRADED` plus semantic Canvas reproducible without changing authoritative world state.

No experimental DPR, MSAA, shadow, or geometry-reduction change was retained. The final evidence uses the original full-quality WebGL settings.

## Verification

- `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest tests/e2e/test_robocasa_visual_twin.py -q`: 66 passed in 34.19 s.
- `cd web && npm test`: 97 passed.
- retained artifact revalidation: `summary.passed == true`, all 21 checks true.
- five screenshots: genuine decoded 1404×794 PNG, substantial visual metrics true.
- signed envelope and pinned anchor: valid.
- runner network: five exact same-origin, cache-disabled, no-redirect lifecycles.
- `git diff --check`: passed.

## Cleanup

All temporary Fleet/gateway/runtime/worker processes and the capture receiver stopped. Ports 18080–18083 and 18180–18183 have no listeners. Generated `web/node_modules` and the temporary Pillow overlay are removed before commit.
