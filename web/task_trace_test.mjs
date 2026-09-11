// The replay exists to answer "what happened, and where did it break?".
// These tests therefore focus on the alignment rules: a trace that shows the
// steps but hides a missing-proof bug would be worse than no trace at all.

import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

// The console loads its scripts as classic scripts under a strict Content
// Security Policy, so task_trace.js must not contain `export`. Running it the
// same way here means these tests exercise the exact artifact the browser
// executes, including the global it publishes.
const context = vm.createContext({});
vm.runInContext(await readFile(new URL("./task_trace.js", import.meta.url), "utf8"), context);
const {
  MUTATING_TOOLS,
  analyseAlignment,
  buildTaskTrace,
  declaredIntentSteps,
  describeArguments,
  escapeHTML,
  renderTaskTrace,
  toolLabel,
  traceVerdict,
} = context.TangyingTaskTrace;

const OBSERVED_AT = 1_789_124_864_400;

function capture(overrides = {}) {
  return {
    schemaVersion: "evidence.capture.v1",
    id: overrides.id || "cap-1",
    taskId: "task-1",
    taskRevision: 1,
    stepId: "task01-pick",
    captureId: overrides.captureId || "xlerobot/head-rgbd-1-2",
    sourceId: "xlerobot/head-rgbd",
    sourceType: "rgbd_camera",
    observedAtUnixMs: OBSERVED_AT,
    recordedAt: "2026-09-11T11:07:45.000Z",
    expired: false,
    rgbSha256: "a".repeat(64),
    depthSha256: "b".repeat(64),
    snapshotSha256: "c".repeat(64),
    rgbBytes: 44_863,
    depthBytes: 6_913,
    ...overrides,
  };
}

function toolEvent(sequence, stepId, toolName, activityStatus, extra = {}) {
  return {
    sequence,
    type: "TOOL_ACTIVITY",
    stepId,
    occurredAt: extra.occurredAt || new Date(OBSERVED_AT + (sequence - 10) * 100).toISOString(),
    payload: {
      toolName,
      activityStatus,
      stepId,
      taskRevision: extra.taskRevision ?? 1,
      commandId: extra.commandId || `task-1/revision/1/step/${stepId}`,
      ...extra.payload,
    },
  };
}

function stateEvent(sequence, message) {
  return { sequence, type: "STATE_CHANGED", message, occurredAt: new Date(OBSERVED_AT).toISOString(), payload: {} };
}

/** A successful two-object task, shaped like the real API responses. */
function healthyTask() {
  const task = {
    id: "task-1",
    request: "把红色杯子放进右侧收纳盒，然后把蓝色瓶子拿过来",
    adapter: "mujoco",
    state: "SUCCEEDED",
    approved: true,
    currentRevision: 1,
    createdAt: new Date(OBSERVED_AT - 5_000).toISOString(),
    updatedAt: new Date(OBSERVED_AT + 15_000).toISOString(),
    intent: {
      action: "pick_and_place",
      object: { category: "cup", attributes: { color: "red" } },
      destination: { category: "storage_bin", relation: "right_side" },
      sequence: [
        { action: "pick_and_place", object: { category: "cup", attributes: { color: "red" } },
          destination: { category: "storage_bin", relation: "right_side" } },
        { action: "fetch", object: { category: "bottle", attributes: { color: "blue" } },
          destination: { category: "delivery_tray", relation: "front_side" } },
      ],
    },
    events: [
      { sequence: 1, type: "TASK_CREATED", occurredAt: new Date(OBSERVED_AT - 5_000).toISOString(), payload: {} },
      { sequence: 2, type: "TASK_APPROVED", occurredAt: new Date(OBSERVED_AT - 4_000).toISOString(), payload: {} },
      stateEvent(3, "local execution started"),
      toolEvent(4, "task01-observe", "observe_scene", "SENDING"),
      toolEvent(5, "task01-observe", "observe_scene", "RUNNING"),
      toolEvent(6, "task01-observe", "observe_scene", "CONFIRMED", {
        payload: { evidenceIds: [], receiptObservationId: "xlerobot/head-rgbd-1-2" },
      }),
      toolEvent(7, "task01-pick", "manipulation.pick", "SENDING", {
        payload: { arguments: { targetRef: "red-cup", keepUpright: true } },
      }),
      toolEvent(8, "task01-pick", "manipulation.pick", "RUNNING"),
      toolEvent(9, "task01-pick", "manipulation.pick", "CONFIRMED", {
        payload: {
          evidenceIds: ["xlerobot/head-rgbd-1-2"],
          receiptObservationId: "xlerobot/head-rgbd-1-2",
          evidenceSource: "command_observation",
        },
      }),
      toolEvent(10, "task01-verify_grasp", "verify_grasp", "SENDING", {
        payload: { arguments: { objectId: "red-cup" } },
      }),
      toolEvent(11, "task01-verify_grasp", "verify_grasp", "CONFIRMED", {
        payload: { evidenceIds: ["xlerobot/head-rgbd-1-2"] },
      }),
      { sequence: 12, type: "LOCAL_RUN_SUCCEEDED", occurredAt: new Date(OBSERVED_AT + 15_000).toISOString(), payload: {} },
    ],
  };
  return { task, observations: [capture()] };
}

function traceOf(overrides = {}) {
  const base = healthyTask();
  return buildTaskTrace({ ...base, ...overrides });
}

test("every mutating tool the runtime knows is treated as a write", () => {
  for (const tool of ["manipulation.pick", "manipulation.place", "navigation.navigate", "recover_to_safe_pose"]) {
    assert.ok(MUTATING_TOOLS.has(tool), tool);
  }
  assert.equal(MUTATING_TOOLS.has("observe_scene"), false);
  assert.equal(MUTATING_TOOLS.has("verify_grasp"), false);
});

test("a healthy task produces steps, evidence and no severe findings", () => {
  const trace = traceOf();
  assert.equal(trace.state, "SUCCEEDED");
  assert.equal(trace.steps.length, 3);
  assert.equal(trace.summary.captures, 1);
  assert.equal(trace.summary.errors, 0);

  const pick = trace.steps.find(step => step.stepId === "task01-pick");
  assert.equal(pick.status, "CONFIRMED");
  assert.equal(pick.mutatesWorld, true);
  assert.equal(pick.evidence.length, 1);
  assert.equal(pick.evidence[0].found, true);
  assert.equal(pick.attempts, 1);
  assert.ok(pick.durationS >= 0);
  // Arguments are readable, not a raw payload dump.
  assert.match(pick.arguments, /目标 red-cup/);
});

test("the declared decomposition is flattened into readable lines", () => {
  const steps = declaredIntentSteps(healthyTask().task);
  assert.equal(steps.length, 2);
  assert.match(steps[0], /red cup → right_side storage_bin/);
  assert.match(steps[1], /blue bottle/);
});

test("a confirmed write with no evidence is reported as a severe finding", () => {
  const { task, observations } = healthyTask();
  const pick = task.events.find(event => event.sequence === 9);
  delete pick.payload.evidenceIds;
  delete pick.payload.receiptObservationId;
  const trace = buildTaskTrace({ task, observations });
  const codes = trace.findings.map(finding => finding.code);
  assert.ok(codes.includes("MUTATION_WITHOUT_EVIDENCE"), codes.join(","));
  assert.equal(trace.summary.errors >= 1, true);
});

test("evidence captured before its own command is reported", () => {
  const { task } = healthyTask();
  const pickAt = Date.parse(task.events.find(event => event.sequence === 7).occurredAt);
  // Same capture identity and step binding, but acquired one second before the
  // pick was dispatched: it cannot prove what the pick did.
  const stale = capture({
    // Bound to the pick step, but acquired a second before the pick was
    // dispatched. The earlier observe step legitimately has older evidence, so
    // the capture must be attributed to the step whose proof it claims to be.
    stepId: "task01-pick",
    observedAtUnixMs: pickAt - 1000,
    recordedAt: new Date(pickAt - 1000).toISOString(),
  });
  const trace = buildTaskTrace({ task, observations: [stale] });
  const codes = trace.findings.map(finding => finding.code);
  assert.ok(codes.includes("EVIDENCE_BEFORE_COMMAND"), codes.join(","));
  const pick = trace.steps.find(step => step.stepId === "task01-pick");
  assert.equal(pick.evidenceAfterDispatch, false);
});

test("an evidence id missing from the history is reported, not silently dropped", () => {
  const { task } = healthyTask();
  const trace = buildTaskTrace({ task, observations: [] });
  const codes = trace.findings.map(finding => finding.code);
  assert.ok(codes.includes("EVIDENCE_MISSING_FROM_HISTORY"), codes.join(","));
  const pick = trace.steps.find(step => step.stepId === "task01-pick");
  assert.equal(pick.evidence[0].found, false);
});

test("success is questioned when a write never confirmed", () => {
  const { task, observations } = healthyTask();
  const pick = task.events.find(event => event.sequence === 9);
  pick.payload.activityStatus = "FAILED";
  pick.payload.error = "rpc error: DeadlineExceeded";
  const trace = buildTaskTrace({ task, observations });
  const codes = trace.findings.map(finding => finding.code);
  assert.ok(codes.includes("SUCCESS_WITH_UNCONFIRMED_MUTATION"), codes.join(","));
  assert.equal(trace.steps.find(step => step.stepId === "task01-pick").status, "FAILED");
  assert.match(trace.steps.find(step => step.stepId === "task01-pick").error, /DeadlineExceeded/);
});

test("a task that failed before any tool ran says so instead of looking empty", () => {
  const task = {
    id: "task-2", request: "去厨房拿杯子", state: "RECOVERABLE_FAILURE", approved: true,
    currentRevision: 1, intent: {},
    createdAt: new Date(OBSERVED_AT).toISOString(), updatedAt: new Date(OBSERVED_AT + 1_000).toISOString(),
    events: [
      { sequence: 1, type: "TASK_CREATED", occurredAt: new Date(OBSERVED_AT).toISOString(), payload: {} },
      { sequence: 2, type: "STATE_CHANGED", message: "ground subtask 1: grounding absent: objects=0 destinations=1",
        occurredAt: new Date(OBSERVED_AT + 1_000).toISOString(), payload: {} },
    ],
  };
  const trace = buildTaskTrace({ task, observations: [] });
  assert.equal(trace.steps.length, 0);
  const codes = trace.findings.map(finding => finding.code);
  assert.ok(codes.includes("TERMINAL_WITHOUT_TOOL_ACTIVITY"));
  const html = renderTaskTrace(trace);
  assert.match(html, /没有任何工具步骤被调用/);
  assert.match(html, /grounding absent/);
});

test("a resumed task keeps its two runs apart", () => {
  const { task, observations } = healthyTask();
  // Revision 2 re-runs the same read-only step ids after a pause.
  task.currentRevision = 2;
  task.events.push(
    stateEvent(20, "local execution started"),
    toolEvent(21, "task01-observe", "observe_scene", "SENDING", { taskRevision: 2 }),
    toolEvent(22, "task01-observe", "observe_scene", "CONFIRMED", { taskRevision: 2 }),
  );
  const trace = buildTaskTrace({ task, observations });
  const observeRuns = trace.steps.filter(step => step.stepId === "task01-observe");
  assert.equal(observeRuns.length, 2);
  assert.deepEqual([...observeRuns.map(step => step.taskRevision)], [1, 2]);
});

test("repeated dispatches of one step are counted and surfaced", () => {
  const { task, observations } = healthyTask();
  task.events.splice(7, 0, toolEvent(70, "task01-pick", "manipulation.pick", "SENDING").valueOf());
  const trace = buildTaskTrace({ task, observations });
  const pick = trace.steps.find(step => step.stepId === "task01-pick");
  assert.equal(pick.attempts, 2);
  assert.ok(trace.findings.some(finding => finding.code === "STEP_RETRIED" && finding.severity === "info"));
});

test("an unreferenced capture is listed as information, not as an error", () => {
  const { task } = healthyTask();
  const orphan = capture({ id: "cap-orphan", captureId: "xlerobot/head-rgbd-9-9", stepId: "task01-place" });
  const trace = buildTaskTrace({ task, observations: [capture(), orphan] });
  const finding = trace.findings.find(item => item.code === "CAPTURE_UNREFERENCED");
  assert.ok(finding);
  assert.equal(finding.severity, "info");
});

test("an unexplained unknown outcome is the headline, an explained one is background", () => {
  const { task, observations } = healthyTask();
  const pick = task.events.find(event => event.sequence === 9);
  pick.payload.activityStatus = "FAILED";
  pick.payload.error = "rpc error: DeadlineExceeded";
  const base = {
    task, observations,
    recovery: { canResume: false, requiresReconciliation: true,
                reasonCode: "PHYSICAL_OUTCOME_UNKNOWN", uncertainStepIds: ["task01-pick"] },
  };

  // The step failed with a stated reason, so the cause is already visible and
  // the reconciliation notice is context rather than the finding.
  const explained = buildTaskTrace(base);
  const explainedFinding = explained.findings.find(item => item.code === "RECONCILIATION_REQUIRED");
  assert.equal(explainedFinding.severity, "info");
  assert.match(explainedFinding.message, /没有完成记录/);

  // No failed step carries the story: this must be impossible to miss.
  const unexplained = buildTaskTrace({
    task, observations, recovery: { ...base.recovery, uncertainStepIds: [] },
  });
  assert.equal(
    unexplained.findings.find(item => item.code === "RECONCILIATION_REQUIRED").severity,
    "error");
});

test("verdict text summarises the outcome in one line", () => {
  const trace = traceOf();
  const verdict = traceVerdict(trace);
  assert.match(verdict, /任务成功/);
  assert.match(verdict, /3 个工具步骤/);
  assert.match(verdict, /1 份采集/);
});

test("tool labels are plain language and fall back to the raw name", () => {
  assert.equal(toolLabel("manipulation.pick"), "拿取物品");
  assert.equal(toolLabel("something.new"), "something.new");
  assert.equal(toolLabel(""), "未命名能力");
});

test("argument descriptions stay readable for every tool shape", () => {
  assert.equal(describeArguments("observe_scene", {}), "无参数（读取当前观测）");
  assert.match(describeArguments("manipulation.pick", { targetRef: "red-cup" }), /目标 red-cup/);
  assert.match(describeArguments("navigation.navigate", { goalPose: [1, 2, 3, 1, 0, 0, 0] }), /目标位姿/);
  assert.match(describeArguments("emergency_stop", { reason: "operator" }), /原因 operator/);
  assert.equal(describeArguments("arm.move", {}), "无参数");
});

test("rendering escapes untrusted content", () => {
  const { task, observations } = healthyTask();
  task.request = '<img src=x onerror="alert(1)">';
  const html = renderTaskTrace(buildTaskTrace({ task, observations }));
  assert.ok(!html.includes("<img src=x"), "request must be escaped");
  assert.ok(html.includes("&lt;img src=x"));
  assert.equal(escapeHTML("a&b<c>\"d'"), "a&amp;b&lt;c&gt;&quot;d&#39;");
});

test("the rendered replay shows intent, steps, evidence and the event table", () => {
  const html = renderTaskTrace(traceOf());
  assert.match(html, /任务是什么/);
  assert.match(html, /执行链路/);
  assert.match(html, /一致性检查/);
  assert.match(html, /全部事件/);
  assert.match(html, /拿取物品/);
  // Evidence is reachable and labelled as historical, with both frames.
  assert.match(html, /observations\/cap-1\/rgb/);
  assert.match(html, /observations\/cap-1\/depth/);
  // The sensor observation id stays visible as provenance.
  assert.match(html, /采集编号/);
  assert.match(html, /xlerobot\/head-rgbd-1-2/);
  assert.match(html, /彩色/);
  assert.match(html, /深度（同一次采集）/);
  assert.match(html, /RGB SHA-256/);
});

test("the rendered replay marks a write without evidence in the step itself", () => {
  const { task, observations } = healthyTask();
  delete task.events.find(event => event.sequence === 9).payload.evidenceIds;
  const html = renderTaskTrace(buildTaskTrace({ task, observations }));
  assert.match(html, /改变了物理世界，但没有关联观测证据/);
});

test("a missing capture renders as a visible gap rather than a broken image", () => {
  const { task } = healthyTask();
  const html = renderTaskTrace(buildTaskTrace({ task, observations: [] }));
  assert.match(html, /证据缺失/);
  assert.match(html, /无法回看原始画面/);
});

test("an empty trace returns the caller's placeholder", () => {
  assert.equal(renderTaskTrace(null, { empty: "<p>无</p>" }), "<p>无</p>");
  assert.equal(renderTaskTrace(buildTaskTrace({}), { empty: "<p>无</p>" }), "<p>无</p>");
});

test("retries and read-only steps are distinguished in the summary", () => {
  const trace = traceOf();
  assert.equal(trace.summary.physicalSteps, 1);
  assert.equal(trace.summary.confirmed, 3);
  assert.equal(trace.summary.failed, 0);
});

test("both dispatch and completion times are reported per step", () => {
  const trace = traceOf();
  for (const step of trace.steps) {
    assert.ok(step.startMs != null, `${step.stepId} start`);
    assert.ok(step.endMs != null, `${step.stepId} end`);
    assert.ok(step.endMs >= step.startMs, `${step.stepId} ordering`);
  }
});

test("alignment checks run against a task with no events at all", () => {
  const findings = analyseAlignment({ task: { state: "SUCCEEDED" }, timeline: [], steps: [], captures: [], captureById: new Map() });
  assert.equal(findings.length, 0);
});
