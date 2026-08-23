import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { WebGLSceneRenderer } from "./src/webgl_scene_renderer.js";

class FakeCanvas {
  constructor() {
    this.width = 800;
    this.height = 400;
    this.clientWidth = 800;
    this.clientHeight = 400;
    this.dataset = {};
    this.listeners = new Map();
    this.rect = { left: 0, top: 0, width: 800, height: 400 };
  }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name, handler) {
    if (this.listeners.get(name) === handler) this.listeners.delete(name);
  }
  getBoundingClientRect() { return this.rect; }
  emit(name, event = {}) { this.listeners.get(name)?.(event); }
}

class FakeThreeRenderer {
  constructor() {
    this.shadowMap = {};
    this.calls = [];
    this.disposed = false;
    this.setPixelRatioCalls = 0;
    this.setSizeCalls = 0;
  }
  setPixelRatio(value) { this.pixelRatio = value; this.setPixelRatioCalls += 1; }
  setSize(width, height, updateStyle) { this.size = [width, height, updateStyle]; this.setSizeCalls += 1; }
  render(scene, camera) { this.calls.push([scene, camera]); }
  dispose() { this.disposed = true; }
}

function bundle() {
  const scene = new THREE.Group();
  scene.name = "Kitchen";
  scene.add(new THREE.Mesh(new THREE.BoxGeometry(2, 2, 0.1), new THREE.MeshStandardMaterial()));
  const robot = new THREE.Group();
  const pivot = new THREE.Group();
  pivot.name = "Upper_Arm";
  pivot.add(new THREE.Mesh(new THREE.BoxGeometry(0.2, 0.2, 0.4), new THREE.MeshStandardMaterial()));
  robot.add(pivot);
  return {
    scene,
    robotTemplate: robot,
    binding: {
      "joint.left.pitch": {
        node: "Upper_Arm", axis: [1, 0, 0], direction: 1,
        offset: 0, minimum: -1, maximum: 1,
      },
    },
    modelHash: "a".repeat(64),
  };
}

function snapshot(revision = 1) {
  return {
    revision,
    worldId: "robocasa-handoff-v1",
    robots: {
      "robot-1": {
        robotId: "robot-1", pose: [-0.5, 0, 0, 1, 0, 0, 0], freshness: "FRESH",
        state: { "joint.left.pitch": 0.4 }, emergencyStopped: false,
      },
      "robot-2": {
        robotId: "robot-2", pose: [0.5, 0, 0, 1, 0, 0, 0], freshness: "FRESH",
        state: { "joint.left.pitch": -0.4 }, emergencyStopped: false,
      },
    },
    entities: {
      kitchen: { entityId: "kitchen", attributes: { static: "true", model_hash: "a".repeat(64) } },
      "red-block": { entityId: "red-block", category: "block", pose: [0, 0, 0.2, 1, 0, 0, 0], freshness: "FRESH" },
    },
  };
}

function createHarness() {
  const canvas = new FakeCanvas();
  const gpu = new FakeThreeRenderer();
  const renderer = WebGLSceneRenderer.create(canvas, {
    bundle: bundle(),
    rendererFactory: () => gpu,
    devicePixelRatio: 4,
    pixelRatioCap: 2,
    autoStart: false,
    now: () => 1000,
  });
  return { canvas, gpu, renderer };
}

test("renderer creates a capped Z-up scene with separate lifecycle roots", () => {
  const { canvas, gpu, renderer } = createHarness();
  assert.deepEqual(renderer.scene.up.toArray(), [0, 0, 1]);
  assert.deepEqual(renderer.camera.up.toArray(), [0, 0, 1]);
  assert.ok(Math.abs(renderer.camera.fov - 2 * Math.atan(1 / 1.8) * 180 / Math.PI) < 1e-12);
  assert.equal(renderer.staticSceneRoot.parent, renderer.scene);
  assert.equal(renderer.robotRoot.parent, renderer.scene);
  assert.equal(renderer.dynamicObjectRoot.parent, renderer.scene);
  assert.equal(gpu.pixelRatio, 2);
  assert.deepEqual(gpu.size, [800, 400, false]);
  assert.equal(canvas.listeners.has("webglcontextlost"), true);
  assert.equal(renderer.status.state, "READY");
  renderer.render(snapshot(1), 1000);
  renderer.render(snapshot(2), 1100);
  assert.equal(gpu.setPixelRatioCalls, 1);
  assert.equal(gpu.setSizeCalls, 1);
});

test("renderer exposes a rolling steady-stage FPS measurement for browser acceptance", () => {
  const { canvas, renderer } = createHarness();
  const current = snapshot(1);
  for (let timestamp = 1000; timestamp <= 2020; timestamp += 20) {
    assert.equal(renderer.render(current, timestamp), true);
  }
  assert.ok(Number(canvas.dataset.steadyFps) >= 49);
  assert.ok(Number(canvas.dataset.steadyFps) <= 51);
});

test("renderer exposes raw GPU render durations from its monotonic clock", () => {
  const canvas = new FakeCanvas();
  const gpu = new FakeThreeRenderer();
  const clock = [100, 104, 200, 207, 300, 303];
  const renderer = WebGLSceneRenderer.create(canvas, {
    bundle: bundle(),
    rendererFactory: () => gpu,
    autoStart: false,
    now: () => clock.shift(),
  });

  renderer.render(snapshot(1), 1000);
  renderer.render(snapshot(1), 1020);
  renderer.render(snapshot(1), 1040);

  assert.deepEqual(JSON.parse(canvas.dataset.renderDurationSamples), [4, 7, 3]);
  assert.deepEqual(JSON.parse(canvas.dataset.renderDurationTimeline), [
    { atMs: 1000, durationMs: 4 },
    { atMs: 1020, durationMs: 7 },
    { atMs: 1040, durationMs: 3 },
  ]);
});

test("renderer applies newer fact revisions without mutating snapshots", () => {
  const { renderer } = createHarness();
  const authoritative = snapshot(2);
  const before = structuredClone(authoritative);
  assert.equal(renderer.render(authoritative, 1000), true);
  assert.deepEqual(authoritative, before);
  assert.equal(renderer.revision, 2);
  assert.equal(renderer.robotInstances.size, 2);
  assert.equal(renderer.dynamicObjects.size, 1);
  const firstMesh = renderer.robotInstances.get("robot-1").node("Upper_Arm").children[0];
  const secondMesh = renderer.robotInstances.get("robot-2").node("Upper_Arm").children[0];
  assert.equal(firstMesh.geometry, secondMesh.geometry);
  assert.equal(firstMesh.material, secondMesh.material);

  const old = snapshot(1);
  old.entities["red-block"].pose[0] = 9;
  assert.equal(renderer.render(old, 1100), false);
  assert.equal(renderer.dynamicObjects.get("red-block").position.x, 0);
});

test("renderer accepts Fleet xyz-yaw robot poses and preserves heading", () => {
  const { renderer } = createHarness();
  const fleetSnapshot = snapshot(1);
  fleetSnapshot.robots["robot-1"].pose = [1.15, -1.15, 0.035, Math.PI / 2];
  fleetSnapshot.robots["robot-2"].pose = [2.65, -1.15, 0.035, -Math.PI / 2];

  assert.equal(renderer.render(fleetSnapshot, 1000), true);
  const first = renderer.robotInstances.get("robot-1").root;
  const second = renderer.robotInstances.get("robot-2").root;
  const expectedFirst = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 0, 1),
    Math.PI / 2,
  );
  const expectedSecond = new THREE.Quaternion().setFromAxisAngle(
    new THREE.Vector3(0, 0, 1),
    -Math.PI / 2,
  );
  assert.ok(first.quaternion.angleTo(expectedFirst) < 1e-12);
  assert.ok(second.quaternion.angleTo(expectedSecond) < 1e-12);
});

test("invalid dynamic entities fail closed without advancing the revision", () => {
  const { renderer } = createHarness();
  const invalid = snapshot(3);
  invalid.entities.broken = { category: "block", pose: [0, 0, 0] };
  assert.equal(renderer.render(invalid, 1000), false);
  assert.equal(renderer.revision, -1);
  assert.equal(renderer.status.code, "WEBGL_SNAPSHOT_INVALID");
});

test("equal revisions preserve robot facts while repeated FRESH leaves interpolation timing unchanged", () => {
  const { renderer } = createHarness();
  const first = snapshot(1);
  first.robots["robot-1"].state["joint.left.pitch"] = 0;
  renderer.render(first, 1000);
  const moving = snapshot(2);
  moving.robots["robot-1"].state["joint.left.pitch"] = 1;
  moving.robots["robot-1"].activity = "MOVE";
  moving.robots["robot-1"].held = "red-block";
  renderer.render(moving, 1100);

  const volatile = snapshot(2);
  volatile.robots["robot-1"].state["joint.left.pitch"] = -1;
  volatile.robots["robot-1"].activity = "display-only";
  volatile.robots["robot-1"].held = "";
  volatile.robots["robot-1"].emergencyStopped = true;
  assert.equal(renderer.render(volatile, 1150), true);

  const instance = renderer.robotInstances.get("robot-1");
  assert.equal(instance.transitionStartedAt, 1100);
  assert.equal(instance.transitionDuration, 100);
  assert.equal(instance.target.joints["joint.left.pitch"], 1);
  assert.equal(instance.sample(1200).joints["joint.left.pitch"], 1);
  assert.equal(instance.sample(1200).activity, "MOVE");
  assert.equal(instance.sample(1200).held, "red-block");
  assert.equal(instance.sample(1200).emergencyStopped, false);
});

test("equal-revision FRESH to STALE freezes interpolation without accepting fact changes", () => {
  const { renderer } = createHarness();
  const first = snapshot(1);
  first.robots["robot-1"].state["joint.left.pitch"] = 0;
  renderer.render(first, 1000);
  const moving = snapshot(2);
  moving.robots["robot-1"].state["joint.left.pitch"] = 1;
  renderer.render(moving, 1100);

  const volatile = snapshot(2);
  volatile.robots["robot-1"].pose = [9, 9, 9, 1, 0, 0, 0];
  volatile.robots["robot-1"].state["joint.left.pitch"] = -1;
  volatile.robots["robot-1"].freshness = "STALE";
  volatile.robots["robot-1"].emergencyStopped = true;
  volatile.entities["red-block"].pose = [8, 8, 8, 1, 0, 0, 0];
  volatile.entities["red-block"].freshness = "STALE";
  assert.equal(renderer.render(volatile, 1150), true);

  const instance = renderer.robotInstances.get("robot-1");
  const frozen = instance.sample(5000);
  assert.ok(Math.abs(frozen.joints["joint.left.pitch"] - 0.5) < 1e-12);
  assert.deepEqual(instance.root.position.toArray(), [-0.5, 0, 0]);
  assert.equal(frozen.freshness, "STALE");
  assert.equal(frozen.emergencyStopped, false);
  assert.equal(instance.root.userData.pickEntity.pose[0], -0.5);
  assert.equal(renderer.dynamicObjects.get("red-block").position.x, 0);
  assert.equal(renderer.dynamicObjects.get("red-block").userData.pickEntity.freshness, "STALE");

  const sameRevisionRecovery = snapshot(2);
  sameRevisionRecovery.robots["robot-1"].freshness = "FRESH";
  assert.equal(renderer.render(sameRevisionRecovery, 1180), true);
  assert.equal(instance.sample(5000).freshness, "STALE");

  const older = snapshot(1);
  older.robots["robot-1"].freshness = "FRESH";
  assert.equal(renderer.render(older, 1200), false);
  assert.equal(instance.sample(5000).freshness, "STALE");
});

test("late-bound follow receives the renderer's monotonic freshness", () => {
  const { canvas, renderer } = createHarness();
  renderer.render(snapshot(2), 1000);
  const stale = snapshot(2);
  stale.robots["robot-1"].freshness = "STALE";
  renderer.render(stale, 1100);

  const worldCamera = {
    target: [0, 0, 0], yaw: 0.7, pitch: 0.7, distance: 3,
    basis() { return { position: [2, -2, 2] }; },
    drag() {}, zoomAt() {}, applyPreset() { return true; },
    toJSON() { return { yaw: this.yaw, pitch: this.pitch, distance: this.distance, target: [...this.target] }; },
  };
  renderer.setWorldCamera(worldCamera);
  const controller = WebGLSceneRenderer.bindInteraction(canvas, renderer, {
    camera: worldCamera,
    keyTarget: { addEventListener() {}, removeEventListener() {} },
  });
  controller.setFollow("robot-1");
  renderer.render(snapshot(2), 1200);

  assert.deepEqual(worldCamera.target, [0, 0, 0]);
  assert.equal(renderer.robotInstances.get("robot-1").freshness, "STALE");
  controller.dispose();
});

test("full-world robot entities never become dynamic cubes and focus resolves the articulated model", () => {
  const { renderer } = createHarness();
  const world = snapshot(1);
  world.entities["robot-1"] = {
    entityId: "robot-1", category: "robot", pose: [-0.5, 0, 0, 1, 0, 0, 0], freshness: "FRESH",
  };
  world.entities["robot-2"] = {
    entityId: "robot-2", category: "device", pose: [0.5, 0, 0, 1, 0, 0, 0], freshness: "FRESH",
  };
  world.entities["robot-shadow"] = {
    entityId: "robot-shadow", category: "robot", pose: [0, 0, 0, 1, 0, 0, 0], freshness: "FRESH",
  };
  renderer.render(world, 1000);
  assert.deepEqual([...renderer.dynamicObjects.keys()], ["red-block"]);
  assert.equal(renderer.focus(world.entities["robot-1"]), true);
  assert.equal(renderer.robotInstances.get("robot-1").root.parent, renderer.robotRoot);
});

test("renderer picks and focuses authoritative dynamic objects", () => {
  const { renderer } = createHarness();
  renderer.render(snapshot(), 1000);
  renderer.camera.position.set(0, -3, 1);
  renderer.camera.lookAt(0, 0, 0.2);
  renderer.scene.updateMatrixWorld(true);

  const picked = renderer.pick(400, 200);
  assert.equal(picked?.entityId, "red-block");
  assert.equal(renderer.focus(picked), true);
  assert.deepEqual(renderer.focusTarget.toArray().map((value) => Number(value.toFixed(3))), [0, 0, 0.2]);
});

test("pick consumes CSS client pixels correctly when the drawing buffer is DPR2", () => {
  const { canvas, renderer } = createHarness();
  canvas.width = 1600;
  canvas.height = 800;
  canvas.rect = { left: 100, top: 50, width: 800, height: 400 };
  renderer.render(snapshot(), 1000);
  renderer.camera.position.set(0, -3, 1);
  renderer.camera.lookAt(0, 0, 0.2);
  renderer.scene.updateMatrixWorld(true);
  assert.equal(renderer.pick(500, 250)?.entityId, "red-block");
});

test("renderer focuses an authoritative pose even before it has pick geometry", () => {
  const { renderer } = createHarness();
  assert.equal(renderer.focus({ entityId: "semantic-zone", pose: [1, 2, 0.1] }), true);
  assert.deepEqual(renderer.focusTarget.toArray(), [1, 2, 0.1]);
});

test("context loss degrades status and dispose releases owned resources once", () => {
  const { canvas, gpu, renderer } = createHarness();
  let prevented = false;
  canvas.emit("webglcontextlost", { preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  assert.equal(renderer.status.code, "WEBGL_CONTEXT_LOST");
  canvas.emit("webglcontextrestored");
  assert.equal(renderer.status.state, "READY");

  renderer.render(snapshot(), 1000);
  renderer.dispose();
  renderer.dispose();
  assert.equal(renderer.status.state, "DISPOSED");
  assert.equal(gpu.disposed, true);
  assert.equal(canvas.listeners.size, 0);
  assert.equal(renderer.render(snapshot(2), 1100), false);
});

test("renderer attaches semantic evidence without replacing model geometry", () => {
  const { renderer } = createHarness();
  const world = snapshot(1);
  world.entities["left-start-zone"] = { entityId: "left-start-zone", category: "source_zone", pose: [1, 0, 0.1], freshness: "FRESH" };
  world.entities["handoff-zone"] = { entityId: "handoff-zone", category: "handoff_zone", pose: [2, 0, 0.1], freshness: "FRESH" };
  world.entities["right-target-zone"] = { entityId: "right-target-zone", category: "target_zone", pose: [3, 0, 0.1], freshness: "FRESH" };
  world.entities["red-block"].relations = { inside: "left-start-zone" };
  world.resources = { "block:red-block": { owner: "robot-1", fencingToken: 1, freshness: "FRESH" } };

  renderer.setVisibility({ models: true, bounds: true, labels: true, path: true });
  assert.equal(renderer.render(world, 1000), true);
  assert.equal(renderer.semanticOverlay.root.parent, renderer.scene);
  assert.equal(renderer.dynamicObjects.has("red-block"), true);
  assert.deepEqual(renderer.semanticOverlay.zoneIds(), ["left-start-zone", "handoff-zone", "right-target-zone"]);
  assert.equal(renderer.semanticOverlay.custody().owner, "robot-1");

  renderer.setVisibility({ models: false, bounds: false, labels: false, path: true });
  assert.equal(renderer.staticSceneRoot.visible, false);
  assert.equal(renderer.robotRoot.visible, false);
  assert.equal(renderer.dynamicObjectRoot.visible, false);
  assert.equal(renderer.semanticOverlay.path().visible, true);
});

test("renderer selection and WorldCamera synchronization remain view-only", () => {
  const { renderer } = createHarness();
  const worldCamera = {
    yaw: 0.2, pitch: 0.6, distance: 4, target: [1, 2, 0.3],
    basis() { return { position: [2, -1, 3] }; },
    toJSON() { return { yaw: this.yaw, pitch: this.pitch, distance: this.distance, target: [...this.target] }; },
    applyPreset() { return true; },
  };
  renderer.setWorldCamera(worldCamera);
  renderer.syncWorldCamera();
  assert.deepEqual(renderer.camera.position.toArray(), [2, -1, 3]);
  assert.deepEqual(renderer.focusTarget.toArray(), [1, 2, 0.3]);

  const entity = { entityId: "red-block", category: "block", pose: [0, 0, 0.2] };
  renderer.select(entity);
  assert.equal(renderer.selectedEntityId, "red-block");
  assert.equal(renderer.semanticOverlay.selectedEntityId, "red-block");
  assert.equal(renderer.focus(entity), true);
  assert.deepEqual(worldCamera.target, [0, 0, 0.4]);
  assert.equal(worldCamera.distance, 1.35);
});

test("equal revisions update overlay freshness without accepting changed world facts", () => {
  const { renderer } = createHarness();
  const first = snapshot(4);
  first.entities["red-block"].relations = { inside: "left-start-zone" };
  first.resources = { "block:red-block": { owner: "robot-1", fencingToken: 7, freshness: "FRESH" } };
  first.health = { degradedSources: [], conflicts: ["red-block custody mismatch"] };
  renderer.render(first, 1000);
  const originalLabelPosition = renderer.semanticOverlay.label("red-block").position.toArray();

  const volatile = snapshot(4);
  volatile.entities["red-block"].pose = [9, 9, 9, 1, 0, 0, 0];
  volatile.entities["red-block"].relations = { held_by: "robot-2" };
  volatile.entities["red-block"].freshness = "STALE";
  volatile.robots["robot-1"].held = "red-block";
  volatile.resources = { "block:red-block": { owner: "robot-2", fencingToken: 99, freshness: "STALE" } };
  renderer.render(volatile, 1100);

  assert.deepEqual(renderer.semanticOverlay.label("red-block").position.toArray(), originalLabelPosition);
  assert.match(renderer.semanticOverlay.label("red-block").text, /STALE/);
  assert.equal(renderer.semanticOverlay.custody().owner, "robot-1");
  assert.equal(renderer.semanticOverlay.custody().fencingToken, 7);
  assert.equal(renderer.semanticOverlay.custody().freshness, "STALE");
  assert.deepEqual(renderer.semanticOverlay.custody().robotHolders, []);
  assert.equal(renderer.semanticOverlay.custody().conflict, true);
  assert.match(renderer.semanticOverlay.label("red-block").text, /CONFLICT/);
});
