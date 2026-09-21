// The step guide draws the wizard's `guide` object and nothing else.
//
// Three lines are held here. Every kind in the contract must draw, and a kind
// this build has never seen must draw nothing rather than something wrong. The
// panel must outlive the step it is showing — one context, re-shown — because a
// context per poll is how a browser runs out of them halfway through a
// calibration. And a guide must survive not being able to draw at all: the
// wizard's sentence is what the operator acts on, and it is in the DOM either way.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import * as THREE from "three";

import { CalibrationGuide } from "./src/calibration_guide.js";

class FakeCanvas {
  constructor(width = 640, height = 360) {
    this.width = width;
    this.height = height;
    this.clientWidth = width;
    this.clientHeight = height;
    this.hidden = false;
    this.dataset = {};
    this.listeners = new Map();
    this.attributes = new Map();
  }
  getContext() { return null; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  addEventListener(name, handler) { this.listeners.set(name, handler); }
  removeEventListener(name, handler) {
    if (this.listeners.get(name) === handler) this.listeners.delete(name);
  }
}

class FakeRenderer {
  constructor() {
    this.frames = [];
    this.disposed = 0;
    this.contextLosses = 0;
    this.pixelRatio = 0;
    this.size = null;
  }
  setPixelRatio(value) { this.pixelRatio = value; }
  setSize(width, height, updateStyle) { this.size = [width, height, updateStyle]; }
  render(scene, camera) { this.frames.push({ scene, camera }); }
  dispose() { this.disposed += 1; }
  forceContextLoss() { this.contextLosses += 1; }
}

/** A frame source that keeps its callbacks, so a leaked frame is visible. */
function frameSource() {
  const pending = new Map();
  let next = 1;
  return {
    pending,
    requestFrame(callback) { const handle = next++; pending.set(handle, callback); return handle; },
    cancelFrame(handle) { pending.delete(handle); },
  };
}

function harness(guide = null, options = {}) {
  const canvas = new FakeCanvas();
  const renderer = new FakeRenderer();
  const frames = frameSource();
  const reported = [];
  const instance = new CalibrationGuide(canvas, guide, {
    rendererFactory: () => renderer,
    requestFrame: frames.requestFrame,
    cancelFrame: frames.cancelFrame,
    now: () => 1000,
    onProgress: progress => reported.push(progress),
    ...options,
  });
  return { canvas, renderer, frames, reported, guide: instance };
}

//: The contract, as the wizard writes it. Every kind appears exactly once.
const GUIDES = [
  { kind: "connect", ports: [
    { id: "power", port: "power", label: "接通机器人电源" },
    { id: "usb", port: "usb", label: "插入控制板数据线" },
    { id: "head_camera", port: "camera", label: "插入头部 RGB-D 相机" },
    { id: "base_camera", port: "camera", label: "插入底盘 RGB-D 相机" },
  ] },
  { kind: "preflight", checks: [
    { id: "hardware_connected", label: "机器人已通电，USB 线已插好" },
    { id: "area_clear", label: "机械臂周围一个手臂范围内没有人和杂物" },
  ] },
  { kind: "zero", motor: "left_arm_1", side: "left", joint: "1" },
  { kind: "travel", side: "left", joints: ["1", "2", "3", "4", "5", "gripper"] },
  { kind: "intrinsics", camera: "head-rgbd", views: 12 },
  { kind: "handeye", cameras: ["head-rgbd"], poses: 8 },
  { kind: "review" },
];

//: Steps a deployment can really send that the frozen examples above do not:
//: a plan that lists nothing to work through, the other arm, a joint written as
//: a number, and a joint this build has no motion for.
const EDGE_GUIDES = [
  { kind: "connect", ports: [] },
  { kind: "preflight", checks: [] },
  { kind: "zero", motor: "right_arm_3", side: "right", joint: "3" },
  { kind: "zero", motor: "base_left_wheel", side: "shared", joint: "base_left_wheel" },
  { kind: "zero", motor: "head_motor_2", side: "shared", joint: "head_motor_2" },
  { kind: "zero", motor: "left_arm_mystery_joint", side: "left", joint: "mystery_joint" },
  { kind: "travel", side: "right", joints: [] },
  { kind: "intrinsics", camera: "wrist-rgbd", views: 4 },
  { kind: "handeye", cameras: ["head-rgbd", "wrist-rgbd"], poses: 3 },
];

test("every kind in the contract draws a frame without throwing", () => {
  for (const guide of GUIDES) {
    const { canvas, renderer, guide: instance } = harness(guide);
    assert.equal(instance.status.state, "READY", guide.kind);
    assert.equal(instance.status.code, "CALIBRATION_GUIDE_READY", guide.kind);
    assert.equal(instance.stage.children.length > 0, true, `${guide.kind} drew nothing`);
    assert.equal(instance.render(1016), true, guide.kind);
    assert.equal(renderer.frames.length, 1, guide.kind);
    assert.deepEqual(renderer.size, [canvas.width, canvas.height, false], guide.kind);
    instance.dispose();
  }
});

test("an unknown kind is a no-op rather than a wrong picture", () => {
  const unknown = harness({ kind: "teleport" });
  assert.equal(unknown.guide.status.state, "READY");
  assert.equal(unknown.guide.status.code, "CALIBRATION_GUIDE_KIND_UNKNOWN");
  assert.equal(unknown.guide.stage.children.length, 0, "an unknown step must not invent a scene");
  assert.equal(unknown.guide.render(1000), true);
  assert.equal(unknown.renderer.frames.length, 1);
  // A guide with no kind at all is the same state, reported differently.
  const empty = harness({});
  assert.equal(empty.guide.status.code, "CALIBRATION_GUIDE_EMPTY");
  assert.equal(empty.guide.render(1000), true);
  unknown.guide.dispose();
  empty.guide.dispose();
});

test("switching guides rebuilds the scene and gives the last one back", () => {
  const { guide } = harness(GUIDES[0]);
  const first = guide.stage.children[0];
  const tracked = guide.owned[0];
  let released = 0;
  const release = tracked.dispose.bind(tracked);
  tracked.dispose = () => { released += 1; release(); };

  guide.show(GUIDES[4]);
  assert.equal(guide.kind, "intrinsics");
  assert.equal(guide.status.code, "CALIBRATION_GUIDE_READY");
  // The panel is long-lived and the wizard walks twenty steps through it, so a
  // geometry from the step before must be released, not accumulated.
  assert.equal(released, 1);
  assert.equal(guide.owned.includes(tracked), false);
  assert.notEqual(guide.stage.children[0], first);
  assert.equal(first.parent, null, "the old step's nodes are detached");
  guide.dispose();
});

test("dispose is idempotent and leaves no frame, listener or context behind", () => {
  const { canvas, renderer, frames, guide } = harness(GUIDES[0]);
  assert.equal(frames.pending.size, 1, "an animating guide holds one frame");
  guide.dispose();
  assert.equal(frames.pending.size, 0);
  assert.equal(guide.frameRequest, null);
  assert.equal(canvas.listeners.size, 0);
  assert.equal(renderer.disposed, 1);
  assert.equal(renderer.contextLosses, 1);
  assert.equal(guide.status.state, "DISPOSED");
  assert.equal(guide.render(2000), false, "a disposed guide draws nothing");
  guide.dispose();
  guide.show(GUIDES[1]);
  assert.equal(renderer.disposed, 1, "disposing twice must not dispose the renderer twice");
  assert.equal(renderer.frames.length, 0);
});

test("a canvas that cannot give WebGL degrades to a reported no-op", () => {
  const canvas = new FakeCanvas();
  const frames = frameSource();
  let guide = null;
  assert.doesNotThrow(() => {
    guide = new CalibrationGuide(canvas, GUIDES[0], {
      rendererFactory: () => { throw new Error("Error creating WebGL context."); },
      requestFrame: frames.requestFrame,
      cancelFrame: frames.cancelFrame,
      now: () => 1000,
    });
  });
  assert.equal(guide.status.state, "UNAVAILABLE");
  assert.equal(guide.status.code, "WEBGL_UNAVAILABLE");
  assert.equal(guide.render(1000), false);
  assert.equal(frames.pending.size, 0);
  assert.doesNotThrow(() => { guide.show(GUIDES[2]); guide.dispose(); guide.dispose(); });

  // No element at all is a different reason, and just as quiet.
  const missing = new CalibrationGuide(null, GUIDES[0], { rendererFactory: () => new FakeRenderer() });
  assert.equal(missing.status.code, "CALIBRATION_GUIDE_CANVAS_MISSING");
  assert.equal(missing.render(1000), false);
  missing.dispose();
});

test("reduced motion draws one settled frame and never animates", () => {
  const { guide } = harness({ kind: "travel", side: "left", joints: ["1", "2"] }, { reducedMotion: true });
  assert.equal(guide.render(1000), true);
  const first = guide.stage.children.map(child => child.rotation.y).join(",");
  const at = guide.elapsedMs;
  assert.equal(guide.render(9000), true);
  assert.equal(guide.elapsedMs, at, "the clock is frozen, so every frame is the same picture");
  assert.equal(guide.stage.children.map(child => child.rotation.y).join(","), first);
  // It is still a real frame: the settled pose is the arm half way through its
  // sweep, not an empty stage.
  assert.equal(guide.stage.children.length > 0, true);
  assert.equal(at, guide.staticFrameMs);
  guide.dispose();
});

test("the travel arc shows the range covered, stop to stop", () => {
  const { guide } = harness({ kind: "travel", side: "left", joints: [] });
  const lit = () => guide.arcDots.filter(({ dot }) => dot.material.emissiveIntensity > 0.1).length;
  guide.render(1000); // the lower stop: nothing covered yet
  const atLowerStop = lit();
  guide.render(1000 + guide.staticFrameMs); // mid-sweep
  const atMiddle = lit();
  guide.render(1000 + guide.staticFrameMs * 2); // the upper stop
  const atUpperStop = lit();
  assert.ok(atLowerStop <= 2, `expected an empty arc at the lower stop, saw ${atLowerStop}`);
  assert.ok(atMiddle > atLowerStop, "the arc must fill as the arm sweeps");
  assert.ok(atUpperStop > atMiddle, "and keep filling to the far stop");
  assert.equal(atUpperStop, guide.arcDots.length);
  guide.dispose();
});

test("the hand-eye board stays anchored while the arm carries the camera", () => {
  const { guide } = harness({ kind: "handeye", cameras: ["head-rgbd"], poses: 8 });
  const anchored = guide.anchoredBoard;
  const rest = anchored.position.toArray().join(",");
  const rotation = anchored.rotation.toArray().slice(0, 3).join(",");
  for (let pose = 0; pose < 8; pose += 1) {
    guide.render(1000 + pose * 1700 + 100);
    assert.equal(anchored.position.toArray().join(","), rest, `pose ${pose} moved the board`);
    assert.equal(anchored.rotation.toArray().slice(0, 3).join(","), rotation, `pose ${pose} rotated the board`);
  }
  // The board the operator must not move is the one thing the animation must not
  // move, and the arm it does move is a different object.
  assert.equal(anchored.parent, guide.stage);
  guide.dispose();
});

/**
 * Where each object a step draws lands on screen, in normalised canvas
 * coordinates, at one moment. The floor is left out on purpose: it runs off the
 * edge of every picture by design, and measuring it would say nothing about
 * whether the machine is in frame.
 */
function projectedExtent(stage, camera) {
  const corner = new THREE.Vector3();
  const extent = { left: Infinity, right: -Infinity, bottom: Infinity, top: -Infinity };
  stage.traverse(object => {
    if (!object.isMesh && !object.isLine) return;
    for (let parent = object; parent; parent = parent.parent) {
      if (parent.name === "guide-ground") return;
    }
    if (!object.geometry.boundingBox) object.geometry.computeBoundingBox();
    const box = object.geometry.boundingBox;
    if (!box) return;
    for (const x of [box.min.x, box.max.x]) {
      for (const y of [box.min.y, box.max.y]) {
        for (const z of [box.min.z, box.max.z]) {
          corner.set(x, y, z).applyMatrix4(object.matrixWorld).project(camera);
          extent.left = Math.min(extent.left, corner.x);
          extent.right = Math.max(extent.right, corner.x);
          extent.bottom = Math.min(extent.bottom, corner.y);
          extent.top = Math.max(extent.top, corner.y);
        }
      }
    }
  });
  return extent;
}

test("every kind stays inside its canvas as the animation runs", () => {
  // Nobody can watch the animation in a test, so it is measured instead: every
  // corner of every object a step draws has to land inside the picture, and the
  // step has to fill enough of it to be worth looking at.
  for (const guide of [...GUIDES, ...EDGE_GUIDES]) {
    const { guide: instance } = harness(guide);
    for (const factor of [0, 1, 2, 3]) {
      const label = `${guide.kind}${guide.joint ? `:${guide.joint}` : ""}`;
      instance.render(instance.startedAt + instance.staticFrameMs * factor + 120);
      const extent = projectedExtent(instance.stage, instance.camera);
      const coverage = Math.min(extent.right - extent.left, extent.top - extent.bottom) / 2;
      assert.ok(extent.left > -1.02 && extent.right < 1.02 && extent.bottom > -1.02 && extent.top < 1.02,
        `${label} draws outside the canvas: x ${extent.left.toFixed(2)}..${extent.right.toFixed(2)},`
        + ` y ${extent.bottom.toFixed(2)}..${extent.top.toFixed(2)}`);
      assert.ok(coverage > 0.25, `${label} fills only ${(coverage * 100).toFixed(0)}% of the canvas`);
    }
    instance.dispose();
  }
});

test("the guide reports which item of the step it is showing", () => {
  const { guide, reported } = harness({ kind: "connect", ports: GUIDES[0].ports });
  assert.deepEqual(reported.at(-1), { index: 0, count: 4, kind: "connect" });
  guide.render(3000);
  assert.deepEqual(reported.at(-1), { index: 1, count: 4, kind: "connect" });
  guide.render(9100);
  assert.deepEqual(reported.at(-1), { index: 0, count: 4, kind: "connect" });

  // A step that lists nothing to work through reports nothing to highlight.
  const none = harness({ kind: "connect", ports: [] });
  assert.deepEqual(none.reported.at(-1), { index: -1, count: 0, kind: "connect" });
  assert.equal(none.guide.connections.length, 0);
  guide.dispose();
  none.guide.dispose();
});

// --- the seam with the page -------------------------------------------------

const calibrationSource = await readFile(new URL("./calibration.js", import.meta.url), "utf8");

class FakeNode {
  constructor(tag = "") {
    this.tagName = tag;
    this.className = "";
    this.textContent = "";
    this.children = [];
    this.hidden = false;
    this.attributes = new Map();
    this.style = {};
    this.dataset = {};
  }
  append(...nodes) { this.children.push(...nodes); }
  replaceChildren(...nodes) { this.children = [...nodes]; }
  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  removeAttribute(name) { this.attributes.delete(name); }
  // Server strings are text here, and only text: a fake that accepted markup
  // would let the panel start injecting it without any test noticing.
  get innerHTML() { return ""; }
  set innerHTML(_) { throw new Error("server strings must never be injected as HTML"); }
}

/** The guide panel as index.html declares it, plus a recording CalibrationGuide. */
function panelHarness({ withWebGL = true } = {}) {
  const nodes = new Map();
  // index.html declares these three hidden until a session has a current step.
  const startsHidden = new Set(["calibration-guide", "calibration-guide-note", "calibration-guide-legend"]);
  const node = (id) => {
    if (!nodes.has(id)) {
      const created = new FakeNode(id.includes("canvas") ? "canvas" : "div");
      created.hidden = startsHidden.has(id);
      nodes.set(id, created);
    }
    return nodes.get(id);
  };
  const constructed = [];
  class RecordingGuide {
    constructor(canvas, guide) {
      this.canvas = canvas;
      this.shown = [guide];
      this.progress = { index: 0, count: 0 };
      this.status = { state: "READY", code: "CALIBRATION_GUIDE_READY" };
      this.disposed = 0;
      constructed.push(this);
    }
    show(guide) { this.shown.push(guide); return this; }
    dispose() { this.disposed += 1; }
  }
  const sandboxGlobal = withWebGL ? { TangyingWebGL: { CalibrationGuide: RecordingGuide } } : {};
  const context = vm.createContext({
    console,
    document: { createElement: tag => new FakeNode(tag), getElementById: id => node(id) },
    globalThis: sandboxGlobal,
  });
  vm.runInContext(calibrationSource, context, { filename: "calibration.js" });
  return { api: sandboxGlobal.TangyingCalibration, node, constructed };
}

function flowWith(guide, current = {}) {
  return {
    kind: "running",
    current: { id: "step", index: 1, title: "左臂 · 夹爪", instruction: "用手让两个指头刚好轻轻接触。", guide, ...current },
  };
}

test("the guide panel follows the current step, reusing one view", () => {
  const { api, node, constructed } = panelHarness();
  const panel = node("calibration-guide");
  const canvas = node("calibration-guide-canvas");
  assert.equal(panel.hidden, true, "a panel with no session starts hidden");

  const first = api.syncCalibrationGuide(flowWith(api.stepGuide(GUIDES[0])));
  assert.equal(constructed.length, 1);
  assert.equal(first.shown[0].kind, "connect");
  assert.equal(panel.hidden, false);
  assert.equal(canvas.hidden, false);
  assert.match(node("calibration-guide-caption").textContent, /电源/);
  assert.equal(canvas.attributes.get("aria-label"), "标定步骤示意图：左臂 · 夹爪");

  // The legend is the wizard's own labels, marking the item the animation is on.
  const legend = node("calibration-guide-legend");
  assert.equal(legend.children.length, 4);
  assert.equal(legend.children[0].textContent, "接通机器人电源");
  assert.equal(legend.children[0].attributes.get("aria-current"), "step");
  assert.equal(legend.hidden, false);

  // The next step is shown on the same view: one context for the whole session.
  const second = api.syncCalibrationGuide(flowWith(api.stepGuide(GUIDES[2]), {
    id: "zero:left_arm_shoulder_pan", index: 2, title: "左臂 · 肩部旋转", instruction: "用手把这一节转到正中间。",
  }));
  assert.equal(constructed.length, 1, "a step change must not build a second context");
  assert.equal(second, first);
  assert.deepEqual(first.shown.map(guide => guide.kind), ["connect", "zero"]);
  assert.equal(canvas.attributes.get("aria-label"), "标定步骤示意图：左臂 · 肩部旋转");
});

test("the guide panel is released when the flow leaves the step behind", () => {
  const { api, node, constructed } = panelHarness();
  const panel = node("calibration-guide");
  const view = api.syncCalibrationGuide(flowWith(api.stepGuide(GUIDES[5])));
  assert.equal(constructed.length, 1);
  assert.equal(node("calibration-guide-legend").children.length, 1, "hand-eye counts poses");
  // The camera is named because the wizard named it: the step is about one
  // camera on a robot that has several.
  assert.equal(node("calibration-guide-legend").children[0].textContent, "head-rgbd · 第 1 / 8 个姿态");

  // A finished calibration has no current step, so the panel hands its context back.
  assert.equal(api.syncCalibrationGuide({ kind: "done", current: null }), null);
  assert.equal(view.disposed, 1);
  assert.equal(panel.hidden, true);
  assert.equal(api.syncCalibrationGuide(null), null);
  api.disposeCalibrationGuide();
  assert.equal(view.disposed, 1, "teardown must be idempotent");
});

test("a step with no animation still names itself and says so in words", () => {
  const { api, node } = panelHarness();
  // Through stepGuide, because that is the only way the app ever builds a step.
  const view = api.syncCalibrationGuide(flowWith(api.stepGuide({ kind: "interpretive-dance" })));
  assert.equal(view.shown[0].kind, "interpretive-dance");
  assert.equal(node("calibration-guide-caption").textContent, "这一步没有示意图，照上面的文字做就可以。");
  assert.equal(node("calibration-guide-note").textContent, "这一步没有示意图，照上面的文字做就可以。");
  assert.equal(node("calibration-guide-note").hidden, false);
  assert.equal(node("calibration-guide-legend").hidden, true);
});

test("without a WebGL build the panel keeps the words and loses only the picture", () => {
  const { api, node } = panelHarness({ withWebGL: false });
  assert.equal(api.syncCalibrationGuide(flowWith(api.stepGuide(GUIDES[4]))), null);
  assert.equal(node("calibration-guide-canvas").hidden, true);
  assert.equal(node("calibration-guide-note").textContent, "这台设备暂时不能显示动画，照上面的文字做这一步就可以。");
  assert.match(node("calibration-guide-caption").textContent, /标定板/);
  assert.doesNotThrow(() => api.disposeCalibrationGuide());
});

test("leaving the page and coming back rebuilds the animation", () => {
  // app.js renders the cards through a key that means "this is already drawn", and
  // it disposes the guide when the operator navigates away. If the teardown did not
  // invalidate that key, the identical render on return would be skipped as a
  // duplicate and the animation would never come back.
  const { api, node, constructed } = panelHarness();
  const body = node("calibration-body");
  const flow = flowWith(api.stepGuide(GUIDES[0]));

  api.syncCalibrationGuide(flow);
  assert.equal(constructed.length, 1);
  // What app.js does for a step it just drew.
  body.dataset.renderKey = "drawn";

  api.disposeCalibrationGuide();
  assert.equal(constructed[0].disposed, 1, "the context is given back on the way out");
  assert.equal(body.dataset.renderKey, undefined,
    "tearing the animation down makes 'already drawn' untrue");
  assert.equal(node("calibration-guide").hidden, true);

  // Coming back: the same flow, rendered again, must produce a visible animation.
  api.syncCalibrationGuide(flow);
  assert.equal(constructed.length, 2, "the return trip creates a fresh view");
  assert.equal(constructed[1].disposed, 0);
  assert.equal(node("calibration-guide").hidden, false);
});
