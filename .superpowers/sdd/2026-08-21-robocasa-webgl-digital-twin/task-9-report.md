# Task 9 Report: RoboCasa WebGL handoff acceptance

## Outcome

Task 9 is complete. The real process-backed RoboCasa Fleet stack completed the exact Chinese request:

> 让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区

The provenance-bound round-1 acceptance run is `a6684b56201a46548b627b5e432040aa` / `task-028d36f641cd278b23a8f16c`. Its final machine-readable summary reports `SUCCEEDED`, both intent Harness verdicts as `SATISFIED`, 14 finite canonical joints for each robot, `red-block` inside `right-target-zone`, resource owner `environment`, matching scene/model identity and content hashes, and same-origin assets. All 16 fail-closed checks are true.

## TDD and implementation

- Added public unauthenticated JSON/byte asset helpers and visual-twin E2E tests for manifest identity, GLB SHA-256, same-origin URLs, canonical joint counts/movement, final placement, custody, and Harness verdicts.
- Extended the acceptance runner with `world-moving.json`, visual manifest/network/performance artifacts, screenshot paths, and a fail-closed summary predicate. A regression test first failed with missing `browserCapture`; the implementation now preserves the browser request inventory while adding automated asset hash requests.
- Extended the existing handoff test to require both robot IDs and at least 12 finite `joint.*` values per robot.
- Hardened the summary against boolean joints, malformed/non-lowercase model hashes, task/scene/intent substitutions, one stationary robot, stale sources, held final state, non-monotonic fencing, missing/corrupt/JPEG-under-PNG screenshots, stale run IDs/digests, unbound manifest/asset network files, external origins, and performance threshold bypasses. The adversarial cases were observed RED before the gate was implemented and are now GREEN.
- Public asset reads validate the requested origin before I/O, disable redirects, and validate the final response URL. The redirect regression proves the external target receives zero requests.
- Documented asset generation, served URL, interaction controls, fallback behavior, file-mode boundary, physical-evidence boundary, and real-hardware manifest/joint registration.

### Scope deviation: necessary Task 6 defect fixes

Real browser acceptance initially showed `WORLD LIVE / VISUAL DEGRADED` with `WEBGL_SNAPSHOT_INVALID`. Blob texture console messages were not causal: the served CSP allowed `img-src 'self' blob: data:`, and GLTF loading continued. The authoritative `/v1/world` robot pose contract is `[x, y, z, yaw]`; the renderer accepted only 3- or 7-element poses.

A focused renderer regression first failed (`false !== true`). The minimal fix accepts the Fleet 4-element pose in both the scene validator and robot instance, converting yaw to a Z-axis quaternion. Browser acceptance then showed `WORLD LIVE / VISUAL LIVE`. A second regression introduced a rolling `data-steady-fps` measurement; it failed before implementation and passed after the one-second rolling-window counter was added. These necessary fixes touch `web/src/robot_model.js`, `web/src/webgl_scene_renderer.js`, `web/webgl_scene_test.mjs`, and rebuilt `web/webgl_scene.js` outside the original Task 9 file list.

## Browser acceptance

The in-app Browser runtime was used at `http://127.0.0.1:18080/`; no standalone Playwright browser was started.

- Complete kitchen meshes/materials and two complete articulated XLeRobot models were visible.
- R1 and R2 arm/gripper poses changed during the live handoff; API snapshots exposed 14 canonical joints for each robot.
- Final UI/API state agreed on task success, both Harness verdicts, final block placement, owner `environment` with fencing token 3, robot activity/held state, and fresh sources at completion.
- Left pan, pointer-anchored wheel zoom, overview/top/R1/R2 presets, follow/cancel-follow, selection, double-click focus, `F` reset, and refresh camera restore were exercised. Browser-client CUA does not expose a right-button parameter, so right orbit is covered by the deterministic interaction test rather than claimed as a manual CUA gesture.
- Models, bounds, labels, and path toggles changed independently.
- A same-origin proxy forced only the scene GLB to return 503. The captured frame shows `WORLD LIVE / VISUAL DEGRADED` and the usable semantic Canvas.
- The in-app Browser URL policy rejected the requested raw `file://` URL. No workaround or different browser was used. Automated `app_test.mjs` verifies that file mode shows the HTTP service link and starts no API/WebSocket retry loop.
- The browser inventory used for this run recorded only `http://127.0.0.1:18080` URLs; external origins were empty. The same run separately fetched and SHA-256 checked the scene GLB, robot GLB, and binding through the public origin without redirects.
- First interaction readiness was 3178 ms (target <= 5000 ms); rolling steady-stage rendering was 81.0 FPS (target >= 50 FPS); refresh recovery was 564 ms.
- After the proxy stopped, pointer-anchored zoom still changed the already-loaded fallback Canvas. The in-app Browser rejects raw `file://`; the deterministic app test covers its zero-network service-link behavior without claiming a visual capture that the browser could not make.
- Browser screenshot buffers were JPEG, so each was explicitly converted to PNG. The gate reopens and decodes every image with Pillow, verifies declared format, dimensions, byte count and SHA-256, and binds it to the exact REST snapshot served to that page load. The five revisions are 30429, 31255, 32011, 33663 and 36309.

Screenshots:

- `artifacts/robocasa-harness/manual/visual/overview.png`
- `artifacts/robocasa-harness/manual/visual/robot-1.png`
- `artifacts/robocasa-harness/manual/visual/robot-2.png`
- `artifacts/robocasa-harness/manual/visual/handoff-final.png`
- `artifacts/robocasa-harness/manual/visual/fallback.png`

Machine-readable evidence:

- `artifacts/robocasa-harness/manual/summary.json`
- `artifacts/robocasa-harness/manual/visual-manifest.json`
- `artifacts/robocasa-harness/manual/visual-asset-network.json`
- `artifacts/robocasa-harness/manual/visual-network.json`
- `artifacts/robocasa-harness/manual/visual-performance.json`
- `artifacts/robocasa-harness/manual/browser-evidence.json`
- `artifacts/robocasa-harness/manual/run-context.json`
- `artifacts/robocasa-harness/manual/world-initial.json`
- `artifacts/robocasa-harness/manual/world-moving.json`
- `artifacts/robocasa-harness/manual/world-final.json`

## Verification

- `cd web && npm run build && npm test`: 94 passed.
- `cd web && node --test world_view_test.mjs app_test.mjs`: 37 passed.
- `go test ./...`: passed.
- Focused fail-closed/redirect tests: 30 passed.
- Visual-twin acceptance file: 32 passed.
- `make robocasa-web-assets`: passed twice; second run produced no tracked asset diff.
- `make robocasa-acceptance`: passed; summary `passed: true`.
- Final `make test` with the main development venv plus a temporary, cleaned visual-dependency overlay: Go packages passed, Python 349 passed / 29 skipped, Web 37 passed.
- `git diff --check`: passed.

The main venv intentionally excludes the RoboCasa visual extra, so a direct first `make test` stopped during collection at missing `pygltflib`. An initial overlay accidentally exposed NumPy 2.4 to conda children and reproduced Numba's unrelated `NumPy 2.2 or less` failure; that run was stopped after preserving its trace. The final successful gate removed NumPy from the isolated `/tmp` visual overlay so each conda subprocess retained its own compatible dependency set. The overlay was deleted after the run and did not modify either environment.

## Cleanup

Docker was unavailable, so `scripts/robocasa-fleet.sh start` could not bring up the Compose profile. Browser acceptance instead used the same real Fleet control-plane binary, RoboCasa Runtime, and two Edge Worker processes provided by the process E2E harness on the required URL. The temporary normal stack, fault-injection proxy/backend, generated `node_modules`, temporary dependency overlay, and worktree venv symlink were stopped or removed. Ports 18080-18083 and 18180-18183 have no listeners.
