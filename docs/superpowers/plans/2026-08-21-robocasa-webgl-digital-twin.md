# RoboCasa WebGL Digital Twin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render the complete `robocasa-handoff-v1` kitchen, two articulated XLeRobots, dynamic task objects, and authoritative semantic markers in the Fleet Console without external runtime assets.

**Architecture:** A deterministic Python exporter converts only the active composed MJCF scene and the reusable XLeRobot visual tree into local GLB assets plus a versioned manifest. The existing `WorldSnapshot` remains the sole state authority; canonical joint telemetry drives a vendored Three.js renderer while the current Canvas renderer remains the fallback and semantic evidence layer.

**Tech Stack:** Go 1.24, Python 3.11, MuJoCo 3.3.x, RoboCasa 1.0.1, trimesh 4.12.2, pygltflib 1.16.5, Node 24, Three.js 0.180.0, esbuild 0.25.9, WebGL2, Go embed, Node test runner, pytest, browser acceptance.

**Spec:** `docs/superpowers/specs/2026-08-21-robocasa-webgl-digital-twin-design.md`

## Global Constraints

- The fixed first-stage scene is `robocasa-handoff-v1`; do not ship the complete RoboCasa asset library.
- GLB decides appearance only; `WorldSnapshot` decides pose, state, freshness, custody, and Harness evidence.
- The browser must not run a second MuJoCo simulation or infer task completion from animation.
- All JavaScript, GLB, textures, manifests, and decoders are served locally; runtime network requests to CDNs are forbidden.
- Preserve `default-src 'self'` and `script-src 'self'`; do not add `unsafe-eval` or a remote CSP source.
- Keep the existing Canvas world renderer as a working fallback for WebGL2, asset, hash, and GPU-context failures.
- A visual manifest is valid only when its `sceneId` and `modelHash` match the authoritative model identity.
- Canonical joint names are adapter-neutral and shared by simulation and future real XLeRobot observation adapters.
- Stale world state stops pose extrapolation; rendering interpolation never creates a new revision or freshness fact.
- Two browser robots share model geometry/material assets but own independent node transforms.
- `file://` pages must stop API retry loops and direct the user to `http://127.0.0.1:18080/`.
- Target local first interaction is at most 5 seconds. The automated controlled-browser gate requires signed full-quality steady renderer submission capacity >=50 FPS and retains actual display rAF plus tail latency without treating capacity as display cadence. An unthrottled visible-browser display rAF >=50 remains a separate real-hardware/browser acceptance item.
- Every task is test-first, preserves unrelated user changes, and ends with a focused commit.

## File Structure

- `sim/robocasa/tangying_robocasa/model_identity.py`: path-independent MJCF and asset-content identity.
- `sim/robocasa/tangying_robocasa/gltf_export.py`: MJCF visual-tree to GLB conversion primitives.
- `sim/robocasa/tangying_robocasa/visual_assets.py`: scene bundle, XLeRobot binding, manifest, hashes, and licenses.
- `scripts/export_robocasa_web_assets.py`: deterministic command-line export entry point.
- `web/assets/scenes/robocasa-handoff-v1/`: committed scene GLB, robot GLB, binding, manifest, and attribution.
- `web/src/asset_registry.js`: manifest validation and GLB loading.
- `web/src/robot_model.js`: articulated model instance and bounded interpolation.
- `web/src/semantic_overlay.js`: zones, bounds, task path, labels, selection, custody, and alerts.
- `web/src/interaction_controller.js`: pan, orbit, pointer-anchored zoom, presets, focus, and follow.
- `web/src/webgl_scene_renderer.js`: Three.js scene lifecycle and snapshot application.
- `web/src/webgl_entry.js`: browser global installation and Canvas fallback factory boundary.
- `web/webgl_scene.js`: committed esbuild browser bundle; no build tool is needed at runtime.
- `web/app.js`: application mode, authoritative world client, renderer selection, and `file://` guard.
- `web/world_view.js`: existing Canvas semantic fallback; keep its public interfaces stable.

---

### Task 1: Portable model identity for visual assets

**Files:**
- Create: `sim/robocasa/tangying_robocasa/model_identity.py`
- Create: `sim/robocasa/tests/test_model_identity.py`
- Modify: `sim/robocasa/tangying_robocasa/composer.py:350-370`
- Modify: `sim/robocasa/tests/test_composer.py`

**Interfaces:**
- Consumes: an expanded MJCF `xml.etree.ElementTree.Element` whose asset `file` values may be absolute.
- Produces: `model_content_hash(root: ET.Element) -> str`, a 64-character SHA-256 independent of checkout path but sensitive to XML and referenced file bytes.
- Keeps: `ComposedScene.xml` compile-ready with absolute asset paths; only `ComposedScene.model_hash` changes to the portable identity.

- [ ] **Step 1: Write failing path-independence and content-sensitivity tests**

```python
def test_model_hash_is_path_independent_and_asset_sensitive(tmp_path):
    left = tmp_path / "left" / "mesh.stl"
    right = tmp_path / "right" / "mesh.stl"
    left.parent.mkdir(); right.parent.mkdir()
    left.write_bytes(b"same-mesh"); right.write_bytes(b"same-mesh")
    assert model_content_hash(mjcf_with_mesh(left)) == model_content_hash(mjcf_with_mesh(right))
    right.write_bytes(b"changed-mesh")
    assert model_content_hash(mjcf_with_mesh(left)) != model_content_hash(mjcf_with_mesh(right))
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_model_identity.py`

Expected: FAIL because `tangying_robocasa.model_identity` does not exist.

- [ ] **Step 3: Implement canonical hashing without mutating the compile tree**

```python
def model_content_hash(root: ET.Element) -> str:
    canonical = copy.deepcopy(root)
    for element in canonical.iter():
        file_name = element.get("file")
        if file_name:
            asset = Path(file_name)
            element.set("file", f"sha256:{hashlib.sha256(asset.read_bytes()).hexdigest()}")
    payload = ET.tostring(canonical, encoding="utf-8", short_empty_elements=True)
    return hashlib.sha256(payload).hexdigest()
```

Use the new function in both `compose_handoff_scene` and `compose_fixture_scene_for_test`. Raise `FileNotFoundError` with the missing absolute path rather than hashing an unresolved reference.

- [ ] **Step 4: Run identity and composition tests**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_model_identity.py sim/robocasa/tests/test_composer.py`

Expected: PASS; moving the same fixture assets between temporary roots preserves `model_hash`, changing bytes changes it, and both composed scenes compile.

- [ ] **Step 5: Commit**

```bash
git add sim/robocasa/tangying_robocasa/model_identity.py sim/robocasa/tangying_robocasa/composer.py sim/robocasa/tests/test_model_identity.py sim/robocasa/tests/test_composer.py
git commit -m "fix: make RoboCasa model identity path independent"
```

### Task 2: Canonical articulated robot observation state

**Files:**
- Modify: `sim/robocasa/tangying_robocasa/world.py:541-570`
- Modify: `sim/mujoco/tangying_sim/world.py:280-295`
- Modify: `sim/robocasa/tests/test_world.py`
- Modify: `sim/mujoco/tests/test_world.py`
- Modify: `edge/worker/telemetry.go:82-110,248-275`
- Modify: `edge/worker/telemetry_test.go:113-135`
- Modify: `core/worldmodel/projector_test.go`

**Interfaces:**
- Consumes: Runtime `robot_state.joint_positions` maps with MuJoCo names such as `robot-1__Rotation_L` or `Rotation_L`.
- Produces: `canonicalJointState(state map[string]any, robotID string) map[string]float64` and `WorldSnapshot.robots[id].state` keys `joint.left.rotation` through `joint.head.tilt`.
- Preserves: reward, pick count, step count, and verification confidence scalar state.

- [ ] **Step 1: Write failing Python tests for arm and head joint observations**

```python
def test_joint_positions_include_articulated_arms_and_head(world):
    positions = world.joint_positions()
    assert len([name for name in positions if "Rotation_" in name or "Pitch_" in name or "Elbow_" in name or "Wrist_" in name or "Jaw_" in name]) == 12
    assert any(name.endswith("head_pan_joint") for name in positions)
    assert any(name.endswith("head_tilt_joint") for name in positions)
```

If the legacy tabletop model lacks a head joint, assert only its 12 arm keys there; RoboCasa must expose all 14 canonical inputs.

- [ ] **Step 2: Write failing Go normalization and projection tests**

```go
func TestNumericRobotStateCanonicalizesJoints(t *testing.T) {
    got := numericRobotState(map[string]any{"joint_positions": map[string]any{
        "robot-1__Rotation_L": 0.25,
        "robot-1__Jaw_R": 0.8,
        "robot-1__head_pan_joint": -0.1,
    }}, "robot-1")
    if got["joint.left.rotation"] != 0.25 || got["joint.right.jaw"] != 0.8 || got["joint.head.pan"] != -0.1 {
        t.Fatalf("canonical joints = %#v", got)
    }
}
```

Add a projector round-trip assertion that `joint.left.rotation` remains present after `RobotStateUpsert` and snapshot cloning.

- [ ] **Step 3: Run tests and verify the missing joint state fails**

Run: `go test ./edge/worker ./core/worldmodel`

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_world.py sim/mujoco/tests/test_world.py`

Expected: FAIL because head joints are absent and `numericRobotState` has no `robotID` argument or canonical nested-map handling.

- [ ] **Step 4: Implement the explicit adapter-neutral alias table**

```go
var canonicalJointNames = map[string]string{
    "Rotation_L": "joint.left.rotation", "Pitch_L": "joint.left.pitch",
    "Elbow_L": "joint.left.elbow", "Wrist_Pitch_L": "joint.left.wrist_pitch",
    "Wrist_Roll_L": "joint.left.wrist_roll", "Jaw_L": "joint.left.jaw",
    "Rotation_R": "joint.right.rotation", "Pitch_R": "joint.right.pitch",
    "Elbow_R": "joint.right.elbow", "Wrist_Pitch_R": "joint.right.wrist_pitch",
    "Wrist_Roll_R": "joint.right.wrist_roll", "Jaw_R": "joint.right.jaw",
    "head_pan_joint": "joint.head.pan", "head_tilt_joint": "joint.head.tilt",
}
```

Strip exactly one `${robotID}__` prefix, accept `map[string]any` and `map[string]float64`, keep finite values only, and change `sampleFromTelemetry` to call `numericRobotState(snapshot.RobotState, w.config.RobotID)`.

- [ ] **Step 5: Publish head joints from RoboCasa when present**

```python
for stem in ("head_pan_joint", "head_tilt_joint"):
    name = f"{self.robot_id}__{stem}"
    joint_id = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT, name)
    if joint_id >= 0:
        positions[name] = float(self.data.qpos[int(self.model.jnt_qposadr[joint_id])])
```

Use the same guarded logic without the robot prefix in `tangying_sim.world`.

- [ ] **Step 6: Run the focused contract suite**

Run: `gofmt -w edge/worker/telemetry.go edge/worker/telemetry_test.go core/worldmodel/projector_test.go && go test ./edge/worker ./core/worldmodel`

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_world.py sim/mujoco/tests/test_world.py`

Expected: PASS; the WorldSnapshot JSON contains finite canonical joint values and no MuJoCo-prefixed private names.

- [ ] **Step 7: Commit**

```bash
git add edge/worker/telemetry.go edge/worker/telemetry_test.go core/worldmodel/projector_test.go sim/robocasa/tangying_robocasa/world.py sim/robocasa/tests/test_world.py sim/mujoco/tangying_sim/world.py sim/mujoco/tests/test_world.py
git commit -m "feat: publish canonical XLeRobot joint state"
```

### Task 3: Reusable XLeRobot GLB and joint binding export

**Files:**
- Modify: `pyproject.toml`
- Modify: `scripts/setup-robocasa.sh`
- Modify: `tests/install/test_robocasa_setup.py`
- Create: `sim/robocasa/tangying_robocasa/gltf_export.py`
- Create: `sim/robocasa/tests/test_gltf_export.py`

**Interfaces:**
- Consumes: an MJCF root plus resolved mesh and texture files.
- Produces: `export_mjcf_visual(root: ET.Element, output: Path, body_filter: Callable[[str], bool]) -> ExportedGLB`.
- Produces: `build_xlerobot_binding(root: ET.Element) -> dict[str, JointBinding]` where each binding contains `node`, `axis`, `direction`, `offset`, `minimum`, and `maximum`.

- [ ] **Step 1: Add failing dependency and exporter contract tests**

```python
def test_visual_extra_is_isolated_and_pinned():
    project = tomllib.loads(Path("pyproject.toml").read_text())
    assert project["project"]["optional-dependencies"]["visual"] == [
        "trimesh==4.12.2", "pygltflib==1.16.5"
    ]

def test_export_keeps_named_joint_nodes_and_embeds_buffers(tmp_path):
    exported = export_mjcf_visual(articulated_fixture(), tmp_path / "robot.glb", lambda _name: True)
    gltf = GLTF2().load_binary(str(exported.path))
    assert {node.name for node in gltf.nodes} >= {"chassis", "Rotation_L", "Upper_Arm"}
    assert all(not buffer.uri for buffer in gltf.buffers)
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.venv/bin/pytest -q tests/install/test_robocasa_setup.py sim/robocasa/tests/test_gltf_export.py`

Expected: FAIL because the visual extra and exporter do not exist.

- [ ] **Step 3: Pin the isolated exporter dependencies**

Append this exact key to the existing `[project.optional-dependencies]` table:

```toml
visual = [
  "trimesh==4.12.2",
  "pygltflib==1.16.5",
]
```

Change the RoboCasa environment validity check to import `trimesh` and `pygltflib`, and install `-e "$PROJECT_ROOT[visual]"`. Do not install these packages into the main `.venv` during normal AgentOS setup.

Run: `bash scripts/setup-robocasa.sh`

Expected: the existing `tangying-robocasa` environment is updated in place and `conda run -n tangying-robocasa python -c 'import trimesh, pygltflib'` exits zero.

- [ ] **Step 4: Implement MJCF visual traversal and GLB export**

Use a `trimesh.Scene` graph. Convert `box`, `sphere`, `cylinder`, `capsule`, `plane`, and `mesh` geoms; apply body/geom `pos`, `quat`, mesh `scale`, and RGBA/material properties; skip invisible alpha-zero and collision-only groups. Export one binary GLB with embedded buffers and images:

```python
blob = trimesh.exchange.gltf.export_glb(scene, include_normals=True)
output.write_bytes(blob)
return ExportedGLB(path=output, sha256=hashlib.sha256(blob).hexdigest(), node_names=tuple(sorted(nodes)))
```

- [ ] **Step 5: Implement the complete canonical binding table**

```python
CANONICAL_JOINTS = {
    "joint.left.rotation": "Rotation_L", "joint.left.pitch": "Pitch_L",
    "joint.left.elbow": "Elbow_L", "joint.left.wrist_pitch": "Wrist_Pitch_L",
    "joint.left.wrist_roll": "Wrist_Roll_L", "joint.left.jaw": "Jaw_L",
    "joint.right.rotation": "Rotation_R", "joint.right.pitch": "Pitch_R",
    "joint.right.elbow": "Elbow_R", "joint.right.wrist_pitch": "Wrist_Pitch_R",
    "joint.right.wrist_roll": "Wrist_Roll_R", "joint.right.jaw": "Jaw_R",
    "joint.head.pan": "head_pan_joint", "joint.head.tilt": "head_tilt_joint",
}
```

Read axis and range from MJCF and record the body node owning each joint. Fail export if any of the 12 arm bindings is absent; head bindings are required for the pinned RoboCasa XLeRobot model.

- [ ] **Step 6: Run synthetic and real XLeRobot export tests**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_gltf_export.py`

Expected: PASS; GLB parses, contains no external buffer/image URI, includes the complete visual hierarchy, and binding keys match the canonical telemetry keys.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml scripts/setup-robocasa.sh tests/install/test_robocasa_setup.py sim/robocasa/tangying_robocasa/gltf_export.py sim/robocasa/tests/test_gltf_export.py
git commit -m "feat: export articulated XLeRobot GLB"
```

### Task 4: Complete RoboCasa scene bundle and manifest

**Files:**
- Create: `sim/robocasa/tangying_robocasa/visual_assets.py`
- Create: `sim/robocasa/tests/test_visual_assets.py`
- Create: `scripts/export_robocasa_web_assets.py`
- Modify: `Makefile`
- Create: `web/assets/scenes/robocasa-handoff-v1/scene.glb`
- Create: `web/assets/scenes/robocasa-handoff-v1/xlerobot.glb`
- Create: `web/assets/scenes/robocasa-handoff-v1/xlerobot.binding.json`
- Create: `web/assets/scenes/robocasa-handoff-v1/manifest.json`
- Create: `web/assets/scenes/robocasa-handoff-v1/LICENSE-XLeRobot`
- Create: `web/assets/scenes/robocasa-handoff-v1/PROVENANCE-XLeRobot.md`
- Create: `web/assets/scenes/robocasa-handoff-v1/LICENSE-RoboCasa`

**Interfaces:**
- Consumes: `ComposedScene`, the unprefixed XLeRobot MJCF, and the active RoboCasa visual assets.
- Produces: `export_visual_bundle(scene: ComposedScene, output_dir: Path) -> VisualAssetManifest` using schema `tangying.visual-asset.v1`.
- Produces: `make robocasa-web-assets` and stable exact output paths under the fixed scene directory.

- [ ] **Step 1: Write failing bundle tests**

```python
def test_bundle_excludes_dynamic_bodies_and_matches_model_hash(tmp_path, composed_scene):
    manifest = export_visual_bundle(composed_scene, tmp_path)
    assert manifest.schema_version == "tangying.visual-asset.v1"
    assert manifest.scene_id == "robocasa-handoff-v1"
    assert manifest.model_hash == composed_scene.model_hash
    scene = GLTF2().load_binary(str(tmp_path / "scene.glb"))
    names = {node.name for node in scene.nodes}
    assert "robot-1__chassis" not in names
    assert "robot-2__chassis" not in names
    assert "red-block" not in names
    assert {"scene.glb", "xlerobot.glb", "xlerobot.binding.json"} <= set(manifest.content_hashes)
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_visual_assets.py`

Expected: FAIL because `visual_assets.py` does not exist.

- [ ] **Step 3: Implement static-scene selection and manifest validation**

Exclude every body below `robot-1__chassis`, `robot-2__chassis`, and `red-block`. Include active RoboCasa fixture visuals, floor, walls, lights represented by browser lights, and primitive scene geometry. Write JSON with sorted keys and compact separators:

```python
manifest = {
    "schemaVersion": "tangying.visual-asset.v1",
    "sceneId": scene.scene_id,
    "modelHash": scene.model_hash,
    "worldFrame": "world",
    "upAxis": "Z",
    "units": "meter",
    "sceneAsset": with_hash_query("scene.glb", scene_sha),
    "robotModels": {"xlerobot": {
        "asset": with_hash_query("xlerobot.glb", robot_sha),
        "binding": with_hash_query("xlerobot.binding.json", binding_sha),
    }},
    "contentHashes": hashes,
    "licenses": ["LICENSE-XLeRobot", "PROVENANCE-XLeRobot.md", "LICENSE-RoboCasa"],
}
```

`with_hash_query("scene.glb", sha)` returns the literal filename, `?v=`, and the supplied 64-character lowercase SHA-256, so stable paths receive immutable caching only when content-addressed by query.

- [ ] **Step 4: Implement the CLI and Make target**

```python
def main() -> int:
    scene = compose_handoff_scene()
    manifest = export_visual_bundle(scene, Path("web/assets/scenes/robocasa-handoff-v1"))
    print(json.dumps({"sceneId": manifest.scene_id, "modelHash": manifest.model_hash}, sort_keys=True))
    return 0
```

Add `robocasa-web-assets` to `Makefile` using `conda run --no-capture-output -n tangying-robocasa`.

- [ ] **Step 5: Generate and validate the committed first-stage assets**

Run: `make robocasa-web-assets`

Run: `conda run -n tangying-robocasa python -m pytest -q sim/robocasa/tests/test_visual_assets.py`

Expected: all seven output files exist; GLBs parse; the scene contains real fixture mesh nodes; manifest hashes match bytes; repeated export leaves identical hashes and `git diff --exit-code` for generated assets.

- [ ] **Step 6: Commit**

```bash
git add Makefile scripts/export_robocasa_web_assets.py sim/robocasa/tangying_robocasa/visual_assets.py sim/robocasa/tests/test_visual_assets.py web/assets/scenes/robocasa-handoff-v1
git commit -m "feat: generate RoboCasa browser scene assets"
```

### Task 5: Offline static asset publication and security

**Files:**
- Modify: `web/embed.go`
- Create: `web/embed_test.go`
- Modify: `fleet/auth/auth.go:347-355`
- Modify: `fleet/auth/auth_test.go`
- Modify: `fleet/server_test.go`
- Modify: `console/server_test.go`

**Interfaces:**
- Consumes: the fixed `web/assets` directory and asset URLs whose `v` query is a 64-character lowercase SHA-256.
- Produces: public read-only `/assets/` responses with correct MIME, cache, CSP, and authentication behavior.
- Preserves: authenticated API routes and existing public `index.html`, scripts, and styles.

- [ ] **Step 1: Write failing embed, MIME, cache, and auth tests**

```go
func TestVersionedGLBIsEmbeddedAndImmutable(t *testing.T) {
    request := httptest.NewRequest(http.MethodGet, "/assets/scenes/robocasa-handoff-v1/xlerobot.glb?v="+strings.Repeat("a", 64), nil)
    recorder := httptest.NewRecorder()
    Handler().ServeHTTP(recorder, request)
    if recorder.Code != http.StatusOK || recorder.Header().Get("Content-Type") != "model/gltf-binary" {
        t.Fatalf("status=%d content-type=%q", recorder.Code, recorder.Header().Get("Content-Type"))
    }
    if !strings.Contains(recorder.Header().Get("Cache-Control"), "immutable") {
        t.Fatalf("cache=%q", recorder.Header().Get("Cache-Control"))
    }
}
```

Add Fleet auth assertions that `/assets/scenes/.../manifest.json` is public but `/assets/../v1/tasks` is not treated as a static asset.

- [ ] **Step 2: Run tests and verify failure**

Run: `go test ./web ./fleet/auth ./fleet ./console`

Expected: FAIL because assets are not embedded/whitelisted and no immutable cache policy exists.

- [ ] **Step 3: Embed the complete local static package and add cache policy**

```go
//go:embed index.html app.js world_view.js webgl_scene.js styles.css assets
var assets embed.FS

func assetCacheControl(r *http.Request) string {
    if strings.HasPrefix(r.URL.Path, "/assets/") && sha256Pattern.MatchString(r.URL.Query().Get("v")) {
        return "public, max-age=31536000, immutable"
    }
    if strings.HasSuffix(r.URL.Path, "/manifest.json") {
        return "no-cache"
    }
    return "no-cache"
}
```

Set `.glb` to `model/gltf-binary` before serving. Reject non-clean asset paths with `http.StatusNotFound`. Extend `isStaticAsset` only for clean paths beginning `/assets/` and for `/webgl_scene.js`.

- [ ] **Step 4: Run static and security tests**

Run: `gofmt -w web/embed.go web/embed_test.go fleet/auth/auth.go fleet/auth/auth_test.go fleet/server_test.go console/server_test.go && go test ./web ./fleet/auth ./fleet ./console`

Expected: PASS; manifest is no-cache, hash-query GLB is immutable, CSP remains self-only, API routes still require auth, and traversal is rejected.

- [ ] **Step 5: Commit**

```bash
git add web/embed.go web/embed_test.go fleet/auth/auth.go fleet/auth/auth_test.go fleet/server_test.go console/server_test.go
git commit -m "feat: serve offline visual assets securely"
```

### Task 6: WebGL asset registry and articulated robot renderer

**Files:**
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/build.mjs`
- Create: `web/src/asset_registry.js`
- Create: `web/src/robot_model.js`
- Create: `web/src/webgl_scene_renderer.js`
- Create: `web/src/webgl_entry.js`
- Create: `web/webgl_asset_registry_test.mjs`
- Create: `web/webgl_robot_test.mjs`
- Create: `web/webgl_scene_test.mjs`
- Create: `web/webgl_scene.js`

**Interfaces:**
- Consumes: a matching visual manifest, GLTFLoader results, and authoritative snapshots.
- Produces: asynchronous `AssetRegistry.load(snapshot)` returning a `LoadedVisualBundle` object.
- Produces: `RobotModelInstance.applyState(robot, receivedAtMs)` and `sample(nowMs)` with finite interpolation.
- Produces: `WebGLSceneRenderer.create(canvas, options)`, `render(snapshot)`, `pick(x,y)`, `focus(entity)`, `dispose()`, and `status`.

- [ ] **Step 1: Pin the exact browser build dependencies**

```json
{
  "private": true,
  "type": "module",
  "scripts": {
    "build": "node build.mjs",
    "test": "node --test *_test.mjs"
  },
  "dependencies": {"three": "0.180.0"},
  "devDependencies": {"esbuild": "0.25.9"}
}
```

Run: `cd web && npm install --package-lock-only && npm ci`

Expected: lockfile resolves the exact pinned versions.

- [ ] **Step 2: Write failing manifest selection tests**

```javascript
test("asset registry rejects an authoritative model mismatch", async () => {
  const registry = new AssetRegistry({ fetchJSON: async () => manifest("hash-a"), loadGLB: async () => ({}) });
  await assert.rejects(() => registry.load(snapshot("hash-b")), /VISUAL_MODEL_MISMATCH/);
});
```

Also assert manifest schema, fixed scene ID, 64-hex content hash, local relative URL, and one-fetch cache behavior.

- [ ] **Step 3: Write failing independent clone and interpolation tests**

```javascript
test("two robot instances share geometry and move joints independently", () => {
  const first = RobotModelInstance.fromTemplate(template, binding, "robot-1");
  const second = RobotModelInstance.fromTemplate(template, binding, "robot-2");
  assert.equal(first.node("Upper_Arm").geometry, second.node("Upper_Arm").geometry);
  first.applyState(robotState({"joint.left.pitch": 0.8}), 1000);
  second.applyState(robotState({"joint.left.pitch": -0.2}), 1000);
  assert.notEqual(first.sample(1100).joints["joint.left.pitch"], second.sample(1100).joints["joint.left.pitch"]);
});
```

Assert interpolation clamps at the latest target, rejects NaN, and freezes immediately when freshness is not `FRESH`.

- [ ] **Step 4: Run Node tests and verify failure**

Run: `cd web && npm test`

Expected: FAIL because registry and renderer modules do not exist.

- [ ] **Step 5: Implement registry, robot instances, and Three scene lifecycle**

`AssetRegistry` extracts model identity from entity attributes, resolves all manifest-relative URLs through `new URL(relative, manifestURL)`, and rejects non-local origins. `RobotModelInstance` deep-clones node transforms while sharing geometry/materials, maps canonical joint values through binding axis/direction/offset/range, and visually marks stale/emergency states.

`WebGLSceneRenderer` creates a Z-up `THREE.Scene`, perspective camera, WebGLRenderer with capped device pixel ratio, hemisphere/key lights, shadow map, static scene root, robot root, and dynamic object root. Applying snapshot revision N replaces only dynamic transforms/state; it never mutates the snapshot.

- [ ] **Step 6: Build a self-contained classic-script bundle**

```javascript
await build({
  entryPoints: ["src/webgl_entry.js"],
  outfile: "webgl_scene.js",
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  legalComments: "eof",
});
```

`webgl_entry.js` assigns `globalThis.TangyingWebGL = { AssetRegistry, RobotModelInstance, WebGLSceneRenderer }`. The bundle must contain no runtime `import`, remote URL, eval, or source map reference.

- [ ] **Step 7: Run tests and deterministic bundle checks**

Run: `cd web && npm run build && npm test && shasum -a 256 webgl_scene.js`

Run `npm run build` a second time and assert `git diff --exit-code -- webgl_scene.js` after staging the first generated result.

Expected: tests pass; bundle is deterministic and locally self-contained.

- [ ] **Step 8: Commit**

```bash
git add web/package.json web/package-lock.json web/build.mjs web/src web/webgl_*_test.mjs web/webgl_scene.js
git commit -m "feat: render articulated XLeRobots with local WebGL"
```

### Task 7: Semantic overlays and production camera interaction

**Files:**
- Create: `web/src/semantic_overlay.js`
- Create: `web/src/interaction_controller.js`
- Modify: `web/src/webgl_scene_renderer.js`
- Create: `web/webgl_semantic_overlay_test.mjs`
- Create: `web/webgl_interaction_test.mjs`
- Modify: `web/webgl_scene_test.mjs`
- Modify: `web/world_view_test.mjs`
- Modify: `web/webgl_scene.js`

**Interfaces:**
- Consumes: world entities/resources, the existing handoff path semantics, pointer events, and persisted camera JSON.
- Produces: `SemanticOverlay.apply(snapshot, visibility)` and `InteractionController.bind(canvas, renderer)`.
- Preserves: current `WorldCamera` pan/orbit/zoom/focus semantics and local-storage camera shape.

- [ ] **Step 1: Write failing overlay tests for the complete evidence layer**

```javascript
test("semantic overlay keeps models and authoritative markers together", () => {
  const overlay = new SemanticOverlay(fakeThree());
  overlay.apply(handoffSnapshot(), { models: true, bounds: true, labels: true, path: true });
  assert.deepEqual(overlay.zoneIds(), ["left-start-zone", "handoff-zone", "right-target-zone"]);
  assert.equal(overlay.bound("robot-1").visible, true);
  assert.equal(overlay.label("robot-2").text.includes("FRESH"), true);
  assert.equal(overlay.custody().owner, "environment");
});
```

Add cases for selected fixtures, held object, task stage color, stale desaturation, emergency outline, and robot/entity custody conflict.

- [ ] **Step 2: Write failing interaction tests**

Assert left drag changes target but not yaw, right drag changes yaw/pitch but not target, wheel preserves the world point under the pointer, double-click focuses the picked entity, F resets overview, pointer capture is released, and context menu is prevented.

- [ ] **Step 3: Run tests and verify failure**

Run: `cd web && npm test`

Expected: FAIL because semantic and interaction modules do not exist.

- [ ] **Step 4: Implement overlays as a separate Three/DOM layer**

Create transparent zone planes, task-path lines, AABB line segments, dynamic red-block mesh/outline, robot selection bounds, and DOM labels projected from world position each animation frame. Ordinary fixture labels are distance-culled; selected objects and robot status remain visible. `setVisibility({models,bounds,labels,path})` toggles layers independently without changing world data.

- [ ] **Step 5: Implement interaction by adapting the existing camera contract**

Use pointer capture and the same `WorldCamera.drag`, `zoomAt`, `applyPreset`, and persisted `toJSON` behavior. Convert that camera basis into the Three perspective camera every frame. Follow mode updates only target from fresh robot pose; user pan/orbit cancels follow.

- [ ] **Step 6: Rebuild and run all world-view tests**

Run: `cd web && npm run build && npm test && node --test world_view_test.mjs app_test.mjs`

Expected: PASS; existing interaction tests remain valid and new WebGL overlays expose the same authoritative task state.

- [ ] **Step 7: Commit**

```bash
git add web/src/semantic_overlay.js web/src/interaction_controller.js web/src/webgl_scene_renderer.js web/webgl_semantic_overlay_test.mjs web/webgl_interaction_test.mjs web/webgl_scene_test.mjs web/world_view_test.mjs web/webgl_scene.js
git commit -m "feat: overlay authoritative world evidence on WebGL"
```

### Task 8: Console integration, visual state, fallback, and file entry repair

**Files:**
- Modify: `web/index.html`
- Modify: `web/styles.css`
- Modify: `web/app.js`
- Modify: `web/app_test.mjs`
- Modify: `web/observability_test.go`
- Modify: `web/embed.go`
- Modify: `web/world_view.js`

**Interfaces:**
- Consumes: `globalThis.TangyingWebGL`, `WorldRealtimeClient`, current Fleet auth, and `file:` or HTTP location.
- Produces: separate `WORLD LIVE/STALE` and `VISUAL LOADING/LIVE/DEGRADED` status, model/bounds/labels/path toggles, retry, and correct fallback.
- Produces: `renderServiceRequired(url)` and a boot result of `"file"` without API polling for raw HTML.

- [ ] **Step 1: Write failing raw-file and visual-state application tests**

```javascript
test("file pages explain the service entry and make no API request", async () => {
  const harness = createHarness({ protocol: "file:" });
  await harness.hooks.bootApplication();
  assert.equal(harness.fetches.length, 0);
  assert.match(harness.elements.get("scene-frame-message").textContent, /127\.0\.0\.1:18080/);
  assert.equal(harness.elements.get("open-service-console").href, "http://127.0.0.1:18080/");
});
```

Add tests that matching assets choose WebGL, mismatch/context loss selects Canvas, world LIVE is unchanged by visual degradation, and all four toggles update `aria-pressed`.

- [ ] **Step 2: Run tests and verify failure**

Run: `cd web && node --test app_test.mjs && cd .. && go test ./web`

Expected: FAIL because the file guard, WebGL canvas, visual status, and toolbar controls are absent.

- [ ] **Step 3: Add the layered scene markup and status controls**

Add `fleet-godview-webgl` as the primary canvas, retain `fleet-godview-canvas` as semantic fallback, add an accessible DOM label layer, `fleet-visual-state`, retry button, and toolbar buttons `fleet-world-models-toggle`, `fleet-world-fixtures-toggle`, `fleet-world-labels-toggle`, and `fleet-world-path-toggle`. Load `/webgl_scene.js` before `/world_view.js` and `/app.js` as local deferred scripts.

- [ ] **Step 4: Integrate progressive renderer selection**

```javascript
async function createFleetWorldRenderer() {
  try {
    const renderer = await globalThis.TangyingWebGL.WebGLSceneRenderer.create($("#fleet-godview-webgl"), {
      manifestURL: "/assets/scenes/robocasa-handoff-v1/manifest.json",
      fallback: fleetWorldCanvasRenderer,
    });
    setFleetVisualState("LIVE");
    return renderer;
  } catch (error) {
    setFleetVisualState("DEGRADED", stableVisualError(error));
    return fleetWorldCanvasRenderer;
  }
}
```

Keep the realtime client alive while visual assets load. On context loss or mismatch, show the Canvas immediately; retry creates a new WebGL renderer from the latest snapshot. Do not reconnect the world socket for a visual-only error.

- [ ] **Step 5: Stop raw-file network loops and show the correct entry**

At the first line of `bootApplication`, return `"file"` after `renderServiceRequired("http://127.0.0.1:18080/")` when `location.protocol === "file:"`. The helper must unhide a real anchor with `target="_blank"`, explain that Runtime/Fleet data is served over HTTP, and mark connection state `SERVICE REQUIRED` rather than `UNAVAILABLE`.

- [ ] **Step 6: Apply intentional visual styling and responsive behavior**

Use one dark operations-stage surface, restrained cyan robot identity accents, amber task/custody accents, red fault outlines, physically legible lighting, a compact top-left toolbar, and a right evidence rail. Labels must not cover the complete kitchen at overview distance. At widths below 760px, keep the 3D stage at least 220px high and move status controls below it.

- [ ] **Step 7: Run frontend, embed, CSP, and regression tests**

Run: `cd web && npm run build && npm test && node --test world_view_test.mjs app_test.mjs && cd .. && go test ./web ./fleet/auth ./fleet ./console`

Expected: PASS; no test performs an external request, raw-file mode performs zero fetches, and Canvas fallback remains fully interactive.

- [ ] **Step 8: Commit**

```bash
git add web/index.html web/styles.css web/app.js web/app_test.mjs web/observability_test.go web/embed.go web/world_view.js web/webgl_scene.js
git commit -m "feat: integrate complete WebGL world into Console"
```

### Task 9: Real RoboCasa task, failure acceptance, performance, and visual evidence

**Files:**
- Create: `tests/e2e/test_robocasa_visual_twin.py`
- Modify: `tests/e2e/test_robocasa_handoff.py`
- Modify: `tests/e2e/robocasa_harness.py`
- Modify: `scripts/run_robocasa_harness.py`
- Modify: `docs/robocasa-handoff.md`
- Modify: `docs/user-console.md`
- Modify: `README.md`

**Interfaces:**
- Consumes: the running RoboCasa Fleet stack, `/v1/world`, browser Console, generated visual manifest, and the Chinese handoff request.
- Produces: automated state/asset assertions and browser screenshots under `artifacts/robocasa-harness/manual/visual/`.
- Produces: operational commands for service URL, asset rebuild, offline verification, and real-hardware visual registration.

- [ ] **Step 1: Write failing end-to-end state and asset identity tests**

```python
def test_visual_manifest_matches_live_world_and_joints_move(robocasa_stack):
    initial = robocasa_stack.api("/v1/world")
    manifest = robocasa_stack.public_json("/assets/scenes/robocasa-handoff-v1/manifest.json")
    model_hash = next(e["attributes"]["model_hash"] for e in initial["entities"].values() if "model_hash" in e.get("attributes", {}))
    assert manifest["modelHash"] == model_hash
    task_id = robocasa_stack.create_and_approve()
    moving = robocasa_stack.wait_world(lambda world: any(abs(v) > 1e-3 for robot in world["robots"].values() for k, v in robot.get("state", {}).items() if k.startswith("joint.")))
    assert moving["revision"] > initial["revision"]
    assert robocasa_stack.wait_task(task_id)["state"] == "SUCCEEDED"
```

Also fetch both GLBs without auth, check declared content hash, and assert no asset URL has a non-local origin.

Add this explicit helper to `RoboCasaHandoffStack` so the test does not reuse operator credentials for public assets:

```python
def public_json(self, path: str) -> dict[str, object]:
    with urllib.request.urlopen(f"{self.base_url}{path}", timeout=5) as response:
        return json.load(response)
```

- [ ] **Step 2: Run the test and verify failure before the completed integration**

Run: `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q tests/e2e/test_robocasa_visual_twin.py`

Expected: FAIL until the live server exposes matching assets and canonical joint state.

- [ ] **Step 3: Extend acceptance artifacts with visual identity and browser checklist**

Write `visual-manifest.json`, `visual-network.json`, `visual-performance.json`, and screenshot paths into the existing run summary. The pass predicate requires matching model hash, both robot IDs, at least 12 finite canonical joints per robot, no external asset origin, final `right-target-zone`, resource owner `environment`, and Harness `SATISFIED` for both intents.

- [ ] **Step 4: Run complete automated verification**

Run: `make robocasa-web-assets`

Run: `cd web && npm ci && npm run build && npm test && node --test world_view_test.mjs app_test.mjs`

Run: `go test ./...`

Run: `PYTHONNOUSERSITE=1 conda run --no-capture-output -n tangying-robocasa pytest -q sim/robocasa/tests tests/e2e/test_robocasa_handoff.py tests/e2e/test_robocasa_faults.py tests/e2e/test_robocasa_visual_twin.py`

Expected: all suites PASS and a second `make robocasa-web-assets` produces no tracked diff.

- [ ] **Step 5: Perform real browser acceptance at the served URL**

Start: `bash scripts/robocasa-fleet.sh start`

Open: `http://127.0.0.1:18080/`

Verify in the browser, against the current `/v1/world` response:

1. the complete kitchen meshes/materials are visible instead of only AABBs;
2. two complete XLeRobot models are visible and their arm/gripper poses change during the Chinese handoff;
3. red block, three zones, path stage, owner/token, freshness, activity, held, and Harness verdict agree with the API;
4. left pan, right orbit, pointer-anchored wheel zoom, overview/top/R1/R2 presets, follow, select, double-click focus, F reset, and refresh restore work;
5. models, bounds, labels, and path toggles act independently;
6. forced asset failure shows `WORLD LIVE / VISUAL DEGRADED` and usable Canvas fallback;
7. opening `file:///Users/wanglian/Projects/tangying-robot-agent-os/web/index.html` shows the HTTP service link without repeated fetch failures;
8. the Network panel contains no request to an external origin;
9. local first interaction is within 5 seconds; signed full-quality steady renderer submission capacity is at least 50 FPS; actual display rAF cadence and tails are reported separately. Do not infer or claim display rAF >=50 from submission capacity.

Save screenshots as `overview.png`, `robot-1.png`, `robot-2.png`, `handoff-final.png`, and `fallback.png` under `artifacts/robocasa-harness/manual/visual/`.

- [ ] **Step 6: Document operations and real-hardware registration**

Document these exact commands and boundaries:

```bash
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
open http://127.0.0.1:18080/
```

Explain that raw `file://` is not a live Console, visual assets never prove physical success, real maps register the same manifest schema, real XLeRobot adapters publish canonical joint keys, and model-revision mismatch deliberately falls back to semantics.

- [ ] **Step 7: Stop the temporary stack and inspect final repository state**

Run: `bash scripts/robocasa-fleet.sh stop`

Run: `git status --short && git diff --check`

Expected: only the intended Task 9 documentation/test changes remain; no temporary server or browser acceptance process remains.

- [ ] **Step 8: Commit**

```bash
git add tests/e2e/test_robocasa_visual_twin.py tests/e2e/test_robocasa_handoff.py tests/e2e/robocasa_harness.py scripts/run_robocasa_harness.py docs/robocasa-handoff.md docs/user-console.md README.md
git commit -m "test: accept complete RoboCasa WebGL handoff"
```

### Task 10: Reproducible authenticated acceptance workflow

**Files:**
- Modify: `scripts/run_robocasa_harness.py`
- Create: `scripts/upload_robocasa_browser_capture.py`
- Modify: `tests/e2e/test_robocasa_visual_twin.py`
- Modify: `Makefile`
- Modify: `docs/robocasa-handoff.md`
- Modify: `README.md`
- Modify: this plan

**Recorded performance ruling:** The cadence-limited controlled Edge surface measured 33.8038 display rAF FPS at full quality, while the signed steady `renderer.render()` submission-capacity window measured 107.9176 FPS. The automated gate accepts the latter against >=50 FPS and always reports display cadence and mean/median/p90/p95/max durations. It must not claim controlled display rAF >=50. A visible, unthrottled real-machine browser sustaining display rAF >=50 is a separate acceptance gate. Cost: renderer submission capacity can overestimate displayed smoothness when GPU completion, compositor, automation, or display scheduling is the bottleneck.

- [ ] Add an atomic single-use receiver: after valid bearer and bounded length checks, reserve before reading the body; exactly one of eight concurrent valid POSTs is 201 and the others are 409. Release a failed reservation only if no capture file was committed.
- [ ] Split receive/write from runner finalization. Keep the ephemeral Ed25519 private key only in a temporary directory until the fail-closed summary exists; sign a canonical final attestation covering the summary hash, capture-envelope hash, and every retained file; destroy the private key immediately afterward.
- [ ] Add explicit `--candidate`, `--promote-anchor`, and `--revalidate` modes. Candidate uses a bounded nonzero browser wait and writes only an untrusted candidate anchor. Promotion recomputes every acceptance check and verifies every signed artifact before atomically replacing the tracked anchor. Revalidation starts no stack.
- [ ] Add `scripts/upload_robocasa_browser_capture.py`; require the runner-created 0600 session, enforce loopback identity, perform one POST without redirect/retry, handle HTTP errors, and never log the bearer.
- [ ] Make `make robocasa-acceptance` revalidate only pinned `artifacts/robocasa-harness/round3`; add separate candidate and promotion targets. No default path may build a new summary with zero browser wait.
- [ ] Verify the adversarial receiver/signature/anchor/workflow cases, the retained round3 pack, relevant Go/Web regressions, and `git diff --check` before the focused Task 10 commit.

## Final Verification Gate

- [ ] Run `git diff --check` and confirm the worktree contains no unintended files.
- [ ] Run `make test` and record the Go, Python, and Web totals.
- [ ] Run `make robocasa-acceptance` and confirm the pinned round3 summary remains `SUCCEEDED`, both Harness intents remain `SATISFIED`, and the final attestation/anchor revalidate without starting a new stack.
- [ ] Compare final screenshot state with `/v1/world`: both robot poses/joints, `red-block` placement, resource owner/token, freshness, task path stage, and final verdict must agree.
- [ ] Confirm all visual requests are same-origin and the Console remains usable with networking disabled after local assets are cached.
- [ ] Confirm `file://` mode points to the served URL and performs no API/WebSocket retry loop.
- [ ] Confirm `git log --oneline -10` shows one focused commit per task and the design/plan commits remain separate.
