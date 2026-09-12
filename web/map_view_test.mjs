// The map panel draws what the robot has observed. The tests here hold the two
// things an operator would be misled by: an unknown area rendered as if it were
// free, and an empty grid presented as a map.

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

function fakeElement(tag) {
  return {
    tagName: tag, className: "", textContent: "", children: [], dataset: {}, style: {},
    attributes: {},
    append(...nodes) { this.children.push(...nodes); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getContext() { return null; },
  };
}
const context = vm.createContext({ document: { createElement: fakeElement } });
vm.runInContext(await readFile(new URL("./map_view.js", import.meta.url), "utf8"), context);
const { buildMapView, renderMapNodes, gridImage, robotCell, summariseGrid } = context.TangyingMapView;

function treeText(node) {
  return [node.textContent, ...node.children.map(treeText)].filter(Boolean).join(" ");
}

function findTag(node, tag) {
  if (node.tagName === tag) return node;
  for (const child of node.children) {
    const found = findTag(child, tag);
    if (found) return found;
  }
  return null;
}

// 4x4: 8 unknown, 5 free, 2 occupied (one of them lethal).
const CELLS = [-1, -1, -1, -1, -1, -1, -1, -1, 0, 0, 0, 0, 0, 20, 100, -1];

function payload(overrides = {}) {
  return {
    ready: true, mode: "mapping", mapRevision: "map-one", width: 4, height: 4, resolution: 0.05,
    cells: CELLS, origin: [0, 0, 0, 1, 0, 0, 0], mapPose: [0.05, 0.1, 0, 1, 0, 0, 0],
    poseSource: "rtabmap_tf", ...overrides,
  };
}

test("unknown space is counted separately from free space", () => {
  const summary = summariseGrid(CELLS);
  assert.deepEqual(
    { total: summary.total, unknown: summary.unknown, free: summary.free, occupied: summary.occupied, lethal: summary.lethal },
    { total: 16, unknown: 9, free: 5, occupied: 2, lethal: 1 },
  );
});

test("unknown cells are drawn lighter than free cells, never the same", () => {
  // The whole point of the picture: an operator must be able to see where the
  // robot has not been. Same colour for both hides exactly that.
  const pixels = gridImage(CELLS, 4, 4);
  const unknown = [pixels[0], pixels[1], pixels[2]];
  const free = [pixels[8 * 4], pixels[8 * 4 + 1], pixels[8 * 4 + 2]];
  const lethal = [pixels[14 * 4], pixels[14 * 4 + 1], pixels[14 * 4 + 2]];
  assert.notDeepEqual(unknown, free);
  assert.notDeepEqual(free, lethal);
  const brightness = colour => colour[0] + colour[1] + colour[2];
  assert.ok(brightness(unknown) < brightness(free), "unknown must read darker than free space");
  assert.ok(brightness(lethal) < brightness(unknown), "obstacles must be the darkest");
});

test("a ready map reports coverage and puts the robot on it", () => {
  const view = buildMapView(payload());
  assert.equal(view.available, true);
  assert.equal(view.stats.coverage, 44, "7 known of 16 cells");
  assert.equal(view.robot.column, 1);
  assert.equal(view.robot.row, 2);
  assert.match(view.headline, /地图覆盖 44%/);
  assert.match(view.headline, /正在建图/);
  assert.match(view.hint, /浅色区域是还没走过的地方/);
});

test("localisation mode is not described as mapping", () => {
  const view = buildMapView(payload({ mode: "localization" }));
  assert.doesNotMatch(view.headline, /正在建图/);
  assert.match(view.hint, /跨房间任务需要先把它补齐/);
});

test("a robot pose outside the grid is dropped rather than drawn wrongly", () => {
  assert.equal(robotCell(payload({ mapPose: [99, 99, 0, 1, 0, 0, 0] })), null);
  assert.equal(robotCell(payload({ mapPose: [] })), null);
  assert.equal(robotCell(payload({ resolution: 0 })), null);
});

test("a map that is not ready says why instead of drawing an empty grid", () => {
  for (const input of [null, undefined, {}, { ready: false, mode: "mapping" }]) {
    const view = buildMapView(input);
    assert.equal(view.available, false);
    assert.equal(view.stats, null);
    assert.ok(view.headline && view.hint);
  }
  assert.match(buildMapView({ ready: false }).hint, /开发诊断/);
  assert.match(buildMapView(null).hint, /先连上机器人/);
});

test("the rendered panel carries the coverage, the counts and a labelled canvas", () => {
  const nodes = renderMapNodes(buildMapView(payload()));
  const text = treeText(nodes);
  assert.match(text, /地图覆盖 44%/);
  assert.match(text, /还没走过/);
  assert.match(text, /9 格/);
  assert.match(text, /0\.05 米\/格/);
  assert.match(text, /机器人当前位置/);
  const canvas = findTag(nodes, "canvas");
  assert.ok(canvas, "the map must be drawn");
  assert.match(canvas.attributes["aria-label"] || "", /覆盖 44%/);
});

test("an unavailable map renders its explanation and nothing else", () => {
  const nodes = renderMapNodes(buildMapView({ ready: false }));
  assert.equal(findTag(nodes, "canvas"), null);
  assert.match(treeText(nodes), /地图还不可用/);
});

test("an empty payload renders nothing rather than an empty shell", () => {
  assert.equal(renderMapNodes(null), null);
});
