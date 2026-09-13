// The onboarding screen is what a non-expert trusts to decide "can I use the robot
// yet?", so these tests are about the promise it must never break: it may not claim
// readiness it has not observed, and every unfinished item has to say what to do.

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

// Loaded exactly as the console does: a classic script under a strict CSP, so a
// stray `export` would parse as an error and leave the global undefined.
// A minimal document, because the console builds real elements instead of
// assigning markup (server strings must never reach innerHTML).
function fakeElement(tag) {
  return {
    tagName: tag, className: "", textContent: "", children: [], dataset: {}, style: {},
    append(...nodes) { this.children.push(...nodes); },
    listeners: new Map(),
    addEventListener(name, callback) { this.listeners.set(name, callback); },
    click() { this.listeners.get("click")?.(); },
  };
}
const context = vm.createContext({ document: { createElement: fakeElement } });
vm.runInContext(await readFile(new URL("./onboarding.js", import.meta.url), "utf8"), context);
const { buildReadiness, calibrationReadiness, renderReadinessNodes } = context.TangyingOnboarding;

test("calibration readiness uses the validated service document cameras", () => {
  const state = calibrationReadiness({
    available: true,
    revision: "service-rev",
    document: { source: "simulation", cameras: { head: {}, base: {} } },
    session: { status: "completed" },
  }, {});
  assert.equal(state.cameraCount, 2);
  assert.equal(state.camerasMeasured, true);
  assert.equal(state.ready, true);
});

test("calibration readiness accepts the runtime public validation fields", () => {
  const state = calibrationReadiness(null, {
    calibration: { revision: "public-rev", source: "simulation", valid: true, cameraCount: 2, camerasMeasured: true },
  });
  assert.equal(state.revision, "public-rev");
  assert.equal(state.ready, true);
  assert.equal(state.cameraCount, 2);
  assert.equal(state.camerasMeasured, true);
});

function treeText(node) {
  return [node.textContent, ...node.children.map(treeText)].filter(Boolean).join(" ");
}

const READY_INPUT = {
  connection: "LIVE",
  adapter: "xlerobot",
  emergencyStopped: false,
  safetyAcknowledged: true,
  calibration: { revision: "a".repeat(64), source: "measured", cameraCount: 2, camerasMeasured: true },
  map: { ready: true, summary: "地图已覆盖五个房间。" },
};

function withInput(overrides = {}) {
  return buildReadiness({ ...READY_INPUT, ...overrides });
}

test("a fully prepared robot says so and asks for nothing", () => {
  const readiness = withInput();
  assert.equal(readiness.ready, true);
  assert.equal(readiness.completed, readiness.total);
  assert.match(readiness.headline, /可以直接给机器人下指令/);
  assert.equal(readiness.next, null);
});

test("nothing is assumed when the runtime has not reported it", () => {
  const readiness = buildReadiness({});
  assert.equal(readiness.ready, false, "an empty input must never read as ready");
  const states = readiness.items.map(entry => entry.state);
  assert.ok(states.includes("unknown") || states.includes("action"));
  for (const entry of readiness.items) {
    if (entry.state !== "ready") {
      assert.ok(entry.action, `${entry.id} must tell the user what to do`);
    }
  }
});

test("the first thing to do is the most blocking one", () => {
  const readiness = withInput({ connection: "UNAVAILABLE" });
  assert.equal(readiness.ready, false);
  assert.equal(readiness.next.id, "connection");
  assert.match(readiness.headline, /下一步：连接机器人/);
  assert.match(readiness.items[0].action, /USB/);
});

test("an uncalibrated robot is not allowed to be used", () => {
  const readiness = withInput({ calibration: null });
  assert.equal(readiness.ready, false);
  const calibration = readiness.items.find(entry => entry.id === "calibration");
  assert.equal(calibration.state, "action");
  assert.match(calibration.situation, /不要让它抓东西/);
  assert.equal(calibration.target, "calibration");
});

test("readiness follows service state rather than adapter provenance", () => {
  const readiness = withInput({
    calibration: { revision: "b".repeat(64), source: "simulation", cameraCount: 2, camerasMeasured: true },
  });
  assert.equal(readiness.ready, true);
  const calibration = readiness.items.find(entry => entry.id === "calibration");
  assert.match(calibration.situation, /参数已就绪/);
  assert.doesNotMatch(calibration.situation, /仿真|真机/);
});

test("an emergency stop outranks everything else", () => {
  const readiness = withInput({ emergencyStopped: true });
  assert.equal(readiness.next.id, "safety");
  assert.match(readiness.next.situation, /急停/);
});

test("an incomplete map turns into the next place to drive to", () => {
  const readiness = withInput({
    map: {
      ready: false,
      summary: "还不能开始任务：厨房只覆盖了 20%。",
      problems: ["厨房只覆盖了 20%，请进去走一圈"],
      nextTargets: [{ x: 2.2, y: 3.3, instruction: "开到地图上坐标 (2.2, 3.3) 附近，慢速通过" }],
    },
  });
  assert.equal(readiness.ready, false);
  const map = readiness.items.find(entry => entry.id === "map");
  assert.match(map.action, /厨房只覆盖了 20%/);
  assert.match(map.action, /\(2\.2, 3\.3\)/, "the user is told where to drive, not just what is wrong");
});

test("missing camera parameters are reported before the servos are blamed", () => {
  const readiness = withInput({
    calibration: { revision: "c".repeat(64), source: "measured", cameraCount: 0, camerasMeasured: false },
  });
  const cameras = readiness.items.find(entry => entry.id === "cameras");
  assert.equal(cameras.state, "action");
  assert.match(cameras.action, /相机标定/);
});

test("every unfinished item carries a target the console can navigate to", () => {
  const readiness = buildReadiness({});
  for (const entry of readiness.items) {
    if (entry.state === "ready") continue;
    assert.ok(["devices", "calibration", "mapping", ""].includes(entry.target),
      `${entry.id} points at an unknown place: ${entry.target}`);
  }
});

test("an unreadable map is not blamed on the connection", () => {
  // The robot can be connected and the map still unavailable (no navigation
  // stack); telling the user to connect would send them to the wrong place.
  const map = withInput({ map: null }).items.find(entry => entry.id === "map");
  assert.equal(map.state, "unknown");
  assert.doesNotMatch(map.action, /连上机器人/);
  assert.match(map.action, /重新检查|SLAM 建图/);
});

test("the screen renders one row per prerequisite with its state in words", () => {
  const nodes = renderReadinessNodes(withInput({ connection: "UNAVAILABLE" }));
  assert.ok(nodes, "a readiness result must produce a subtree");
  const text = treeText(nodes);
  assert.match(text, /连接机器人/);
  assert.match(text, /需要处理/);
  assert.match(text, /重试连接/);
  assert.match(text, /去处理/, "the user gets a way to act on it");
  // Plain language, not field names.
  assert.doesNotMatch(text, /calibration_revision|emergencyStopped|nextTargets|undefined/);
});

test("every workbench action button is bound to its declared page", () => {
  const targets = [];
  context.TangyingConsoleUI = { navigate(route) { targets.push(route); } };
  const nodes = renderReadinessNodes(buildReadiness({}));
  const buttons = [];
  const visit = node => {
    if (node?.dataset?.target) buttons.push(node);
    for (const child of node.children) visit(child);
  };
  visit(nodes);
  for (const button of buttons) button.click();
  assert.ok(buttons.some(button => button.dataset.target === "calibration"));
  assert.deepEqual(targets, buttons.map(button => button.dataset.target));
});

test("an empty readiness result renders nothing rather than an empty shell", () => {
  assert.equal(renderReadinessNodes(null), null);
});
