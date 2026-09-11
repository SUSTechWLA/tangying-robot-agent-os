// Task replay: turn one natural-language task into a trace a developer can read.
//
// The console already shows the live state, the event log, the observation
// gallery and the task history, each in its own panel. Answering "what actually
// happened, and where did it go wrong?" meant reading all four and correlating
// ids by eye. This module does that correlation once, in one place, and reports
// what it could not line up.
//
// Everything here is pure: it takes the API payloads and returns plain data.
// Rendering is a separate, equally testable string builder. Keeping the join
// out of the DOM is what makes the alignment rules testable at all.
//
// Data sources (all already exposed by the Local Agent API):
//   GET /v1/tasks/{id}                  task: intent, events, state
//   GET /v1/tasks/{id}/observations     evidence.capture.v1 records
//   GET /v1/tasks/{id}/experience       step judgements in plain language
//   GET /v1/tasks/{id}/recovery         whether resume is possible, and why

/** Tools that change the physical world and therefore need post-command proof. */
const MUTATING_TOOLS = new Set([
  "manipulation.pick",
  "manipulation.place",
  "navigation.navigate",
  "recover_to_safe_pose",
]);

/** Tool name -> plain-language label. Unknown tools fall back to the raw name. */
const TOOL_LABELS = {
  observe_scene: "观察环境",
  resolve_targets: "确认任务目标",
  "navigation.navigate": "移动到操作位置",
  "navigation.stop": "中止导航",
  "navigation.explore": "搜索目标",
  "navigation.get_pose": "读取当前位置",
  "arm.move": "移动机械臂",
  "manipulation.pick": "拿取物品",
  "manipulation.place": "放置物品",
  verify_grasp: "检查是否拿稳",
  verify_placement: "检查放置结果",
  verify_arrival: "确认到达房间",
  recover_to_safe_pose: "恢复安全姿态",
  emergency_stop: "紧急停止",
};

function toolLabel(name) {
  if (!name) return "未命名能力";
  return TOOL_LABELS[name] || name;
}

const VERIFICATION_TOOLS = new Set(["verify_grasp", "verify_placement", "verify_arrival"]);

/**
 * Index the history by the ids a tool event can reference.
 *
 * Tool events carry the *capture* id — the sensor observation id the runtime
 * issued — while the evidence API is addressed by the record's own content
 * hash. Both are indexed so a reference resolves to the record, and the hash is
 * kept separately because it is what the HTTP routes accept.
 */
function indexCaptures(records) {
  const index = new Map();
  for (const record of records || []) {
    for (const key of [record?.captureId, record?.id]) {
      if (typeof key === "string" && key) index.set(key, record);
    }
  }
  return index;
}

function timeValue(iso) {
  const parsed = Date.parse(iso || "");
  return Number.isFinite(parsed) ? parsed : null;
}

/**
 * Signed seconds from `fromMs` to `toMs`.
 *
 * The sign is meaningful and therefore preserved: a negative value means the
 * second timestamp happened *before* the first, which for evidence means it
 * cannot prove the command that came later. Clamping to zero here would make
 * that case indistinguishable from "exactly on time".
 */
function secondsBetween(fromMs, toMs) {
  if (fromMs == null || toMs == null) return null;
  return (toMs - fromMs) / 1000;
}

/** A compact, human-readable form of a tool's arguments, never a raw dump. */
function describeArguments(toolName, payload = {}) {
  const parts = [];
  const add = (label, value) => {
    if (value == null || value === "") return;
    parts.push(`${label} ${Array.isArray(value) ? value.join(", ") : value}`);
  };
  if (payload.objectId) add("物体", payload.objectId);
  if (payload.destinationId) add("目标位置", payload.destinationId);
  if (payload.targetRef) add("目标", payload.targetRef);
  if (payload.objectConfidence != null) add("物体置信度", Number(payload.objectConfidence).toFixed(2));
  if (payload.destinationConfidence != null) add("终点置信度", Number(payload.destinationConfidence).toFixed(2));
  if (payload.goalPose) {
    const pose = payload.goalPose.map(value => Number(value).toFixed(2)).join(", ");
    add("目标位姿", pose);
  }
  if (payload.reason) add("原因", payload.reason);
  if (parts.length === 0) return toolName === "observe_scene" ? "无参数（读取当前观测）" : "无参数";
  return parts.join(" · ");
}

function nodeFromEvent(event) {
  const payload = event.payload || {};
  const isTool = event.type === "TOOL_ACTIVITY";
  const toolName = isTool ? payload.toolName || "" : "";
  const status = isTool ? payload.activityStatus || "" : "";
  const at = timeValue(event.occurredAt);
  return {
    sequence: Number(event.sequence) || 0,
    occurredAt: event.occurredAt || "",
    atMs: at,
    type: event.type,
    stepId: payload.stepId || event.stepId || "",
    taskRevision: Number(payload.taskRevision || 1),
    commandId: payload.commandId || "",
    fencingToken: payload.fencingToken || 0,
    toolName,
    toolLabel: isTool ? toolLabel(toolName) : "",
    status,
    mutatesWorld: MUTATING_TOOLS.has(toolName),
    // The runner puts canonical tool arguments under payload.arguments; the
    // rest of the payload is activity metadata. Only the SENDING event carries
    // them, so whether arguments were present is recorded separately: a later
    // event with none must not overwrite what the command was called with.
    arguments: isTool ? describeArguments(toolName, payload.arguments || {}) : "",
    hasArguments: isTool && Boolean(payload.arguments && Object.keys(payload.arguments).length),
    message: event.message || payload.error || "",
    errorCode: payload.code || payload.error || "",
    evidenceIds: Array.isArray(payload.evidenceIds) ? [...payload.evidenceIds] : [],
    receiptObservationId: payload.receiptObservationId || payload.observationId || "",
    evidenceSource: payload.evidenceSource || "",
  };
}

/**
 * Build the full trace: a summary, one entry per step, and the raw timeline.
 *
 * Steps are keyed by `taskRevision + stepId` because a resumed task re-executes
 * its read-only steps under the same ids; without the revision the two runs
 * would merge and the replay would show commands out of order.
 */
function buildTaskTrace({ task, observations = [], experience = null, recovery = null } = {}) {
  const events = [...(task?.events || [])].sort((a, b) => (a.sequence || 0) - (b.sequence || 0));
  const captures = Array.isArray(observations) ? observations : [];
  const captureById = indexCaptures(captures);

  const timeline = [];
  const stepsByKey = new Map();
  const stepOrder = [];
  const dispatchTimes = new Map();

  for (const event of events) {
    const node = nodeFromEvent(event);
    timeline.push(node);
    if (!node.stepId || !node.toolName) continue;
    const key = `${node.taskRevision}/${node.stepId}`;
    if (!stepsByKey.has(key)) {
      stepsByKey.set(key, {
        key,
        stepId: node.stepId,
        taskRevision: node.taskRevision,
        toolName: node.toolName,
        label: node.toolLabel,
        mutatesWorld: node.mutatesWorld,
        status: "PENDING",
        commandIds: [],
        startMs: null,
        endMs: null,
        durationS: null,
        arguments: "",
        error: "",
        errorCode: "",
        evidence: [],
        attempts: 0,
        rawEventSequences: [],
      });
      stepOrder.push(key);
    }
    const step = stepsByKey.get(key);
    step.rawEventSequences.push(node.sequence);
    if (node.commandId && !step.commandIds.includes(node.commandId)) step.commandIds.push(node.commandId);
    if (node.hasArguments) step.arguments = node.arguments;
    if (node.status === "SENDING") {
      step.attempts += 1;
      if (step.startMs == null) step.startMs = node.atMs;
      if (node.atMs != null) dispatchTimes.set(key, node.atMs);
    }
    if (node.status === "CONFIRMED" || node.status === "FAILED" || node.status === "CANCELLED") {
      step.status = node.status;
      step.endMs = node.atMs;
      if (node.message) step.error = node.message;
      if (node.errorCode) step.errorCode = node.errorCode;
      for (const evidenceId of node.evidenceIds) {
        const record = captureById.get(evidenceId);
        step.evidence.push({
          id: evidenceId,
          // The API address of this capture is the record hash, not the capture
          // id that the tool event referenced.
          evidenceId: record?.id || "",
          found: Boolean(record),
          record: record || null,
          observedAt: record?.observedAtUnixMs ?? null,
          capturedAt: record ? new Date(record.observedAtUnixMs).toISOString() : "",
          stepId: record?.stepId || "",
          sourceId: record?.sourceId || "",
          rgbSha256: record?.rgbSha256 || "",
          depthSha256: record?.depthSha256 || "",
          snapshotSha256: record?.snapshotSha256 || "",
          rgbBytes: record?.rgbBytes ?? null,
          depthBytes: record?.depthBytes ?? null,
          expired: Boolean(record?.expired),
        });
      }
    }
  }

  for (const key of stepOrder) {
    const step = stepsByKey.get(key);
    step.durationS = Math.max(0, secondsBetween(step.startMs, step.endMs) ?? 0);
    const dispatch = dispatchTimes.get(key);
    for (const item of step.evidence) {
      item.secondsAfterDispatch = secondsBetween(dispatch, item.observedAt);
    }
    const offsets = step.evidence
      .map(item => item.secondsAfterDispatch)
      .filter(value => value != null);
    step.evidenceAfterDispatch = offsets.length > 0 && offsets.every(value => value >= 0);
  }

  const steps = stepOrder.map(key => stepsByKey.get(key));
  const findings = analyseAlignment({ task, timeline, steps, captures, captureById, recovery });
  const counts = { SENDING: 0, RUNNING: 0, CONFIRMED: 0, FAILED: 0, CANCELLED: 0 };
  for (const node of timeline) {
    if (node.type === "TOOL_ACTIVITY" && node.status in counts) counts[node.status] += 1;
  }

  return {
    taskId: task?.id || "",
    request: task?.request || "",
    state: task?.state || "",
    adapter: task?.adapter || "",
    approved: Boolean(task?.approved),
    currentRevision: Number(task?.currentRevision || 1),
    createdAt: task?.createdAt || "",
    updatedAt: task?.updatedAt || "",
    durationS: Math.max(0, secondsBetween(timeValue(task?.createdAt), timeValue(task?.updatedAt)) ?? 0),
    understanding: experience?.understanding || "",
    headline: experience?.headline || "",
    experienceSteps: experience?.steps || [],
    // The declared decomposition, when the task stored one. The Local path
    // derives its decomposition during execution, so this is often empty; the
    // tool steps below are the authoritative record either way.
    declaredSteps: declaredIntentSteps(task),
    timeline,
    steps,
    captures,
    recovery: recovery || null,
    findings,
    counts,
    summary: {
      events: timeline.length,
      steps: steps.length,
      physicalSteps: steps.filter(step => step.mutatesWorld).length,
      confirmed: counts.CONFIRMED,
      failed: counts.FAILED,
      captures: captures.length,
      expiredCaptures: captures.filter(record => record?.expired).length,
      errors: findings.filter(finding => finding.severity === "error").length,
      warnings: findings.filter(finding => finding.severity === "warn").length,
    },
  };
}

/**
 * Judge whether the records line up, and say so in plain language.
 *
 * These are the checks a developer would otherwise perform by hand — and the
 * ones that actually explain a failure: a step that ran with no proof, proof
 * that arrived before its own command, a capture nothing refers to, or a final
 * state that contradicts the evidence.
 */
function analyseAlignment({ task, timeline, steps, captures, captureById, recovery }) {
  const findings = [];
  const push = (severity, code, message, detail = {}) => findings.push({ severity, code, message, ...detail });

  // 1. A world-mutating step must be confirmed by post-command evidence.
  for (const step of steps) {
    if (!step.mutatesWorld || step.status === "PENDING") continue;
    if (step.status !== "CONFIRMED") continue;
    if (step.evidence.length === 0) {
      push("error", "MUTATION_WITHOUT_EVIDENCE",
        `「${step.label}」被记为完成，但没有关联任何观测证据。写工具的完成必须由命令后的新鲜观测确认。`,
        { stepId: step.stepId, tool: step.toolName });
    }
    if (step.evidence.length > 0 && !step.evidenceAfterDispatch) {
      push("error", "EVIDENCE_BEFORE_COMMAND",
        `「${step.label}」的证据采集时间早于命令派发时刻，不能证明这次动作改变了世界。`,
        { stepId: step.stepId, tool: step.toolName });
    }
  }

  // 2. Every referenced capture must actually be present in the history.
  for (const step of steps) {
    for (const item of step.evidence) {
      if (!item.found) {
        push("error", "EVIDENCE_MISSING_FROM_HISTORY",
          `「${step.label}」引用的观测 ${item.id} 不在任务的历史记录里，原始画面无法回看。`,
          { stepId: step.stepId, evidenceId: item.id });
      } else if (item.stepId && item.stepId !== executionStepId(step)) {
        push("warn", "EVIDENCE_BOUND_TO_OTHER_STEP",
          `观测 ${item.id} 登记的步骤是「${item.stepId}」，与引用它的「${executionStepId(step)}」不一致。`,
          { stepId: step.stepId, evidenceId: item.id, boundStep: item.stepId });
      }
    }
  }

  // 3. Captures nothing points at: invisible work, or a broken link.
  const referenced = new Set();
  for (const step of steps) {
    for (const item of step.evidence) {
      referenced.add(item.id);
      if (item.evidenceId) referenced.add(item.evidenceId);
    }
  }
  for (const node of timeline) if (node.receiptObservationId) referenced.add(node.receiptObservationId);
  for (const record of captures) {
    if (record?.captureId && !referenced.has(record.captureId)) {
      push("info", "CAPTURE_UNREFERENCED",
        `观测 ${record.captureId}（步骤「${record.stepId || "未标注"}」）没有被任何工具活动引用。`,
        { captureId: record.captureId, stepId: record.stepId || "" });
    }
  }

  // 4. The terminal state must not overstate what the evidence shows.
  const terminal = ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE", "SAFETY_STOPPED"];
  const pendingPhysical = steps.filter(step => step.mutatesWorld && step.status !== "CONFIRMED");
  if (task?.state === "SUCCEEDED" && pendingPhysical.length > 0) {
    push("error", "SUCCESS_WITH_UNCONFIRMED_MUTATION",
      `任务报告成功，但「${pendingPhysical.map(step => step.label).join("、")}」没有确认完成。`,
      { steps: pendingPhysical.map(step => step.stepId) });
  }
  if (terminal.includes(task?.state) && task?.state !== "SUCCEEDED" && steps.length === 0) {
    push("warn", "TERMINAL_WITHOUT_TOOL_ACTIVITY",
      `任务以「${task.state}」结束，但没有任何工具活动记录：失败发生在分解或绑定阶段。`,
      { state: task.state });
  }

  // 5. Verification steps must report a decision, not just run.
  for (const step of steps) {
    if (!VERIFICATION_TOOLS.has(step.toolName)) continue;
    if (step.status === "CONFIRMED" && step.evidence.length === 0) {
      push("warn", "VERIFICATION_WITHOUT_CAPTURE",
        `「${step.label}」通过，但这次判定没有留下采集记录。`,
        { stepId: step.stepId });
    }
  }

  // 6. Retries are legitimate, but the reader should see them.
  for (const step of steps) {
    if (step.attempts > 1 && step.status === "CONFIRMED") {
      push("info", "STEP_RETRIED",
        `「${step.label}」派发了 ${step.attempts} 次才完成（恢复或重试）。`,
        { stepId: step.stepId, attempts: step.attempts });
    }
  }

  // 7. A failure should carry a reason and a recovery path.
  const failed = steps.filter(step => step.status === "FAILED");
  for (const step of failed) {
    if (!step.error) {
      push("warn", "FAILURE_WITHOUT_REASON",
        `「${step.label}」失败但没有记录原因。`, { stepId: step.stepId });
    }
  }
  if (recovery && recovery.requiresReconciliation) {
    // The runtime reports which steps never recorded an outcome. When the trace
    // already shows that step failing with a reason, the reader has the cause
    // and this is background; when the stop is unexplained it is the headline.
    const uncertain = recovery.uncertainStepIds || [];
    const explained = uncertain.filter(id =>
      steps.some(step => step.stepId === id && step.status === "FAILED" && step.error));
    const unexplained = uncertain.filter(id => !explained.includes(id));
    const detail = { reasonCode: recovery.reasonCode || "", uncertainSteps: uncertain };
    if (unexplained.length > 0 || uncertain.length === 0) {
      push("error", "RECONCILIATION_REQUIRED",
        `存在缺少完成记录的物理动作（${uncertain.join("、") || "未标注步骤"}）。系统不会重放，也不允许通过修改任务版本绕过；必须先现场核对机器人和物体状态。`,
        detail);
    } else {
      push("info", "RECONCILIATION_REQUIRED",
        `物理步骤 ${explained.join("、")} 没有完成记录，因此本任务不能直接继续；上面已给出该步骤的失败原因，核对现场后再决定。`,
        detail);
    }
  }

  return findings;
}

function executionStepId(step) {
  return step.taskRevision > 1 ? `revision-${step.taskRevision}/${step.stepId}` : step.stepId;
}

/**
 * Flatten the parser's intent sequence into readable lines.
 *
 * One sentence can carry several actions; showing them separately is how a
 * developer sees that "把红杯放进盒子，然后把蓝瓶拿过来" became two subtasks
 * rather than one.
 */
function declaredIntentSteps(task) {
  const sequence = task?.intent?.sequence;
  const items = Array.isArray(sequence) && sequence.length ? sequence : task?.intent ? [task.intent] : [];
  return items.map((item, index) => {
    const object = [item?.object?.attributes?.color, item?.object?.category].filter(Boolean).join(" ");
    const destination = [item?.destination?.relation, item?.destination?.category].filter(Boolean).join(" ");
    const action = item?.action || "unknown";
    const robot = item?.robotId ? ` @${item.robotId}` : "";
    return `${index + 1}. ${action}${robot}：${object || "（未指定物体）"} → ${destination || "（未指定终点）"}`;
  });
}

/** One line summarising the outcome, for the top of the replay. */
function traceVerdict(trace) {
  if (!trace) return "";
  const { summary, state } = trace;
  const parts = [`${summary.steps} 个工具步骤`];
  if (summary.confirmed) parts.push(`${summary.confirmed} 次确认`);
  if (summary.failed) parts.push(`${summary.failed} 次失败`);
  parts.push(`${summary.captures} 份采集`);
  if (summary.errors) parts.push(`${summary.errors} 个严重不一致`);
  else if (summary.warnings) parts.push(`${summary.warnings} 个提示`);
  const suffix = {
    SUCCEEDED: "任务成功",
    RECOVERABLE_FAILURE: "任务可恢复失败",
    FAILED: "任务失败",
    CANCELLED: "任务已取消",
    SAFETY_STOPPED: "任务因安全停止中断",
  }[state] || `任务状态 ${state || "未知"}`;
  return `${suffix} · ${parts.join(" · ")}`;
}

// ---------------------------------------------------------------------------
// Rendering
//
// HTML strings rather than DOM calls: the same output can then be asserted in
// node tests without a browser, which is how the layout rules below are kept
// honest. The caller assigns the result to one container's innerHTML.
// ---------------------------------------------------------------------------

function escapeHTML(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  if (seconds < 1) return `${Math.round(seconds * 1000)} ms`;
  if (seconds < 60) return `${seconds.toFixed(2)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} 分 ${(seconds - minutes * 60).toFixed(1)} s`;
}

function formatClock(iso) {
  const value = Date.parse(iso || "");
  if (!Number.isFinite(value)) return "—";
  // Local time with milliseconds: a replay is read at sub-second resolution.
  const date = new Date(value);
  const pad = (number, width = 2) => String(number).padStart(width, "0");
  return `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}.${pad(date.getMilliseconds(), 3)}`;
}

function statusClass(status) {
  return {
    CONFIRMED: "ok",
    FAILED: "bad",
    CANCELLED: "muted",
    RUNNING: "busy",
    SENDING: "busy",
    PENDING: "muted",
  }[status] || "muted";
}

function statusText(status) {
  return {
    CONFIRMED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
    RUNNING: "执行中",
    SENDING: "已下发",
    PENDING: "等待执行",
  }[status] || status || "未知";
}

function evidenceCard(item, taskId) {
  if (!item.found) {
    return `<li class="trace-evidence missing"><strong>证据缺失</strong>
      <code>${escapeHTML(item.id)}</code>
      <p>这次采集不在任务历史里，无法回看原始画面。</p></li>`;
  }
  const record = item.record;
  const base = `/v1/tasks/${encodeURIComponent(taskId)}/observations/${encodeURIComponent(item.evidenceId || item.id)}`;
  const hash = escapeHTML((item.rgbSha256 || "").slice(0, 16));
  return `<li class="trace-evidence">
    <div class="trace-evidence-images">
      <figure><img src="${base}/rgb" alt="该步骤确认时的彩色画面" loading="lazy"
        width="160" height="120"><figcaption>彩色 · ${formatClock(item.capturedAt)}</figcaption></figure>
      <figure><img src="${base}/depth" alt="同一次采集的深度预览" loading="lazy"
        width="160" height="120"><figcaption>深度（同一次采集）</figcaption></figure>
    </div>
    <dl class="trace-evidence-facts">
      <dt>采集时间</dt><dd>${escapeHTML(item.capturedAt || "—")}</dd>
      <dt>相对命令</dt><dd class="${item.secondsAfterDispatch != null && item.secondsAfterDispatch < 0 ? "trace-error" : ""}">${
        item.secondsAfterDispatch == null
          ? "—"
          : `${item.secondsAfterDispatch >= 0 ? "命令后 " : "命令前 "}${Math.abs(item.secondsAfterDispatch).toFixed(2)} s`}</dd>
      <dt>来源</dt><dd><code>${escapeHTML(item.sourceId || "—")}</code></dd>
      <dt>登记步骤</dt><dd><code>${escapeHTML(item.stepId || "—")}</code></dd>
      <dt>采集编号</dt><dd><code>${escapeHTML(item.id)}</code></dd>
      <dt>RGB 大小</dt><dd>${item.rgbBytes == null ? "—" : `${item.rgbBytes} 字节`}</dd>
      <dt>RGB SHA-256</dt><dd><code>${hash}…</code></dd>
      ${item.expired ? "<dt>保留状态</dt><dd>原始内容已按保留预算清理</dd>" : ""}
    </dl>
    <p class="trace-evidence-links">
      <a href="${base}/rgb" target="_blank" rel="noopener">打开原图</a>
      <a href="${base}/depth" target="_blank" rel="noopener">打开深度图</a>
      <a href="${base}" target="_blank" rel="noopener">该次采集的完整 JSON</a>
      ${record?.snapshotSha256 ? `<span class="hint">规范重建 ${escapeHTML(record.snapshotSha256.slice(0, 12))}…</span>` : ""}
    </p>
  </li>`;
}

function stepSection(step, taskId, index) {
  const status = statusClass(step.status);
  const evidence = step.evidence.length
    ? `<ul class="trace-evidence-list">${step.evidence.map(item => evidenceCard(item, taskId)).join("")}</ul>`
    : step.mutatesWorld && step.status === "CONFIRMED"
      ? `<p class="trace-warning">这一步改变了物理世界，但没有关联观测证据 —— 完成判定缺少依据。</p>`
      : `<p class="hint">这一步没有采集记录（只读步骤不要求证据）。</p>`;
  return `<li class="trace-step ${status}">
    <header>
      <span class="trace-step-index">${index + 1}</span>
      <div>
        <h4>${escapeHTML(step.label)} <code>${escapeHTML(step.toolName)}</code></h4>
        <p class="trace-step-meta">
          <span class="trace-status ${status}">${escapeHTML(statusText(step.status))}</span>
          <span>耗时 ${formatDuration(step.durationS)}</span>
          <span>派发 ${step.attempts} 次</span>
          ${step.mutatesWorld ? '<span class="trace-tag">改变世界</span>' : '<span class="trace-tag read">只读</span>'}
          ${step.taskRevision > 1 ? `<span>任务版本 ${step.taskRevision}</span>` : ""}
        </p>
      </div>
    </header>
    ${step.rawEventSequences.length > 1 ? `<details class="trace-detail"><summary>生命周期（${step.rawEventSequences.length} 条事件，序号 ${step.rawEventSequences.join("、")}）</summary>` : `<details class="trace-detail"><summary>调用参数与命令编号</summary>`}
      <dl>
        <dt>调用参数</dt><dd>${escapeHTML(step.arguments || "（该步骤的事件未回显参数）")}</dd>
        <dt>命令编号</dt><dd>${step.commandIds.map(id => `<code>${escapeHTML(id)}</code>`).join("<br>") || "—"}</dd>
        ${step.error ? `<dt>失败原因</dt><dd class="trace-error">${escapeHTML(step.error)}</dd>` : ""}
      </dl>
    </details>
    ${evidence}
  </li>`;
}

function timelineRows(timeline) {
  return timeline.map(node => {
    const label = node.type === "TOOL_ACTIVITY"
      ? `${node.toolLabel}${node.status ? ` · ${statusText(node.status)}` : ""}`
      : { TASK_CREATED: "任务创建", TASK_APPROVED: "用户批准", STATE_CHANGED: "状态变化",
          LOCAL_PAUSE_REQUESTED: "请求暂停", LOCAL_RUN_SUCCEEDED: "执行成功收尾",
          LOCAL_RECOVERY_BLOCKED: "恢复被阻止", REVISION_PROPOSED: "提出任务修订",
          REVISION_STATUS_CHANGED: "任务修订状态" }[node.type] || node.type;
    const detail = node.type === "TOOL_ACTIVITY"
      ? `${node.arguments}${node.evidenceIds.length ? ` · 证据 ${node.evidenceIds.length} 份` : ""}`
      : node.message || "";
    return `<tr>
      <td>${node.sequence}</td>
      <td>${formatClock(node.occurredAt)}</td>
      <td>${escapeHTML(label)}</td>
      <td><code>${escapeHTML(node.stepId || "—")}</code></td>
      <td>${escapeHTML(detail)}</td>
      <td>${node.errorCode ? `<code>${escapeHTML(node.errorCode)}</code>` : ""}</td>
    </tr>`;
  }).join("");
}

/**
 * Render the whole replay.
 *
 * `empty` is returned verbatim when there is nothing to show, so the caller
 * controls the placeholder rather than this module guessing.
 */
function renderTaskTrace(trace, { empty = "" } = {}) {
  if (!trace || (!trace.taskId && trace.timeline.length === 0)) return empty;

  const severe = trace.findings.filter(finding => finding.severity !== "info");
  const info = trace.findings.filter(finding => finding.severity === "info");

  const intent = trace.timeline.find(node => node.type === "TASK_CREATED");

  const sections = [];

  sections.push(`<section class="trace-block trace-summary">
    <h3>任务是什么</h3>
    <dl>
      <dt>原始指令</dt><dd class="trace-request">${escapeHTML(trace.request || "—")}</dd>
      <dt>系统理解为</dt><dd>${escapeHTML(trace.understanding || trace.headline || "—")}</dd>
      <dt>适配器</dt><dd><code>${escapeHTML(trace.adapter || "—")}</code></dd>
      <dt>任务版本</dt><dd>${trace.currentRevision}</dd>
      <dt>总耗时</dt><dd>${formatDuration(trace.durationS)}</dd>
      <dt>起止</dt><dd>${escapeHTML(formatClock(trace.createdAt))} → ${escapeHTML(formatClock(trace.updatedAt))}</dd>
      ${intent ? `<dt>创建于事件</dt><dd>序号 ${intent.sequence}</dd>` : ""}
    </dl>
    ${trace.experienceSteps.length ? `<div class="trace-plain-steps">${trace.experienceSteps.map(item =>
      `<div class="trace-plain-step ${statusClass(item.status)}">
         <strong>${escapeHTML(item.statusText || item.status)}</strong>
         <span>${escapeHTML(item.explanation || "")}</span>
         <em>${escapeHTML(item.evidenceText || "")}</em>
       </div>`).join("")}</div>` : ""}
    ${trace.declaredSteps.length ? `<div class="trace-declared">
      <p class="hint">系统拆解出的步骤</p>
      <ol>${trace.declaredSteps.map(item => `<li>${escapeHTML(item)}</li>`).join("")}</ol>
    </div>` : ""}
  </section>`);

  sections.push(`<section class="trace-block">
    <h3>执行链路 <span class="hint">${trace.summary.steps} 个工具步骤，按发生顺序</span></h3>
    ${trace.steps.length
      ? `<ol class="trace-steps">${trace.steps.map((step, index) => stepSection(step, trace.taskId, index)).join("")}</ol>`
      : '<p class="hint">没有任何工具步骤被调用：失败发生在任务分解或目标绑定阶段，机器人没有动作。</p>'}
  </section>`);

  sections.push(`<section class="trace-block">
    <h3>一致性检查 <span class="hint">${severe.length === 0 ? "未发现问题" : `${severe.length} 个发现`}</span></h3>
    ${severe.length === 0
      ? '<p class="trace-ok">每一步的完成都有对应证据，事件顺序与证据时间一致。</p>'
      : `<ul class="trace-findings">${severe.map(finding =>
          `<li class="trace-finding ${finding.severity}">
             <strong>${escapeHTML(finding.message)}</strong>
             <code>${escapeHTML(finding.code)}</code>
           </li>`).join("")}</ul>`}
    ${info.length ? `<details class="trace-detail"><summary>其他说明（${info.length} 条）</summary><ul class="trace-findings">${info.map(finding =>
      `<li class="trace-finding info"><strong>${escapeHTML(finding.message)}</strong><code>${escapeHTML(finding.code)}</code></li>`).join("")}</ul></details>` : ""}
  </section>`);

  sections.push(`<section class="trace-block">
    <h3>全部事件 <span class="hint">${trace.summary.events} 条，含状态变化与命令编号</span></h3>
    <div class="trace-table-wrap"><table class="trace-table">
      <thead><tr><th>#</th><th>时刻</th><th>事件</th><th>步骤</th><th>参数 / 说明</th><th>错误码</th></tr></thead>
      <tbody>${timelineRows(trace.timeline)}</tbody>
    </table></div>
  </section>`);

  if (trace.recovery) {
    sections.push(`<section class="trace-block trace-recovery ${trace.recovery.canResume ? "resumable" : "blocked"}">
      <h3>恢复状态</h3>
      <p>${escapeHTML(trace.recovery.reason || "")}</p>
      <dl>
        <dt>原因码</dt><dd><code>${escapeHTML(trace.recovery.reasonCode || "—")}</code></dd>
        <dt>可继续</dt><dd>${trace.recovery.canResume ? "是" : "否"}</dd>
        <dt>需现场核对</dt><dd>${trace.recovery.requiresReconciliation ? "是（禁止自动重放）" : "否"}</dd>
        <dt>已完成步骤</dt><dd>${(trace.recovery.completedStepIds || []).join("、") || "无"}</dd>
        <dt>结果未知步骤</dt><dd>${(trace.recovery.uncertainStepIds || []).join("、") || "无"}</dd>
      </dl>
    </section>`);
  }

  return `<article class="task-trace" data-task-id="${escapeHTML(trace.taskId)}">
    <p class="trace-verdict ${trace.summary.errors ? "bad" : trace.summary.warnings ? "warn" : "ok"}">${escapeHTML(traceVerdict(trace))}</p>
    ${sections.join("")}
  </article>`;
}

// index.html loads the console as classic scripts, so the same functions are
// also published on the global for app.js to call. Keeping the named exports
// lets the node test runner import them directly.

// ---------------------------------------------------------------------------
// DOM construction
//
// app.js is held to a rule that server strings are never assigned through
// markup injection, so the replay builds real elements. The string renderer
// above is still used by the node tests, where asserting markup is far cheaper
// than asserting a DOM tree; both read the same trace object.
// ---------------------------------------------------------------------------

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function definitionList(pairs) {
  const list = element("dl");
  for (const [label, value] of pairs) {
    if (value == null || value === false || value === "") continue;
    list.append(element("dt", "", label), element("dd", "", value));
  }
  return list;
}

const TIMELINE_LABELS = {
  TASK_CREATED: "任务创建",
  TASK_APPROVED: "用户批准",
  STATE_CHANGED: "状态变化",
  LOCAL_PAUSE_REQUESTED: "请求暂停",
  LOCAL_RUN_SUCCEEDED: "执行成功收尾",
  LOCAL_RECOVERY_BLOCKED: "恢复被阻止",
  REVISION_PROPOSED: "提出任务修订",
  REVISION_STATUS_CHANGED: "任务修订状态",
};

function findingList(findings) {
  const list = element("ul", "trace-findings");
  for (const finding of findings) {
    const item = element("li", `trace-finding ${finding.severity}`);
    item.append(element("strong", "", finding.message), element("code", "", finding.code));
    list.append(item);
  }
  return list;
}

function evidenceNode(item, taskId) {
  if (!item.found) {
    const missing = element("li", "trace-evidence missing");
    missing.append(
      element("strong", "", "证据缺失"),
      element("code", "", item.id),
      element("p", "", "这次采集不在任务历史里，无法回看原始画面。"),
    );
    return missing;
  }
  // The route takes the record hash; the capture id stays visible as provenance.
  const recordHash = item.evidenceId || item.id;
  const base = `/v1/tasks/${encodeURIComponent(taskId)}/observations/${encodeURIComponent(recordHash)}`;
  const card = element("li", "trace-evidence");

  const images = element("div", "trace-evidence-images");
  for (const [label, route, alt] of [
    [`彩色 · ${formatClock(item.capturedAt)}`, "rgb", "该步骤确认时的彩色画面"],
    ["深度（同一次采集）", "depth", "同一次采集的深度预览"],
  ]) {
    const figure = element("figure");
    const image = element("img");
    image.src = `${base}/${route}`;
    image.alt = alt;
    image.loading = "lazy";
    image.width = 160;
    image.height = 120;
    figure.append(image, element("figcaption", "", label));
    images.append(figure);
  }
  card.append(images);
  card.append(definitionList([
    ["采集时间", item.capturedAt || "—"],
    ["相对命令", item.secondsAfterDispatch == null
      ? "—"
      : `${item.secondsAfterDispatch >= 0 ? "命令后 " : "命令前 "}${Math.abs(item.secondsAfterDispatch).toFixed(2)} s`],
    ["来源", item.sourceId || "—"],
    ["登记步骤", item.stepId || "—"],
    ["采集编号", item.id],
    ["RGB 大小", item.rgbBytes == null ? "—" : `${item.rgbBytes} 字节`],
    ["RGB SHA-256", `${(item.rgbSha256 || "").slice(0, 16)}…`],
    ["保留状态", item.expired ? "原始内容已按保留预算清理" : ""],
  ]));

  const links = element("p", "trace-evidence-links");
  for (const [label, url] of [
    ["打开原图", `${base}/rgb`],
    ["打开深度图", `${base}/depth`],
    ["该次采集的完整 JSON", base],
  ]) {
    const anchor = element("a", "", label);
    anchor.href = url;
    anchor.target = "_blank";
    anchor.rel = "noopener";
    links.append(anchor);
  }
  const snapshot = item.record?.snapshotSha256;
  if (snapshot) links.append(element("span", "hint", `规范重建 ${snapshot.slice(0, 12)}…`));
  card.append(links);
  return card;
}

function stepNode(step, taskId, index) {
  const status = statusClass(step.status);
  const item = element("li", `trace-step ${status}`);
  const header = element("header");
  const title = element("div");
  const heading = element("h4");
  heading.append(element("span", "", step.label), element("code", "", step.toolName));
  const meta = element("p", "trace-step-meta");
  meta.append(
    element("span", `trace-status ${status}`, statusText(step.status)),
    element("span", "", `耗时 ${formatDuration(step.durationS)}`),
    element("span", "", `派发 ${step.attempts} 次`),
    element("span", step.mutatesWorld ? "trace-tag" : "trace-tag read",
      step.mutatesWorld ? "改变世界" : "只读"),
  );
  if (step.taskRevision > 1) meta.append(element("span", "", `任务版本 ${step.taskRevision}`));
  title.append(heading, meta);
  header.append(element("span", "trace-step-index", String(index + 1)), title);
  item.append(header);

  const detail = element("details", "trace-detail");
  detail.append(element("summary", "",
    step.rawEventSequences.length > 1
      ? `生命周期（${step.rawEventSequences.length} 条事件，序号 ${step.rawEventSequences.join("、")}）`
      : "调用参数与命令编号"));
  detail.append(definitionList([
    ["调用参数", step.arguments || "（该步骤的事件未回显参数）"],
    ["命令编号", step.commandIds.join(" / ") || "—"],
    ["失败原因", step.error || ""],
  ]));
  item.append(detail);

  if (step.evidence.length) {
    const list = element("ul", "trace-evidence-list");
    for (const evidence of step.evidence) list.append(evidenceNode(evidence, taskId));
    item.append(list);
  } else if (step.mutatesWorld && step.status === "CONFIRMED") {
    item.append(element("p", "trace-warning", "这一步改变了物理世界，但没有关联观测证据 —— 完成判定缺少依据。"));
  } else {
    item.append(element("p", "hint", "这一步没有采集记录（只读步骤不要求证据）。"));
  }
  return item;
}

function timelineNode(timeline) {
  const wrap = element("div", "trace-table-wrap");
  const table = element("table", "trace-table");
  const head = element("thead");
  const headRow = element("tr");
  for (const label of ["#", "时刻", "事件", "步骤", "参数 / 说明", "错误码"]) {
    headRow.append(element("th", "", label));
  }
  head.append(headRow);
  const body = element("tbody");
  for (const node of timeline) {
    const row = element("tr");
    const label = node.type === "TOOL_ACTIVITY"
      ? `${node.toolLabel}${node.status ? ` · ${statusText(node.status)}` : ""}`
      : TIMELINE_LABELS[node.type] || node.type;
    const detail = node.type === "TOOL_ACTIVITY"
      ? `${node.arguments}${node.evidenceIds.length ? ` · 证据 ${node.evidenceIds.length} 份` : ""}`
      : node.message || "";
    for (const value of [node.sequence, formatClock(node.occurredAt), label, node.stepId || "—",
                         detail, node.errorCode || ""]) {
      row.append(element("td", "", value));
    }
    body.append(row);
  }
  table.append(head, body);
  wrap.append(table);
  return wrap;
}

/**
 * Build the replay as a DOM subtree.
 *
 * Returns ``null`` when there is nothing to show, so the caller decides what
 * placeholder to display.
 */
function renderTaskTraceNodes(trace) {
  if (!trace || (!trace.taskId && trace.timeline.length === 0)) return null;
  const root = element("article", "task-trace");
  root.dataset.taskId = trace.taskId;

  const verdictClass = trace.summary.errors ? "bad" : trace.summary.warnings ? "warn" : "ok";
  root.append(element("p", `trace-verdict ${verdictClass}`, traceVerdict(trace)));

  const summary = element("section", "trace-block trace-summary");
  summary.append(element("h3", "", "任务是什么"));
  summary.append(definitionList([
    ["原始指令", trace.request || "—"],
    ["系统理解为", trace.understanding || trace.headline || "—"],
    ["适配器", trace.adapter || "—"],
    ["任务版本", String(trace.currentRevision)],
    ["总耗时", formatDuration(trace.durationS)],
    ["起止", `${formatClock(trace.createdAt)} → ${formatClock(trace.updatedAt)}`],
  ]));
  if (trace.declaredSteps.length) {
    const declared = element("div", "trace-declared");
    declared.append(element("p", "hint", "系统拆解出的步骤"));
    const list = element("ol");
    for (const line of trace.declaredSteps) list.append(element("li", "", line));
    declared.append(list);
    summary.append(declared);
  }
  if (trace.experienceSteps.length) {
    const plain = element("div", "trace-plain-steps");
    for (const item of trace.experienceSteps) {
      const row = element("div", `trace-plain-step ${statusClass(item.status)}`);
      row.append(
        element("strong", "", item.statusText || item.status || ""),
        element("span", "", item.explanation || ""),
        element("em", "", item.evidenceText || ""),
      );
      plain.append(row);
    }
    summary.append(plain);
  }
  root.append(summary);

  const chain = element("section", "trace-block");
  const chainTitle = element("h3");
  chainTitle.append(element("span", "", "执行链路"),
    element("span", "hint", `${trace.summary.steps} 个工具步骤，按发生顺序`));
  chain.append(chainTitle);
  if (trace.steps.length) {
    const list = element("ol", "trace-steps");
    trace.steps.forEach((step, index) => list.append(stepNode(step, trace.taskId, index)));
    chain.append(list);
  } else {
    chain.append(element("p", "hint",
      "没有任何工具步骤被调用：失败发生在任务分解或目标绑定阶段，机器人没有动作。"));
  }
  root.append(chain);

  const checks = element("section", "trace-block");
  const severe = trace.findings.filter(finding => finding.severity !== "info");
  const info = trace.findings.filter(finding => finding.severity === "info");
  const checksTitle = element("h3");
  checksTitle.append(element("span", "", "一致性检查"),
    element("span", "hint", severe.length ? `${severe.length} 个发现` : "未发现问题"));
  checks.append(checksTitle);
  if (severe.length === 0) {
    checks.append(element("p", "trace-ok", "每一步的完成都有对应证据，事件顺序与证据时间一致。"));
  } else {
    checks.append(findingList(severe));
  }
  if (info.length) {
    const detail = element("details", "trace-detail");
    detail.append(element("summary", "", `其他说明（${info.length} 条）`), findingList(info));
    checks.append(detail);
  }
  root.append(checks);

  const events = element("section", "trace-block");
  const eventsTitle = element("h3");
  eventsTitle.append(element("span", "", "全部事件"),
    element("span", "hint", `${trace.summary.events} 条，含状态变化与命令编号`));
  events.append(eventsTitle, timelineNode(trace.timeline));
  root.append(events);

  if (trace.recovery) {
    const recovery = element("section",
      `trace-block trace-recovery ${trace.recovery.canResume ? "resumable" : "blocked"}`);
    recovery.append(element("h3", "", "恢复状态"), element("p", "", trace.recovery.reason || ""));
    recovery.append(definitionList([
      ["原因码", trace.recovery.reasonCode || "—"],
      ["可继续", trace.recovery.canResume ? "是" : "否"],
      ["需现场核对", trace.recovery.requiresReconciliation ? "是（禁止自动重放）" : "否"],
      ["已完成步骤", (trace.recovery.completedStepIds || []).join("、") || "无"],
      ["结果未知步骤", (trace.recovery.uncertainStepIds || []).join("、") || "无"],
    ]));
    root.append(recovery);
  }

  return root;
}

// Published last, once every declaration exists: index.html loads the console
// as classic scripts and app.js reads this object, so a partially built global
// would silently drop functions.
globalThis.TangyingTaskTrace = {
  MUTATING_TOOLS,
  TOOL_LABELS,
  toolLabel,
  describeArguments,
  buildTaskTrace,
  analyseAlignment,
  declaredIntentSteps,
  traceVerdict,
  escapeHTML,
  renderTaskTrace,
  renderTaskTraceNodes,
};
