// Streaming levels of detail. The tests that matter here are the ones about not
// showing the operator something wrong: a truncated chunk drawn as geometry, or a
// failed refinement emptying the view.

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const context = vm.createContext({});
vm.runInContext(await readFile(new URL("./map_cloud.js", import.meta.url), "utf8"), context);
const { decodeChunk, selectLodLevel, cameraDistance, planLevels, formatBytes, MapCloudLayer } =
  context.TangyingMapCloud;

/** Build a chunk the way the Python pipeline writes one. */
function makeChunk(points, { level = 0, colour = true, truncate = 0, magic = 0x43505954, version = 1 } = {}) {
  const count = points.length;
  const size = 20 + count * 12 + (colour ? count * 3 : 0) - truncate;
  const buffer = new ArrayBuffer(Math.max(size, 0));
  const view = new DataView(buffer);
  view.setUint32(0, magic, true);
  view.setUint32(4, version, true);
  view.setUint32(8, count, true);
  view.setUint32(12, level, true);
  view.setUint32(16, colour ? 1 : 0, true);
  points.forEach((point, index) => {
    const offset = 20 + index * 12;
    if (offset + 12 <= buffer.byteLength) {
      view.setFloat32(offset, point[0], true);
      view.setFloat32(offset + 4, point[1], true);
      view.setFloat32(offset + 8, point[2], true);
    }
  });
  if (colour) {
    points.forEach((point, index) => {
      const offset = 20 + count * 12 + index * 3;
      if (offset + 3 <= buffer.byteLength) {
        view.setUint8(offset, point[3] || 0);
        view.setUint8(offset + 1, point[4] || 0);
        view.setUint8(offset + 2, point[5] || 0);
      }
    });
  }
  return buffer;
}

test("a chunk decodes to typed arrays in the order the pipeline wrote them", () => {
  const decoded = decodeChunk(makeChunk([[1, 2, 3, 10, 20, 30], [4, 5, 6, 40, 50, 60]]));
  assert.equal(decoded.count, 2);
  assert.equal(decoded.level, 0);
  assert.equal(decoded.hasColour, true);
  // The module runs in its own vm realm, so `instanceof` would compare prototypes
  // across realms and always fail; check the shape instead.
  assert.equal(decoded.positions.BYTES_PER_ELEMENT, 4, "positions must be float32");
  assert.equal(decoded.colors.BYTES_PER_ELEMENT, 1, "colours must be uint8");
  assert.deepEqual([...decoded.positions], [1, 2, 3, 4, 5, 6]);
  assert.deepEqual([...decoded.colors], [10, 20, 30, 40, 50, 60]);
});

test("a chunk without colour decodes to null colours rather than zeros", () => {
  const decoded = decodeChunk(makeChunk([[1, 2, 3]], { colour: false }));
  assert.equal(decoded.hasColour, false);
  assert.equal(decoded.colors, null, "the renderer must be able to tell 'no colour' from 'black'");
});

test("a truncated chunk is rejected instead of being drawn", () => {
  // This is the failure that matters: half a chunk decoded as geometry looks like
  // a map, and the operator has no way to tell it is wrong.
  assert.throws(() => decodeChunk(makeChunk([[1, 2, 3], [4, 5, 6]], { truncate: 8 })), /carries/);
  assert.throws(() => decodeChunk(makeChunk([[1, 2, 3]], { truncate: 12 })), /carries/);
});

test("a foreign or future chunk is refused by name", () => {
  assert.throws(() => decodeChunk(makeChunk([[1, 2, 3]], { magic: 0x58585858 })), /not a TYPC chunk/);
  assert.throws(() => decodeChunk(makeChunk([[1, 2, 3]], { version: 2 })), /unsupported chunk version 2/);
  assert.throws(() => decodeChunk(new ArrayBuffer(4)), /shorter than its own header/);
  assert.throws(() => decodeChunk(null), /ArrayBuffer/);
});

test("level selection is coarse when far and fine when close", () => {
  const levels = 5;
  assert.equal(selectLodLevel(1, levels), 4, "close up: the finest level");
  assert.equal(selectLodLevel(3, levels), 4, "at the near boundary: still finest");
  assert.equal(selectLodLevel(40, levels), 0, "far away: the coarsest");
  assert.equal(selectLodLevel(200, levels), 0, "beyond the far bound: still coarsest");
  // Monotone: getting closer never selects a coarser level.
  let previous = -1;
  for (let distance = 60; distance >= 1; distance -= 1) {
    const level = selectLodLevel(distance, levels);
    assert.ok(level >= previous, `level went backwards at ${distance}m`);
    previous = level;
  }
});

test("level selection survives degenerate inputs", () => {
  assert.equal(selectLodLevel(10, 0), 0);
  assert.equal(selectLodLevel(10, undefined), 0);
  assert.equal(selectLodLevel(NaN, 5), 4);
});

test("camera distance is measured to the middle of the map", () => {
  const bounds = { min: [0, 0, 0], max: [2, 2, 2] };
  assert.equal(cameraDistance([1, 1, 1], bounds), 0);
  assert.equal(cameraDistance([1, 1, 4], bounds), 3);
  assert.equal(cameraDistance({ x: 1, y: 1, z: 4 }, bounds), 3);
  assert.equal(cameraDistance(null, bounds), 0);
});

test("the coarsest level is always fetched and never evicted", () => {
  // A failed refinement must not empty the screen, which only holds if level 0 is
  // pinned.
  const plan = planLevels({ distanceMetres: 1, lodLevels: 5, loaded: [], maxResident: 2 });
  assert.ok(plan.fetch.includes(0));
  assert.equal(plan.evict.length, 0);

  const settled = planLevels({ distanceMetres: 1, lodLevels: 5, loaded: [0, 1, 2, 3, 4] });
  assert.ok(!settled.evict.includes(0), "level 0 must never be evicted");
  assert.ok(settled.evict.length > 0, "resident levels must be bounded");
});

test("levels already loaded are not fetched again", () => {
  const plan = planLevels({ distanceMetres: 1, lodLevels: 5, loaded: [0, 3, 4] });
  assert.deepEqual([...plan.fetch], []);
  assert.equal(plan.target, 4);
});

function fakeRenderer() {
  const shown = new Map();
  return {
    shown,
    show(level, geometry) { shown.set(level, geometry); },
    hide(level) { shown.delete(level); },
  };
}

function responseFor(buffer, { ok = true, status = 200 } = {}) {
  return { ok, status, arrayBuffer: async () => buffer };
}

test("the layer fetches a level, hands geometry to the renderer and reports it", async () => {
  const renderer = fakeRenderer();
  const requested = [];
  const layer = new MapCloudLayer({
    baseUrl: "http://127.0.0.1:8787", mapId: "home-loadtest", lodLevels: 3,
    bounds: { min: [0, 0, 0], max: [1, 1, 1] }, renderer,
    fetchImpl: async url => { requested.push(url); return responseFor(makeChunk([[1, 2, 3]], { level: 0 })); },
  });
  const ok = await layer.loadLevel(0);
  assert.equal(ok, true);
  assert.equal(requested[0], "http://127.0.0.1:8787/v1/maps/home-loadtest/cloud?lod=0");
  assert.ok(renderer.shown.has(0));
  assert.deepEqual([...layer.status().loaded], [0]);
  assert.ok(layer.status().bytes > 0);
});

test("a failed level fetch keeps the previous level on screen", async () => {
  const renderer = fakeRenderer();
  let calls = 0;
  const layer = new MapCloudLayer({
    baseUrl: "http://x", mapId: "m", lodLevels: 3, bounds: { min: [0, 0, 0], max: [1, 1, 1] },
    renderer,
    fetchImpl: async () => {
      calls += 1;
      if (calls === 1) return responseFor(makeChunk([[1, 2, 3]], { level: 0 }));
      throw new Error("network down");
    },
  });
  await layer.loadLevel(0);
  const failed = await layer.loadLevel(1);
  assert.equal(failed, false);
  assert.ok(renderer.shown.has(0), "the coarse level must survive a failed refinement");
  assert.deepEqual([...layer.status().loaded], [0]);
  assert.match(layer.status().error, /network down/);
});

test("an http error is reported and does not mark the level loaded", async () => {
  const layer = new MapCloudLayer({
    baseUrl: "http://x", mapId: "m", lodLevels: 2, renderer: fakeRenderer(),
    fetchImpl: async () => responseFor(new ArrayBuffer(0), { ok: false, status: 404 }),
  });
  assert.equal(await layer.loadLevel(1), false);
  assert.deepEqual([...layer.status().loaded], [], "a failed level must not count as loaded");
  assert.match(layer.status().error, /404/);
});

test("updating for a camera position fetches the missing levels and evicts the rest", async () => {
  const renderer = fakeRenderer();
  const layer = new MapCloudLayer({
    baseUrl: "http://x", mapId: "m", lodLevels: 5, bounds: { min: [0, 0, 0], max: [2, 2, 2] },
    renderer, maxResident: 2,
    fetchImpl: async url => {
      const level = Number(url.split("lod=")[1]);
      return responseFor(makeChunk([[1, 2, 3]], { level }));
    },
  });
  const close = await layer.update([1, 1, 1]);          // finest
  assert.ok(close.target >= 3);
  await new Promise(resolve => setTimeout(resolve, 10));
  assert.ok(layer.status().loaded.includes(0));

  const far = await layer.update([1, 1, 200]);          // coarsest
  assert.equal(far.target, 0);
  assert.ok(far.evict.length > 0, "moving away must release detail");
  assert.ok(!far.evict.includes(0), "the coarse level stays");
});

test("dispose releases everything the layer was holding", async () => {
  const renderer = fakeRenderer();
  const layer = new MapCloudLayer({
    baseUrl: "http://x", mapId: "m", lodLevels: 2, renderer,
    bounds: { min: [0, 0, 0], max: [1, 1, 1] },
    fetchImpl: async url => responseFor(makeChunk([[1, 2, 3]], { level: Number(url.split("lod=")[1]) })),
  });
  await layer.loadLevel(0);
  await layer.loadLevel(1);
  assert.equal(renderer.shown.size, 2);
  layer.dispose();
  assert.equal(renderer.shown.size, 0);
  assert.equal(layer.status().bytes, 0);
});

test("a layer needs a map id and a renderer, and says so", () => {
  assert.throws(() => new MapCloudLayer({ mapId: "m", renderer: fakeRenderer() }), /base URL/);
  assert.throws(() => new MapCloudLayer({ baseUrl: "http://x", renderer: fakeRenderer() }), /map id/);
  assert.throws(() => new MapCloudLayer({ baseUrl: "http://x", mapId: "m" }), /renderer/);
});

test("byte counts read the way a person expects", () => {
  assert.equal(formatBytes(512), "512 B");
  assert.equal(formatBytes(2048), "2.0 KB");
  assert.equal(formatBytes(5 * 1024 * 1024), "5.0 MB");
  assert.equal(formatBytes(-1), "—");
});
