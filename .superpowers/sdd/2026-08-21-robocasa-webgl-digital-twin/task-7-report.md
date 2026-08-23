# Task 7 Report: Semantic overlays and production camera interaction

## Outcome

Task 7 is complete from baseline
`a54b5368419ceb6654cd323907883141618f7089`. The WebGL renderer now keeps the
full RoboCasa/XLeRobot model layers and the authoritative semantic evidence
layer visible together, while preserving the existing `WorldCamera` behavior
and persisted `{yaw,pitch,distance,target}` JSON shape. The change stays within
the Task 7 overlay, interaction, renderer, generated bundle, and focused test
surface; Task 8 retains console DOM wiring and fallback presentation.

## Implementation

- Added `SemanticOverlay` with separate Three.js roots for transparent handoff
  zones, staged task-path lines, AABB line segments, selection/emergency/held
  outlines, and stale robot veils. Stale veils are per-instance overlay
  primitives, so Task 6's shared robot geometry/material contract remains
  intact.
- Added DOM label projection on every rendered frame. Ordinary fixture labels
  are distance-culled; selected entities, the red block, and robot status labels
  remain visible. Text is assigned through `textContent` and uses the current
  console's live/custody/fault/selection palette.
- Derived path stage and custody solely from the incoming snapshot. Resource
  owner/fencing, entity `held_by`, robot `held`, reported conflicts, stale
  status, emergency stop, and selection remain distinct evidence. A
  robot/entity custody disagreement is rendered as `CONFLICT`; the object is
  never re-parented or moved away from its authoritative entity pose.
- Added independent `{models,bounds,labels,path}` visibility. Hiding models
  affects the static kitchen, articulated robots, dynamic block, and stale
  model veil without suppressing selected bounds, labels, or task-path evidence.
- Added `InteractionController.bind(canvas, renderer)`, using the existing
  `WorldCamera.drag`, `zoomAt`, `applyPreset`, `basis`, and `toJSON` contract.
  It supports left-button pan, right-button orbit, CSS-pixel pointer-anchored
  wheel zoom, click selection, double-click focus, focused-canvas `F` overview,
  pointer capture/release, pointer cancellation, context-menu suppression, and
  listener disposal.
- Follow mode updates only the camera target of a `FRESH` robot. Pointer camera
  operations, wheel, double-click, and `F` cancel follow. Renderer integration
  accepts only monotonic equal-revision freshness projection: `FRESH` may
  degrade, but `STALE`/`UNKNOWN` cannot become `FRESH` until a newer revision.
  Activity, held state, emergency stop, poses, joints, relationships, resource
  owner, fencing token, and custody conflict remain fixed at that revision.
- Added `WebGLSceneRenderer.bindInteraction(...)` so the existing three-key
  classic bundle API can expose the production interaction path without
  changing Task 6's top-level global shape.

## TDD evidence

### Initial RED

After installing the exact lockfile with `npm ci`, the existing 52 tests stayed
green and four expected failures remained:

- `ERR_MODULE_NOT_FOUND` for `semantic_overlay.js`;
- `ERR_MODULE_NOT_FOUND` for `interaction_controller.js`;
- renderer tests failed because `setVisibility` did not exist;
- renderer tests failed because `setWorldCamera` did not exist.

### Focused RED/GREEN cycles

- Overlay tests first failed for missing complete evidence layers, then covered
  ordered zones, staged paths, bounds, labels, independent visibility,
  selection, stale veil, emergency outline, held object, custody conflict, DOM
  projection, distance culling, and removal without ghost evidence.
- Interaction tests first failed with the module missing, then covered the
  pan/orbit split, pointer-anchored zoom, focus, overview, pointer lifecycle,
  context menu, camera persistence shape, fresh-only follow, and user-operation
  follow cancellation.
- Renderer integration regressions separately failed before support for
  overlay attachment, model-layer visibility, WorldCamera synchronization,
  classic-bundle interaction binding, and equal-revision volatile projection.
- An equal-revision regression proved that changed block pose, relationship,
  owner, fencing token, robot status, and custody evidence stay rejected while
  entity/resource freshness may only degrade their view-only presentation.

### Review round 1 RED/GREEN cycles

- Ten focused RED failures reproduced the review findings: concurrent pointer
  capture, same-revision follow revival, PerspectiveCamera mismatch, mutable
  same-revision activity/emergency facts, recomputed custody conflict, missing
  x/y label clipping, missing custody DOM evidence, and freshness revival.
- A direct `RobotModelInstance` RED test proved that its volatile entry point
  also needed the same monotonic freshness boundary. A later late-bind RED test
  proved that a newly attached follow controller must receive renderer-owned
  effective freshness rather than the repeated snapshot's rejected `FRESH`.
- The production camera now uses the exact `WorldCamera` vertical convention,
  `2 * atan(1 / 1.8)` (about 58.109 degrees). Real Three.js ray/unproject tests
  preserve the wheel anchor at DPR1 and DPR2.
- The visible custody DOM badge reports owner, fencing token, resource
  freshness, and conflict through `textContent`; label visibility controls it,
  same-revision refresh cannot erase conflict facts, and disposal removes it.
- Pointer handling ignores additional pointers while a drag owns capture,
  releases capture and the drag CSS class on disposal, and clips labels outside
  x/y/z clip space with a small margin.

### Review round 2 RED/GREEN cycles

- Four focused failures reproduced the public interaction-boundary defects:
  an equal-revision mutated pose could become the first followed pose, an older
  global revision could introduce and move to a target, re-following a fresh
  robot at the accepted revision did not restore its target, and the custody
  badge had no pre-snapshot `display: none` state.
- `InteractionController` now keeps only the minimal accepted follow view: one
  global latest revision plus each robot's copied three-coordinate pose and
  freshness. A newer revision replaces that cache, an equal revision preserves
  poses and only degrades freshness, and an older revision is rejected before
  any per-robot data is inspected.
- `setFollow` immediately applies a cached fresh target, including after a user
  operation cancelled follow at the same revision. Same-revision stale state
  remains non-fresh until a newer revision and cannot revive follow.
- The custody badge is explicitly hidden as it is constructed, before any
  authoritative snapshot or label-visibility projection is available.

## Final verification

Fresh verification completed successfully:

```text
cd web && npm run build && npm test
76 tests passed, 0 failed

cd web && node --test world_view_test.mjs app_test.mjs webgl_scene_test.mjs
35 tests passed, 0 failed

go test -count=1 ./web
ok github.com/SUSTechWLA/tangying-robot-agent-os/web 0.833s
```

Two consecutive builds produced the same bundle:

```text
324855cd48be13199b780cc726b6185d533cbd6e7122e6f4b2e7a04c322633b8  web/webgl_scene.js
```

After staging the first build, the second `npm run build` left
`git diff --exit-code -- web/webgl_scene.js` empty.

Fresh static checks passed for every changed JavaScript/test module, staged and
worktree whitespace, the generated classic-script bundle's lack of runtime
imports, remote URL literals, `eval`, or source map references, and the bundled
`WebGLSceneRenderer.bindInteraction` entry.

## Self-review

- No snapshot is mutated or retained as a copied second fact store; overlay
  maps contain only disposable rendering projections and label/status metadata.
- Equal revisions cannot move overlay geometry or replace custody owner/token.
- Equal revisions cannot change robot facts or recompute custody conflict;
  freshness is the sole volatile projection and is monotonic within a revision.
- Follow state consumes the renderer's effective freshness, so a repeated stale
  revision cannot resume follow even when its input claims `FRESH` again.
- The interaction boundary rejects older revisions globally. Its small follow
  cache copies only pose/freshness needed for immediate re-follow; it does not
  retain the authoritative snapshot or other robot facts.
- Shared GLB geometry/materials remain shared; only renderer-owned overlay
  geometry/materials are disposed by `SemanticOverlay`.
- Selection and visibility alter only view state. A hidden model cannot advance
  tasks, change custody, or suppress conflict evidence.
- Pointer listeners and captures are released on cancel/dispose, and native
  context menus are prevented only on the bound canvas.
