import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("./world_view.js", import.meta.url), "utf8");
const context = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
vm.runInContext(source, context, { filename: "world_view.js" });
const { WorldCamera, WorldRealtimeClient } = context.globalThis.TangyingWorld;

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
