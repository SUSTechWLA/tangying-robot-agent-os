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

// The dead end this file used to have.
//
// `safetyAcknowledged` was hardcoded true in READY_INPUT and the console never
// assigned it, so the checklist could not reach "ready" in the real product while
// these tests said it could. The fix is a control, and a control is only real if
// it is rendered and if pressing it changes the verdict — so both halves are
// asserted here against the real functions.
test("the checklist offers a way to clear the safety item", () => {
  const readiness = withInput({ safetyAcknowledged: false });
  assert.equal(readiness.ready, false, "an unacknowledged area must not read as ready");
  const safety = readiness.items.find(entry => entry.id === "safety");
  assert.equal(safety.state, "action");
  assert.equal(safety.confirm, "safety", "the safety item must be clearable by the operator");
  assert.match(safety.confirmLabel, /确认/);
});

test("only the safety item is cleared by confirming rather than by fixing", () => {
  const readiness = withInput({ safetyAcknowledged: false, connection: "UNAVAILABLE" });
  const confirming = readiness.items.filter(entry => entry.confirm);
  // Copied into this realm's array before comparing: the items come from the vm
  // context, and a strict comparison against a host array fails on the prototype
  // rather than on the contents.
  assert.deepEqual([...confirming.map(entry => entry.id)], ["safety"]);
});

test("the rendered checklist contains the confirmation control and it reports a click", () => {
  const readiness = withInput({ safetyAcknowledged: false });
  const root = renderReadinessNodes(readiness);
  const button = findButton(root, "onboarding-confirm");
  assert.ok(button, "the readiness panel rendered no way to confirm safety");
  assert.equal(button.dataset.confirm, "safety");
  assert.equal(button.textContent, "我已确认现场安全");

  // The module asks; it never decides. A layer that could mark itself satisfied
  // could claim an acknowledgement nobody gave.
  const dispatched = [];
  context.dispatchEvent = event => dispatched.push(event);
  context.CustomEvent = FakeEvent;
  button.listeners.get("click")();
  assert.equal(dispatched.length, 1);
  assert.equal(dispatched[0].type, "tangying:onboarding-confirm");
  assert.equal(dispatched[0].detail.id, "safety");
});

test("acknowledging safety is what makes the checklist completable", () => {
  const before = withInput({ safetyAcknowledged: false });
  assert.equal(before.ready, false);
  assert.match(before.headline, /下一步：安全确认/);

  const after = withInput({ safetyAcknowledged: true });
  assert.equal(after.ready, true);
  assert.match(after.headline, /可以直接给机器人下指令/);
});

class FakeEvent {
  constructor(type, options = {}) {
    this.type = type;
    this.detail = options.detail;
  }
}

// findButton walks the rendered subtree for a class, which is how the assertions
// above stay about what a user can actually see and press.
function findButton(node, className) {
  if (!node || typeof node !== "object") return null;
  if (node.tagName === "button" && String(node.className).includes(className)) return node;
  for (const child of node.children || []) {
    const found = findButton(child, className);
    if (found) return found;
  }
  return null;
}

// The panel must not contradict the server.
//
// The browser computes connection, safety, calibration, cameras and map from what
// it can see; the server computes faults, unknown-outcome actions and supervision
// from what only it can see. If the panel ignored the server's rows it would say
// "可以直接下指令" while the API said the robot has a blocking fault — two answers
// to one question, and the wrong one in front of the user.
test("a blocking row from the server blocks the checklist", () => {
  const withServer = buildReadiness({
    ...READY_INPUT,
    server: {
      ready: false,
      summary: "机器人还不能用：机器人报告了故障，相关能力已停用。",
      nextId: "faults",
      checks: [
        { id: "faults", title: "机器人自检", state: "action",
          situation: "机器人报告了故障，相关能力已停用。",
          action: "先完成巡检建图并在控制台启用地图。", detail: "chassis:NAV_MAP_NOT_READY", blocking: true },
      ],
    },
  });
  assert.equal(withServer.ready, false, "the panel reported ready while the server reported a blocking fault");
  const faults = withServer.items.find(entry => entry.id === "server:faults");
  assert.ok(faults, "the server's blocking row never reached the checklist");
  assert.equal(faults.state, "action");
  assert.match(faults.action, /巡检建图/);
  assert.equal(faults.detail, "chassis:NAV_MAP_NOT_READY");
});

test("the server does not get a second opinion on a row the browser owns", () => {
  const readiness = buildReadiness({
    ...READY_INPUT,
    server: {
      ready: true,
      checks: [{ id: "map", title: "场景地图", state: "ready", situation: "地图已启用。" }],
    },
  });
  const map = readiness.items.filter(entry => entry.id === "map" || entry.id === "server:map");
  assert.equal(map.length, 1, "the map question was rendered twice, from two sources");
});

test("a server row with nothing to do still says something to do", () => {
  const readiness = buildReadiness({
    ...READY_INPUT,
    server: {
      ready: false, nextId: "reconciliation",
      checks: [{ id: "reconciliation", title: "动作结果", state: "action",
                 situation: "有动作的结果没能确认。", action: "", blocking: true }],
    },
  });
  const row = readiness.items.find(entry => entry.id === "server:reconciliation");
  assert.ok(row.action, "a blocking server row reached the user with no next step");
});

test("an unconfigured model is stated even when everything else is ready", () => {
  const readiness = buildReadiness({
    ...READY_INPUT,
    server: {
      ready: true, summary: "机器人可以用了。",
      language: { provider: "deterministic", modelConfigured: false,
                  vocabulary: "颜色 + 物品 + 位置", note: "还没有配置模型，只认固定词表。" },
    },
  });
  // It is not an item: a fixed vocabulary still works. It is a statement, and it
  // survives into the render.
  assert.equal(readiness.ready, true);
  assert.equal(readiness.language.modelConfigured, false);
  const root = renderReadinessNodes(readiness);
  assert.match(treeText(root), /只认固定词表/);
});

test("the language note is absent once a model is configured", () => {
  const readiness = buildReadiness({
    ...READY_INPUT,
    server: { ready: true, language: { provider: "openai", modelConfigured: true, note: "已配置模型。" } },
  });
  const root = renderReadinessNodes(readiness);
  assert.doesNotMatch(treeText(root), /只认固定词表/);
});


test("Gazebo readiness reports observed stop state without claiming physical safety acknowledgement", () => {
  const safety = input => buildReadiness(input).items.find(row => row.id === "safety");
  assert.equal(safety({ adapter: "gazebo", emergencyStopped: false }).state, "ready");
  assert.equal(safety({ adapter: "gazebo", emergencyStopped: true }).state, "action");
  assert.equal(safety({ adapter: "gazebo" }).state, "unknown");
  assert.equal(safety({ adapter: "xlerobot_direct", emergencyStopped: false }).state, "action");
  const map = buildReadiness({ map: { ready: true } }).items.find(row => row.id === "map");
  assert.doesNotMatch(map.situation, /已覆盖房间/);
});
