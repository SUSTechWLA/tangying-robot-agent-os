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
    this.listeners = new Map();
  }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name, handler) {
    if (this.listeners.get(name) === handler) this.listeners.delete(name);
  }
  getBoundingClientRect() { return { left: 0, top: 0, width: 800, height: 400 }; }
  emit(name, event = {}) { this.listeners.get(name)?.(event); }
}

class FakeThreeRenderer {
  constructor() {
    this.shadowMap = {};
    this.calls = [];
    this.disposed = false;
  }
  setPixelRatio(value) { this.pixelRatio = value; }
  setSize(width, height, updateStyle) { this.size = [width, height, updateStyle]; }
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
  assert.equal(renderer.staticSceneRoot.parent, renderer.scene);
  assert.equal(renderer.robotRoot.parent, renderer.scene);
  assert.equal(renderer.dynamicObjectRoot.parent, renderer.scene);
  assert.equal(gpu.pixelRatio, 2);
  assert.deepEqual(gpu.size, [800, 400, false]);
  assert.equal(canvas.listeners.has("webglcontextlost"), true);
  assert.equal(renderer.status.state, "READY");
});

test("renderer applies only increasing revisions without mutating snapshots", () => {
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

test("invalid dynamic entities fail closed without advancing the revision", () => {
  const { renderer } = createHarness();
  const invalid = snapshot(3);
  invalid.entities.broken = { category: "block", pose: [0, 0, 0] };
  assert.equal(renderer.render(invalid, 1000), false);
  assert.equal(renderer.revision, -1);
  assert.equal(renderer.status.code, "WEBGL_SNAPSHOT_INVALID");
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
