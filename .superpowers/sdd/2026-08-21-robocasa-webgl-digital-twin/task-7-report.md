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
  accepts equal-revision freshness/activity/held/emergency projection without
  accepting changed poses, relationships, resource owner, or fencing token.
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
  owner, and fencing token stay rejected while entity/resource freshness and
  accepted robot held status update their view-only conflict evidence.

## Final verification

Fresh verification completed successfully:

```text
cd web && npm run build && npm test
66 tests passed, 0 failed

cd web && node --test world_view_test.mjs app_test.mjs
21 tests passed, 0 failed

go test -count=1 ./web
ok github.com/SUSTechWLA/tangying-robot-agent-os/web 0.504s
```

Two consecutive builds produced the same bundle:

```text
f3cc8deacbfba1a718d8c90ccda3e0aa3bf71633f2850b098b4bc7d3a0208ba7  web/webgl_scene.js
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
- Shared GLB geometry/materials remain shared; only renderer-owned overlay
  geometry/materials are disposed by `SemanticOverlay`.
- Selection and visibility alter only view state. A hidden model cannot advance
  tasks, change custody, or suppress conflict evidence.
- Pointer listeners and captures are released on cancel/dispose, and native
  context menus are prevented only on the bound canvas.
