import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("./console_ui.js", import.meta.url), "utf8");
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const ui = sandbox.TangyingConsoleUI;

test("operator routes cannot expose diagnostics, including direct links", () => {
  assert.equal(ui.resolveRoute("#diagnostics", false), "workspace");
  assert.equal(ui.resolveRoute("#diagnostics", true), "diagnostics");
  assert.equal(ui.resolveRoute("#devices", false), "devices");
  assert.equal(ui.resolveRoute("#unknown", true), "workspace");
  assert.equal(ui.resolveRoute("#constructor", true), "workspace");
});

test("unknown and disconnected state never becomes a ready robot", () => {
  assert.equal(ui.connectionPresentation("CONNECTING").tone, "pending");
  assert.equal(ui.connectionPresentation("LOADING").label, "正在加载画面");
  assert.equal(ui.connectionPresentation("UNAVAILABLE").tone, "warning");
  assert.equal(ui.connectionPresentation("STALE").tone, "warning");
  assert.equal(ui.connectionPresentation("LIVE").label, "场景已同步");
  assert.equal(ui.connectionPresentation("surprise").label, "状态待确认");
  assert.equal(ui.connectionPresentation("constructor").label, "状态待确认");
});

test("task state labels distinguish failure, cancellation and waiting for approval", () => {
  assert.equal(ui.taskPresentation("SUCCEEDED").label, "任务已完成");
  assert.equal(ui.taskPresentation("FAILED").tone, "danger");
  assert.equal(ui.taskPresentation("CANCELLED").label, "任务已取消");
  assert.equal(ui.taskPresentation("AWAITING_APPROVAL").tone, "warning");
  assert.equal(ui.taskPresentation("unrecognized").label, "状态待确认");
  assert.equal(ui.taskPresentation("constructor").label, "状态待确认");
  assert.equal(ui.taskPresentation("SAFETY_STOPPED").tone, "danger");
  assert.equal(ui.taskPresentation("WAITING_USER").label, "需要你处理");
});

test("support summary includes correlation fields but excludes credentials and raw payloads", () => {
  const summary = ui.supportSummary({
    mode: "fleet", connection: "STALE", worldRevision: 92, eventCursor: "cursor-8",
    task: { id: "task-7", state: "FAILED", revision: 2, request: "private request", token: "secret" },
    token: "secret", password: "secret", telemetry: { apiKey: "secret" },
  });
  assert.equal(summary.task.id, "task-7");
  assert.equal(summary.world.revision, 92);
  assert.equal(summary.world.eventCursor, "cursor-8");
  assert.doesNotMatch(JSON.stringify(summary), /secret|private request|apiKey|password/);
});

test("task templates follow the selected simulation and never submit an action", () => {
  assert.match(ui.taskExamples("robocasa")[0].request, /方块/);
  assert.match(ui.taskExamples("mujoco")[0].request, /杯子/);
  assert.equal(ui.taskExamples("xlerobot_direct").length, 0);
});
