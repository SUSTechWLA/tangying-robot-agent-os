import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
const context = vm.createContext({});
vm.runInContext(await readFile(new URL("./navigation_view.js", import.meta.url), "utf8"), context);
const view = context.TangyingNavigationView;
const grid = () => ({ frameId:"map",mapRevision:"one",width:2,height:2,resolution:.5,cells:[-1,0,50,100],origin:[0,0,0,1,0,0,0],mapPose:[.25,.25,0,1,0,0,0],poseSource:"rtabmap_tf",poseObservedAtUnixMs:1000 });
test("navigation map keeps unknown distinct from free and occupied", () => {
  assert.equal(view.validateGrid(grid()),true);
  assert.notDeepEqual([...view.cellColor(-1)],[...view.cellColor(0)]);
  assert.notDeepEqual([...view.cellColor(-1)],[...view.cellColor(100)]);
  for (const change of [{cells:[0]}, {cells:[-1,0,101,0]}, {frameId:"world"}, {resolution:0}, {origin:[0,0,0,0,0,0,0]}]) assert.equal(view.validateGrid({...grid(),...change}),false);
});
test("map robot marker uses measured map pose with Y flip and rotated origin", () => {
  assert.deepEqual({...view.robotPixel(grid(),1000)},{x:.5,y:1.5,yaw:0});
  const map = {...grid(),origin:[1,0,0,Math.SQRT1_2,0,0,Math.SQRT1_2],mapPose:[.75,.25,0,Math.SQRT1_2,0,0,Math.SQRT1_2]};
  const pixel=view.robotPixel(map,1000);
  assert.ok(Math.abs(pixel.x-.5)<1e-8 && Math.abs(pixel.y-1.5)<1e-8);
  assert.equal(view.robotPixel(grid(),2001),null);
  assert.equal(view.robotPixel({...grid(),poseSource:"sim_truth"},1000),null);
});

async function displayFixture() {
  let now = 1000, marker = false;
  const timeouts = new Map();
  let timer = 0;
  const ctx = { clearRect() { marker = false; }, drawImage() {}, save() {}, translate() {}, rotate() {}, beginPath() {}, moveTo() {}, lineTo() {}, closePath() {}, fill() { marker = true; }, stroke() {}, restore() {} };
  const elements = {
    "local-navigation-panel": { open: true, addEventListener() {} },
    "navigation-map-canvas": { width: 320, height: 240, getContext() { return ctx; } },
    "navigation-map-help": {}, "navigation-map-state": { dataset: {} }, "navigation-map-details": {},
  };
  const requests = [];
  const root = vm.createContext({ Date: { now: () => now }, AbortController,
    setTimeout(fn, delay) { timeouts.set(++timer, { fn, due: now + delay }); return timer; },
    clearTimeout(id) { timeouts.delete(id); }, setInterval() { return 1; },
    fetch() { return new Promise((resolve, reject) => requests.push({ resolve, reject })); },
    document: { getElementById: id => elements[id], createElement: () => ({ getContext: () => ({ createImageData: (w, h) => ({ data: new Uint8ClampedArray(w*h*4) }), putImageData() {} }) }) },
  });
  vm.runInContext(await readFile(new URL("./navigation_view.js", import.meta.url), "utf8"), root);
  const snapshot = { robotId: "robot-1", robotState: { navigation: { backend: "rtabmap_nav2" } } };
  root.TangyingNavigationView.update(snapshot);
  requests[0].resolve({ ok: true, json: async () => ({ ...grid(), robotId: "robot-1", ready: true }) });
  await new Promise(resolve => setImmediate(resolve));
  return { root, snapshot, elements, requests, marker: () => marker,
    advance(value) { now = value; for (const [id, entry] of [...timeouts]) if (entry.due <= now) { timeouts.delete(id); entry.fn(); } } };
}

test("localization expires while the next map HTTP request is still pending", async () => {
  const fixture = await displayFixture();
  assert.equal(fixture.marker(), true);
  assert.equal(fixture.elements["navigation-map-state"].dataset.tone, "success");
  fixture.root.TangyingNavigationView.update(fixture.snapshot); // Deliberately unresolved second HTTP request.
  assert.equal(fixture.requests.length, 2);
  fixture.advance(2001);
  assert.equal(fixture.marker(), false);
  assert.equal(fixture.elements["navigation-map-canvas"].hidden, false); // Saved grid remains readable.
  assert.equal(fixture.elements["navigation-map-state"].dataset.tone, "pending");
});

test("map disconnection retains only saved geometry and cannot keep a live marker", async () => {
  const fixture = await displayFixture();
  fixture.root.TangyingNavigationView.update(fixture.snapshot);
  fixture.requests[1].reject(new Error("offline"));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(fixture.marker(), false);
  assert.equal(fixture.elements["navigation-map-canvas"].hidden, false);
  assert.match(fixture.elements["navigation-map-help"].textContent, /已保存地图/);
});
