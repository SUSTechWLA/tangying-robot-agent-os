import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const context = vm.createContext({});
vm.runInContext(await readFile(new URL("./task_trace.js", import.meta.url), "utf8"), context);
const { buildTaskTrace, renderTaskTrace } = context.TangyingTaskTrace;
const start = Date.parse("2026-10-05T03:33:00.000Z");
function event(sequence, type, offset, payload = {}, stepId = "rev-1-cap-01") {
  return { sequence, type, stepId, occurredAt: new Date(start + offset).toISOString(), payload };
}
function task(tool, events) {
  return { id: "long-goal", state: "SUCCEEDED", currentRevision: 1, plan: { capabilities: { calls: [{ tool, arguments: {} }] } }, events };
}
function mappingEvents() {
  return [
    event(1, "CAPABILITY_CALL", 0, { tool: "mapping.build", commandId: "long-goal/rev-1-cap-01", arguments: { mode: "survey" } }),
    event(2, "TOOL_ACTIVITY", 1, { toolName: "mapping.build", activityStatus: "RUNNING" }),
    event(3, "CAPABILITY_RECEIPT", 5, { tool: "mapping.build", result: { sessionId: "scan-1", state: "moving" } }),
    event(4, "TOOL_ACTIVITY", 6, { toolName: "mapping.build", activityStatus: "AWAITING_EVIDENCE" }),
    event(5, "CAPABILITY_PROGRESS", 120000, { operationId: "scan-1", state: "moving" }),
    event(6, "CAPABILITY_VERIFIED", 540000, { tool: "mapping.build", evidence: { sessionId: "scan-1", state: "completed", activeMap: { mapId: "map-1", mapRevision: "rev-a" } } }),
    event(7, "TOOL_ACTIVITY", 540001, { toolName: "mapping.build", activityStatus: "CONFIRMED" }),
  ];
}

test("mapping parent uses capability lifecycle for physical effects, one dispatch and nine-minute duration", () => {
  const trace = buildTaskTrace({ task: task("mapping.build", mappingEvents()) });
  const step = trace.steps[0];
  assert.equal(step.mutatesWorld, true);
  assert.equal(step.attempts, 1);
  assert.equal(step.durationS, 540);
  assert.equal(step.status, "CONFIRMED");
  assert.equal(step.capabilityVerified, true);
  assert.equal(trace.counts.CONFIRMED, 1, "mirrored TOOL_ACTIVITY must not count a second confirmation");
  assert.equal(trace.summary.errors, 0, "structured contract verification is not a missing RGB-D capture");
  const html = renderTaskTrace(trace);
  assert.match(html, /耗时 9 分 0\.0 s/);
  assert.match(html, /派发 1 次/);
  assert.match(html, /改变世界/);
  assert.match(html, /结构化状态/);
  assert.match(html, /地图 map-1/);
  assert.doesNotMatch(html, /完成判定缺少依据/);
});

test("a capability receipt and mirrored confirmation alone do not certify completion", () => {
  const events = mappingEvents().filter(e => e.type !== "CAPABILITY_VERIFIED");
  const trace = buildTaskTrace({ task: task("mapping.build", events) });
  assert.notEqual(trace.steps[0].status, "CONFIRMED");
  assert.equal(trace.steps[0].capabilityVerified, false);
  assert.ok(trace.findings.some(f => f.code === "SUCCESS_WITH_UNCONFIRMED_MUTATION"));
  assert.equal(trace.steps[0].durationS, null, "missing finish time must not appear as 0 ms");
});

test("an empty capability verification record cannot hide missing proof", () => {
  const events = mappingEvents();
  events.find(e => e.type === "CAPABILITY_VERIFIED").payload.evidence = {};
  const trace = buildTaskTrace({ task: task("mapping.build", events) });
  assert.equal(trace.steps[0].capabilityVerified, false);
  assert.ok(trace.findings.some(f => f.code === "CAPABILITY_WITHOUT_VERIFICATION"));
});

test("robot.task contract verification keeps physical child RGB-D proof checks intact", () => {
  const child = "rev-1-cap-01-navigate-1";
  const trace = buildTaskTrace({ task: task("robot.task", [
    event(1, "CAPABILITY_CALL", 0, { tool: "robot.task", commandId: "parent" }),
    event(2, "TOOL_ACTIVITY", 1, { toolName: "navigation.navigate", activityStatus: "SENDING", commandId: "child" }, child),
    event(3, "TOOL_ACTIVITY", 1000, { toolName: "navigation.navigate", activityStatus: "CONFIRMED", commandId: "child" }, child),
    event(4, "CAPABILITY_VERIFIED", 1001, { tool: "robot.task", evidence: { basis: "LEGACY_RUNNER_VERIFIED_CHILD_STEPS" } }),
  ]) });
  assert.equal(trace.steps[0].capabilityVerified, true);
  assert.equal(trace.steps[1].capability, false);
  assert.ok(trace.findings.some(f => f.code === "MUTATION_WITHOUT_EVIDENCE" && f.stepId === child));
  assert.ok(!trace.findings.some(f => f.code === "MUTATION_WITHOUT_EVIDENCE" && f.stepId === "rev-1-cap-01"));
});

test("unlisted capability effects remain unknown and never default to read-only", () => {
  const trace = buildTaskTrace({ task: task("custom.peek", [
    event(1, "CAPABILITY_CALL", 0, { tool: "custom.peek" }),
    event(2, "CAPABILITY_RECEIPT", 1, { tool: "custom.peek", result: {} }),
    event(3, "CAPABILITY_VERIFIED", 2, { tool: "custom.peek", evidence: { readResult: { value: 1 } } }),
  ]) });
  assert.equal(trace.steps[0].mutatesWorld, null);
  assert.match(renderTaskTrace(trace), /效果未声明/);
  assert.doesNotMatch(renderTaskTrace(trace), /class="trace-tag read"/);
});

test("capability read recovery counts both dispatches while deduplicating mirrored activity", () => {
  const trace = buildTaskTrace({ task: task("calibration.get", [
    event(1, "CAPABILITY_CALL", 0, { tool: "calibration.get", commandId: "read-1" }),
    event(2, "CAPABILITY_FAILED", 5, { tool: "calibration.get", error: "temporary network failure" }),
    event(3, "TOOL_ACTIVITY", 6, { toolName: "calibration.get", activityStatus: "FAILED", error: "temporary network failure" }),
    event(4, "CAPABILITY_READ_RETRY", 100, { tool: "calibration.get", commandId: "read-1" }),
    event(5, "CAPABILITY_RECEIPT", 101, { tool: "calibration.get", result: { revision: "cal-1" } }),
    event(6, "CAPABILITY_VERIFIED", 105, { tool: "calibration.get", evidence: { readResult: { revision: "cal-1" } } }),
  ]) });
  assert.equal(trace.steps[0].attempts, 2);
  assert.equal(trace.steps[0].durationS, 0.105);
  assert.equal(trace.steps[0].error, "");
  assert.equal(trace.steps[0].mutatesWorld, false);
  assert.equal(trace.counts.FAILED, 1);
  assert.ok(trace.findings.some(f => f.code === "STEP_RETRIED"));
});

test("the DOM renderer used by the console shows capability duration and structured proof", () => {
  context.document = { createElement(tagName) {
    return { tagName, textContent: "", children: [], dataset: {},
      append(...children) { this.children.push(...children); },
      setAttribute(name, value) { this[name] = String(value); },
    };
  } };
  const textOf = node => [node.textContent, ...node.children.map(textOf)].join(" ");
  const trace = buildTaskTrace({ task: task("mapping.build", mappingEvents()) });
  const text = textOf(context.TangyingTaskTrace.renderTaskTraceNodes(trace));
  assert.match(text, /耗时 9 分 0\.0 s/);
  assert.match(text, /派发 1 次/);
  assert.match(text, /改变世界/);
  assert.match(text, /地图 map-1/);
  assert.match(text, /结构化状态/);
  assert.doesNotMatch(text, /完成判定缺少依据/);
});

function timedTask(state, events) {
  return { id: "timed-task", state, createdAt: new Date(start).toISOString(),
    updatedAt: new Date(start + 3600000).toISOString(), events };
}

test("post-failure ops diagnostics do not extend task execution duration", () => {
  for (const [type, payload] of [
    ["STATE_CHANGED", { state: "RECOVERABLE_FAILURE" }],
    ["state.transition", { state: "RECOVERABLE_FAILURE" }],
    ["state.transition", { from: "EXECUTING", to: "RECOVERABLE_FAILURE" }],
  ]) {
    const trace = buildTaskTrace({ task: timedTask("RECOVERABLE_FAILURE", [
      event(1, type, 554000, payload),
      event(2, "ops.anomaly_detected", 1800000, { code: "READ_FAILED" }),
      event(3, "ops.recovery_executed", 3600000, { executed: true }),
    ]) });
    assert.equal(trace.durationS, 554, `${type} ${JSON.stringify(payload)}`);
    assert.equal(trace.executionEndAt, new Date(start + 554000).toISOString());
    assert.equal(trace.updatedAt, new Date(start + 3600000).toISOString(), "the stored update time remains truthful");
  }
});

test("a resumed task uses its last terminal transition, including after pause", () => {
  const trace = buildTaskTrace({ task: timedTask("SUCCEEDED", [
    event(1, "state.transition", 10000, { to: "RECOVERABLE_FAILURE" }),
    event(2, "STATE_CHANGED", 20000, { state: "EXECUTING" }),
    event(3, "state.transition", 30000, { to: "PAUSED" }),
    event(4, "state.transition", 40000, { to: "EXECUTING" }),
    event(5, "STATE_CHANGED", 60000, { state: "SUCCEEDED" }),
    event(6, "state.transition", 60000, { to: "SUCCEEDED" }),
    event(7, "ops.recovery_executed", 3600000, {}),
  ]) });
  assert.equal(trace.durationS, 60);
  assert.equal(trace.executionEndAt, new Date(start + 60000).toISOString());
});

test("active tasks and legacy tasks without a terminal timestamp retain updatedAt fallback", () => {
  const active = timedTask("EXECUTING", [
    event(1, "state.transition", 10000, { to: "RECOVERABLE_FAILURE" }),
    event(2, "state.transition", 20000, { to: "EXECUTING" }),
  ]);
  const legacy = timedTask("FAILED", [event(1, "STATE_CHANGED", 10000, { reason: "legacy row has no state" })]);
  const incompleteResume = { ...active, state: "FAILED" };
  for (const task of [active, legacy, incompleteResume]) {
    const trace = buildTaskTrace({ task });
    assert.equal(trace.durationS, 3600, "must not reuse a failure from an earlier execution segment");
    assert.equal(trace.executionEndAt, task.updatedAt);
  }
});

test("both replay renderers show the same last terminal endpoint used for total duration", () => {
  context.document = { createElement(tagName) {
    return { tagName, textContent: "", children: [], dataset: {},
      append(...children) { this.children.push(...children); },
      setAttribute(name, value) { this[name] = String(value); },
    };
  } };
  const trace = buildTaskTrace({ task: timedTask("RECOVERABLE_FAILURE", [
    event(1, "state.transition", 10000, { to: "RECOVERABLE_FAILURE" }),
    event(2, "state.transition", 20000, { to: "EXECUTING" }),
    event(3, "state.transition", 756000, { to: "RECOVERABLE_FAILURE" }),
    event(4, "ops.recovery_executed", 3600000, {}),
  ]) });
  const clock = offset => {
    const date = new Date(start + offset);
    return [date.getHours(), date.getMinutes(), date.getSeconds()].map(value => String(value).padStart(2, "0")).join(":") + ".000";
  };
  const expected = `${clock(0)} → ${clock(756000)}`;
  assert.equal(trace.durationS, 756);
  assert.equal(renderTaskTrace(trace).match(/<dt>起止<\/dt><dd>([^<]*)<\/dd>/)?.[1], expected);
  const root = context.TangyingTaskTrace.renderTaskTraceNodes(trace);
  const flatten = node => [node, ...node.children.flatMap(flatten)];
  const nodes = flatten(root);
  const label = nodes.findIndex(node => node.tagName === "dt" && node.textContent === "起止");
  assert.ok(label >= 0, "the live DOM renderer includes the endpoint label");
  assert.equal(nodes[label + 1].textContent, expected);
  assert.equal(trace.updatedAt, new Date(start + 3600000).toISOString());
});
