# Task 14 Report: Fleet xyz-yaw semantic overlays

## Outcome

The WebGL semantic overlay now accepts the Fleet production robot pose contract
`[x, y, z, yaw]`. A real RoboCasa-style pose such as
`[1.15, -1.15, 0.035, pi/2]` creates the robot label, semantic bound, status
model, stale veil, emergency/held outline, and custody evidence instead of being
discarded before `#applyRobots`.

Three-component xyz and seven-component `[x, y, z, qw, qx, qy, qz]` poses stay
compatible. Quaternion input is normalized with an overflow-safe scale before
extracting its planar yaw. Zero quaternions, unsupported lengths, and non-finite
values fail closed and remove any prior robot semantic projection.

## Root cause and yaw contract

`web/src/semantic_overlay.js` previously allowed only pose lengths 3 and 7.
Fleet's worker intentionally projects robot telemetry to four components, so
the retained round3 snapshot's two robots never entered `#applyRobots` even
though `RobotModelInstance` already supported that shape.

The overlay now uses the same convention as `RobotModelInstance`: positive yaw
rotates about world +Z, so `pi/2` maps local +X to world +Y. The robot bound,
emergency/held outline, and stale veil all receive that same rotation. Labels
remain upright DOM evidence at the robot's authoritative world position.

## TDD evidence

The shared handoff fixture was first changed from synthetic 3D poses to the
retained production positions and four-component yaw shape. Before production
code changed, the focused test had 9 failures: `model`, `bound`, and labels were
missing for the real pose; the orientation compatibility assertion failed; and
a zero quaternion remained accepted.

After the minimal parser and overlay-transform change, the focused suite passed
11/11. The added regressions cover:

- stale, activity, held-object, emergency, and custody semantics on the 4D pose;
- positive-Z yaw on bounds, outline, and stale veil;
- preserved 3D and 7D quaternion behavior; and
- fail-closed cleanup for non-finite yaw, zero quaternion, and unsupported
  length.

## Verification

Fresh verification from the Task 14 worktree:

```text
cd web && node --test webgl_semantic_overlay_test.mjs
11 passed, 0 failed

cd web && npm test
100 passed, 0 failed

go test -count=1 ./web
ok github.com/SUSTechWLA/tangying-robot-agent-os/web 0.854s
```

`npm run build` was run twice. Both generated the identical local bundle:

```text
f6c89f4b920220bc81f186f3dcc33246a3505ecea2665574f351e5ed20ed6473  web/webgl_scene.js
```

`node --check` passed for the source, focused test, and generated bundle. Static
checks found no remote URL, source-map reference, or `eval` call in the bundle.
`git diff --check` passed.

## Scope

Only `semantic_overlay.js`, its focused test, the deterministic generated
bundle, and this report are part of the Task 14 commit. Concurrent Task 15
acceptance workflow changes are intentionally excluded.
