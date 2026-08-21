import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

import { InteractionController } from "./src/interaction_controller.js";
import { WebGLSceneRenderer } from "./src/webgl_scene_renderer.js";

const worldSource = await readFile(new URL("./world_view.js", import.meta.url), "utf8");
const context = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
vm.runInContext(worldSource, context, { filename: "world_view.js" });
const { WorldCamera } = context.globalThis.TangyingWorld;

class EventTarget {
  constructor() { this.listeners = new Map(); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(handler);
  }
  removeEventListener(name, handler) { this.listeners.get(name)?.delete(handler); }
  emit(name, event = {}) {
    event.type = name;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    for (const handler of this.listeners.get(name) || []) handler(event);
    return event;
  }
}

class FakeCanvas extends EventTarget {
  constructor() {
    super();
    this.width = 1600;
    this.height = 800;
    this.clientWidth = 800;
    this.clientHeight = 400;
    this.captured = new Set();
    this.classList = { add() {}, remove() {} };
    this.ownerDocument = { activeElement: this };
  }
  getBoundingClientRect() { return { left: 100, top: 50, width: 800, height: 400 }; }
  setPointerCapture(id) { this.captured.add(id); }
  hasPointerCapture(id) { return this.captured.has(id); }
  releasePointerCapture(id) { this.captured.delete(id); }
}

function createHarness() {
  const canvas = new FakeCanvas();
  const keyTarget = new EventTarget();
  const camera = new WorldCamera({ yaw: 0.5, pitch: 0.4, distance: 4, target: [0, 0, 0] });
  const saved = [];
  const renderer = {
    worldCamera: camera,
    latestSnapshot: { revision: 1, entities: {}, robots: {} },
    selected: null,
    focused: null,
    syncCalls: 0,
    setWorldCamera(next) { this.worldCamera = next; return this; },
    syncWorldCamera() { this.syncCalls += 1; },
    pick() { return { entityId: "red-block", category: "block", pose: [1, 2, 0.2] }; },
    focus(entity) { this.focused = entity; this.worldCamera.target = [1, 2, 0.4]; this.worldCamera.distance = 1.35; return true; },
    select(entity) { this.selected = entity; },
  };
  const controller = InteractionController.bind(canvas, renderer, {
    keyTarget,
    onCameraChange: (json) => saved.push(structuredClone(json)),
  });
  return { canvas, keyTarget, camera, renderer, controller, saved };
}

test("left drag pans and right drag orbits through the existing WorldCamera contract", () => {
  const { canvas, camera } = createHarness();
  const initialYaw = camera.yaw;
  canvas.emit("pointerdown", { button: 0, pointerId: 4, clientX: 300, clientY: 200 });
  canvas.emit("pointermove", { pointerId: 4, clientX: 340, clientY: 220 });
  canvas.emit("pointerup", { pointerId: 4, clientX: 340, clientY: 220 });
  assert.equal(camera.yaw, initialYaw);
  assert.notDeepEqual([...camera.target], [0, 0, 0]);

  const panned = [...camera.target];
  canvas.emit("pointerdown", { button: 2, pointerId: 5, clientX: 300, clientY: 200 });
  canvas.emit("pointermove", { pointerId: 5, clientX: 340, clientY: 220 });
  canvas.emit("pointerup", { pointerId: 5, clientX: 340, clientY: 220 });
  assert.notEqual(camera.yaw, initialYaw);
  assert.deepEqual([...camera.target], panned);
});

test("wheel uses CSS-local pointer coordinates and preserves its world anchor", () => {
  const { canvas, camera, saved } = createHarness();
  const before = camera.unprojectToGround(600, 200, 800, 400);
  const event = canvas.emit("wheel", { clientX: 700, clientY: 250, deltaY: -240 });
  const after = camera.unprojectToGround(600, 200, 800, 400);

  assert.equal(event.defaultPrevented, true);
  assert.ok(Math.hypot(before[0] - after[0], before[1] - after[1]) < 1e-6, `${before} -> ${after}`);
  assert.deepEqual(Object.keys(saved.at(-1)), ["yaw", "pitch", "distance", "target"]);
});

test("double click focuses the picked entity and F restores the authoritative overview", () => {
  const { canvas, keyTarget, camera, renderer } = createHarness();
  canvas.emit("dblclick", { clientX: 500, clientY: 250 });
  assert.equal(renderer.focused.entityId, "red-block");
  assert.equal(renderer.selected.entityId, "red-block");
  assert.deepEqual([...camera.target], [1, 2, 0.4]);

  renderer.latestSnapshot = {
    revision: 2,
    entities: {
      floor: { entityId: "floor", pose: [2.75, -1.5, 0], attributes: { bounds: "0,-3,0,5.5,0,1" } },
    },
    robots: {},
  };
  keyTarget.emit("keydown", { key: "f" });
  assert.ok(Math.abs(camera.target[0] - 2.75) < 1e-12);
  assert.ok(camera.distance >= 5.4);
});

test("capture is always released and the native context menu is suppressed", () => {
  const { canvas, controller } = createHarness();
  canvas.emit("pointerdown", { button: 0, pointerId: 8, clientX: 300, clientY: 200 });
  assert.equal(canvas.captured.has(8), true);
  canvas.emit("pointercancel", { pointerId: 8, clientX: 305, clientY: 205 });
  assert.equal(canvas.captured.has(8), false);
  assert.equal(canvas.emit("contextmenu", {}).defaultPrevented, true);
  controller.dispose();
  assert.equal(canvas.listeners.get("pointerdown").size, 0);
});

test("follow accepts only fresh robot poses and every direct camera operation cancels it", () => {
  const { canvas, renderer, controller } = createHarness();
  assert.equal(controller.setFollow("robot-2"), true);
  controller.applySnapshot({
    revision: 2,
    robots: { "robot-2": { robotId: "robot-2", pose: [2.6, -1.1, 0.035], freshness: "STALE" } },
  });
  assert.deepEqual([...renderer.worldCamera.target], [0, 0, 0]);
  controller.applySnapshot({
    revision: 2,
    robots: { "robot-2": { robotId: "robot-2", pose: [2.6, -1.1, 0.035], freshness: "FRESH" } },
  });
  assert.deepEqual([...renderer.worldCamera.target], [2.6, -1.1, 0.35]);

  canvas.emit("pointerdown", { button: 2, pointerId: 10, clientX: 300, clientY: 200 });
  assert.equal(controller.followId, "");
  controller.setFollow("robot-2");
  canvas.emit("wheel", { clientX: 500, clientY: 250, deltaY: 20 });
  assert.equal(controller.followId, "");
});

test("the production WebGL bundle entry can bind the interaction controller", () => {
  const canvas = new FakeCanvas();
  const camera = new WorldCamera();
  const renderer = {
    worldCamera: camera,
    setWorldCamera(next) { this.worldCamera = next; },
    syncWorldCamera() {},
  };

  const controller = WebGLSceneRenderer.bindInteraction(canvas, renderer, { keyTarget: new EventTarget() });

  assert.ok(controller instanceof InteractionController);
  assert.equal(canvas.emit("contextmenu", {}).defaultPrevented, true);
  controller.dispose();
});
