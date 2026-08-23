import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("./world_view.js", import.meta.url), "utf8");
const context = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
vm.runInContext(source, context, { filename: "world_view.js" });
const {
  WorldCamera,
  WorldRealtimeClient,
  WorldRenderer,
  fixtureBounds,
  fixtureFaces,
  handoffPath,
} = context.globalThis.TangyingWorld;

class RecordingContext {
  constructor() {
    this.fills = 0;
    this.strokes = 0;
    this.fillStyles = [];
  }

  beginPath() {}
  clearRect() {}
  closePath() {}
  fill() { this.fills += 1; this.fillStyles.push(this.fillStyle); }
  fillRect() { this.fills += 1; this.fillStyles.push(this.fillStyle); }
  fillText() {}
  lineTo() {}
  moveTo() {}
  restore() {}
  save() {}
  setLineDash() {}
  stroke() { this.strokes += 1; }
  strokeRect() { this.strokes += 1; }
  arc() {}
}

test("left drag pans while right drag orbits", () => {
  const camera = new WorldCamera({ yaw: 0.5, pitch: 0.4, distance: 4, target: [0, 0, 0] });
  const initialYaw = camera.yaw;
  camera.drag({ button: 0, dx: 40, dy: 20, viewport: [1200, 600] });
  assert.equal(camera.yaw, initialYaw);
  assert.notDeepEqual([...camera.target], [0, 0, 0]);
  const panned = [...camera.target];
  camera.drag({ button: 2, dx: 40, dy: 20, viewport: [1200, 600] });
  assert.notEqual(camera.yaw, initialYaw);
  assert.deepEqual([...camera.target], panned);
});

test("wheel zoom preserves the ground point under the pointer", () => {
  const camera = new WorldCamera({ yaw: 0.7, pitch: 0.7, distance: 3, target: [0.3, 0.3, 0] });
  const before = camera.unprojectToGround(810, 320, 1200, 600);
  camera.zoomAt(-240, 810, 320, 1200, 600);
  const after = camera.unprojectToGround(810, 320, 1200, 600);
  assert.ok(Math.hypot(before[0] - after[0], before[1] - after[1]) < 1e-6, `${before} -> ${after}`);
});

test("persisted WorldCamera JSON keeps the stable yaw pitch distance target shape", () => {
  const camera = new WorldCamera({ yaw: 0.91, pitch: 0.51, distance: 6.2, target: [2.75, -1.5, 0.4] });
  const persisted = structuredClone(camera.toJSON());
  const restored = new WorldCamera(persisted);

  assert.deepEqual(Object.keys(persisted), ["yaw", "pitch", "distance", "target"]);
  assert.deepEqual({
    yaw: restored.yaw,
    pitch: restored.pitch,
    distance: restored.distance,
    target: Array.from(restored.target),
  }, persisted);
  restored.target[0] = 9;
  assert.equal(persisted.target[0], 2.75, "restoration must not alias persisted JSON arrays");
});

test("revision gap stops rendering and requests a fresh snapshot", async () => {
  let requests = 0;
  const client = new WorldRealtimeClient({
    requestSnapshot: async () => {
      requests += 1;
      return { revision: 12, entities: {}, robots: {}, resources: {}, sources: {} };
    },
  });
  client.acceptSnapshot({ revision: 10, entities: {}, robots: {}, resources: {}, sources: {} });

  await client.receive({ revision: 12, snapshot: { revision: 12 } });

  assert.equal(requests, 1);
  assert.equal(client.revision, 12);
  assert.equal(client.state, "LIVE");
});

test("old or duplicate revisions never replace current world truth", async () => {
  const rendered = [];
  const client = new WorldRealtimeClient({ render: (snapshot) => rendered.push(snapshot.revision) });
  client.acceptSnapshot({ revision: 5 });
  await client.receive({ revision: 5, snapshot: { revision: 5 } });
  await client.receive({ revision: 4, snapshot: { revision: 4 } });
  assert.deepEqual(rendered, [5]);
  assert.equal(client.revision, 5);
});

test("MuJoCo fixture bounds render as selectable three-dimensional geometry", () => {
  const fixture = {
    entityId: "counter-main",
    category: "counter",
    pose: [1, 0, 0.45],
    attributes: {
      bounds: "0,-0.5,0,2,0.5,0.9",
      label: "主工作台",
      model_source: "mujoco",
      static: "true",
    },
  };
  const bounds = fixtureBounds(fixture);
  assert.deepEqual(Array.from(bounds.minimum), [0, -0.5, 0]);
  assert.deepEqual(Array.from(bounds.maximum), [2, 0.5, 0.9]);

  const drawing = new RecordingContext();
  const canvas = { width: 1400, height: 700, getContext: () => drawing };
  const renderer = new WorldRenderer(
    canvas,
    new WorldCamera({ yaw: 0.7, pitch: 0.7, distance: 4, target: [1, 0, 0] }),
  );
  renderer.render({ revision: 1, entities: { "counter-main": fixture }, robots: {}, resources: {} });
  const point = renderer.camera.project(fixture.pose, canvas.width, canvas.height);

  assert.ok(drawing.fills >= 3);
  assert.equal(renderer.pick(point[0], point[1]).entityId, "counter-main");
});

test("MuJoCo walls render as a non-occluding spatial shell", () => {
  const drawing = new RecordingContext();
  const renderer = new WorldRenderer(
    { width: 1400, height: 700, getContext: () => drawing },
    new WorldCamera({ yaw: 0.7, pitch: 0.7, distance: 7, target: [2.75, -1.5, 0.4] }),
  );
  renderer.drawFixture({
    entityId: "wall-front",
    category: "wall",
    pose: [2.75, -3, 1.5],
    attributes: { bounds: "0,-3.04,0,5.5,-2.96,3", model_source: "mujoco" },
  }, 1400, 700);

  const wallFills = drawing.fillStyles.filter((style) => String(style).startsWith("#344b55"));
  assert.equal(wallFills.length, 3);
  assert.ok(wallFills.every((style) => Number.parseInt(style.slice(-2), 16) <= 0x30));
});

test("fixture faces turn toward the current camera", () => {
  const bounds = { minimum: [0, 0, 0], maximum: [2, 2, 2] };

  assert.deepEqual(Array.from(fixtureFaces(bounds, [-2, -3, 5]), (face) => Array.from(face)), [
    [0, 3, 7, 4],
    [0, 1, 5, 4],
    [4, 5, 6, 7],
  ]);
  assert.deepEqual(Array.from(fixtureFaces(bounds, [4, 5, 5]), (face) => Array.from(face)), [
    [1, 2, 6, 5],
    [3, 2, 6, 7],
    [4, 5, 6, 7],
  ]);
});

test("camera presets frame the whole MuJoCo room or follow a robot", () => {
  const snapshot = {
    entities: {
      floor: {
        entityId: "floor",
        category: "floor",
        pose: [2.75, -1.5, -0.02],
        attributes: { bounds: "-0.04,-3.04,-0.04,5.54,0.04,0", model_source: "mujoco" },
      },
    },
    robots: { "robot-2": { robotId: "robot-2", pose: [2.65, -1.15, 0.035] } },
  };
  const camera = new WorldCamera();

  camera.applyPreset("top", snapshot);
  assert.ok(camera.pitch > 1.2);
  assert.ok(camera.distance >= 5);
  assert.ok(Math.abs(camera.target[0] - 2.75) < 0.01);
  camera.applyPreset("robot-2", snapshot);
  assert.deepEqual(Array.from(camera.target), [2.65, -1.15, 0.35]);
  assert.ok(camera.distance <= 2.2);
  assert.ok(camera.basis().position[1] < snapshot.robots["robot-2"].pose[1], "robot camera should look in from the open front aisle");
});

test("handoff path preserves source to handoff to target order and current stage", () => {
  const snapshot = {
    entities: {
      "left-start-zone": { entityId: "left-start-zone", pose: [1, -0.5, 1] },
      "handoff-zone": { entityId: "handoff-zone", pose: [2, -0.5, 1] },
      "right-target-zone": { entityId: "right-target-zone", pose: [3, -0.5, 1] },
      "red-block": { entityId: "red-block", relations: { inside: "handoff-zone" } },
    },
  };

  const path = handoffPath(snapshot);

  assert.deepEqual(Array.from(path.points, (point) => point.entityId), [
    "left-start-zone",
    "handoff-zone",
    "right-target-zone",
  ]);
  assert.equal(path.stage, 1);
});

test("handoff path does not regress while either robot carries the block", () => {
  const entities = {
    "left-start-zone": { entityId: "left-start-zone", pose: [1, -0.5, 1] },
    "handoff-zone": { entityId: "handoff-zone", pose: [2, -0.5, 1] },
    "right-target-zone": { entityId: "right-target-zone", pose: [3, -0.5, 1] },
  };

  assert.equal(handoffPath({ entities: {
    ...entities,
    "red-block": { entityId: "red-block", relations: { held_by: "robot-1" } },
  } }).stage, 0);
  assert.equal(handoffPath({ entities: {
    ...entities,
    "red-block": { entityId: "red-block", relations: { held_by: "robot-2" } },
  } }).stage, 1);
});
