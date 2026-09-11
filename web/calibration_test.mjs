// The calibration card flow renders what the Python wizard reports. These tests
// hold two lines: the console must not invent steps or progress the wizard did not
// report, and it must not treat "no session yet" as a failure.

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

function fakeElement(tag) {
  return {
    tagName: tag, className: "", textContent: "", children: [], dataset: {}, style: {},
    append(...nodes) { this.children.push(...nodes); },
  };
}
const context = vm.createContext({ document: { createElement: fakeElement } });
vm.runInContext(await readFile(new URL("./calibration.js", import.meta.url), "utf8"), context);
const { buildCalibrationFlow, renderCalibrationNodes } = context.TangyingCalibration;

function treeText(node) {
  return [node.textContent, ...node.children.map(treeText)].filter(Boolean).join(" ");
}

function snapshot(overrides = {}) {
  return {
    available: true,
    robotId: "xlerobot-01",
    completed: 2,
    total: 4,
    summary: "已完成 2/4 步；下一步：左臂 · 肘部。",
    steps: [
      { id: "preflight", index: 1, total: 4, group: "准备", title: "开始前的安全检查", instruction: "逐条确认下面四项。", detail: "", status: "done" },
      { id: "zero:left_arm_shoulder_pan", index: 2, total: 4, group: "左臂", title: "左臂 · 肩部旋转", instruction: "用手把这一节转到正对机器人正前方的位置。", detail: "总线 left，舵机 ID 1。", status: "done" },
      { id: "zero:left_arm_gripper", index: 3, total: 4, group: "左臂", title: "左臂 · 夹爪", instruction: "用手让两个指头刚好轻轻接触。", detail: "总线 left，舵机 ID 6。", status: "current" },
      { id: "review", index: 4, total: 4, group: "完成", title: "检查并保存", instruction: "确认下面的参数。", detail: "", status: "pending" },
    ],
    ...overrides,
  };
}

test("no session yet is a normal state with a way forward, not an error", () => {
  const flow = buildCalibrationFlow({ available: false, reason: "还没有标定会话记录。" });
  assert.equal(flow.kind, "unavailable");
  assert.equal(flow.steps.length, 0);
  assert.match(flow.headline, /还没有开始标定/);
  assert.match(flow.hint, /打开标定向导/);
  assert.match(treeText(renderCalibrationNodes(flow)), /打开标定向导/);
});

test("a missing snapshot is handled the same way as an explicit absence", () => {
  assert.equal(buildCalibrationFlow(null).kind, "unavailable");
  assert.equal(buildCalibrationFlow(undefined).kind, "unavailable");
});

test("progress comes from the wizard unchanged", () => {
  const flow = buildCalibrationFlow(snapshot());
  assert.equal(flow.completed, 2);
  assert.equal(flow.total, 4);
  assert.equal(flow.percent, 50);
  assert.equal(flow.kind, "running");
  assert.equal(flow.current.id, "zero:left_arm_gripper");
  assert.match(flow.headline, /第 3 \/ 4 步/);
});

test("the instruction shown is the one the wizard wrote", () => {
  const flow = buildCalibrationFlow(snapshot());
  // Not re-worded, not summarised: the operator reads the same sentence the
  // terminal prints.
  assert.equal(flow.hint, "用手让两个指头刚好轻轻接触。");
  const text = treeText(renderCalibrationNodes(flow));
  assert.match(text, /用手让两个指头刚好轻轻接触/);
  assert.match(text, /总线 left，舵机 ID 6/, "the current step keeps its bus and servo detail");
});

test("only the current step and what follows it are shown as cards", () => {
  const flow = buildCalibrationFlow(snapshot());
  const text = treeText(renderCalibrationNodes(flow));
  assert.match(text, /左臂 · 夹爪/);
  assert.match(text, /检查并保存/);
  // Finished work collapses instead of pushing the current step off the screen.
  assert.match(text, /已完成的 2 步/);
});

test("a finished calibration says so and stops asking for work", () => {
  const flow = buildCalibrationFlow(snapshot({
    completed: 4,
    steps: snapshot().steps.map(step => ({ ...step, status: "done" })),
  }));
  assert.equal(flow.kind, "done");
  assert.equal(flow.current, null);
  assert.equal(flow.percent, 100);
  assert.match(flow.headline, /标定已完成/);
  assert.match(flow.hint, /可以开始建图或执行任务/);
});

test("the screen never invents a step or a step count", () => {
  const flow = buildCalibrationFlow(snapshot({ steps: [] , total: 0, completed: 0 }));
  assert.equal(flow.steps.length, 0);
  assert.equal(flow.total, 0);
  assert.equal(flow.current, null);
  assert.equal(renderCalibrationNodes(flow) === null, false, "an empty flow still renders its state");
});

test("an empty flow renders nothing rather than an empty shell", () => {
  assert.equal(renderCalibrationNodes(null), null);
});
