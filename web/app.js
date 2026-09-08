const $ = (id) => document.querySelector(id);
const requestInput = $("#request");
const adapterInput = $("#adapter");
const stateLabel = $("#state");
const taskLabel = $("#task-id");
const eventList = $("#events");
const localUnderstanding = $("#local-understanding");
const localStepRibbon = $("#local-step-ribbon");
const localToolActivities = $("#local-tool-activities");
const connectionLabel = $("#connection");
const approveButton = $("#approve");
const cancelButton = $("#cancel");
const canvas = $("#scene-canvas");
const context = canvas.getContext("2d");
const sceneFrame = $("#scene-frame");
const sceneStage = $("#scene-stage");
const sceneLiveState = $("#scene-live-state");

let activeTask = null;
let socket = null;
let latestTelemetry = null;
let primaryTelemetry = null;
let selectedCameraSource = "";
let frameObjectURL = null;
let pendingFrameObjectURL = null;
let pendingSceneImage = null;
let displayedFrameSource = "";
let displayedFrameMode = "";
let displayedFrameSnapshot = null;
let sceneFrameSamples = [];
let sceneFrameKeys = new Set();
let localModeStarted = false;
let cameraPageWasVisible = true;
let telemetryGeneration = 0;
let telemetryController = null;
let telemetryPollInFlight = null;
const TELEMETRY_REQUEST_TIMEOUT_MS = 5000;
let sceneViewMode = "live";
let sceneViewGeneration = 0;
let displayedFrameObservedAt = null;
let cloudCameraSource = "";
const sceneCamera = { yaw: 0.6, pitch: 0.42, distance: 2.4, target: [0, 0.4, 0.7] };
let orbitDragging = false;
let orbitLastX = 0;
let orbitLastY = 0;
const lastObservedAtByAdapter = new Map();
const discoveredAdapters = new Set();
const trails = new Map();
let fleetAcceptanceFrameSamplingStarted = false;
let localTaskExperienceState = { taskId: "", revision: 0, aggregateVersion: 0, cursor: 0 };
let localExperienceRequestGeneration = 0;
let fleetExperienceRequestGeneration = 0;
let localRecovery = null;
let localRecoveryGuidance = null;
let localRecoveryRequestGeneration = 0;
let localSelectionGeneration = 0;
let localTaskRequestGeneration = 0;
let localTaskListGeneration = 0;
let localActionPending = false;
let localEventTaskId = "";
const localEvents = new Map();
let localEvidenceRecords = [];
let localEvidenceSelectedId = "";
let localEvidenceNextBefore = null;
let localEvidencePaginationStarted = false;
let localEvidenceChoicesKey = "";
let localEvidenceListGeneration = 0;
let localEvidenceImageGeneration = 0;
let localEvidenceController = null;
let localEvidenceVerification = null;
const localNavigationEvidence = new Map();
const localEvidenceURLs = new Map();
let localMissionActivities = [];
let localMissionSteps = [];
let localExperienceLoadStatus = "loading";

function pageVisible(...pages) {
  return !document.hidden && pages.includes(document.body.dataset.page || "workspace");
}

function scenePageVisible() { return pageVisible("workspace"); }

function resetSceneFrameStats() {
  sceneFrameSamples = [];
  sceneFrameKeys = new Set();
  renderSceneFrameStats();
}

function markSceneFrame(snapshot, capturedAt, mode = sceneViewMode) {
  const source = snapshot?.reconstruction?.sourceId || snapshot?.robotState?.perception?.source_id || snapshot?.adapter || "";
  const key = `${snapshot?.adapter}/${source}/${capturedAt}`;
  if (!sceneFrameKeys.has(key)) {
    sceneFrameKeys.add(key);
    if (sceneFrameKeys.size > 256) sceneFrameKeys.delete(sceneFrameKeys.values().next().value);
    sceneFrameSamples.push(Date.now());
  }
  displayedFrameObservedAt = capturedAt;
  displayedFrameSource = source;
  displayedFrameMode = mode;
  displayedFrameSnapshot = snapshot;
  renderSceneFrameStats();
}

function renderSceneFrameStats(now = Date.now()) {
  const output = $("#scene-frame-stats");
  if (!output) return;
  sceneFrameSamples = sceneFrameSamples.filter(time => now - time <= 5000);
  const elapsed = sceneFrameSamples.length > 1 ? Math.max(1, now - sceneFrameSamples[0]) : 0;
  const fps = elapsed ? (sceneFrameSamples.length - 1) * 1000 / elapsed : null;
  const age = displayedFrameObservedAt == null ? null : Math.max(0, now - displayedFrameObservedAt);
  const paused = !scenePageVisible();
  output.textContent = `${paused ? "刷新已暂停" : fps == null ? "— FPS · 采样中" : `${fps.toFixed(1)} FPS`} · ${age == null ? "尚无画面" : `画面 ${(age / 1000).toFixed(1)} 秒前`}`;
  output.dataset.fps = !paused && fps != null ? fps.toFixed(2) : "";
  output.dataset.captureAgeMs = age == null ? "" : String(Math.round(age));
  output.dataset.sourceId = displayedFrameSource;
  output.dataset.mode = displayedFrameMode;
}

function holdSceneFrame(message, { background = false } = {}) {
  // Fetching the next frame is normal streaming, not a connection transition.
  // Explicit switches still label the old source/mode until replacement decode.
  const expectedSource = selectedCameraSource || primaryCameraSource() || adapterInput.value;
  if (background && sceneLiveState.textContent === "LIVE"
    && displayedFrameSource === expectedSource && displayedFrameMode === sceneViewMode
    && displayedFrameObservedAt != null && sceneTimestampFresh(displayedFrameObservedAt, displayedFrameSnapshot)) return;
  const previous = displayedFrameObservedAt == null ? "" : `；暂显上一帧：${sceneCameraLabel(displayedFrameSource)} / ${{ live: "彩色", depth: "深度", cloud: "点云", orbit: "语义诊断" }[displayedFrameMode] || "画面"}`;
  setSceneVisualState("LOADING", message + previous);
  renderSceneFrameStats();
}

function prepareSceneImage(url, onload, onerror) {
  const image = document.createElement("img");
  image.decoding = "async";
  pendingFrameObjectURL = url;
  pendingSceneImage = image;
  image.onload = () => {
    if (typeof image.decode === "function") image.decode().then(onload, onerror);
    else onload();
  };
  image.onerror = onerror;
  image.src = url;
}

function publishSceneImage(url, snapshot, capturedAt, mode) {
  const previous = frameObjectURL;
  if (pendingSceneImage) { pendingSceneImage.onload = null; pendingSceneImage.onerror = null; }
  pendingSceneImage = null;
  pendingFrameObjectURL = null;
  // The detached image has decoded. Commit pixels and their metadata together.
  sceneFrame.src = url;
  frameObjectURL = url;
  sceneFrame.hidden = false;
  canvas.hidden = true;
  markSceneFrame(snapshot, capturedAt, mode);
  if (previous && previous !== url) URL.revokeObjectURL(previous);
}

function sizeSceneCanvas() {
  const width = sceneStage.clientWidth;
  const height = sceneStage.clientHeight;
  if (!(width > 0 && height > 0)) return false;
  const bitmapHeight = Math.round(1200 * height / width);
  if (canvas.width === 1200 && canvas.height === bitmapHeight) return false;
  canvas.width = 1200;
  canvas.height = bitmapHeight;
  cloudCameraSource = "";
  return true;
}

function handlePageVisibility() {
  const visible = scenePageVisible();
  const resumed = visible && !cameraPageWasVisible;
  if (!visible) {
    globalThis.TangyingNavigationView?.update(null);
    invalidateTelemetryPolling();
    fleetFrameRequest?.controller.abort();
    fleetFrameRequest = null;
    if (cameraPageWasVisible) resetSceneFrameStats();
    holdSceneFrame("画面刷新已暂停，机器人任务继续执行");
  }
  cameraPageWasVisible = visible;
  renderSceneFrameStats();
  applyFleetSceneView();
  if (!localModeStarted && !fleetMode) return;
  if (visible && resumed) {
    lastObservedAtByAdapter.delete(adapterInput.value);
    resetSceneFrameStats();
    holdSceneFrame("正在获取最新观测");
    if (fleetMode) { void pollFleetFrames(); void pollFleetMap(); }
    else { void pollTelemetry(); void pollLocalTask(); }
  }
  if (localModeStarted && pageVisible("tasks")) void loadLocalTasks();
  if (localModeStarted && pageVisible("devices", "diagnostics")) void pollTelemetry();
  if (localModeStarted && pageVisible("diagnostics")) { void pollMetrics(); void pollLocalWorld(); }
}

globalThis.addEventListener?.("tangying:page-change", handlePageVisibility);
document.addEventListener?.("visibilitychange", handlePageVisibility);
globalThis.addEventListener?.("resize", () => {
  if (scenePageVisible() && ["cloud", "orbit"].includes(sceneViewMode)) {
    if (sizeSceneCanvas()) resetSceneCamera();
  }
});

function startFleetAcceptanceFrameSampling() {
  if (fleetAcceptanceFrameSamplingStarted
    || typeof globalThis.requestAnimationFrame !== "function") return;
  fleetAcceptanceFrameSamplingStarted = true;
  const samples = [];
  const output = $("#fleet-acceptance-raf-samples");
  const sample = (timestamp) => {
    if (!Number.isFinite(timestamp) || samples.length >= 600) return;
    if (samples.length === 0) output.dataset.timeOriginMs = String(Date.now() - timestamp);
    samples.push(timestamp);
    output.textContent = JSON.stringify(samples);
    output.dataset.sampleCount = String(samples.length);
    if (samples.length < 600) globalThis.requestAnimationFrame(sample);
  };
  globalThis.requestAnimationFrame(sample);
}

document.querySelector("#create").addEventListener("click", createTask);
approveButton.addEventListener("click", () => taskAction("approve"));
cancelButton.addEventListener("click", () => taskAction("cancel"));
$("#pause").addEventListener("click", () => taskAction("pause"));
$("#resume").addEventListener("click", () => taskAction("resume"));
$("#refresh-local-evidence").addEventListener("click", () => { if (activeTask) void loadLocalEvidence(activeTask.id, { refreshSelected: true }); });
$("#local-evidence-select").addEventListener("change", event => { void selectLocalEvidence(event.target.value); });
$("#local-evidence-older").addEventListener("click", () => { if (activeTask) void loadLocalEvidence(activeTask.id, { older: true }); });
$("#refresh-local-tasks").addEventListener("click", () => { void loadLocalTasks(); });
adapterInput.addEventListener("change", () => {
  invalidateTelemetryPolling();
  lastObservedAtByAdapter.delete(adapterInput.value);
  latestTelemetry = null;
  primaryTelemetry = null;
  selectedCameraSource = "";
  resetSceneFrameStats();
  clearSceneFrame("已切换适配器，等待新观测");
  renderTelemetry(null);
  void pollTelemetry();
});
$("#save-llm").addEventListener("click", saveLLMConfig);
$("#view-live").addEventListener("click", () => setSceneViewMode("live"));
$("#view-depth").addEventListener("click", () => setSceneViewMode("depth"));
$("#view-cloud").addEventListener("click", () => setSceneViewMode("cloud"));
$("#scene-camera").addEventListener("change", event => { void selectSceneCamera(event.target.value); });
$("#audience-toggle").addEventListener("click", () => {
  if (sceneViewMode === "orbit" && document.body.dataset.audience !== "developer") void setSceneViewMode("live", { force: true });
});
$("#view-orbit").addEventListener("click", () => setSceneViewMode("orbit"));
$("#cloud-labels").addEventListener("change", () => { if (sceneViewMode === "cloud") renderScene(latestTelemetry); });
$("#reset-view").addEventListener("click", resetSceneCamera);
canvas.addEventListener("pointerdown", (event) => {
  if (!["orbit", "cloud"].includes(sceneViewMode)) return;
  orbitDragging = true;
  orbitLastX = event.clientX;
  orbitLastY = event.clientY;
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!orbitDragging || !["orbit", "cloud"].includes(sceneViewMode)) return;
  const dx = event.clientX - orbitLastX;
  const dy = event.clientY - orbitLastY;
  orbitLastX = event.clientX;
  orbitLastY = event.clientY;
  sceneCamera.yaw += dx * 0.01;
  sceneCamera.pitch = Math.min(1.4, Math.max(-0.1, sceneCamera.pitch + dy * 0.01));
  if (latestTelemetry) renderScene(latestTelemetry);
});
canvas.addEventListener("pointerup", (event) => {
  orbitDragging = false;
  canvas.releasePointerCapture(event.pointerId);
});
canvas.addEventListener("wheel", (event) => {
  if (!["orbit", "cloud"].includes(sceneViewMode)) return;
  event.preventDefault();
  sceneCamera.distance = Math.min(100, Math.max(0.05, sceneCamera.distance + event.deltaY * 0.001));
  if (latestTelemetry) renderScene(latestTelemetry);
}, { passive: false });

async function loadLLMConfig() {
  try {
    const response = await fetch("/v1/config/status");
    if (!response.ok) return;
    const status = await response.json();
    $("#llm-provider").value = status.provider || "deterministic";
    $("#llm-base-url").value = status.baseUrl || "";
    $("#llm-model").value = status.model || "";
    $("#llm-status").textContent = status.provider === "openai"
      ? `${status.model || "未选择模型"} · ${status.hasApiKey ? "密钥已配置" : "缺少密钥"}`
      : "确定性离线模式";
  } catch (_) {
    $("#llm-status").textContent = "配置读取失败";
  }
}

async function saveLLMConfig() {
  const response = await fetch("/v1/config/llm", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      provider: $("#llm-provider").value,
      baseUrl: $("#llm-base-url").value.trim(),
      model: $("#llm-model").value.trim(),
      apiKey: $("#llm-api-key").value,
    }),
  });
  const result = await response.json();
  if (!response.ok) {
    $("#settings-message").textContent = result.message || "保存失败";
    return;
  }
  $("#llm-api-key").value = "";
  $("#settings-message").textContent = result.restartRequired
    ? "配置已安全保存，请运行 robot-agent restart local 后生效。"
    : "配置已保存。";
  await loadLLMConfig();
}

async function createTask() {
  const button = $("#create");
  if (button.disabled) return;
  if (!requestInput.value.trim() || !adapterInput.value) {
    globalThis.TangyingConsoleUI?.feedback("请先连接机器人环境，并描述要完成的任务。", true);
    return;
  }
  button.disabled = true;
  button.textContent = "正在理解…";
  const selectionGeneration = ++localSelectionGeneration;
  try {
    const response = await fetch("/v1/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ request: requestInput.value.trim(), adapter: adapterInput.value }),
    });
    const body = await response.json();
    if (!response.ok) {
      stateLabel.textContent = `${body.code || "ERROR"}: ${body.message || "请求失败"}`;
      globalThis.TangyingConsoleUI?.feedback(body.message || "任务未能创建，请检查描述后重试。", true);
      return;
    }
    if (selectionGeneration !== localSelectionGeneration) { void loadLocalTasks(); return; }
    activeTask = body;
    renderTask(body);
    connectEvents(body.id);
    void loadLocalTaskExperience(body.id);
    void loadLocalRecovery(body.id);
    void loadLocalTasks();
    globalThis.TangyingConsoleUI?.feedback("任务已生成。请查看机器人理解和执行步骤，再批准物理动作。");
  } catch (_) {
    globalThis.TangyingConsoleUI?.feedback("连接中断，任务创建结果尚未确认，请检查任务状态后再重试。", true);
  } finally {
    button.disabled = false;
    button.textContent = "生成任务";
  }
}

async function taskAction(action) {
  const taskId = activeTask?.id;
  if (!taskId || localActionPending || !["approve", "cancel", "pause", "resume"].includes(action)) return;
  if (action === "pause" && (!localRecovery?.canPause || localRecovery.taskId !== taskId)) return;
  if (action === "resume" && (!localRecovery?.canResume || localRecovery.requiresReconciliation || localRecovery.taskId !== taskId)) return;
  localActionPending = true;
  updateLocalActionButtons();
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}/${action}`, { method: "POST" });
    if (activeTask?.id !== taskId) return;
    const body = await response.json();
    if (activeTask?.id !== taskId) return;
    if (!response.ok) {
      globalThis.TangyingConsoleUI?.feedback(body.message || "操作未得到服务确认，请查看任务状态后重试。", true);
      return;
    }
    if (body.taskId === taskId) {
      renderLocalRecovery(body);
      await refreshTask(taskId);
    } else {
      const task = body.task || body;
      if (task.id !== taskId) return;
      activeTask = task;
      renderTask(activeTask);
    }
    if (activeTask?.id !== taskId) return;
    void loadLocalTaskExperience(taskId);
    if (action === "pause") globalThis.TangyingConsoleUI?.feedback("已请求安全暂停，等待服务确认当前动作结束。");
    if (action === "resume") globalThis.TangyingConsoleUI?.feedback("继续请求已提交，正在核对并恢复任务。");
  } catch (_) {
    if (activeTask?.id === taskId) globalThis.TangyingConsoleUI?.feedback("连接中断，操作结果尚未确认。网页取消不能替代实体急停。", true);
  } finally {
    localActionPending = false;
    if (activeTask?.id === taskId) {
      await loadLocalRecovery(taskId);
      updateLocalActionButtons();
      void loadLocalTasks();
    }
    updateLocalActionButtons();
  }
}

function connectEvents(taskId) {
  if (socket) socket.close();
  syncLocalEvents(taskId, activeTask?.id === taskId ? activeTask.events : []);
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const taskSocket = new WebSocket(`${protocol}://${location.host}/v1/tasks/${taskId}/events/ws`);
  socket = taskSocket;
  const isCurrent = () => socket === taskSocket && activeTask?.id === taskId;
  taskSocket.addEventListener("open", () => { if (isCurrent()) setConnection(true); });
  taskSocket.addEventListener("message", (message) => {
    if (!isCurrent()) return;
    let event;
    try { event = JSON.parse(message.data); } catch (_) { return; }
    appendEvent(event);
    if (event.type === "STATE_CHANGED") void refreshTask(taskId);
    void loadLocalTaskExperience(taskId);
    void loadLocalRecovery(taskId);
    if (event.type === "LOCAL_RUN_SUCCEEDED" && event.payload?.completedSteps) {
      $("#completed-count").textContent = event.payload.completedSteps.length;
    }
  });
  taskSocket.addEventListener("close", () => {
    if (!isCurrent()) return;
    setConnection(false);
    socket = null;
  });
}

function setConnection(online) {
  connectionLabel.textContent = online ? "实时连接" : "连接关闭";
  connectionLabel.classList.toggle("online", online);
}

function setRuntimeConnection(online) {
  $("#connection-dot").classList.toggle("online", online);
  $("#connection-text").textContent = online ? "Runtime 在线" : "Runtime 离线";
}

function localEventTitle(event) {
  switch (event.type) {
    case "TASK_CREATED": return "任务已创建";
    case "TASK_APPROVED": return "任务批准";
    case "STATE_CHANGED": return "任务状态同步";
    case "INTENT_STARTED": return "开始理解任务";
    case "INTENT_SUCCEEDED": return "意图完成";
    case "TOOL_ACTIVITY": return "能力调用";
    case "RECOVERY_ACTIVITY": return "安全恢复";
    case "LOCAL_RUN_SUCCEEDED": return "本地执行成功";
    case "LOCAL_RUN_FAILED": return "本地执行失败";
    case "LOCAL_PAUSE_REQUESTED":
    case "PAUSE_REQUESTED": return "已请求安全暂停";
    case "TASK_PAUSED": return "任务已安全暂停";
    case "TASK_RESUMED": return "任务已继续";
    case "STEP_STARTED": return "动作开始";
    case "STEP_COMPLETED": return "动作完成记录已保存";
    default: return event.type || event.code || "事件更新";
  }
}

function localEventDetail(event) {
  const payload = event.payload || {};
  if (event.type === "TOOL_ACTIVITY") {
    const parts = [
      payload.toolName || "未命名能力",
      payload.activityStatus ? `状态：${payload.activityStatus}` : "",
      payload.robotId ? `机器人：${payload.robotId}` : "",
      payload.stepId ? `步骤：${payload.stepId}` : "",
    ].filter(Boolean);
    if (payload.objectId) parts.push(`对象：${missionReferenceLabel(payload.objectId)}`);
    if (payload.targetRef) parts.push(`目标：${missionReferenceLabel(payload.targetRef)}`);
    return `${parts.join(" · ")}${payload.evidenceIds?.length ? "（已确认）" : ""}`;
  }
  if (event.type === "RECOVERY_ACTIVITY") {
    const parts = [
      payload.knownState || "系统正在确认恢复状态",
      payload.automaticAction ? `自动动作：${payload.automaticAction}` : "",
      payload.userActions?.length ? `待处理动作：${payload.userActions.join("，")}` : "",
    ].filter(Boolean);
    return parts.join(" · ");
  }
  return event.message || event.code || "系统正在更新任务";
}

function appendEvent(event) {
  const sequence = Number(event?.sequence);
  if (!Number.isInteger(sequence) || sequence < 1 || localEvents.has(sequence)) return;
  localEvents.set(sequence, event);
  const ordered = [...localEvents.values()].sort((a, b) => Number(a.sequence) - Number(b.sequence));
  eventList.replaceChildren(...ordered.map(localEventElement));
  $("#local-event-count").textContent = `${ordered.length} 条记录 · 最新序号 ${ordered.at(-1).sequence}`;
  globalThis.TangyingConsoleUI?.update({ taskEventSequence: ordered.at(-1).sequence });
}

function syncLocalEvents(taskId, events = []) {
  if (localEventTaskId !== taskId) {
    localEventTaskId = taskId;
    localEvents.clear();
    eventList.replaceChildren();
    $("#local-event-count").textContent = "暂无执行事件";
    globalThis.TangyingConsoleUI?.update({ taskEventSequence: null });
  }
  for (const event of events || []) appendEvent(event);
}

function localEventElement(event) {
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = event.sequence ?? "–";
  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = localEventTitle(event);
  const detail = document.createElement("p");
  detail.textContent = localEventDetail(event);
  content.append(title, detail);
  if (event.occurredAt) content.append(makeTextElement("span", "event-timestamp", new Date(event.occurredAt).toLocaleString()));
  const payload = event.payload || {};
  const fields = {
    "任务": localEventTaskId, "步骤": event.stepId || payload.stepId,
    "命令": payload.commandId, "观测证据": (payload.evidenceIds || []).join("、") || payload.observationId,
    "任务版本": payload.taskRevision, "错误码": event.code || payload.code,
  };
  const details = document.createElement("details");
  details.className = "event-correlation-details";
  details.append(makeTextElement("summary", "", "查看编号与证据"));
  const facts = document.createElement("dl");
  facts.className = "event-correlation";
  for (const [label, value] of Object.entries(fields)) {
    if (value == null || value === "") continue;
    facts.append(makeTextElement("dt", "", label), makeTextElement("dd", "", value));
  }
  details.append(facts);
  content.append(details);
  item.append(time, content);
  return item;
}

async function refreshTask(taskId) {
  if (activeTask?.id !== taskId) return false;
  const generation = ++localTaskRequestGeneration;
  const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}`);
  if (response.ok && activeTask?.id === taskId) {
    const task = await response.json();
    if (generation !== localTaskRequestGeneration || activeTask?.id !== taskId || task.id !== taskId) return false;
    activeTask = task;
    renderTask(activeTask);
    return true;
  }
  return false;
}

function renderTask(task) {
  globalThis.TangyingConsoleUI?.update({ task });
  stateLabel.textContent = globalThis.TangyingConsoleUI?.taskPresentation(task.state).label || task.state;
  taskLabel.textContent = task.id;
  if (localTaskExperienceState.taskId !== task.id) {
    resetLocalTaskExperience(task.id);
  }
  if (localUnderstanding && task.request && localTaskExperienceState.revision === 0) {
    localUnderstanding.textContent = task.request;
  }
  updateLocalActionButtons();
  syncLocalEvents(task.id, task.events);
  const source = task.plan?.source || "deterministic";
  $("#plan-source").textContent = source === "llm_consensus" ? "LLM consensus" : source;
  const intents = task.intent?.sequence?.length ? task.intent.sequence : task.intent ? [task.intent] : [];
  $("#subtask-count").textContent = intents.length;
  // Once evidence has arrived, a task metadata refresh must not reset its
  // confirmed steps to the static plan's pending state.
  if (localTaskExperienceState.revision === 0) renderPlanSteps(task.plan?.plans || [], task);
}

function renderPlanSteps(plans, task) {
  const steps = [];
  for (const [planIndex, plan] of (plans || []).entries()) {
    for (const step of (plan.steps || [])) {
      steps.push(`${planIndex + 1}.${step.id || step.stepId || "step"} ${step.skill || step.explanation || "系统计划"} ${step.robotId ? `@${step.robotId}` : ""}`.trim());
    }
  }
  if (!localStepRibbon) return;
  const renderKey = JSON.stringify(["fallback", task?.id, task?.state, localExperienceLoadStatus, steps]);
  if (localStepRibbon.dataset.renderKey === renderKey) return;
  localStepRibbon.dataset.renderKey = renderKey;
  localStepRibbon.replaceChildren();
  if (!steps.length) {
    localStepRibbon.appendChild(makeTextElement("li", "", (localExperienceLoadStatus === "loading" ? "正在读取已保存的任务步骤…" : localExperienceLoadStatus === "failed" ? "任务步骤暂时无法读取，请检查连接后刷新。" : ["SUCCEEDED", "FAILED", "CANCELLED", "RECOVERABLE_FAILURE"].includes(task?.state) ? "这条历史记录未保存详细计划。可在开发模式查看已有事件；未保存的执行证据无法补回。" : "任务计划还未到达前端，系统正在等待解析。")));
    return;
  }
  for (const [index, text] of steps.entries()) {
    const item = document.createElement("li");
    item.className = "mission-step pending";
    item.append(makeTextElement("strong", "", `${index + 1}. ${text}`));
    localStepRibbon.append(item);
  }
}

function resetLocalTaskExperience(taskId) {
  resetLocalEvidence(taskId);
  localMissionActivities = [];
  localMissionSteps = [];
  localExperienceLoadStatus = "loading";
  localExperienceRequestGeneration += 1;
  localRecoveryRequestGeneration += 1;
  localRecovery = null;
  localRecoveryGuidance = null;
  $("#pause").disabled = true;
  $("#resume").disabled = true;
  $("#local-recovery").hidden = true;
  $("#local-professional-trace").textContent = "正在读取此任务的命令与观测关联…";
  $("#local-trace-version").textContent = taskId || "尚未选择任务";
  globalThis.TangyingConsoleUI?.update({ recovery: null });
  localTaskExperienceState = {
    taskId: String(taskId || ""),
    revision: 0,
    aggregateVersion: 0,
    cursor: 0,
  };
  if (localUnderstanding) localUnderstanding.textContent = "任务理解中，请耐心等待系统拆解";
  if (localStepRibbon) {
    delete localStepRibbon.dataset.renderKey;
    localStepRibbon.replaceChildren();
    localStepRibbon.appendChild(makeTextElement("li", "mission-step", "等待系统输出任务步骤..."));
  }
  if (localToolActivities) {
    delete localToolActivities.dataset.renderKey;
    localToolActivities.replaceChildren();
    localToolActivities.appendChild(makeTextElement("p", "", "任务开始后，这里会展示每一步对应的能力执行情况。"));
  }
}

function updateLocalActionButtons() {
  const task = activeTask;
  const terminal = !task || ["SUCCEEDED", "CANCELLED", "FAILED", "FAILED_SAFE"].includes(task.state);
  approveButton.disabled = localActionPending || terminal || task?.approved || !["READY", "WAITING_APPROVAL"].includes(task?.state);
  cancelButton.disabled = localActionPending || terminal;
  const current = localRecovery?.taskId === task?.id ? localRecovery : null;
  const currentState = !current?.state || current.state === task?.state;
  $("#pause").disabled = Boolean(localActionPending || terminal || !currentState || !current?.canPause || current.pauseRequested);
  $("#resume").disabled = Boolean(localActionPending || terminal || !currentState || !current?.canResume || current.requiresReconciliation);
  $("#pause").textContent = current?.pauseRequested ? "等待安全暂停…" : "安全暂停";
}

function localGroundingGuidance() {
  if (activeTask?.state !== "RECOVERABLE_FAILURE") return "";
  const latestState = [...localEvents.values()].filter(event => event.type === "STATE_CHANGED").sort((a, b) => b.sequence - a.sequence)[0];
  const match = /grounding ambiguous: objects=(\d+) destinations=(\d+)/.exec(latestState?.message || "");
  if (!match) return "";
  const objectMissing = Number(match[1]) === 0;
  const destinationMissing = Number(match[2]) === 0;
  if (objectMissing && destinationMissing) return "相机还没有识别到任务中的物品和放置区域，请确认它们都在视野内后继续。";
  if (objectMissing) return "相机还没有识别到任务中的物品，请确认物品在视野内后继续。";
  if (destinationMissing) return "相机还没有识别到任务中的放置区域，请确认目标区域在视野内后继续。";
  return "";
}

function renderLocalRecovery(recovery) {
  if (recovery && recovery.taskId !== activeTask?.id) return false;
  localRecovery = recovery;
  updateLocalActionButtons();
  const panel = $("#local-recovery");
  const content = $("#local-recovery-content");
  if (["SUCCEEDED", "CANCELLED"].includes(activeTask?.state)) {
    localRecoveryGuidance = null;
    panel.hidden = true; content.replaceChildren();
    globalThis.TangyingConsoleUI?.update({ recovery: null });
    return true;
  }
  const guidance = localRecoveryGuidance;
  const needsRecovery = recovery && (recovery.pauseRequested || recovery.canResume || recovery.requiresReconciliation
    || ["PAUSED", "RECOVERABLE_FAILURE", "FAILED", "FAILED_SAFE", "SAFETY_STOPPED", "RECOVERING", "WAITING_USER"].includes(activeTask?.state)
    || (recovery.reasonCode === "RECOVERY_STATUS_UNAVAILABLE" && !["SUCCEEDED", "CANCELLED", "FAILED"].includes(activeTask?.state)));
  panel.hidden = !activeTask || (!needsRecovery && !guidance);
  content.replaceChildren();
  if (panel.hidden) return true;
  const uncertain = recovery?.requiresReconciliation === true;
  panel.dataset.tone = uncertain ? "danger" : "warning";
  $("#local-recovery-heading").textContent = uncertain ? "先核对动作结果" : recovery?.pauseRequested ? "正在等待安全暂停" : recovery?.canResume ? "可以继续这个任务" : "任务恢复状态";
  const groundingHelp = !uncertain && localGroundingGuidance();
  if (groundingHelp) {
    $("#local-recovery-heading").textContent = "先确认相机中的任务目标";
    content.append(makeTextElement("p", "user-recovery-cause", groundingHelp));
  }
  if (recovery?.reason) content.append(makeTextElement("p", "", recovery.reason));
  if (recovery?.pauseRequested) content.append(makeTextElement("p", "", "暂停请求已经记录。当前动作结束并保存结果后，机器人会暂停后续步骤。"));
  if (uncertain) content.append(makeTextElement("p", "", "有物理动作开始后未收到可信完成记录。请联系维护人员核对现场，不要重新下达相同动作。"));
  const completed = recovery?.completedStepIds || [];
  const uncertainSteps = recovery?.uncertainStepIds || [];
  if (completed.length || uncertainSteps.length) {
    content.append(makeTextElement("p", "local-recovery-counts", `已记录完成 ${completed.length} 步${uncertainSteps.length ? ` · 待核对 ${uncertainSteps.length} 步` : ""}`));
  }
  if (guidance) {
    for (const text of [guidance.knownState, guidance.robotSafetyState, guidance.automaticAction]) {
      if (text && text !== recovery?.reason) content.append(makeTextElement("p", "", missionReferenceLabel(text)));
    }
    if (guidance.timeline?.length) {
      const timeline = document.createElement("ol");
      timeline.className = "mission-recovery-timeline";
      for (const entry of guidance.timeline) {
        timeline.append(makeTextElement("li", "", [entry.knownState, entry.action].filter(Boolean).map(missionReferenceLabel).join("：")));
      }
      content.append(timeline);
    }
    if (guidance.userActions?.length) {
      const actions = document.createElement("ul");
      for (const action of guidance.userActions) actions.append(makeTextElement("li", "", missionReferenceLabel(action)));
      content.append(actions);
    }
  }
  globalThis.TangyingConsoleUI?.update({ recovery });
  return true;
}

async function loadLocalRecovery(taskId) {
  if (!taskId || activeTask?.id !== taskId) return false;
  const generation = ++localRecoveryRequestGeneration;
  const isCurrent = () => generation === localRecoveryRequestGeneration && activeTask?.id === taskId;
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}/recovery`, { cache: "no-store" });
    if (!isCurrent()) return false;
    if (!response.ok) {
      renderLocalRecovery({ taskId, reason: response.status === 404 ? "当前服务尚未提供恢复状态，请更新服务后查看。" : "恢复状态暂时无法确认，请等待连接恢复。", reasonCode: "RECOVERY_STATUS_UNAVAILABLE" });
      return false;
    }
    const recovery = await response.json();
    if (!isCurrent() || recovery.taskId !== taskId) return false;
    return renderLocalRecovery(recovery);
  } catch (_) {
    if (isCurrent()) renderLocalRecovery({ taskId, reason: "恢复状态暂时无法确认，请等待连接恢复。", reasonCode: "RECOVERY_STATUS_UNAVAILABLE" });
    return false;
  }
}

async function loadLocalTasks(options = {}) {
  const generation = ++localTaskListGeneration;
  try {
    const response = await fetch("/v1/tasks", { cache: "no-store" });
    if (!response.ok || generation !== localTaskListGeneration) throw new Error("history unavailable");
    const tasks = await response.json();
    if (generation !== localTaskListGeneration || !Array.isArray(tasks)) return false;
    const ordered = [...tasks].sort((a, b) => Date.parse(b.updatedAt || b.createdAt || 0) - Date.parse(a.updatedAt || a.createdAt || 0));
    const list = $("#local-task-list");
    const renderKey = JSON.stringify([activeTask?.id, ordered.slice(0, 50).map(task => [task.id, task.request, task.state, task.updatedAt, task.createdAt])]);
    if (list.dataset.renderKey !== renderKey) {
    list.dataset.renderKey = renderKey;
    list.replaceChildren();
    for (const task of ordered.slice(0, 50)) {
      const item = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.setAttribute("aria-current", String(activeTask?.id === task.id));
      const description = document.createElement("span");
      description.append(makeTextElement("strong", "", task.request || "未命名任务"));
      if (task.updatedAt || task.createdAt) description.append(makeTextElement("time", "", new Date(task.updatedAt || task.createdAt).toLocaleString()));
      const presentation = globalThis.TangyingConsoleUI?.taskPresentation(task.state) || { label: task.state, tone: "neutral" };
      const status = makeTextElement("span", "status-pill", presentation.label);
      status.dataset.tone = presentation.tone;
      button.append(description, status);
      button.addEventListener("click", () => { void openLocalTask(task.id); });
      item.append(button);
      list.append(item);
    }
    }
    $("#local-history-state").textContent = ordered.length ? `已保存 ${ordered.length} 个任务${ordered.length > 50 ? "，显示最近 50 个" : ""}` : "还没有任务。回到工作台描述一件想让机器人完成的事。";
    if (options.openLatest && !activeTask && ordered[0]) await openLocalTask(ordered[0].id, { navigate: false });
    return true;
  } catch (_) {
    if (generation === localTaskListGeneration) $("#local-history-state").textContent = "记录暂时无法读取，请检查连接后刷新。";
    return false;
  }
}

async function openLocalTask(taskId, options = {}) {
  const generation = ++localSelectionGeneration;
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}`, { cache: "no-store" });
    if (!response.ok || generation !== localSelectionGeneration) return false;
    const task = await response.json();
    if (generation !== localSelectionGeneration || task.id !== taskId) return false;
    activeTask = task;
    renderTask(task);
    connectEvents(taskId);
    if (options.navigate !== false) globalThis.TangyingConsoleUI?.navigate("workspace");
    await Promise.all([loadLocalTaskExperience(taskId), loadLocalRecovery(taskId), loadLocalEvidence(taskId)]);
    return true;
  } catch (_) {
    if (generation === localSelectionGeneration) globalThis.TangyingConsoleUI?.feedback("这个任务暂时无法打开，请检查服务连接后重试。", true);
    return false;
  }
}

async function pollLocalTask() {
  const taskId = activeTask?.id;
  if (!taskId) return;
  try {
    await refreshTask(taskId);
    if (activeTask?.id !== taskId) return;
    const requests = [loadLocalRecovery(taskId)];
    if (pageVisible("workspace", "diagnostics")) requests.push(loadLocalTaskExperience(taskId));
    if (scenePageVisible()) requests.push(loadLocalEvidence(taskId));
    await Promise.all(requests);
    if (!socket && activeTask?.id === taskId) connectEvents(taskId);
  } catch (_) {
    if (activeTask?.id === taskId) setConnection(false);
  }
}

function clearLocalEvidenceImages() {
  localEvidenceImageGeneration += 1;
  localEvidenceVerification = null;
  localEvidenceController?.abort();
  localEvidenceController = null;
  for (const kind of ["rgb", "depth"]) {
    const image = $(`#local-evidence-${kind}`);
    image.onload = null; image.onerror = null;
    image.removeAttribute("src"); image.hidden = true;
    const placeholder = $(`#local-evidence-${kind}-status`);
    placeholder.hidden = false; placeholder.textContent = "尚未选择历史观测";
    const url = localEvidenceURLs.get(kind);
    if (url) URL.revokeObjectURL(url);
  }
  localEvidenceURLs.clear();
}

function resetLocalEvidence(taskId) {
  localEvidenceListGeneration += 1;
  clearLocalEvidenceImages();
  localEvidenceRecords = [];
  localNavigationEvidence.clear();
  localEvidenceSelectedId = "";
  localEvidenceNextBefore = null;
  localEvidencePaginationStarted = false;
  localEvidenceChoicesKey = "";
  $("#local-evidence-select").replaceChildren();
  $("#local-evidence-select").disabled = true;
  $("#refresh-local-evidence").disabled = !taskId;
  $("#local-evidence-older").hidden = true;
  $("#local-evidence-description").textContent = taskId ? "正在读取这个任务保存的历史观测…" : "选择任务后，可回看机器人当时的彩色画面与深度图。";
  $("#local-evidence-details").textContent = "尚未选择历史观测";
  $("#local-evidence-verification").textContent = "";
  $("#local-evidence-json").hidden = true;
  $("#local-evidence-json").removeAttribute("href");
}

function localEvidenceEvent(record) {
  if (record.taskId !== activeTask?.id) return null;
  const revision = Number(record.taskRevision || 1);
  return [...localEvents.values()].sort((a, b) => b.sequence - a.sequence).find(item => {
    const payload = item.payload || {};
    const step = payload.stepId || item.stepId;
    const executionStep = revision > 1 ? `revision-${revision}/${step}` : step;
    return item.type === "TOOL_ACTIVITY" && ["CONFIRMED", "FAILED"].includes(payload.activityStatus)
      && Number(payload.taskRevision || 1) === revision && executionStep === record.stepId
      && Array.isArray(payload.evidenceIds) && payload.evidenceIds.includes(record.captureId);
  }) || null;
}

function localActivityEvidenceRecord(activity) {
  if (!["CONFIRMED", "FAILED"].includes(activity.status)) return null;
  const revision = Number(activeTask?.currentRevision || localTaskExperienceState.revision || 1);
  const executionStep = revision > 1 ? `revision-${revision}/${activity.stepId}` : activity.stepId;
  const event = [...localEvents.values()].sort((a, b) => b.sequence - a.sequence).find(item => item.type === "TOOL_ACTIVITY"
    && item.payload?.activityStatus === activity.status && Number(item.payload.taskRevision || 1) === revision
    && (item.payload.stepId || item.stepId) === activity.stepId);
  return localEvidenceRecords.find(record => record.taskId === activeTask?.id && record.stepId === executionStep
    && Number(record.taskRevision || 1) === revision && Array.isArray(event?.payload?.evidenceIds)
    && event.payload.evidenceIds.includes(record.captureId)) || null;
}

function localEvidenceActivity(record) {
  const revision = Number(record.taskRevision || 1);
  if (revision !== Number(activeTask?.currentRevision || localTaskExperienceState.revision || 1)) return null;
  return localMissionActivities.find(item => (revision > 1 ? `revision-${revision}/${item.stepId}` : item.stepId) === record.stepId);
}

function localTargetDescription(args) {
  const object = typeof args?.objectId === "string" ? args.objectId : "";
  const destination = typeof args?.destinationId === "string" ? args.destinationId : "";
  return [...new Set([object, destination].filter(Boolean))].map(missionReferenceLabel).join(" → ");
}

function localActivityDisplayName(activity, record = localActivityEvidenceRecord(activity || {})) {
  const step = String(activity?.stepId || "");
  if (/^(?:task\d+-)?observe_after_navigation(?:\/resume-read\/.+)?$/.test(step)) return "到位后重新观察";
  if (/^(?:task\d+-)?navigate(?:\/resume-read\/.+)?$/.test(step)) {
    const source = localNavigationVerification(record)?.map_receipt?.completion_source;
    return source === "pose_confirmation" ? "确认当前操作位置" : source === "nav2_action" ? "移动到操作位置" : "前往或确认操作位置";
  }
  return activity?.displayName || "";
}

function evidenceStepLabel(record) {
  const event = localEvidenceEvent(record);
  if (!event) return "历史现场观测";
  const activity = localEvidenceActivity(record);
  const names = { observe_scene: "观察环境", resolve_targets: "确认任务目标", "navigation.navigate": "移动到操作位置", plan_grasp: "规划抓取", "manipulation.pick": "拿取物品", verify_grasp: "检查是否拿稳", "manipulation.place": "放置物品", verify_placement: "检查放置结果", recover_to_safe_pose: "恢复安全姿态" };
  const title = localActivityDisplayName(activity || { stepId: event.payload.stepId || event.stepId }, record) || names[event.payload.toolName] || "执行后观测";
  return [title, localTargetDescription(event.payload.arguments) || localTargetDescription(activity?.safeArguments)].filter(Boolean).join(" · ");
}

function localEvidenceIsCommandObservation(record) {
  const payload = localEvidenceEvent(record)?.payload;
  return payload?.evidenceSource === "command_observation"
    && payload.receiptObservationId === record.captureId;
}

function localEvidenceSourceLabel(record) {
  const original = localEvidenceIsCommandObservation(record);
  const tool = localEvidenceEvent(record)?.payload?.toolName;
  if (original) return ["verify_grasp", "verify_placement"].includes(tool) ? "验证原始观测" : "命令原始观测";
  return "执行后现场画面（未保存原验证输入）";
}

function localNavigationVerification(record) {
  if (!record || record.expired || !localEvidenceIsCommandObservation(record)) return null;
  const event = localEvidenceEvent(record);
  if (event?.payload?.toolName !== "navigation.navigate" || event.payload.activityStatus !== "CONFIRMED") return null;
  const value = localNavigationEvidence.get(record.id)?.value;
  const receipt = value?.map_receipt;
  const checked = receipt?.checked_at_unix_ms;
  const freshAtCheck = stamp => Number.isSafeInteger(stamp) && stamp > 0 && checked >= stamp && checked - stamp <= 1000;
  return value?.kind === "navigation.navigate" && value.passed === true
    && value.source_id === record.sourceId && value.observed_at_unix_ms === record.observedAtUnixMs
    && value.pose_source === "sim_proprioceptive_odom" && receipt?.pose_source === "rtabmap_tf"
    && Number.isFinite(value.position_error_m) && value.position_error_m >= 0 && value.position_error_m <= .015
    && Number.isFinite(value.yaw_error_rad) && value.yaw_error_rad >= 0 && value.yaw_error_rad <= .04
    && ["nav2_action", "pose_confirmation"].includes(receipt.completion_source)
    && Number.isSafeInteger(checked) && checked > 0 && Math.abs(checked - record.observedAtUnixMs) <= 1000
    && freshAtCheck(receipt.pose_observed_at_unix_ms) && freshAtCheck(receipt.completion_pose_observed_at_unix_ms)
    ? value : null;
}

function navigationCompletionText(value) {
  return value.map_receipt.completion_source === "pose_confirmation"
    ? "当前位置已确认，未请求底盘移动" : "Nav2 导航已完成，并通过到位核验";
}

async function loadLocalNavigationEvidence(records) {
  await Promise.all(records.filter(record => !record.expired && localEvidenceIsCommandObservation(record)
    && localEvidenceEvent(record)?.payload?.toolName === "navigation.navigate").map(record => {
    const cached = localNavigationEvidence.get(record.id);
    if (cached) return cached.promise;
    const entry = { value: null, promise: null };
    localNavigationEvidence.set(record.id, entry);
    const current = () => activeTask?.id === record.taskId && localNavigationEvidence.get(record.id) === entry;
    entry.promise = (async () => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 2500);
      try {
        const response = await fetch(`/v1/tasks/${encodeURIComponent(record.taskId)}/observations/${record.id}`, { cache: "no-store", signal: controller.signal });
        if (!current()) return;
        if (!response.ok) { localNavigationEvidence.delete(record.id); return; }
        const detail = await response.json();
        if (!current()) return;
        if (detail.id === record.id && detail.taskId === record.taskId && detail.captureId === record.captureId
          && detail.stepId === record.stepId && Number(detail.taskRevision || 1) === Number(record.taskRevision || 1)
          && detail.observedAtUnixMs === record.observedAtUnixMs && detail.sourceId === record.sourceId && !detail.expired) {
          entry.value = detail.snapshot?.robotState?.navigation || null;
        }
        // Only the labels change; preserve the selected capture and decoded images.
        renderLocalMissionActivities(localMissionActivities);
      } catch (_) { if (current()) localNavigationEvidence.delete(record.id); }
      finally { clearTimeout(timeout); }
    })();
    return entry.promise;
  }));
}

function renderLocalEvidenceVerification(record) {
  const output = $("#local-evidence-verification");
  const event = localEvidenceEvent(record);
  const tool = event?.payload?.toolName;
  if (tool === "navigation.navigate") {
    const navigation = localNavigationVerification(record);
    output.textContent = navigation
      ? `${navigationCompletionText(navigation)}。记录的独立位置误差为 ${(navigation.position_error_m * 1000).toFixed(1)} 毫米，朝向误差为 ${navigation.yaw_error_rad.toFixed(3)} 弧度。这是该次历史回执，当前状态以实时定位为准。`
      : "这份记录尚未提供可核对的导航完成来源，不能据单张画面判断底盘是否移动。";
    return;
  }
  if (!["verify_grasp", "verify_placement"].includes(tool)) { output.textContent = ""; return; }
  const caveat = "单张画面用于人工回看，不能单独证明本次检查条件已满足。";
  if (record.expired) { output.textContent = `此检查的历史快照已清理，无法读取具体验证条件。${caveat}`; return; }
  if (!localEvidenceIsCommandObservation(record)) {
    output.textContent = `这一步有执行结果回执，但这份旧记录未保存验证时使用的原始观测与条件，不能用执行后的图片替代验证输入。${caveat}`;
    return;
  }
  const verification = localEvidenceVerification?.id === record.id ? localEvidenceVerification.value : null;
  const args = event.payload.arguments || {};
  const valid = verification && verification.kind === tool && typeof verification.passed === "boolean"
    && typeof verification.object_id === "string" && verification.object_id.length > 0
    && (!args.objectId || verification.object_id === args.objectId)
    && (tool !== "verify_placement" || (typeof verification.destination_id === "string" && verification.destination_id.length > 0 && (!args.destinationId || verification.destination_id === args.destinationId)))
    && verification.observation_id === record.captureId && verification.source_id === record.sourceId
    && verification.last_observed_at_unix_ms === record.observedAtUnixMs
    && Number.isSafeInteger(verification.first_observed_at_unix_ms) && verification.first_observed_at_unix_ms > 0
    && verification.first_observed_at_unix_ms <= verification.last_observed_at_unix_ms
    && Number.isSafeInteger(verification.sample_count) && (verification.sample_count > 0 || (!verification.passed && verification.sample_count === 0))
    && Number.isFinite(verification.stable_duration_s) && verification.stable_duration_s >= 0
    && Number.isFinite(verification.max_displacement_m) && verification.max_displacement_m >= 0;
  if (!valid) {
    output.textContent = `已关联验证原始观测；${localEvidenceVerification ? "这份快照未提供可核对的结构化验证条件。" : "正在读取具体验证条件…"}${caveat}`;
    return;
  }
  const target = localTargetDescription({ objectId: verification.object_id, destinationId: verification.destination_id });
  if (verification.sample_count === 0) {
    output.textContent = `${target}：记录的检查结果为未通过。当前画面未满足检查条件，未形成稳定样本；下方为实际失败判定帧。${caveat}`;
    return;
  }
  output.textContent = `${target}：${verification.passed ? "记录的检查结果为通过" : "记录的检查结果为未通过"}。连续 ${verification.sample_count} 次观测，覆盖 ${Math.round(verification.stable_duration_s * 1000)} 毫秒，最大位移 ${(verification.max_displacement_m * 1000).toFixed(1)} 毫米。下方是该次检查的最后一帧。${caveat}`;
}

function renderLocalEvidenceMetadata(record) {
  if (!record || record.id !== localEvidenceSelectedId || record.taskId !== activeTask?.id) return;
  const captured = Number.isFinite(record.observedAtUnixMs) ? new Date(record.observedAtUnixMs).toLocaleString(undefined, { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 }) : "未知时间";
  const source = record.sourceType === "rgbd_camera" ? "RGB-D 相机" : "未声明为 RGB-D 的观测源";
  $("#local-evidence-description").textContent = `历史采集时间：${captured} · ${evidenceStepLabel(record)} · ${source}。${localEvidenceEvent(record) ? localEvidenceSourceLabel(record) : "历史现场画面，未关联已完成的工具调用"}。这是当时保存的证据，不是实时画面。${record.expired ? "图像和快照内容已清理，仍保留编号与哈希供追溯。" : ""}`;
  const event = localEvidenceEvent(record);
  $("#local-evidence-details").textContent = JSON.stringify({
    id: record.id, taskId: record.taskId, taskRevision: record.taskRevision,
    stepId: record.stepId, commandId: event?.payload?.commandId,
    evidenceSource: event?.payload?.evidenceSource, receiptObservationId: event?.payload?.receiptObservationId,
    captureId: record.captureId, robotId: record.robotId, adapter: record.adapter,
    sourceId: record.sourceId, sourceType: record.sourceType, sourceFrameId: record.sourceFrameId,
    frameId: record.frameId, transformRevision: record.transformRevision,
    captureSequence: record.captureSequence, observedAtUnixMs: record.observedAtUnixMs,
    recordedAt: record.recordedAt, historical: true, expired: record.expired,
    snapshotSha256: record.snapshotSha256, rgbSha256: record.rgbSha256, depthSha256: record.depthSha256,
    navigation: localNavigationVerification(record) || undefined,
  }, null, 2);
  renderLocalEvidenceVerification(record);
}

function renderLocalEvidenceChoices() {
  const key = JSON.stringify(localEvidenceRecords.map(record => [record.id, record.expired, evidenceStepLabel(record)]));
  $("#local-evidence-older").hidden = !localEvidenceNextBefore;
  if (key === localEvidenceChoicesKey) return;
  localEvidenceChoicesKey = key;
  const select = $("#local-evidence-select");
  select.replaceChildren();
  for (const record of localEvidenceRecords) {
    const option = document.createElement("option");
    option.value = record.id;
    const date = Number.isFinite(record.observedAtUnixMs) ? new Date(record.observedAtUnixMs).toLocaleString() : "采集时间未知";
    option.textContent = `${evidenceStepLabel(record)} · ${date}${record.expired ? " · 图像已清理" : ""}`;
    select.append(option);
  }
  select.disabled = !localEvidenceRecords.length;
  select.value = localEvidenceSelectedId;
  $("#local-evidence-older").hidden = !localEvidenceNextBefore;
}

async function loadLocalEvidence(taskId, options = {}) {
  if (!taskId || activeTask?.id !== taskId) return false;
  const generation = ++localEvidenceListGeneration;
  const current = () => activeTask?.id === taskId && generation === localEvidenceListGeneration;
  const before = options.older ? localEvidenceNextBefore : null;
  if (options.older && !before) return false;
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}/observations?limit=100${before ? `&before=${before}` : ""}`, { cache: "no-store" });
    if (!current()) return false;
    if (!response.ok) {
      if (!localEvidenceRecords.length) $("#local-evidence-description").textContent = response.status === 404 ? "这个任务没有可读取的历史观测图像。已有事件仍可在开发模式查看。" : "历史观测暂时无法读取，请稍后刷新。";
      return false;
    }
    const body = await response.json();
    if (!current() || body.taskId !== taskId || body.historical !== true || !Array.isArray(body.records)) return false;
    const records = body.records.filter(record => record.schemaVersion === "evidence.capture.v1" && record.taskId === taskId && record.historical === true && /^[a-f0-9]{64}$/.test(record.id) && Number.isSafeInteger(record.recordIndex));
    const merged = new Map(localEvidenceRecords.map(record => [record.id, record]));
    for (const record of records) merged.set(record.id, merged.get(record.id)?.expired ? { ...record, expired: true } : record);
    localEvidenceRecords = [...merged.values()].sort((a, b) => b.recordIndex - a.recordIndex);
    const navigationDetails = loadLocalNavigationEvidence(localEvidenceRecords);
    const next = Number.isSafeInteger(body.nextBefore) && body.nextBefore > 0 ? body.nextBefore : null;
    if (options.older || !localEvidencePaginationStarted) localEvidenceNextBefore = next;
    localEvidencePaginationStarted = true;
    const previousChoices = localEvidenceChoicesKey;
    renderLocalEvidenceChoices();
    if (previousChoices !== localEvidenceChoicesKey) {
      renderLocalMissionActivities(localMissionActivities);
      if (localMissionSteps.length) renderLocalMissionSteps(localMissionSteps);
    }
    if (!localEvidenceRecords.length) {
      $("#local-evidence-description").textContent = ["SUCCEEDED", "FAILED", "CANCELLED"].includes(activeTask?.state) ? "这条任务没有保存历史观测图像，可在开发模式查看已有事件。" : "此任务尚未保存历史观测图像。任务开始后会记录实际采集证据。";
      return true;
    }
    if (!localEvidenceSelectedId) await selectLocalEvidence(localEvidenceRecords[0].id);
    else if (options.refreshSelected || localEvidenceRecords.find(record => record.id === localEvidenceSelectedId)?.expired) await selectLocalEvidence(localEvidenceSelectedId);
    await navigationDetails;
    return true;
  } catch (_) {
    if (current() && !localEvidenceRecords.length) $("#local-evidence-description").textContent = "历史观测连接中断，请稍后刷新。";
    return false;
  }
}

async function selectLocalEvidence(id) {
  const record = localEvidenceRecords.find(item => item.id === id && item.taskId === activeTask?.id);
  if (!record) return false;
  clearLocalEvidenceImages();
  localEvidenceSelectedId = id;
  $("#local-evidence-select").value = id;
  renderLocalEvidenceMetadata(record);
  const endpoint = `/v1/tasks/${encodeURIComponent(record.taskId)}/observations/${record.id}`;
  const json = $("#local-evidence-json");
  json.hidden = false; json.href = endpoint;
  if (record.expired) {
    for (const kind of ["rgb", "depth"]) $(`#local-evidence-${kind}-status`).textContent = "历史内容已清理；不会替换为当前画面。";
    return true;
  }
  localEvidenceController = new AbortController();
  const controller = localEvidenceController;
  const generation = localEvidenceImageGeneration;
  const current = () => !controller.signal.aborted && generation === localEvidenceImageGeneration && activeTask?.id === record.taskId && localEvidenceSelectedId === id;
  const readVerification = async () => {
    if (!localEvidenceIsCommandObservation(record) || !["verify_grasp", "verify_placement"].includes(localEvidenceEvent(record)?.payload?.toolName)) return;
    let value = null;
    try {
      const response = await fetch(endpoint, { cache: "no-store", signal: controller.signal });
      if (!current()) return;
      if (response.ok) {
        const detail = await response.json();
        if (!current()) return;
        if (detail.id === record.id && detail.taskId === record.taskId && detail.captureId === record.captureId
          && detail.stepId === record.stepId && Number(detail.taskRevision || 1) === Number(record.taskRevision || 1)
          && detail.observedAtUnixMs === record.observedAtUnixMs && detail.sourceId === record.sourceId && !detail.expired) {
          value = detail.snapshot?.robotState?.verification || null;
        }
      }
    } catch (_) { /* Missing historical details never fall back to live telemetry. */ }
    if (current()) {
      localEvidenceVerification = { id: record.id, value };
      renderLocalEvidenceVerification(record);
    }
  };
  await Promise.all([readVerification(), ...["rgb", "depth"].map(async kind => {
    const status = $(`#local-evidence-${kind}-status`);
    const bytes = kind === "rgb" ? record.rgbBytes : record.depthBytes;
    if (!Number.isSafeInteger(bytes) || bytes <= 0) { status.textContent = "此采集未保存这类图像。"; return; }
    status.textContent = "正在读取当时的图像…";
    try {
      const response = await fetch(`${endpoint}/${kind}`, { cache: "no-store", signal: controller.signal });
      if (!current()) return;
      if (response.status === 410) {
        record.expired = true;
        await selectLocalEvidence(id);
        return;
      }
      if (!response.ok) { status.textContent = "这张历史图像暂不可读取，请刷新重试。"; return; }
      const blob = await response.blob();
      if (!current()) return;
      const url = URL.createObjectURL(blob);
      localEvidenceURLs.set(kind, url);
      const image = $(`#local-evidence-${kind}`);
      image.onload = () => {
        if (!current() || localEvidenceURLs.get(kind) !== url) return;
        image.hidden = false; status.hidden = true;
      };
      image.onerror = () => {
        if (!current()) return;
        URL.revokeObjectURL(url); localEvidenceURLs.delete(kind);
        image.removeAttribute("src"); image.hidden = true;
        status.textContent = "历史图像解码失败；没有替换为实时画面。";
      };
      image.src = url;
    } catch (error) {
      if (error?.name !== "AbortError" && current()) status.textContent = "历史图像连接中断，请刷新重试。";
    }
  })]);
  return current();
}

async function pollLocalWorld() {
  if (!pageVisible("diagnostics")) return;
  try {
    const response = await fetch("/v1/world", { cache: "no-store" });
    if (!response.ok) return;
    const world = await response.json();
    globalThis.TangyingConsoleUI?.update({ worldRevision: world.revision, eventCursor: world.eventCursor });
  } catch (_) { /* Keep the last correlated world version during a disconnect. */ }
}

async function pollTelemetry() {
  if (!pageVisible("workspace", "devices", "diagnostics")) return;
  if (scenePageVisible()) { renderPerception(latestTelemetry); refreshSceneFreshness(); }
  const adapter = adapterInput.value;
  const pending = telemetryPollInFlight;
  const elapsed = pending ? Date.now() - pending.startedAt : 0;
  if (pending && isCurrentTelemetryPoll(pending) && pending.adapter === adapter
    && pending.sourceId === selectedCameraSource && pending.view === sceneViewMode
    && (pending.requests > 0 || pendingSceneImage) && elapsed >= 0 && elapsed < TELEMETRY_REQUEST_TIMEOUT_MS) return;
  const poll = beginTelemetryPoll(adapter);
  poll.requests += 1;
  try {
    const response = await fetch(`/v1/telemetry?adapter=${encodeURIComponent(adapter)}&limit=20`, {
      signal: poll.controller.signal,
    });
    if (!isCurrentTelemetryPoll(poll)) return;
    if (!response.ok) {
      handleTelemetryFailure(`遥测请求失败（HTTP ${response.status}）`);
      return;
    }
    const payload = await response.json();
    if (!isCurrentTelemetryPoll(poll)) return;
    const selected = syncAdapters(payload.adapters || [], adapter);
    if (selected !== adapter) {
      invalidateTelemetryPolling();
      void pollTelemetry();
      return;
    }
    $("#adapter-label").textContent = `adapter: ${selected || "—"}`;
    if (payload.latest) {
      const observedAt = Date.parse(payload.latest.observedAt || "");
      if (!Number.isFinite(observedAt)) {
        handleTelemetryFailure("遥测时间戳无效");
        return;
      }
      if (payload.latest.adapter && payload.latest.adapter !== selected) return;
      primaryTelemetry = payload.latest;
      if (!scenePageVisible()) { renderTelemetry(payload.latest, { metadataOnly: true }); return; }
      syncSceneCameras(primaryTelemetry);
      poll.sourceId = selectedCameraSource;
      if (hasSelectedRGBDCamera()) {
        renderTelemetry(payload.latest, { metadataOnly: true });
        renderSceneControls(latestTelemetry || primaryTelemetry);
        await updateSelectedCamera(poll);
        return;
      }
      const previousObservedAt = lastObservedAtByAdapter.get(selected);
      if (previousObservedAt != null && observedAt <= previousObservedAt) {
        // Same capture can become stale or lose media. Never give it a new timestamp.
        if (observedAt === previousObservedAt && latestTelemetry) {
          latestTelemetry.colorFrameAvailable = payload.latest.colorFrameAvailable;
          latestTelemetry.depthFrameAvailable = payload.latest.depthFrameAvailable;
          renderPerception(latestTelemetry);
          refreshSceneFreshness();
        }
        return;
      }
      lastObservedAtByAdapter.set(selected, observedAt);
      poll.observedAt = observedAt;
      if (!isCurrentTelemetryPoll(poll)) return;
      if (["cloud", "orbit"].includes(sceneViewMode)) renderTelemetry(payload.latest);
      else {
        renderTelemetry(payload.latest, { metadataOnly: true });
        renderSceneControls(payload.latest);
        await updateSceneFrame(payload.latest, poll);
      }
      if (!isCurrentTelemetryPoll(poll)) return;
    }
    else if (!latestTelemetry) {
      clearSceneFrame(`${selected || "Robot Runtime"} 已连接，尚无场景观测`);
      renderTelemetry(null);
    }
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentTelemetryPoll(poll)) return;
    handleTelemetryFailure("遥测连接中断");
  } finally {
    poll.requests -= 1;
  }
}

function beginTelemetryPoll(adapter) {
  invalidateTelemetryPolling();
  telemetryGeneration += 1;
  telemetryController = new AbortController();
  telemetryPollInFlight = {
    adapter,
    controller: telemetryController,
    generation: telemetryGeneration,
    observedAt: null,
    sourceId: selectedCameraSource,
    view: sceneViewMode,
    startedAt: Date.now(),
    requests: 0,
  };
  return telemetryPollInFlight;
}

function invalidateTelemetryPolling() {
  telemetryGeneration += 1;
  if (telemetryController) telemetryController.abort();
  telemetryController = null;
  telemetryPollInFlight = null;
  discardPendingFrame();
}

function isCurrentTelemetryPoll(poll) {
  if (!poll || poll.controller.signal.aborted || !pageVisible("workspace", "devices", "diagnostics")) return false;
  if (poll.generation !== telemetryGeneration || poll.adapter !== adapterInput.value) return false;
  if (poll.observedAt == null) return true;
  return lastObservedAtByAdapter.get(poll.adapter) === poll.observedAt;
}

function syncAdapters(adapters, selectedAdapter) {
  const current = adapterInput.value || selectedAdapter;
  for (const adapter of adapters) {
    if (typeof adapter === "string" && adapter.trim()) discoveredAdapters.add(adapter.trim());
  }
  if (current) discoveredAdapters.add(current);
  const choices = [...discoveredAdapters].sort();
  const renderKey = JSON.stringify(choices);
  if (adapterInput.dataset.choices === renderKey) {
    const selection = choices.includes(current) ? current : choices[0] || "";
    adapterInput.value = selection;
    return selection;
  }
  adapterInput.dataset.choices = renderKey;
  adapterInput.replaceChildren();
  if (!choices.length) {
    const pending = document.createElement("option");
    pending.value = "";
    pending.textContent = "等待 Runtime 适配器…";
    pending.disabled = true;
    pending.selected = true;
    adapterInput.append(pending);
    return "";
  }
  for (const adapter of choices) {
    const option = document.createElement("option");
    option.value = adapter;
    option.textContent = adapterLabel(adapter);
    adapterInput.append(option);
  }
  const selection = choices.includes(current) ? current : choices[0];
  adapterInput.value = selection;
  return selection;
}

function adapterLabel(adapter) {
  return adapter.replaceAll("_", " ").replaceAll("-", " ").toUpperCase();
}

function handleTelemetryFailure(message) {
  setRuntimeConnection(false);
  if (usingSecondaryCamera()) renderTelemetry(null, { camera: true });
  clearSceneFrame(`${message}，当前现场未知。等待重新连接并获取新观测。`);
}

async function pollRuntime() {
  try {
    const response = await fetch("/v1/runtime");
    if (!response.ok) {
      $("#robot-id").textContent = "未连接";
      setRuntimeConnection(false);
      return;
    }
    const snapshot = await response.json();
    setRuntimeConnection(true);
    $("#robot-id").textContent = snapshot.RobotID || "—";
    $("#telemetry-adapter").textContent = snapshot.Adapter || "—";
    $("#software-version").textContent = snapshot.RuntimeVersion || snapshot.SoftwareVersion || "—";
    if (snapshot.Blockers?.length) $("#anomalies").textContent = `阻塞: ${snapshot.Blockers.join(" / ")}`;
  } catch (_) {
    $("#robot-id").textContent = "未连接";
    setRuntimeConnection(false);
  }
}

function renderTelemetry(snapshot, options = {}) {
  if (!options.camera) {
    primaryTelemetry = snapshot;
    globalThis.TangyingNavigationView?.update(scenePageVisible() ? snapshot : null);
    if (!options.metadataOnly) syncSceneCameras(snapshot);
    if (!options.metadataOnly && usingSecondaryCamera()) return;
  }
  globalThis.TangyingConsoleUI?.update({ robotId: snapshot?.robotId, adapter: snapshot?.adapter, emergencyStopped: snapshot?.emergencyStopped, anomalyCount: snapshot?.anomalies?.length || 0 });
  if (!options.metadataOnly) latestTelemetry = snapshot;
  $("#telemetry-time").textContent = snapshot ? new Date(snapshot.observedAt).toLocaleString() : "等待遥测";
  $("#activity").textContent = snapshot?.activity || "—";
  $("#mode").textContent = snapshot?.mode || "—";
  $("#robot-id").textContent = snapshot?.robotId || "—";
  $("#telemetry-adapter").textContent = snapshot?.adapter || "—";
  $("#software-version").textContent = snapshot?.softwareVersion || "—";
  $("#runtime-activity").textContent = `activity: ${snapshot?.activity || "—"}`;
  const estop = $("#estop-state");
  estop.textContent = snapshot?.emergencyStopped ? "已停止" : snapshot ? "未报告急停" : "待确认";
  estop.style.color = snapshot?.emergencyStopped ? "var(--danger)" : "var(--mint)";
  const anomalies = snapshot?.anomalies || [];
  $("#anomalies").textContent = anomalies.length ? `异常: ${anomalies.join(" / ")}` : "";
  const robotState = snapshot?.robotState || {};
  const robotEntities = robotEntitiesFromSnapshot(snapshot?.entities || [], robotState, snapshot);
  if (!options.metadataOnly) { updateSceneIdentity(snapshot, robotEntities[0] || null); renderPerception(snapshot); }
  $("#held-object").textContent = robotState.held ? missionReferenceLabel(robotState.held) : "—";
  $("#active-tool").textContent = robotState.active_tool || robotState.activeTool || "IDLE";
  $("#model-revision").textContent = shortRevision(robotState.model_revision || robotState.modelRevision);
  $("#reward").textContent = Number(robotState.reward || 0).toFixed(2);
  const confidence = robotState.verification_confidence ?? robotState.verificationConfidence;
  $("#verification-confidence").textContent = confidence == null ? "—" : `${(Number(confidence) * 100).toFixed(1)}%`;
  $("#sensor-json").textContent = snapshot
    ? JSON.stringify(
        {
          observedAt: snapshot.observedAt,
          taskId: snapshot.taskId,
          stepId: snapshot.stepId,
          activity: snapshot.activity,
          emergencyStopped: snapshot.emergencyStopped,
          anomalies: snapshot.anomalies,
          lastError: snapshot.lastError,
          reconstruction: snapshot.reconstruction ? { ...snapshot.reconstruction, points: undefined, pointColors: undefined, pointCount: snapshot.reconstruction.points?.length || 0, pointColorCount: snapshot.reconstruction.pointColors?.length || 0 } : undefined,
          robotState: snapshot.robotState || {},
          entities: snapshot.entities || [],
        },
        null,
        2,
      )
    : "等待 Local Agent 上报遥测…";
  if (!options.metadataOnly && !options.skipScene) renderScene(snapshot);
}

function perceptionPresentation(snapshot) {
  const perception = snapshot?.robotState?.perception || {};
  const reconstruction = snapshot?.reconstruction;
  const declaredRGBD = perception.mode === "rgbd";
  const sourceRGBD = reconstruction?.sourceType === "rgbd_camera";
  const simulation = snapshot?.mode === "SIMULATION" || snapshot?.robotState?.simulation === true;
  const mode = declaredRGBD ? "rgbd" : sourceRGBD ? "rgbd_source" : perception.mode || "undeclared";
  const observedAt = Number(perception.observed_at_unix_ms || reconstruction?.observedAtUnixMs) || Date.parse(snapshot?.observedAt || "");
  const age = Date.now() - observedAt;
  const fresh = Number.isFinite(age) && age >= -250 && age <= sceneMaxAgeMs(snapshot);
  return {
    mode,
    label: declaredRGBD ? "RGB-D 环境感知" : sourceRGBD ? "RGB-D 三维观测" : snapshot ? simulation ? "仿真调试画面（非机器人感知）" : "观测来源未声明" : "等待观测来源",
    detail: declaredRGBD ? "物品与位置来自彩色图像和深度测量。可查看相机画面核对机器人看到的现场。"
      : sourceRGBD ? "系统收到 RGB-D 来源的标准三维观测；其他感知输入以机器人声明为准。"
        : snapshot ? "当前来源未声明为 RGB-D 环境感知，不能用于验证相机感知闭环。" : "连接后会显示机器人判断物品和位置所依据的感知来源。",
    fresh,
    sourceId: perception.source_id || reconstruction?.sourceId || "",
    camera: perception.camera || "",
    observationId: reconstruction?.observationId || perception.observation_id || "",
    observedAt: Number.isFinite(observedAt) ? new Date(observedAt).toISOString() : null,
    sourceFrameId: reconstruction?.sourceFrameId || "",
    frameId: reconstruction?.frameId || "",
    transformRevision: reconstruction?.transformRevision || "",
    pointCount: reconstruction?.points?.length || 0,
    entityCount: reconstruction?.entities?.length ?? snapshot?.entities?.length ?? 0,
  };
}

function renderPerception(snapshot) {
  const observation = perceptionPresentation(snapshot);
  $("#perception-label").textContent = observation.label;
  $("#perception-description").textContent = observation.detail;
  const freshness = $("#perception-freshness");
  freshness.textContent = snapshot ? observation.fresh ? "观测已更新" : "观测更新延迟" : "等待观测";
  freshness.dataset.tone = snapshot && observation.fresh ? "good" : "warning";
  const details = $("#perception-details");
  details.replaceChildren();
  const facts = {
    "观测源": observation.sourceId || "未声明", "相机": observation.camera || "未声明",
    "观测编号": observation.observationId || "未附带", "原坐标系": observation.sourceFrameId || "未声明",
    "输出坐标系": observation.frameId || "未声明", "标定版本": observation.transformRevision || "未声明",
    "三维点 / 实体": `${observation.pointCount} / ${observation.entityCount}`,
    "采集时间": observation.observedAt ? new Date(observation.observedAt).toLocaleString() : "未提供",
  };
  for (const [label, value] of Object.entries(facts)) details.append(makeTextElement("dt", "", label), makeTextElement("dd", "", value));
  const rgbd = ["rgbd", "rgbd_source"].includes(observation.mode);
  $("#scene-title").textContent = rgbd ? "机器人视野" : observation.label;
  $("#scene-source-label").textContent = [observation.label, observation.sourceId ? sceneCameraLabel(observation.sourceId) : ""].filter(Boolean).join(" · ");
  renderSceneControls(snapshot);
  globalThis.TangyingConsoleUI?.update({ observation });
}

function renderSceneControls(snapshot) {
  const observation = perceptionPresentation(snapshot);
  const rgbd = ["rgbd", "rgbd_source"].includes(observation.mode);
  $("#view-live").textContent = rgbd || !snapshot ? "彩色画面" : "调试画面";
  $("#view-live").disabled = snapshot?.colorFrameAvailable !== true || !observation.fresh;
  $("#view-depth").disabled = !rgbd || snapshot?.depthFrameAvailable !== true || !observation.fresh;
  $("#view-cloud").disabled = !pointCloudViewData(snapshot).available;
  $("#reset-view").disabled = !["cloud", "orbit"].includes(sceneViewMode);
  $("#cloud-labels-control").hidden = sceneViewMode !== "cloud";

}

function renderScene(snapshot) {
  if (!scenePageVisible()) return;
  const sourceId = snapshot?.reconstruction?.sourceId || snapshot?.robotState?.perception?.source_id;
  if (selectedCameraSource && sourceId !== selectedCameraSource) {
    // Polling, view changes and canvas interactions can redraw while another
    // camera is loading. Keep the old capture explicitly labeled; a redraw
    // must not turn its source into the currently selected camera's LIVE view.
    if (displayedFrameObservedAt != null && !sceneTimestampFresh(displayedFrameObservedAt, displayedFrameSnapshot)) {
      clearSceneFrame(`上一帧已过期，当前现场未知。等待${sceneCameraLabel(selectedCameraSource)}的新观测。`);
    } else {
      holdSceneFrame(`正在获取${sceneCameraLabel(selectedCameraSource)}的新观测`);
    }
    return;
  }
  const entities = snapshot?.entities || [];
  $("#entity-count").textContent = `${entities.length} entities`;
  const list = $("#entity-list");
  list.replaceChildren();
  entities.forEach((entity) => {
    const chip = document.createElement("span");
    chip.className = "entity-chip";
    chip.textContent = missionReferenceLabel(entity.attributes?.label || entity.entityId || entity.category || "未知物体");
    chip.setAttribute("title", `${entity.entityId || ""} · ${entity.category || ""}`);
    list.append(chip);
  });
  drawScene(entities, snapshot?.robotState || {}, snapshot);
}

function sceneMaxAgeMs(snapshot) {
  const reconstruction = snapshot?.reconstruction;
  const sourceId = reconstruction?.sourceId || snapshot?.robotState?.perception?.source_id;
  const sensor = snapshot?.robotProfile?.sensors?.find(item => item.sourceId === sourceId);
  return Number.isFinite(sensor?.maxAgeMs) && sensor.maxAgeMs > 0 ? Math.min(5000, sensor.maxAgeMs) : 3000;
}

function sceneTimestampFresh(timestamp, snapshot) {
  const age = Date.now() - timestamp;
  return Number.isFinite(timestamp) && age >= -250 && age <= sceneMaxAgeMs(snapshot);
}

function sceneCameraLabel(sourceId) {
  if (/(^|\/)base-rgbd$/.test(sourceId)) return "底盘前方 · RGB-D";
  if (/(^|\/)(head-rgbd|head)$/.test(sourceId)) return "顶部桌面 · RGB-D";
  return `RGB-D 相机 · ${sourceId}`;
}

function primaryCameraSource() {
  return primaryTelemetry?.reconstruction?.sourceId || primaryTelemetry?.robotState?.perception?.source_id || "";
}

function hasSelectedRGBDCamera() {
  const sourceId = selectedCameraSource || primaryCameraSource();
  return !!sourceId && primaryTelemetry?.robotProfile?.sensors?.some(sensor => sensor.sourceType === "rgbd_camera" && sensor.sourceId === sourceId);
}

function usingSecondaryCamera() {
  return !!selectedCameraSource && selectedCameraSource !== primaryCameraSource();
}

function syncSceneCameras(snapshot) {
  const profile = snapshot?.robotProfile;
  const sensors = profile?.robotId === snapshot?.robotId && profile?.adapterId === snapshot?.adapter
    ? (profile.sensors || []).filter(sensor => sensor.sourceType === "rgbd_camera" && typeof sensor.sourceId === "string" && sensor.sourceId) : [];
  const ids = [...new Set(sensors.map(sensor => sensor.sourceId))];
  const select = $("#scene-camera");
  const previousSource = selectedCameraSource;
  if (!ids.includes(selectedCameraSource)) selectedCameraSource = ids.includes(primaryCameraSource()) ? primaryCameraSource() : "";
  if (previousSource && previousSource !== selectedCameraSource) {
    sceneViewGeneration += 1;
    latestTelemetry = null;
    lastObservedAtByAdapter.delete(adapterInput.value);
    clearSceneFrame("相机配置已更新，等待所选相机的新观测。");
  }
  if (select.dataset.sources !== JSON.stringify(ids)) {
    select.dataset.sources = JSON.stringify(ids);
    select.replaceChildren();
    for (const id of ids) {
      const option = document.createElement("option"); option.value = id; option.textContent = sceneCameraLabel(id); select.append(option);
    }
  }
  select.value = selectedCameraSource;
  select.disabled = ids.length < 2;
  $("#scene-camera-control").hidden = !ids.length;
}

async function selectSceneCamera(sourceId) {
  if (!primaryTelemetry?.robotProfile?.sensors?.some(sensor => sensor.sourceType === "rgbd_camera" && sensor.sourceId === sourceId)) return false;
  selectedCameraSource = sourceId;
  $("#scene-camera").value = sourceId;
  invalidateTelemetryPolling();
  sceneViewGeneration += 1;
  lastObservedAtByAdapter.delete(adapterInput.value);
  cloudCameraSource = "";
  resetSceneFrameStats();
  holdSceneFrame(`正在切换到${sceneCameraLabel(sourceId)}`);
  await pollTelemetry();
  return true;
}

function pngDataURLBlob(value) {
  if (typeof value !== "string" || value.length > 2800000 || !/^data:image\/png;base64,[A-Za-z0-9+/]+={0,2}$/.test(value)) throw new Error("invalid PNG data");
  const data = atob(value.slice("data:image/png;base64,".length));
  const signature = [137, 80, 78, 71, 13, 10, 26, 10];
  if (data.length < signature.length || signature.some((byte, index) => data.charCodeAt(index) !== byte)) throw new Error("invalid PNG signature");
  return new Blob([Uint8Array.from(data, character => character.charCodeAt(0))], { type: "image/png" });
}

async function updateSelectedCamera(poll) {
  poll.requests += 1;
  const sourceId = selectedCameraSource || primaryCameraSource();
  const view = sceneViewMode;
  const generation = sceneViewGeneration;
  const current = () => isCurrentTelemetryPoll(poll) && sourceId === selectedCameraSource && generation === sceneViewGeneration && view === sceneViewMode;
  const unavailable = () => {
    if (!current()) return;
    renderTelemetry(null, { camera: true });
    clearSceneFrame(`${sceneCameraLabel(sourceId)}不可用、观测过期或不完整，当前现场未知。`);
  };
  try {
    const response = await fetch(`/v1/scene/camera?adapter=${encodeURIComponent(poll.adapter)}&sourceId=${encodeURIComponent(sourceId)}`, { cache: "no-store", signal: poll.controller.signal });
    if (!current()) return;
    if (!response.ok) { unavailable(); return; }
    const body = await response.json();
    if (!current()) return;
    const snapshot = body.snapshot;
    const r = snapshot?.reconstruction;
    const perception = snapshot?.robotState?.perception;
    const sensor = primaryTelemetry?.robotProfile?.sensors?.find(item => item.sourceId === sourceId);
    const captureSensor = snapshot?.robotProfile?.sensors?.find(item => item.sourceId === sourceId);
    if (!r || snapshot.adapter !== poll.adapter || snapshot.robotId !== primaryTelemetry?.robotId
      || r.robotId !== snapshot.robotId || r.sourceId !== sourceId || r.sourceType !== "rgbd_camera"
      || r.schemaVersion !== "scene.reconstruction.v1" || r.frameId !== "world" || r.units !== "m"
      || !r.observationId || !Number.isSafeInteger(r.sequence) || r.sequence < 0
      || !Number.isSafeInteger(r.observedAtUnixMs) || r.sourceFrameId !== sensor?.frameId || r.transformRevision !== sensor?.transformRevision
      || snapshot.robotProfile?.robotId !== snapshot.robotId || snapshot.robotProfile?.adapterId !== snapshot.adapter
      || captureSensor?.sourceType !== "rgbd_camera" || captureSensor.frameId !== sensor?.frameId
      || captureSensor.transformRevision !== sensor?.transformRevision || captureSensor.maxAgeMs !== sensor?.maxAgeMs
      || Date.parse(snapshot.observedAt || "") !== r.observedAtUnixMs
      || (perception?.source_id && perception.source_id !== sourceId)
      || (perception?.observed_at_unix_ms != null && perception.observed_at_unix_ms !== r.observedAtUnixMs)
      || !sceneTimestampFresh(r.observedAtUnixMs, snapshot)) { unavailable(); return; }
    // Validate both members before publishing any part of this atomic capture.
    // Keep CSP restricted to self/blob; data URLs never reach an image element.
    const rgb = pngDataURLBlob(body.rgbDataUrl);
    const depth = pngDataURLBlob(body.depthDataUrl);
    if (rgb.size + depth.size > 2 * 1024 * 1024) { unavailable(); return; }
    if (!current()) return;
    snapshot.colorFrameAvailable = true;
    snapshot.depthFrameAvailable = true;
    if (view === "cloud" || view === "orbit") { renderTelemetry(snapshot, { camera: true }); return; }
    holdSceneFrame(`正在读取${sceneCameraLabel(sourceId)}的本次采集…`, { background: true });
    const nextURL = URL.createObjectURL(view === "depth" ? depth : rgb);
    prepareSceneImage(nextURL, () => {
      if (!current() || pendingFrameObjectURL !== nextURL) { releasePendingFrame(nextURL); return; }
      if (!sceneTimestampFresh(r.observedAtUnixMs, snapshot)) { unavailable(); return; }
      renderTelemetry(snapshot, { camera: true });
      publishSceneImage(nextURL, snapshot, r.observedAtUnixMs, view);
      sceneFrame.alt = `${sceneCameraLabel(sourceId)} · ${view === "depth" ? "深度预览" : "彩色画面"}`;
      setSceneVisualState("LIVE", `${sceneCameraLabel(sourceId)} · ${view === "depth" ? "深度图 · 暖近冷远 · 黑色未知" : "彩色画面"}`);
    }, () => { if (current()) unavailable(); else releasePendingFrame(nextURL); });
  } catch (error) { if (error?.name !== "AbortError") unavailable(); }
  finally { poll.requests -= 1; }
}

function refreshSceneFreshness() {
  if (!scenePageVisible()) return;
  renderSceneFrameStats();
  if (pendingFrameObjectURL && displayedFrameObservedAt != null && sceneTimestampFresh(displayedFrameObservedAt, displayedFrameSnapshot)) return;
  if (sceneViewMode === "cloud" || sceneViewMode === "orbit") { renderScene(latestTelemetry); return; }
  const mediaAvailable = sceneViewMode === "depth" ? latestTelemetry?.depthFrameAvailable : latestTelemetry?.colorFrameAvailable;
  if (frameObjectURL && (!mediaAvailable || !sceneTimestampFresh(displayedFrameObservedAt, latestTelemetry))) {
    // Expiring the previous visible image must not cancel the fresh candidate
    // currently being decoded. Its own capture time is checked before publish.
    clearSceneFrame("画面已过期或暂不可用，当前现场未知。等待新的相机观测。", { preservePending: true });
  }
}

async function updateSceneFrame(snapshot, poll) {
  const requestedAdapter = poll.adapter;
  const requestedMode = sceneViewMode;
  const viewGeneration = sceneViewGeneration;
  const current = () => isCurrentTelemetryPoll(poll) && requestedMode === sceneViewMode && viewGeneration === sceneViewGeneration;
  if (!scenePageVisible() || snapshot.adapter !== requestedAdapter || !current() || !["live", "depth"].includes(requestedMode)) return;
  const depth = requestedMode === "depth";
  const observation = perceptionPresentation(snapshot);
  const rgbd = ["rgbd", "rgbd_source"].includes(observation.mode);
  const available = depth ? snapshot.depthFrameAvailable : snapshot.colorFrameAvailable;
  if (available !== true || !observation.fresh || (depth && !rgbd)) {
    renderTelemetry(snapshot);
    clearSceneFrame(`${depth ? "深度图" : "彩色画面"}不可用或观测已过期，当前现场未知。`);
    return;
  }
  holdSceneFrame(`正在读取${depth ? "深度图" : "彩色画面"}…`, { background: true });
  const endpoint = depth ? "/v1/scene/depth" : "/v1/scene/frame";
  poll.requests += 1;
  try {
    const response = await fetch(`${endpoint}?adapter=${encodeURIComponent(requestedAdapter)}&t=${Date.now()}`, {
      cache: "no-store", signal: poll.controller.signal,
    });
    if (!current()) return;
    if (!response.ok) {
      clearSceneFrame(`${depth ? "深度图" : "彩色画面"}暂不可用，当前现场未知。`);
      return;
    }
    const captureHeader = response.headers?.get("X-Observed-At");
    const capturedAt = captureHeader ? Date.parse(captureHeader) : rgbd ? NaN : Date.parse(snapshot.observedAt);
    const reportedAge = response.headers?.get("X-Frame-Age-Ms");
    if (!sceneTimestampFresh(capturedAt, snapshot) || (reportedAge != null && (!Number.isFinite(Number(reportedAge)) || Number(reportedAge) < -250 || Number(reportedAge) > sceneMaxAgeMs(snapshot)))) {
      clearSceneFrame("相机画面已过期或缺少采集时间，当前现场未知。");
      return;
    }
    const blob = await response.blob();
    if (!current()) return;
    const nextURL = URL.createObjectURL(blob);
    prepareSceneImage(nextURL, () => {
      if (!current() || pendingFrameObjectURL !== nextURL) { releasePendingFrame(nextURL); return; }
      if (!sceneTimestampFresh(capturedAt, snapshot)) {
        clearSceneFrame("相机画面加载时已过期，当前现场未知。");
        return;
      }
      renderTelemetry(snapshot);
      publishSceneImage(nextURL, snapshot, capturedAt, requestedMode);
      sceneFrame.alt = depth ? "机器人 RGB-D 相机深度可视化，暖色近、冷色远，黑色表示未知" : rgbd ? "机器人 RGB-D 相机彩色画面" : "仿真调试画面（非机器人感知）";
      setSceneVisualState("LIVE", depth ? "深度图 · 暖近冷远 · 黑色未知 · 0.02–5 米" : rgbd ? "机器人相机 · 彩色画面" : "仿真调试画面（非机器人感知）");
    }, () => {
      releasePendingFrame(nextURL);
      if (current()) clearSceneFrame("相机画面解码失败，当前现场未知。");
    });
  } catch (error) {
    if (error?.name === "AbortError" || !current()) return;
    clearSceneFrame("相机画面连接失败，当前现场未知。");
  } finally {
    poll.requests -= 1;
  }
}

function clearSceneFrame(message, options = {}) {
  if (!options.preservePending) discardPendingFrame();
  sceneFrame.onload = null;
  sceneFrame.onerror = null;
  sceneFrame.removeAttribute("src");
  sceneFrame.hidden = true;
  canvas.hidden = false;
  if (frameObjectURL) URL.revokeObjectURL(frameObjectURL);
  frameObjectURL = null;
  displayedFrameObservedAt = null;
  displayedFrameSnapshot = null;
  renderSceneFrameStats();
  drawUnknownScene();
  setSceneVisualState("UNAVAILABLE", message);
}

function discardPendingFrame() {
  if (pendingSceneImage) { pendingSceneImage.onload = null; pendingSceneImage.onerror = null; }
  pendingSceneImage = null;
  if (pendingFrameObjectURL) URL.revokeObjectURL(pendingFrameObjectURL);
  pendingFrameObjectURL = null;
}

function releasePendingFrame(url) {
  if (pendingFrameObjectURL === url) discardPendingFrame();
}

function setSceneVisualState(state, message) {
  if (sceneLiveState.dataset.connectionState !== state) {
    globalThis.TangyingConsoleUI?.update({ connection: state });
    sceneLiveState.dataset.connectionState = state;
  }
  const normalized = state.toLowerCase();
  if (sceneLiveState.textContent !== state) sceneLiveState.textContent = state;
  sceneLiveState.className = `scene-state ${normalized}`;
  sceneStage.className = `scene-stage ${normalized}`;
  const caption = $("#scene-frame-message");
  if (caption.textContent !== message) caption.textContent = message;
}

function shortRevision(revision) {
  if (!revision) return "—";
  return String(revision).slice(0, 10);
}

function findRobotEntity(entities) {
  return entities.find((entity) => entity.category === "robot")
    || entities.find((entity) => entity.entityId === "xlerobot");
}

function robotPoseFromState(robotState) {
  const raw = robotState?.base_pose || robotState?.basePose;
  if (!Array.isArray(raw) || raw.length < 3) return null;
  if (raw.some((value) => value == null || value === "" || typeof value === "boolean")) return null;
  const values = raw.map((item) => Number(item));
  if (values.some((value) => !Number.isFinite(value))) return null;
  const xyz = values.slice(0, 3);
  if (values.length >= 7) return [...xyz, values[3], values[4], values[5], values[6]];
  if (values.length >= 4) {
    const yaw = values[3];
    return [...xyz, Math.cos(yaw / 2), 0, 0, Math.sin(yaw / 2)];
  }
  return [...xyz, 1, 0, 0, 0];
}

function robotEntitiesFromSnapshot(entities, robotState, snapshot) {
  const source = Array.isArray(entities) ? entities : [];
  const robots = source.filter((entity) => entity.category === "robot" || entity.entityId === "xlerobot");
  const pose = robotPoseFromState(robotState || {});
  const preferredId = String(snapshot?.robotId || "").trim();
  const own = robots.find((robot) => robot.entityId === preferredId)
    || (robots.length === 1 && (!preferredId || robots[0].entityId === "xlerobot") ? robots[0] : null);
  const positioned = robots.flatMap((robot) => {
    if (robotPoseFromEntity(robot)) return [robot];
    // State belongs only to the reporting robot. Never lend its pose to a
    // different robot, and never mutate the authoritative telemetry snapshot.
    return robot === own && pose ? [{ ...robot, pose }] : [];
  });
  if (!own && pose && (preferredId || robots.length === 0)) {
    positioned.push({
      entityId: preferredId || "xlerobot",
      category: "robot",
      attributes: { color: "blue", source: "state" },
      pose,
    });
  }
  return positioned;
}

function robotPoseFromEntity(entity) {
  const pose = entity?.pose;
  return Array.isArray(pose) && pose.length >= 3 && pose.every(Number.isFinite) ? pose : null;
}

function robotIdentity(snapshot, robot) {
  return snapshot?.robotId || robot?.entityId || robot?.category || "robot";
}

function sceneIdentity(snapshot, robot) {
  return {
    robot: robotIdentity(snapshot, robot),
    adapter: snapshot?.adapter || adapterInput.value || "Robot Runtime",
  };
}

function updateSceneIdentity(snapshot, robot) {
  const identity = sceneIdentity(snapshot, robot);
  $("#scene-identity").textContent = `${identity.robot} · ${identity.adapter}`;
  $("#scene-title").textContent = `${identity.robot} 实时场景`;
  $("#scene-source-label").textContent = identity.adapter;
  sceneFrame.alt = `${identity.robot} 通过 ${identity.adapter} 提供的实时场景画面`;
  canvas.setAttribute("aria-label", `${identity.robot} 的 RGB-D 观测点云，未观测区域不显示`);
}

async function setSceneViewMode(mode, options = {}) {
  if (!["live", "depth", "cloud", "orbit"].includes(mode)) return;
  if (mode === "orbit" && document.body.dataset.audience !== "developer") return;
  const button = $(`#view-${mode}`);
  if (!options.force && mode !== "orbit" && button.disabled) return;
  sceneViewMode = mode;
  sceneViewGeneration += 1;
  invalidateTelemetryPolling();
  resetSceneFrameStats();
  holdSceneFrame("正在切换画面");
  for (const value of ["live", "depth", "cloud", "orbit"]) {
    $(`#view-${value}`).classList.toggle("active", mode === value);
    $(`#view-${value}`).setAttribute("aria-pressed", String(mode === value));
  }
  renderPerception(latestTelemetry);
  if (!scenePageVisible()) return;
  if (mode === "cloud" || mode === "orbit") {
    resetSceneCamera();
  } else if (hasSelectedRGBDCamera()) {
    return updateSelectedCamera(beginTelemetryPoll(adapterInput.value));
  } else if (primaryTelemetry || latestTelemetry) {
    const snapshot = primaryTelemetry || latestTelemetry;
    const poll = beginTelemetryPoll(snapshot.adapter);
    return updateSceneFrame(snapshot, poll);
  }
}

function fitSceneCamera(points) {
  if (!points.length) return;
  const lower = [0, 1, 2].map(axis => Math.min(...points.map(point => point[axis])));
  const upper = [0, 1, 2].map(axis => Math.max(...points.map(point => point[axis])));
  sceneCamera.target = lower.map((value, axis) => (value + upper[axis]) / 2);
  sceneCamera.distance = Math.max(.15, Math.hypot(...lower.map((value, axis) => upper[axis] - value)) * 1.5);
}

function fitCloudCamera(data) {
  const entityPoints = data.entities.map(entity => entity.pose.slice(0, 3));
  let focusPoints = data.points;
  if (entityPoints.length) {
    const lower = [0, 1, 2].map(axis => Math.min(...entityPoints.map(point => point[axis])));
    const upper = [0, 1, 2].map(axis => Math.max(...entityPoints.map(point => point[axis])));
    const padding = Math.max(.12, Math.max(...lower.map((value, axis) => upper[axis] - value)) * .2);
    // Include measured object surfaces around the observed centers, while far
    // background returns cannot shrink the work area. This creates no geometry.
    const nearby = data.points.filter(point => point.every((value, axis) => value >= lower[axis] - padding && value <= upper[axis] + padding));
    focusPoints = [...entityPoints, ...nearby];
  }
  fitSceneCamera(focusPoints);
  const radial = [Math.cos(sceneCamera.pitch) * Math.sin(sceneCamera.yaw), Math.cos(sceneCamera.pitch) * Math.cos(sceneCamera.yaw), Math.sin(sceneCamera.pitch)];
  const forward = radial.map(value => -value);
  const right = normalize3(cross3(forward, [0, 0, 1]));
  const up = cross3(right, forward);
  const focal = canvas.height * .9;
  const horizontal = canvas.width * .42 / focal;
  const vertical = canvas.height * .35 / focal;
  // Fit the actual perspective projection with room for the status overlay,
  // instead of multiplying a world-space diagonal regardless of panel aspect.
  sceneCamera.distance = Math.max(.45, ...focusPoints.map(point => {
    const relative = point.map((value, axis) => value - sceneCamera.target[axis]);
    const depth = dot3(relative, forward);
    return Math.max(Math.abs(dot3(relative, right)) / horizontal - depth,
      Math.abs(dot3(relative, up)) / vertical - depth, .08 - depth);
  }));
}

function resetSceneCamera() {
  Object.assign(sceneCamera, sceneViewMode === "cloud" ? { yaw: .25, pitch: .95 } : { yaw: .6, pitch: .7 });
  const data = pointCloudViewData(latestTelemetry);
  if (data.available) fitCloudCamera(data);
  else if (sceneViewMode === "orbit") fitSceneCamera((latestTelemetry?.entities || []).map(robotPoseFromEntity).filter(Boolean));
  if (["orbit", "cloud"].includes(sceneViewMode)) renderScene(latestTelemetry);
}

function pointCloudViewData(snapshot) {
  const r = snapshot?.reconstruction;
  const unavailable = reason => ({ available: false, reason, points: [], entities: [] });
  if (!r || r.sourceType !== "rgbd_camera") return unavailable("尚无 RGB-D 观测点云，未观测区域未知。");
  if (r.schemaVersion !== "scene.reconstruction.v1" || r.frameId !== "world" || r.units !== "m" || !r.observationId || !r.sourceId || !r.sourceFrameId || !r.transformRevision) return unavailable("点云格式或坐标信息不完整，当前现场未知。");
  if (!snapshot.robotId || r.robotId !== snapshot.robotId) return unavailable("点云机器人身份不一致，当前现场未知。");
  const profile = snapshot.robotProfile;
  const sensor = profile?.sensors?.find(item => item.sourceId === r.sourceId);
  if (profile && (profile.robotId !== r.robotId || profile.adapterId !== snapshot.adapter || !sensor || sensor.sourceType !== "rgbd_camera" || sensor.frameId !== r.sourceFrameId || sensor.transformRevision !== r.transformRevision)) return unavailable("点云来源或标定与设备配置不一致，当前现场未知。");
  const declaredSource = snapshot.robotState?.perception?.source_id;
  if (declaredSource && declaredSource !== r.sourceId) return unavailable("点云与相机来源不一致，当前现场未知。");
  if (!sceneTimestampFresh(r.observedAtUnixMs, snapshot)) return unavailable("观测点云已过期，当前现场未知。等待新的深度测量。");
  if (!Array.isArray(r.points) || !r.points.length) return unavailable("此次深度观测没有有效点，当前现场未知。");
  if (r.points.length > 4096 || r.points.some(point => !Array.isArray(point) || point.length !== 3 || !point.every(value => typeof value === "number" && Number.isFinite(value)))) return unavailable("点云数据无效，当前现场未知。");
  let pointColors = null;
  if (Object.hasOwn(r, "pointColors")) {
    if (!Array.isArray(r.pointColors) || (r.pointColors.length > 0 && r.pointColors.length !== r.points.length)
      || Array.from(r.pointColors).some(color => !Array.isArray(color) || color.length !== 3
        || [color[0], color[1], color[2]].some(value => !Number.isInteger(value) || value < 0 || value > 255))) {
      return unavailable("点云颜色格式无效或与测量点不匹配，已停止显示，等待有效观测。");
    }
    // Older SDK captures explicitly serialize an empty color list.
    pointColors = r.pointColors.length ? r.pointColors : null;
  }
  return { available: true, points: r.points, pointColors, entities: Array.isArray(r.entities) ? r.entities.filter(entity => robotPoseFromEntity(entity)) : [], sourceId: r.sourceId, frameId: r.frameId, observedAt: r.observedAtUnixMs };
}

function drawUnknownScene() {
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#142135";
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#b7c5d7";
  context.font = "16px system-ui, sans-serif";
  context.fillText("等待有效感知数据", Math.max(24, canvas.width / 2 - 68), canvas.height / 2);
}

function prioritizeCloudLabels(entities) {
  const intents = activeTask?.intent?.sequence?.length ? activeTask.intent.sequence : activeTask?.intent ? [activeTask.intent] : [];
  const rank = entity => {
    if (intents.some(intent => intent.object?.category === entity.category
      && Object.entries(intent.object.attributes || {}).every(([key, value]) => entity.attributes?.[key] === value))) return 0;
    return ["storage_bin", "delivery_tray", "environment", "robot"].includes(entity.category) ? 2 : 1;
  };
  return [...entities].sort((a, b) => rank(a) - rank(b));
}

function drawObservedCloud(snapshot) {
  sizeSceneCanvas();
  const data = pointCloudViewData(snapshot);
  sceneFrame.hidden = true;
  canvas.hidden = false;
  if (!data.available) { drawUnknownScene(); setSceneVisualState("UNAVAILABLE", data.reason); return; }
  if (cloudCameraSource !== data.sourceId) { cloudCameraSource = data.sourceId; fitCloudCamera(data); }
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#142135";
  context.fillRect(0, 0, canvas.width, canvas.height);
  const points = data.points.flatMap((point, index) => {
    const projected = projectScenePoint(point, canvas.width, canvas.height);
    return projected ? [{ projected, color: data.pointColors?.[index] }] : [];
  }).sort((a, b) => b.projected[2] - a.projected[2]);
  const displayScale = canvas.width / (canvas.clientWidth > 0 ? canvas.clientWidth : canvas.width);
  for (const { projected: [x, y, distance], color } of points) {
    context.fillStyle = color ? `rgb(${color.join(", ")})` : "#a6bbcc";
    const radius = Math.min(2.4 * displayScale, Math.max(1.2 * displayScale, canvas.height * .9 * .0025 / distance));
    context.beginPath(); context.arc(x, y, radius, 0, Math.PI * 2); context.fill();
  }
  context.save();
  const fontSize = 12 * displayScale;
  const paddingX = 4 * displayScale;
  const paddingY = 3 * displayScale;
  const inset = 8 * displayScale;
  const labelGap = 6 * displayScale;
  context.font = `${fontSize}px system-ui, sans-serif`;
  const labels = [];
  if ($("#cloud-labels").checked) for (const entity of prioritizeCloudLabels(data.entities)) {
    if (labels.length >= 3) break;
    const point = projectScenePoint(entity.pose, canvas.width, canvas.height);
    if (!point) continue;
    const text = missionReferenceLabel(entity.attributes?.label || entity.entityId);
    const textWidth = context.measureText?.(text)?.width ?? String(text).length * fontSize;
    const labelWidth = Math.min(240 * displayScale, textWidth);
    for (const offset of [-20, 8, 34]) {
      // Text, contrast padding, candidate offsets and collision clearance all
      // share CSS-pixel scale; a 1200px bitmap may display in a 478px panel.
      const rect = [point[0] + inset, point[1] + offset * displayScale,
        labelWidth + paddingX * 2, fontSize + paddingY * 2];
      if (rect[0] < inset || rect[1] < 52 * displayScale || rect[0] + rect[2] > canvas.width - inset || rect[1] + rect[3] > canvas.height - inset) continue;
      if (labels.some(other => rect[0] < other[0] + other[2] + labelGap && rect[0] + rect[2] + labelGap > other[0] && rect[1] < other[1] + other[3] + labelGap && rect[1] + rect[3] + labelGap > other[1])) continue;
      context.fillStyle = "rgba(10, 23, 39, .85)"; context.fillRect(...rect);
      context.fillStyle = "#fff"; context.fillText(text, rect[0] + paddingX, rect[1] + paddingY + fontSize * .85, labelWidth);
      labels.push(rect); break;
    }
  }
  context.restore();
  markSceneFrame(snapshot, snapshot.reconstruction.observedAtUnixMs);
  setSceneVisualState("LIVE", `${data.points.length} 个观测点 · ${data.pointColors ? "真实 RGB 颜色" : "未含 RGB · 单色显示"} · world / 米 · 空白区域未知`);
}

function drawScene(entities, robotState, snapshot) {
  if (sceneViewMode === "cloud") { drawObservedCloud(snapshot); return; }
  if (sceneViewMode === "orbit" && document.body.dataset.audience === "developer") { drawScene3D(entities, robotState, snapshot); return; }
  if (!frameObjectURL) drawUnknownScene();
}

function projectScenePoint(point, width, height) {
  const [tx, ty, tz] = sceneCamera.target;
  const px = point[0] - tx;
  const py = point[1] - ty;
  const pz = point[2] - tz;
  const cy = Math.cos(sceneCamera.yaw);
  const sy = Math.sin(sceneCamera.yaw);
  const cp = Math.cos(sceneCamera.pitch);
  const sp = Math.sin(sceneCamera.pitch);
  const camX = tx + sceneCamera.distance * cp * sy;
  const camY = ty + sceneCamera.distance * cp * cy;
  const camZ = tz + sceneCamera.distance * sp;

  // Camera basis (right, up, forward) approximating an orbit camera.
  const forward = normalize3([tx - camX, ty - camY, tz - camZ]);
  const right = normalize3(cross3(forward, [0, 0, 1]));
  const up = cross3(right, forward);
  const rel = [px + tx - camX, py + ty - camY, pz + tz - camZ];
  const x = dot3(rel, right);
  const y = dot3(rel, up);
  const z = dot3(rel, forward);
  if (z <= 0.05) return null;
  const focal = height * 0.9;
  return [width / 2 + (x * focal) / z, height / 2 - (y * focal) / z, z];
}

function normalize3(v) {
  const length = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0] / length, v[1] / length, v[2] / length];
}

function cross3(a, b) {
  return [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
}

function dot3(a, b) {
  return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}

function drawScene3D(entities, _robotState, snapshot) {
  sizeSceneCanvas();
  // Developer diagnostics only. Positions come from reported entities; never add
  // a default table, robot model, camera placement, or missing entity pose.
  context.clearRect(0, 0, canvas.width, canvas.height);
  context.fillStyle = "#142135";
  context.fillRect(0, 0, canvas.width, canvas.height);
  if (!sceneTimestampFresh(Date.parse(snapshot?.observedAt || ""), snapshot)) {
    drawUnknownScene(); setSceneVisualState("UNAVAILABLE", "开发语义数据已过期，当前现场未知。"); return;
  }
  for (const entity of entities) {
    const pose = robotPoseFromEntity(entity);
    if (!pose) continue;
    const point = projectScenePoint(pose, canvas.width, canvas.height);
    if (!point) continue;
    context.fillStyle = entityColor(entity);
    context.beginPath(); context.arc(point[0], point[1], 4, 0, Math.PI * 2); context.fill();
    context.font = "13px system-ui, sans-serif";
    context.fillText(missionReferenceLabel(entity.attributes?.label || entity.entityId), point[0] + 8, point[1]);
  }
  sceneFrame.hidden = true; canvas.hidden = false;
  markSceneFrame(snapshot, Date.parse(snapshot.observedAt), "orbit");
  setSceneVisualState("DEBUG", "开发语义诊断 · 上报实体位置，不代表机器人相机画面");
}

function drawScene2D(entities, robotState, snapshot) {
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const bounds = { minX: -0.72, maxX: 0.72, minY: -0.18, maxY: 0.86 };
  const toX = (x) => ((x - bounds.minX) / (bounds.maxX - bounds.minX)) * width;
  const toY = (y) => ((bounds.maxY - y) / (bounds.maxY - bounds.minY)) * height;
  const now = Date.now();
  const visibleTrailEntities = new Set(
    entities
      .filter((entity) => entity.category !== "robot" && entity.entityId !== "xlerobot" && entity.category !== "environment")
      .map((entity) => entity.entityId)
      .filter(Boolean),
  );
  for (const [entityId, trail] of trails) {
    if (!visibleTrailEntities.has(entityId)) {
      trails.delete(entityId);
      continue;
    }
    const freshTrail = trail.filter((point) => now - point.t <= 4000);
    if (freshTrail.length) trails.set(entityId, freshTrail);
    else trails.delete(entityId);
  }

  context.strokeStyle = "rgba(143,255,196,0.07)";
  context.lineWidth = 1;
  for (let gx = -0.6; gx <= 0.6; gx += 0.1) {
    context.beginPath();
    context.moveTo(toX(gx), 0);
    context.lineTo(toX(gx), height);
    context.stroke();
  }
  for (let gy = -0.4; gy <= 0.4; gy += 0.1) {
    context.beginPath();
    context.moveTo(0, toY(gy));
    context.lineTo(width, toY(gy));
    context.stroke();
  }

  const robots = robotEntitiesFromSnapshot(entities, robotState, snapshot);
  const robotSet = new Set(robots.map((robot) => robot.entityId).filter(Boolean));
  for (const entity of entities) {
    if (entity.category === "robot" || entity.category === "environment") continue;
    if (robotSet.has(entity.entityId)) continue;
    const color = entityColor(entity);
    const x = entity.pose?.[0] ?? 0;
    const y = entity.pose?.[1] ?? 0;
    const trail = trails.get(entity.entityId) || [];
    trail.push({ x, y, t: now });
    trails.set(entity.entityId, trail);
    context.strokeStyle = color;
    context.globalAlpha = 0.35;
    context.beginPath();
    trail.forEach((point, index) => {
      const px = toX(point.x);
      const py = toY(point.y);
      if (index === 0) context.moveTo(px, py);
      else context.lineTo(px, py);
    });
    context.stroke();
    context.globalAlpha = 1;
    context.fillStyle = color;
    if (entity.category === "work_surface") {
      const left = toX(x - 0.45);
      const top = toY(y + 0.32);
      const right = toX(x + 0.45);
      const bottom = toY(y - 0.32);
      context.globalAlpha = 0.15;
      context.fillRect(left, top, right - left, bottom - top);
      context.globalAlpha = 1;
      context.strokeRect(left, top, right - left, bottom - top);
    } else {
      context.beginPath();
      context.arc(toX(x), toY(y), 9, 0, Math.PI * 2);
      context.fill();
    }
    context.fillStyle = "#07120f";
    context.font = "bold 9px ui-monospace, monospace";
    context.textAlign = "center";
    context.fillText(entity.entityId.slice(0, 5), toX(x), toY(y) + 3);
  }

  if (robots.length) {
    for (const robot of robots) {
      drawRobotFootprint(robot, toX, toY, robotState, snapshot);
    }
  } else {
    drawEmptyRobotState(snapshot);
  }
}

function drawRobotFootprint(entity, toX, toY, robotState, snapshot) {
  const pose = robotPoseFromEntity(entity, robotState) || [];
  const x = Number(pose[0] || 0);
  const y = Number(pose[1] || 0);
  const yaw = quaternionYaw(pose.slice(3, 7));
  const centerX = toX(x);
  const centerY = toY(y);
  context.save();
  context.translate(centerX, centerY);
  context.rotate(-yaw);
  context.fillStyle = "rgba(143,255,196,0.2)";
  context.strokeStyle = "#8fffc4";
  context.lineWidth = 2;
  context.fillRect(-28, -18, 56, 36);
  context.strokeRect(-28, -18, 56, 36);
  context.beginPath();
  context.moveTo(28, 0);
  context.lineTo(15, -8);
  context.lineTo(15, 8);
  context.closePath();
  context.fillStyle = "#8fffc4";
  context.fill();
  context.restore();
  context.fillStyle = "#dfffee";
  context.font = "bold 10px ui-monospace, monospace";
  context.textAlign = "center";
  const isReportingRobot = !snapshot?.robotId || entity.entityId === snapshot.robotId || entity.entityId === "xlerobot";
  const held = isReportingRobot && robotState.held ? ` · ${robotState.held}` : "";
  context.fillText(`${entity.entityId || robotIdentity(snapshot, entity)}${held}`, centerX, centerY - 28);
}

function quaternionYaw(quaternion) {
  if (quaternion.length < 4) return 0;
  const [w, x, y, z] = quaternion.map(Number);
  return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
}

function drawEmptyRobotState(snapshot) {
  context.fillStyle = "rgba(223,255,238,0.78)";
  context.font = "600 16px ui-sans-serif, sans-serif";
  context.textAlign = "center";
  const expected = snapshot?.robotId ? `${snapshot.robotId} 的 ` : "";
  context.fillText(`尚未观测到 ${expected}robot 实体，请检查 Robot Runtime`, canvas.width / 2, canvas.height - 28);
}

function entityColor(entity) {
  const color = entity.attributes?.color;
  const colors = { red: "#ff6b6b", blue: "#6bb5ff", green: "#6bffa0", gray: "#9aa9a2", orange: "#ffb86b" };
  return colors[color] || colors[entity.category] || "#8fffc4";
}

async function pollMetrics() {
  if (!pageVisible("diagnostics")) return;
  try {
    const response = await fetch("/v1/orchestration/metrics");
    if (!response.ok) return;
    renderMetrics(await response.json());
  } catch (_) {
    // metrics are best-effort
  }
}

function renderMetrics(metrics) {
  const fields = [
    ["总任务", metrics.totalTasks ?? 0],
    ["LLM 计划率", percent(metrics.llmPlanRate)],
    ["候选通过率", percent(metrics.llmCandidateRate)],
    ["端到端成功率", percent(metrics.endToEndSuccessRate)],
    ["综合编排分", (metrics.orchestrationScore ?? 0).toFixed(1)],
    ["序列任务", metrics.sequenceTasks ?? 0],
    ["LLM 接管", metrics.llmGeneratedTasks ?? 0],
    ["回退任务", metrics.llmFallbackTasks ?? 0],
    ["成功 / 失败", `${metrics.succeededTasks ?? 0} / ${metrics.failedTasks ?? 0}`],
    ["安全停止", metrics.safetyStoppedTasks ?? 0],
  ];
  const grid = $("#metrics-grid");
  grid.replaceChildren();
  for (const [label, value] of fields) {
    const article = document.createElement("article");
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = String(value);
    article.append(span, strong);
    grid.append(article);
  }
}

function percent(value) {
  const number = Number(value || 0);
  return `${(number * 100).toFixed(1)}%`;
}

// ---------------------------------------------------------------------------
// Fleet control-plane console mode. When /healthz reports mode=fleet, the
// same static app becomes the cloud console: operator login, device list,
// multi-robot global map fusion, distributed tasks with intent-level nodes,
// and per-robot telemetry. All fleet API calls carry the operator token.
// ---------------------------------------------------------------------------

let fleetMode = false;
let fleetDemoAuth = false;
let fleetToken = "";
let fleetOperator = "";
try {
  fleetToken = sessionStorage.getItem("fleetToken") || "";
  fleetOperator = sessionStorage.getItem("fleetOperator") || "";
} catch (_) {
  // Non-browser context (tests): stay logged out.
}
let selectedFleetTask = null;
let fleetTaskExperienceState = { taskId: "", revision: 0, aggregateVersion: 0, cursor: 0 };
let fleetPendingTaskRevision = null;
let fleetTaskExperienceResyncing = false;
let fleetTaskSelectionGeneration = 0;
let fleetTaskUpdateAllowed = false;
let fleetWorldClient = null;
let fleetWorldRenderer = null;
let fleetWorldWebGLRenderer = null;
let fleetWorldWebGLInteraction = null;
let fleetWorldCamera = null;
let fleetWorldLatestSnapshot = null;
let fleetVisualGeneration = 0;
let fleetSessionGeneration = 0;
let fleetWorldSocket = null;
let fleetWorldReconnectTimer = null;
let fleetWorldMessageQueue = Promise.resolve();
let fleetWorldDrag = null;
let fleetWorldSelectedEntityId = "";
let fleetWorldFollowId = "";
let fleetWorldLastUpdateAt = 0;
let fleetWorldWatchdog = null;
let fleetAcceptanceNonce = "";
let fleetExecutionAdapter = "auto";
const fleetWorldStaleAfterMs = 3000;
const fleetWorldVisibility = { models: true, bounds: false, labels: false, path: true };
const fleetSceneViews = ["three", "simple", "cameras", "map"];
let fleetSceneView = "three";
let fleetVisualReady = false;
let fleetLatestMap = null;
try {
  const saved = globalThis.localStorage?.getItem("tangyingSceneView");
  if (fleetSceneViews.includes(saved)) fleetSceneView = saved;
} catch (_) { /* The default works when browser storage is disabled. */ }

function applyFleetSceneView() {
  const world = fleetSceneView === "three" || fleetSceneView === "simple";
  $("#fleet-scene-world-panel").hidden = !world;
  $("#fleet-scene-camera-panel").hidden = fleetSceneView !== "cameras";
  $("#fleet-scene-map-panel").hidden = fleetSceneView !== "map";
  $("#fleet-godview-webgl").hidden = !scenePageVisible() || fleetSceneView !== "three" || !fleetVisualReady;
  $("#fleet-godview-canvas").hidden = !world || (fleetSceneView === "three" && fleetVisualReady);
  $("#fleet-world-label-layer").hidden = $("#fleet-godview-webgl").hidden;
  for (const button of document.querySelectorAll?.("[data-scene-view]") || []) {
    const selected = button.dataset.sceneView === fleetSceneView;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  }
  globalThis.TangyingConsoleUI?.update({ sceneView: fleetSceneView });
}

function setFleetSceneView(view) {
  if (!fleetSceneViews.includes(view)) return;
  fleetSceneView = view;
  try { globalThis.localStorage?.setItem("tangyingSceneView", view); } catch (_) { /* Optional preference. */ }
  applyFleetSceneView();
  const snapshot = fleetWorldLatestSnapshot || fleetWorldClient?.snapshot;
  if (snapshot) renderFleetWorld(snapshot);
  if (view === "map" && fleetLatestMap) drawFleetMap(fleetLatestMap, $("#fleet-workspace-map-canvas"));
}

function isCurrentFleetSession(session) {
  if (!session) return false;
  if (session.generation !== fleetSessionGeneration || session.token !== fleetToken) return false;
  return !session.client || session.client === fleetWorldClient;
}

function isCurrentFleetSocket(session, socket) {
  return isCurrentFleetSession(session) && fleetWorldSocket === socket;
}

function staleFleetSessionError() {
  return Object.assign(new Error("fleet session ended"), { code: "FLEET_SESSION_STALE" });
}

function loadFleetWorldCamera() {
  let saved = null;
  try {
    saved = JSON.parse(localStorage.getItem("tangyingFleetWorldCameraV2") || "null");
  } catch (_) {
    saved = null;
  }
  return new globalThis.TangyingWorld.WorldCamera(saved || {
    yaw: -2.36, pitch: 1.03, distance: 6.8, target: [2.75, -1.5, 0.4],
  });
}

function saveFleetWorldCamera() {
  if (!fleetWorldCamera) return;
  try {
    localStorage.setItem("tangyingFleetWorldCameraV2", JSON.stringify(fleetWorldCamera.toJSON()));
  } catch (_) {
    // Camera persistence is optional; world rendering remains authoritative.
  }
}

function stableVisualError(error) {
  const explicit = String(error?.code || "").trim();
  if (/^[A-Z][A-Z0-9_]+$/.test(explicit)) return explicit;
  const prefix = String(error?.message || "").match(/^([A-Z][A-Z0-9_]+)(?::|$)/)?.[1];
  return prefix || "VISUAL_RENDERER_FAILED";
}

function fleetVisualIdentityError(snapshot, renderer) {
  const expectedHash = renderer?.bundle?.modelHash;
  const expectedScene = renderer?.bundle?.manifest?.sceneId;
  if (!expectedHash || !expectedScene) return "";
  const identities = Object.values(snapshot?.entities || {})
    .map((entity) => ({
      sceneId: entity?.attributes?.scene_id ?? entity?.attributes?.sceneId,
      modelHash: entity?.attributes?.model_hash ?? entity?.attributes?.modelHash,
    }))
    .filter((identity) => identity.sceneId !== undefined || identity.modelHash !== undefined);
  if (!identities.length) return "VISUAL_MODEL_IDENTITY_MISSING";
  if (identities.some((identity) => identity.sceneId !== expectedScene || identity.modelHash !== expectedHash)) {
    return "VISUAL_MODEL_MISMATCH";
  }
  return "";
}

function setFleetVisualState(state, detail = "") {
  globalThis.TangyingConsoleUI?.update({ visualState: state, visualCode: detail.split(" ")[0] || "", sceneView: fleetSceneView });
  const normalized = ["LOADING", "LIVE", "DEGRADED"].includes(state) ? state : "DEGRADED";
  const label = $("#fleet-visual-state");
  label.textContent = `VISUAL ${normalized}`;
  label.className = `scene-state ${normalized.toLowerCase()}`;
  $("#fleet-visual-detail").textContent = detail || (normalized === "LIVE"
    ? "本地场景资产与权威模型匹配"
    : normalized === "LOADING" ? "正在校验本地场景资产" : "语义 Canvas 仍保持实时交互");
  $("#fleet-visual-retry").hidden = normalized !== "DEGRADED";
}

function showFleetWorldWebGL(enabled) {
  fleetVisualReady = enabled;
  applyFleetSceneView();
}

function disposeFleetWebGL(renderer, interaction) {
  try {
    interaction?.dispose?.();
  } catch (_) {
    // A broken visual teardown must not escape into the world client.
  }
  try {
    renderer?.dispose?.();
  } catch (_) {
    // The Canvas renderer remains available even if GPU disposal fails.
  }
}

function clearFleetWebGL() {
  const renderer = fleetWorldWebGLRenderer;
  const interaction = fleetWorldWebGLInteraction;
  fleetWorldWebGLRenderer = null;
  fleetWorldWebGLInteraction = null;
  disposeFleetWebGL(renderer, interaction);
  $("#fleet-world-label-layer").replaceChildren();
}

function resetFleetVisualState(detail = "等待登录后加载视觉层") {
  fleetVisualGeneration += 1;
  clearFleetWebGL();
  fleetWorldLatestSnapshot = null;
  fleetWorldSelectedEntityId = "";
  fleetWorldFollowId = "";
  showFleetWorldWebGL(false);
  setFleetVisualState("LOADING", detail);
}

function degradeFleetVisual(error) {
  const code = typeof error === "string" ? error : stableVisualError(error);
  fleetVisualGeneration += 1;
  showFleetWorldWebGL(false);
  setFleetVisualState("DEGRADED", `${code} · 已切换到语义 Canvas`);
  clearFleetWebGL();
  try {
    if (fleetWorldLatestSnapshot && fleetWorldRenderer) fleetWorldRenderer.render(fleetWorldLatestSnapshot);
  } catch (_) {
    // The first Canvas projection already ran before the enhanced branch.
  }
  return fleetWorldRenderer;
}

function applyFleetWorldVisibility() {
  fleetWorldRenderer?.setVisibility?.(fleetWorldVisibility);
  fleetWorldWebGLRenderer?.setVisibility?.(fleetWorldVisibility);
}

function bindFleetWebGLContextFallback(canvas) {
  if (canvas.dataset.visualFallbackBound === "true") return;
  canvas.dataset.visualFallbackBound = "true";
  canvas.addEventListener("webglcontextlost", (event) => {
    if (!fleetWorldWebGLRenderer) return;
    event.preventDefault?.();
    degradeFleetVisual("WEBGL_CONTEXT_LOST");
  });
  const cancelFollow = () => {
    if (!fleetWorldFollowId) return;
    fleetWorldFollowId = "";
    fleetWorldWebGLInteraction?.setFollow?.("");
    updateFleetWorldToolbar();
  };
  for (const eventName of ["pointerdown", "wheel", "dblclick", "keydown"]) {
    canvas.addEventListener(eventName, cancelFollow);
  }
}

async function createFleetWorldRenderer(snapshot) {
  const generation = ++fleetVisualGeneration;
  if (!fleetWorldLatestSnapshot
    || Number(snapshot?.revision) >= Number(fleetWorldLatestSnapshot?.revision)) {
    fleetWorldLatestSnapshot = snapshot;
  }
  clearFleetWebGL();
  showFleetWorldWebGL(false);
  setFleetVisualState("LOADING");
  const webglCanvas = $("#fleet-godview-webgl");
  bindFleetWebGLContextFallback(webglCanvas);
  try {
    if (!globalThis.TangyingWebGL?.AssetRegistry || !globalThis.TangyingWebGL?.WebGLSceneRenderer) {
      throw Object.assign(new Error("local WebGL bundle is unavailable"), { code: "WEBGL_BUNDLE_UNAVAILABLE" });
    }
    const registry = new globalThis.TangyingWebGL.AssetRegistry({
      manifestURL: "/assets/scenes/robocasa-handoff-v1/manifest.json",
    });
    let bundle = await registry.load(snapshot);
    if (generation !== fleetVisualGeneration) return fleetWorldRenderer;
    const latest = fleetWorldLatestSnapshot || fleetWorldClient?.snapshot || snapshot;
    if (latest !== snapshot) {
      bundle = await registry.load(latest);
      if (generation !== fleetVisualGeneration) return fleetWorldRenderer;
    }

    const renderer = await Promise.resolve(globalThis.TangyingWebGL.WebGLSceneRenderer.create(webglCanvas, {
      bundle,
      document,
      labelContainer: $("#fleet-world-label-layer"),
      worldCamera: fleetWorldCamera || undefined,
    }));
    if (generation !== fleetVisualGeneration) {
      disposeFleetWebGL(renderer, null);
      return fleetWorldRenderer;
    }
    fleetWorldWebGLRenderer = renderer;
    renderer.setVisibility?.(fleetWorldVisibility);
    if (fleetWorldCamera) renderer.setWorldCamera?.(fleetWorldCamera);
    if (globalThis.TangyingWebGL.WebGLSceneRenderer.bindInteraction && fleetWorldCamera) {
      fleetWorldWebGLInteraction = globalThis.TangyingWebGL.WebGLSceneRenderer.bindInteraction(webglCanvas, renderer, {
        camera: fleetWorldCamera,
        onCameraChange: saveFleetWorldCamera,
        onFollowChange: (robotId) => {
          fleetWorldFollowId = robotId;
          updateFleetWorldToolbar();
        },
      });
    }
    const originalSelect = renderer.select?.bind(renderer);
    if (originalSelect) {
      renderer.select = (entity) => {
        const selected = originalSelect(entity);
        fleetWorldSelectedEntityId = selected || "";
        $("#fleet-world-selection").textContent = describeFleetWorldEntity(entity || null);
        updateFleetWorldToolbar();
        return selected;
      };
    }
    const renderSnapshot = fleetWorldLatestSnapshot || fleetWorldClient?.snapshot || latest;
    const identityError = fleetVisualIdentityError(renderSnapshot, renderer);
    if (identityError) throw Object.assign(new Error(identityError), { code: identityError });
    const accepted = renderer.render?.(renderSnapshot);
    if (accepted === false || renderer.status?.state === "DEGRADED") {
      throw Object.assign(new Error(renderer.status?.code || "snapshot rejected"), {
        code: renderer.status?.code || "WEBGL_SNAPSHOT_INVALID",
      });
    }
    showFleetWorldWebGL(true);
    setFleetVisualState("LIVE");
    return renderer;
  } catch (error) {
    if (generation !== fleetVisualGeneration) return fleetWorldRenderer;
    return degradeFleetVisual(error);
  }
}

async function retryFleetWorldVisual() {
  const latest = fleetWorldLatestSnapshot || fleetWorldClient?.snapshot;
  if (!latest) return degradeFleetVisual("VISUAL_SNAPSHOT_MISSING");
  return createFleetWorldRenderer(latest);
}

function describeFleetWorldEntity(entity) {
  if (!entity) return "单击对象查看；双击聚焦";
  const label = entity.attributes?.label || entity.entityId || entity.robotId || "未知对象";
  const category = entity.category || (entity.robotId ? "robot" : "entity");
  const pose = entity.pose || entity.robot?.pose;
  const position = Array.isArray(pose)
    ? pose.slice(0, 3).map((value) => Number(value || 0).toFixed(2)).join(", ")
    : "—";
  const freshness = entity.freshness || entity.robot?.freshness;
  return `${label} · ${category} · [${position}]${freshness ? ` · ${freshness}` : ""}`;
}

async function requestFleetWorldSnapshotForSession(session, socket = null, requireSocket = false) {
  const isCurrentRequest = () => isCurrentFleetSession(session)
    && (!requireSocket || fleetWorldSocket === socket);
  if (!isCurrentRequest()) throw staleFleetSessionError();
  const response = await fleetAPI("/v1/world", { cache: "no-store" });
  if (!isCurrentRequest()) throw staleFleetSessionError();
  if (!response.ok) throw new Error(`world snapshot HTTP ${response.status}`);
  const snapshot = await response.json();
  if (!isCurrentRequest()) throw staleFleetSessionError();
  return snapshot;
}

function renderFleetWorld(snapshot) {
  fleetWorldLatestSnapshot = snapshot;
  const snapshotNonce = String(snapshot.acceptanceNonce || "");
  if (/^[a-f0-9]{64}$/.test(snapshotNonce)) fleetAcceptanceNonce = snapshotNonce;
  const acceptanceNonce = fleetAcceptanceNonce;
  $("#fleet-acceptance-world-snapshot").textContent = JSON.stringify({
    ...snapshot,
    ...(acceptanceNonce ? { acceptanceNonce } : {}),
  });
  const acceptanceMarker = $("#fleet-acceptance-marker");
  if (/^[a-f0-9]{64}$/.test(acceptanceNonce)) {
    const acceptanceTask = new URLSearchParams(globalThis.location?.search || "")
      .get("acceptance_task") || "unbound";
    const revision = String(snapshot.revision ?? 0);
    document.documentElement.dataset.acceptanceNonce = acceptanceNonce;
    acceptanceMarker.dataset.nonce = acceptanceNonce;
    acceptanceMarker.dataset.taskId = acceptanceTask;
    acceptanceMarker.dataset.worldRevision = revision;
    acceptanceMarker.textContent = `ACCEPT ${acceptanceNonce} · TASK ${acceptanceTask} · REV ${revision}`;
    acceptanceMarker.hidden = false;
    startFleetAcceptanceFrameSampling();
    $("#fleet-acceptance-fallback").hidden = false;
  } else {
    delete document.documentElement.dataset.acceptanceNonce;
    acceptanceMarker.hidden = true;
    $("#fleet-acceptance-fallback").hidden = true;
  }
  if (fleetWorldRenderer) {
    if ($("#fleet-godview-webgl").hidden && fleetWorldFollowId
      && snapshot.robots?.[fleetWorldFollowId]?.freshness === "FRESH"
      && snapshot.robots[fleetWorldFollowId]?.pose) {
      const pose = snapshot.robots[fleetWorldFollowId].pose;
      fleetWorldCamera.target = [Number(pose[0]), Number(pose[1]), Number(pose[2] || 0) + 0.315];
    }
    fleetWorldRenderer.selectedEntityId = fleetWorldSelectedEntityId;
    fleetWorldRenderer.render(snapshot);
  }
  if (fleetWorldWebGLRenderer) {
    try {
      const identityError = fleetVisualIdentityError(snapshot, fleetWorldWebGLRenderer);
      if (identityError) throw Object.assign(new Error(identityError), { code: identityError });
      fleetWorldWebGLRenderer.select?.(snapshot.entities?.[fleetWorldSelectedEntityId]
        || snapshot.robots?.[fleetWorldSelectedEntityId]
        || null);
      const accepted = fleetWorldWebGLRenderer.render?.(snapshot);
      if (accepted === false || fleetWorldWebGLRenderer.status?.state === "DEGRADED") {
        const code = fleetWorldWebGLRenderer.status?.code || "WEBGL_SNAPSHOT_INVALID";
        throw Object.assign(new Error(code), { code });
      }
    } catch (error) {
      degradeFleetVisual(error);
    }
  }
  const stoppedRobots = Object.values(snapshot.robots || {}).filter((robot) => robot.emergencyStopped);
  globalThis.TangyingConsoleUI?.update({
    worldRevision: snapshot.revision ?? 0, eventCursor: snapshot.eventCursor || "",
    emergencyStopped: stoppedRobots.length > 0,
    anomalyCount: Object.values(snapshot.sources || {}).reduce((count, source) => count + (source.anomalies?.length || 0), 0),
  });
  $("#fleet-world-revision").textContent = `REV ${snapshot.revision ?? 0}`;
  $("#fleet-world-cursor").textContent = snapshot.eventCursor || "—";
  const modelEntity = Object.values(snapshot.entities || {}).find(
    (entity) => entity.attributes?.model_hash,
  );
  if (modelEntity) {
    const attributes = modelEntity.attributes;
    const scene = attributes.scene_id || snapshot.worldId || "未知场景";
    const adapter = attributes.adapter || fleetExecutionAdapter;
    const modelHash = String(attributes.model_hash).slice(0, 12);
    $("#fleet-world-model").textContent = `${scene} · ${adapter} · ${modelHash}`;
  } else {
    $("#fleet-world-model").textContent = `${snapshot.worldId || "等待模型身份"} · ${fleetExecutionAdapter}`;
  }
  const resource = snapshot.resources?.["block:red-block"];
  $("#fleet-block-owner").textContent = resource
    ? `${resource.owner} · token ${resource.fencingToken}`
    : "尚无方块租约";
  const sources = Object.values(snapshot.sources || {});
  const fresh = sources.filter((source) => source.freshness === "FRESH").length;
  $("#fleet-world-sources").textContent = `${fresh} fresh / ${sources.length} total`;
  const fixtures = Object.values(snapshot.entities || {}).filter(
    (entity) => entity.attributes?.model_source === "mujoco" && entity.attributes?.bounds,
  );
  $("#fleet-world-fixtures").textContent = `${fixtures.length} 个实体 · MuJoCo 物理边界`;
  const selectedEntity = snapshot.entities?.[fleetWorldSelectedEntityId];
  const selectedRobot = snapshot.robots?.[fleetWorldSelectedEntityId];
  $("#fleet-world-selection").textContent = describeFleetWorldEntity(
    selectedEntity || (selectedRobot ? {
      entityId: selectedRobot.robotId || fleetWorldSelectedEntityId,
      category: "robot",
      pose: selectedRobot.pose,
      freshness: selectedRobot.freshness,
    } : null),
  );
  const degraded = snapshot.health?.degradedSources || [];
  const conflicts = snapshot.health?.conflicts || [];
  $("#fleet-world-health").textContent = degraded.length || conflicts.length
    ? `degraded ${degraded.length} · conflicts ${conflicts.length}`
    : "一致 · 无冲突";
}

function setFleetWorldState(state) {
  globalThis.TangyingConsoleUI?.update({ connection: state });
  const label = $("#fleet-world-connection");
  label.textContent = `WORLD ${state}`;
  label.className = `scene-state ${String(state).toLowerCase()}`;
}

function noteFleetWorldUpdate(now = Date.now()) {
  fleetWorldLastUpdateAt = now;
  setFleetWorldState("LIVE");
}

function checkFleetWorldFreshness(now = Date.now()) {
  if (!fleetWorldLastUpdateAt || now - fleetWorldLastUpdateAt > fleetWorldStaleAfterMs) {
    setFleetWorldState("STALE");
  }
}

function startFleetWorldWatchdog(session) {
  if (fleetWorldWatchdog) return;
  fleetWorldWatchdog = setInterval(() => {
    if (isCurrentFleetSession(session)) checkFleetWorldFreshness();
  }, 1000);
}

async function connectFleetWorldEvents(session = {
  generation: fleetSessionGeneration,
  token: fleetToken,
  client: fleetWorldClient,
}) {
  if (!fleetToken || !isCurrentFleetSession(session)) return;
  if (fleetWorldSocket) {
    const previousSocket = fleetWorldSocket;
    fleetWorldSocket = null;
    fleetWorldMessageQueue = Promise.resolve();
    previousSocket.close();
  }
  let socket = null;
  try {
    if (!isCurrentFleetSession(session)) return;
    const ticketResponse = await fleetAPI("/v1/auth/ws-ticket", { method: "POST" });
    if (!isCurrentFleetSession(session)) return;
    if (!ticketResponse.ok) throw new Error(`ticket HTTP ${ticketResponse.status}`);
    const ticket = await ticketResponse.json();
    if (!isCurrentFleetSession(session)) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const revision = session.client.revision;
    const url = `${protocol}://${location.host}/v1/world/events/ws?after_revision=${revision}&ticket=${encodeURIComponent(ticket.ticket)}`;
    socket = new WebSocket(url);
    fleetWorldSocket = socket;
    let messageQueue = Promise.resolve();
    fleetWorldMessageQueue = messageQueue;
    socket.addEventListener("open", () => {
      if (!isCurrentFleetSocket(session, socket)) return;
      checkFleetWorldFreshness();
    });
    socket.addEventListener("message", (event) => {
      if (!isCurrentFleetSocket(session, socket)) return;
      messageQueue = messageQueue
        .then(async () => {
          if (!isCurrentFleetSocket(session, socket)) return;
          const before = session.client.revision;
          await session.client.receive(JSON.parse(event.data));
          if (!isCurrentFleetSocket(session, socket)) return;
          if (session.client.revision > before) noteFleetWorldUpdate();
        })
        .catch(async () => {
          if (!isCurrentFleetSocket(session, socket)) return;
          await session.client.resync();
          if (!isCurrentFleetSocket(session, socket)) return;
          noteFleetWorldUpdate();
        });
      fleetWorldMessageQueue = messageQueue;
    });
    socket.addEventListener("error", () => {
      if (!isCurrentFleetSocket(session, socket)) return;
      setFleetWorldState("STALE");
    });
    socket.addEventListener("close", () => {
      if (!isCurrentFleetSocket(session, socket)) return;
      fleetWorldSocket = null;
      fleetWorldMessageQueue = Promise.resolve();
      setFleetWorldState("STALE");
      clearTimeout(fleetWorldReconnectTimer);
      fleetWorldReconnectTimer = setTimeout(() => {
        if (isCurrentFleetSession(session)) void connectFleetWorldEvents(session);
      }, 1000);
    });
  } catch (_) {
    if (!isCurrentFleetSession(session)) return;
    if (socket && fleetWorldSocket !== socket) return;
    if (socket) {
      fleetWorldSocket = null;
      fleetWorldMessageQueue = Promise.resolve();
    }
    setFleetWorldState("STALE");
    clearTimeout(fleetWorldReconnectTimer);
    fleetWorldReconnectTimer = setTimeout(() => {
      if (isCurrentFleetSession(session)) void connectFleetWorldEvents(session);
    }, 1500);
  }
}

function renderCurrentFleetWorld() {
  if (fleetWorldClient?.snapshot) renderFleetWorld(fleetWorldClient.snapshot);
}

function isFleetWorldClick(drag, cancelled = false) {
  return !cancelled && drag?.button === 0 && drag.moved < 5;
}

function bindFleetWorldControls(canvas) {
  if (canvas.dataset.worldControlsBound === "true") return;
  canvas.dataset.worldControlsBound = "true";
  const pointerPosition = (event) => {
    const rect = canvas.getBoundingClientRect();
    return [
      ((event.clientX - rect.left) / Math.max(1, rect.width)) * canvas.width,
      ((event.clientY - rect.top) / Math.max(1, rect.height)) * canvas.height,
    ];
  };
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 && event.button !== 2) return;
    fleetWorldFollowId = "";
    updateFleetWorldToolbar();
    fleetWorldDrag = { button: event.button, x: event.clientX, y: event.clientY, moved: 0 };
    canvas.classList.add("dragging");
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!fleetWorldDrag || !fleetWorldRenderer) return;
    const dx = event.clientX - fleetWorldDrag.x;
    const dy = event.clientY - fleetWorldDrag.y;
    fleetWorldDrag.moved += Math.hypot(dx, dy);
    fleetWorldDrag.x = event.clientX;
    fleetWorldDrag.y = event.clientY;
    fleetWorldCamera.drag({
      button: fleetWorldDrag.button, dx, dy, viewport: [canvas.width, canvas.height],
    });
    renderCurrentFleetWorld();
  });
  const endDrag = (event, cancelled = false) => {
    if (!fleetWorldDrag) return;
    const completedDrag = fleetWorldDrag;
    fleetWorldDrag = null;
    canvas.classList.remove("dragging");
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
    if (isFleetWorldClick(completedDrag, cancelled) && fleetWorldRenderer) {
      const [x, y] = pointerPosition(event);
      const selected = fleetWorldRenderer.pick(x, y);
      fleetWorldSelectedEntityId = selected?.entityId || "";
      if (fleetWorldFollowId && fleetWorldFollowId !== fleetWorldSelectedEntityId) fleetWorldFollowId = "";
      renderCurrentFleetWorld();
      updateFleetWorldToolbar();
    }
    saveFleetWorldCamera();
  };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", (event) => endDrag(event, true));
  canvas.addEventListener("contextmenu", (event) => event.preventDefault());
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault();
    fleetWorldFollowId = "";
    updateFleetWorldToolbar();
    const [x, y] = pointerPosition(event);
    fleetWorldCamera.zoomAt(event.deltaY, x, y, canvas.width, canvas.height);
    renderCurrentFleetWorld();
    saveFleetWorldCamera();
  }, { passive: false });
  canvas.addEventListener("dblclick", (event) => {
    fleetWorldFollowId = "";
    const [x, y] = pointerPosition(event);
    const selected = fleetWorldRenderer.pick(x, y);
    if (selected && fleetWorldRenderer.focus(selected)) {
      fleetWorldSelectedEntityId = selected.entityId || "";
      renderCurrentFleetWorld();
      updateFleetWorldToolbar();
      saveFleetWorldCamera();
    }
  });
  globalThis.addEventListener?.("keydown", (event) => {
    if (event.key?.toLowerCase() !== "f" || document.activeElement !== canvas) return;
    fleetWorldFollowId = "";
    fleetWorldCamera.applyPreset("overview", fleetWorldClient?.snapshot || {});
    renderCurrentFleetWorld();
    updateFleetWorldToolbar("overview");
    saveFleetWorldCamera();
  });
}

function updateFleetWorldToolbar(activePreset = "") {
  for (const button of document.querySelectorAll?.("[data-world-preset]") || []) {
    button.classList.toggle("active", button.dataset.worldPreset === activePreset);
  }
  const follow = $("#fleet-world-follow");
  follow.setAttribute("aria-pressed", String(Boolean(fleetWorldFollowId)));
  follow.classList.toggle("active", Boolean(fleetWorldFollowId));
  follow.textContent = fleetWorldFollowId ? `跟随 ${fleetWorldFollowId}` : "跟随";
}

function bindFleetWorldToolbar() {
  const followButton = $("#fleet-world-follow");
  if (followButton.dataset.worldToolbarBound === "true") return;
  followButton.dataset.worldToolbarBound = "true";
  for (const button of document.querySelectorAll?.("[data-scene-view]") || []) {
    button.addEventListener("click", () => setFleetSceneView(button.dataset.sceneView));
  }
  $("#fleet-camera-refresh")?.addEventListener("click", () => { void pollFleetFrames(); });
  const expand = $("#fleet-scene-expand");
  const stage = $(".fleet-world-column");
  if (expand) {
    expand.hidden = typeof stage?.requestFullscreen !== "function";
    expand.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else await stage.requestFullscreen();
      } catch (_) { globalThis.TangyingConsoleUI?.feedback?.("浏览器暂不支持放大显示，可使用视角和缩放控制。", "warning"); }
    });
    document.addEventListener?.("fullscreenchange", () => {
      expand.textContent = document.fullscreenElement ? "退出放大" : "放大";
      expand.setAttribute("aria-label", document.fullscreenElement ? "退出放大场景" : "放大场景");
      setFleetSceneView(fleetSceneView);
    });
  }
  for (const button of document.querySelectorAll?.("[data-world-preset]") || []) {
    button.addEventListener("click", () => {
      const preset = button.dataset.worldPreset;
      if (!fleetWorldCamera?.applyPreset(preset, fleetWorldClient?.snapshot || {})) return;
      fleetWorldFollowId = "";
      fleetWorldWebGLInteraction?.setFollow?.("");
      if (preset === "robot-1" || preset === "robot-2") fleetWorldSelectedEntityId = preset;
      renderCurrentFleetWorld();
      updateFleetWorldToolbar(preset);
      saveFleetWorldCamera();
    });
  }
  followButton.addEventListener("click", () => {
    const robot = fleetWorldClient?.snapshot?.robots?.[fleetWorldSelectedEntityId];
    fleetWorldFollowId = fleetWorldFollowId ? "" : (robot ? fleetWorldSelectedEntityId : "");
    fleetWorldWebGLInteraction?.setFollow?.(fleetWorldFollowId);
    renderCurrentFleetWorld();
    updateFleetWorldToolbar();
  });
  const layers = {
    "fleet-world-models-toggle": "models",
    "fleet-world-fixtures-toggle": "bounds",
    "fleet-world-labels-toggle": "labels",
    "fleet-world-path-toggle": "path",
  };
  for (const [id, layer] of Object.entries(layers)) {
    const button = $(`#${id}`);
    button.setAttribute("aria-pressed", String(fleetWorldVisibility[layer]));
    button.classList.toggle("active", fleetWorldVisibility[layer]);
    button.addEventListener("click", (event) => {
      fleetWorldVisibility[layer] = !fleetWorldVisibility[layer];
      event.currentTarget.setAttribute("aria-pressed", String(fleetWorldVisibility[layer]));
      event.currentTarget.classList.toggle("active", fleetWorldVisibility[layer]);
      applyFleetWorldVisibility();
      renderCurrentFleetWorld();
    });
  }
  $("#fleet-visual-retry").addEventListener("click", () => { void retryFleetWorldVisual(); });
  $("#fleet-acceptance-fallback").addEventListener("click", () => {
    if (fleetAcceptanceNonce) degradeFleetVisual("ACCEPTANCE_FORCED_FALLBACK");
  });
}

async function startFleetWorld() {
  bindFleetWorldToolbar();
  applyFleetSceneView();
  if (!globalThis.TangyingWorld) {
    setFleetWorldState("UNAVAILABLE");
    return;
  }
  const session = {
    generation: fleetSessionGeneration,
    token: fleetToken,
    client: null,
  };
  const canvas = $("#fleet-godview-canvas");
  fleetWorldCamera ||= loadFleetWorldCamera();
  if (!fleetWorldRenderer) {
    fleetWorldRenderer = new globalThis.TangyingWorld.WorldRenderer(canvas, fleetWorldCamera);
    fleetWorldRenderer.setVisibility?.(fleetWorldVisibility);
    bindFleetWorldControls(canvas);
  }
  if (!fleetWorldClient) {
    const client = new globalThis.TangyingWorld.WorldRealtimeClient({
      requestSnapshot: () => requestFleetWorldSnapshotForSession(
        session,
        fleetWorldSocket,
        true,
      ),
      render: (snapshot) => {
        if (isCurrentFleetSession(session)) renderFleetWorld(snapshot);
      },
      onState: (state) => {
        if (isCurrentFleetSession(session)) setFleetWorldState(state);
      },
    });
    session.client = client;
    fleetWorldClient = client;
  } else {
    session.client = fleetWorldClient;
  }
  try {
    const snapshot = await requestFleetWorldSnapshotForSession(session);
    if (!isCurrentFleetSession(session)) return;
    session.client.acceptSnapshot(snapshot);
    if (!isCurrentFleetSession(session)) return;
    noteFleetWorldUpdate();
    startFleetWorldWatchdog(session);
    void createFleetWorldRenderer(snapshot);
    if (!isCurrentFleetSession(session)) return;
    await connectFleetWorldEvents(session);
    if (!isCurrentFleetSession(session)) return;
  } catch (_) {
    if (!isCurrentFleetSession(session)) return;
    setFleetWorldState("UNAVAILABLE");
  }
}

async function detectFleetMode() {
  try {
    const response = await fetch("/healthz", { cache: "no-store" });
    if (!response.ok) return false;
    const health = await response.json();
    fleetDemoAuth = health.mode === "fleet" && health.authMode === "demo";
    return health.mode === "fleet";
  } catch (_) {
    return false;
  }
}

function fleetAPI(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (fleetToken) headers.Authorization = `Bearer ${fleetToken}`;
  if (options.body) headers["Content-Type"] = "application/json";
  return fetch(path, { ...options, headers });
}

async function initFleetMode() {
  globalThis.TangyingConsoleUI?.update({ mode: "fleet" });
  fleetMode = true;
  document.body.classList.add("fleet-mode");
  const view = $("#fleet-view");
  view.hidden = false;
  $("#fleet-login-button").addEventListener("click", fleetLogin);
  $("#fleet-logout").addEventListener("click", fleetLogout);
  $("#fleet-create").addEventListener("click", createFleetTask);
  $("#fleet-approve").addEventListener("click", () => fleetTaskAction("approve"));
  $("#fleet-cancel")?.addEventListener("click", () => fleetTaskAction("cancel"));
  $("#fleet-revision-preview").addEventListener("click", proposeFleetTaskRevision);
  $("#fleet-revision-confirm").addEventListener("click", confirmFleetTaskRevision);
  $("#fleet-revision-edit").addEventListener("click", editFleetTaskRevision);
  $("#fleet-update-request").addEventListener("input", updateFleetRevisionControls);
  $("#fleet-telemetry-robot").addEventListener("change", pollFleetTelemetry);
  renderFleetAuth();
  if (fleetDemoAuth && !fleetToken) {
    try {
      const response = await fetch("/v1/auth/demo-session", { method: "POST" });
      const session = await response.json();
      if (!response.ok || !session.token) throw new Error("DEMO_SESSION_UNAVAILABLE");
      fleetToken = session.token;
      fleetOperator = session.operator || "demo-operator";
    } catch (_) {
      $("#fleet-login-message").textContent = "演示连接暂不可用，请稍后刷新或使用操作员账号登录。";
    }
  }
  if (fleetToken) {
    showFleetDashboard();
  }
}

function renderFleetAuth() {
  if (fleetToken) {
    $("#fleet-login").hidden = true;
    $("#fleet-dashboard").hidden = false;
    $("#fleet-logout").hidden = false;
    $("#fleet-operator").textContent = `操作员: ${fleetOperator || "—"}`;
    $("#fleet-login-message").textContent = "";
    return;
  }
  $("#fleet-login").hidden = false;
  $("#fleet-dashboard").hidden = true;
  $("#fleet-logout").hidden = true;
  $("#fleet-operator").textContent = "未登录";
}

function showFleetDashboard() {
  renderFleetAuth();
  void startFleetWorld();
  pollFleetDevices();
  pollFleetMap();
  pollFleetFrames();
  pollFleetTasks();
  pollFleetTelemetry();
  setInterval(pollFleetDevices, 5000);
  setInterval(pollFleetMap, 30000);
  setInterval(pollFleetFrames, 1500);
  setInterval(pollFleetTasks, 4000);
}

async function fleetLogin() {
  const user = $("#fleet-user").value.trim();
  const password = $("#fleet-password").value;
  const message = $("#fleet-login-message");
  if (!user || !password) {
    message.textContent = "请输入用户名和密码";
    return;
  }
  try {
    const response = await fetch("/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user, password }),
    });
    const body = await response.json();
    if (!response.ok) {
      message.textContent = body.message || "登录失败";
      return;
    }
    fleetSessionGeneration += 1;
    fleetToken = body.token;
    fleetOperator = body.operator || user;
    try {
      sessionStorage.setItem("fleetToken", fleetToken);
      sessionStorage.setItem("fleetOperator", fleetOperator);
    } catch (_) {
      // Session storage unavailable: keep the token in memory only.
    }
    showFleetDashboard();
  } catch (_) {
    message.textContent = "无法连接 Fleet 控制台";
  }
}

function fleetLogout() {
  globalThis.TangyingConsoleUI?.update({ task: null, adapter: null, robotId: null, worldRevision: null, eventCursor: null, emergencyStopped: null, anomalyCount: 0 });
  fleetSessionGeneration += 1;
  const socket = fleetWorldSocket;
  fleetWorldSocket = null;
  fleetToken = "";
  fleetOperator = "";
  clearFleetFrames();
  fleetLatestMap = null;
  try {
    sessionStorage.removeItem("fleetToken");
    sessionStorage.removeItem("fleetOperator");
  } catch (_) {
    // Session storage unavailable.
  }
  selectedFleetTask = null;
  fleetTaskExperienceState = { taskId: "", revision: 0, aggregateVersion: 0, cursor: 0 };
  fleetPendingTaskRevision = null;
  fleetTaskSelectionGeneration += 1;
  fleetTaskUpdateAllowed = false;
  resetFleetVisualState();
  setFleetWorldState("CONNECTING");
  clearTimeout(fleetWorldReconnectTimer);
  fleetWorldReconnectTimer = null;
  clearInterval(fleetWorldWatchdog);
  fleetWorldWatchdog = null;
  fleetWorldLastUpdateAt = 0;
  fleetWorldClient = null;
  fleetWorldMessageQueue = Promise.resolve();
  socket?.close();
  renderFleetAuth();
}

async function pollFleetDevices() {
  try {
    const response = await fleetAPI("/v1/devices");
    if (response.status === 401) {
      fleetLogout();
      return;
    }
    if (!response.ok) return;
    renderFleetDevices(await response.json());
  } catch (_) {
    // best-effort
  }
}

function renderFleetDevices(devices) {
  const onlineAdapters = new Set(
    (devices || [])
      .filter((device) => device.online && device.adapter)
      .map((device) => device.adapter),
  );
  fleetExecutionAdapter = onlineAdapters.size === 1 ? [...onlineAdapters][0] : "auto";
  globalThis.TangyingConsoleUI?.update({ adapter: fleetExecutionAdapter === "auto" ? "" : fleetExecutionAdapter });
  const body = $("#fleet-devices tbody");
  body.replaceChildren();
  for (const device of devices || []) {
    const row = document.createElement("tr");
    const cells = [
      device.robotId || "—",
      device.adapter || "—",
      device.online ? "在线" : "离线",
      device.leaseExpiry ? new Date(device.leaseExpiry).toLocaleTimeString() : "—",
      device.softwareVersion || device.runtimeVersion || "—",
      `${(device.capabilities || []).length} 项`,
    ];
    for (const [index, text] of cells.entries()) {
      const cell = document.createElement("td");
      if (index === 3 || index === 4) cell.setAttribute("data-developer", "");
      cell.textContent = text;
      row.append(cell);
    }
    row.classList.toggle("offline", !device.online);
    body.append(row);
  }
  $("#fleet-devices-status").textContent = `${(devices || []).length} 台设备`;
}

const fleetFrameURLs = new Map();
const fleetFrameReceivedAt = new Map();
let fleetFrameRequest = null;

function renderFleetFrames(received = new Set()) {
  for (const image of document.querySelectorAll?.("[data-frame-robot]") || []) {
    const robotID = image.dataset.frameRobot;
    const url = fleetFrameURLs.get(robotID);
    image.hidden = !url;
    if (url) image.src = url;
    else image.removeAttribute("src");
    image.classList.toggle("stale", Boolean(url) && !received.has(robotID));
  }
  for (const placeholder of document.querySelectorAll?.("[data-frame-empty]") || []) {
    placeholder.hidden = fleetFrameURLs.has(placeholder.dataset.frameEmpty);
  }
  for (const state of document.querySelectorAll?.("[data-frame-state]") || []) {
    const robotID = state.dataset.frameState;
    const time = fleetFrameReceivedAt.get(robotID);
    state.textContent = received.has(robotID) ? `${time} 收到` : time ? `最后画面 · ${time}` : "等待画面";
  }
  const status = received.size
    ? `${received.size} 路画面已收到 · 每 1.5 秒尝试更新${fleetFrameURLs.size > received.size ? " · 部分画面未更新" : ""}`
    : fleetFrameURLs.size ? "画面未更新，正在显示最后收到的画面" : "尚未收到画面，连接设备后自动显示";
  for (const selector of ["#fleet-godview-status", "#fleet-workspace-camera-status"]) {
    const element = $(selector);
    if (element) element.textContent = status;
  }
}

function clearFleetFrames() {
  fleetFrameRequest?.controller.abort();
  fleetFrameRequest = null;
  for (const url of fleetFrameURLs.values()) URL.revokeObjectURL(url);
  fleetFrameURLs.clear();
  fleetFrameReceivedAt.clear();
  renderFleetFrames();
}

async function pollFleetFrames() {
  if (!pageVisible("workspace", "devices") || !fleetToken || fleetFrameRequest) return;
  const request = { generation: fleetSessionGeneration, token: fleetToken, controller: new AbortController() };
  fleetFrameRequest = request;
  const current = () => pageVisible("workspace", "devices") && isCurrentFleetSession(request) && fleetFrameRequest === request;
  const timer = setTimeout(() => request.controller.abort(), 5000);
  const options = { cache: "no-store", signal: request.controller.signal };
  const received = new Set();
  try {
    const response = await fleetAPI("/v1/scene/frames", options);
    if (!response.ok) throw new Error("Camera list unavailable");
    const payload = await response.json();
    if (!current()) return;
    const visibleRobots = new Set([...document.querySelectorAll("[data-frame-robot]")].map(image => image.dataset.frameRobot));
    const robots = [...new Set((payload.frames || []).map(frame => frame.robotId))].filter(id => visibleRobots.has(id));
    await Promise.all(robots.map(async robotID => {
      try {
        const frameResponse = await fleetAPI(`/v1/scene/frames/${encodeURIComponent(robotID)}?t=${Date.now()}`, options);
        if (!frameResponse.ok || !current()) return;
        const blob = await frameResponse.blob();
        if (!current()) return;
        const url = URL.createObjectURL(blob);
        const previous = fleetFrameURLs.get(robotID);
        fleetFrameURLs.set(robotID, url);
        // This is receipt time, not a guarantee of camera capture freshness.
        fleetFrameReceivedAt.set(robotID, new Date().toLocaleTimeString());
        received.add(robotID);
        renderFleetFrames(received);
        if (previous) URL.revokeObjectURL(previous);
      } catch (_) {
        // Keep the last image and explicitly mark the failed channel below.
      }
    }));
  } catch (_) {
    // Transport failures must not leave an old image labelled as updated.
  } finally {
    clearTimeout(timer);
    if (current()) { renderFleetFrames(received); fleetFrameRequest = null; }
  }
}

async function pollFleetMap() {
  if (!pageVisible("workspace", "diagnostics") || !fleetToken) return;
  const session = { generation: fleetSessionGeneration, token: fleetToken };
  try {
    const response = await fleetAPI("/v1/maps/global");
    if (!response.ok) throw new Error("Map unavailable");
    const global = await response.json();
    if (!isCurrentFleetSession(session)) return;
    fleetLatestMap = global;
    drawFleetMap(global);
    drawFleetMap(global, $("#fleet-workspace-map-canvas"));
    $("#fleet-workspace-map-meta").textContent = `${(global.robots || []).length} 台机器人 · ${(global.entities || []).length} 个物体 · ${new Date().toLocaleTimeString()} 收到地图`;
    $("#fleet-map-meta").textContent =
      `${(global.robots || []).length} 机器人 · ${(global.entities || []).length} 实体 · ${(global.width || 0)}×${(global.height || 0)} 栅格 @${(global.cellSizeM || 0.1).toFixed(2)}m`;
  } catch (_) {
    if (isCurrentFleetSession(session)) $("#fleet-workspace-map-meta").textContent = "地图更新失败，等待重新连接";
  }
}

function worldGridCellRect(global, ix, iy, view) {
  const cellSize = Number(global.cellSizeM) || 0.1;
  const originX = Number(global.originX) || 0;
  const originY = Number(global.originY) || 0;
  const worldX = originX + ix * cellSize;
  const worldY = originY + iy * cellSize;
  return [
    (worldX - view.minX) * view.scale,
    view.height - (worldY + cellSize - view.minY) * view.scale,
    cellSize * view.scale,
    cellSize * view.scale,
  ];
}

function drawFleetMap(global, mapCanvas = document.querySelector("#fleet-map-canvas")) {
  if (!mapCanvas) return;
  const mapContext = mapCanvas.getContext("2d");
  const width = mapCanvas.width;
  const height = mapCanvas.height;
  const userView = mapCanvas.id === "fleet-workspace-map-canvas";
  const labelSize = userView ? Math.min(30, Math.max(15, 12 * width / (mapCanvas.clientWidth || width))) : 10;
  const taskLabels = { "red-block": "红色方块", "left-start-zone": "起点", "handoff-zone": "交接区", "right-target-zone": "目标区" };
  const entityLabel = (entity) => userView ? (taskLabels[entity.entityId] || entity.attributes?.label || "") : entity.entityId;
  mapContext.clearRect(0, 0, width, height);
  mapContext.fillStyle = "#07120f";
  mapContext.fillRect(0, 0, width, height);

  // World bounds from robots and entities so the view auto-fits both tables.
  const points = [];
  for (const robot of global.robots || []) {
    if (robot.pose && robot.pose.length >= 2) points.push(robot.pose);
    for (const point of robot.trajectory || []) points.push(point);
  }
  for (const entity of global.entities || []) {
    if (entity.pose && entity.pose.length >= 2) points.push(entity.pose);
  }
  const occupancyCellSize = Number(global.cellSizeM);
  const occupancyOriginX = Number(global.originX);
  const occupancyOriginY = Number(global.originY);
  if (
    global.width > 0 && global.height > 0
    && Number.isFinite(occupancyCellSize) && occupancyCellSize > 0
    && Number.isFinite(occupancyOriginX) && Number.isFinite(occupancyOriginY)
  ) {
    points.push([occupancyOriginX, occupancyOriginY]);
    points.push([
      occupancyOriginX + global.width * occupancyCellSize,
      occupancyOriginY + global.height * occupancyCellSize,
    ]);
  }
  if (!points.length) {
    mapContext.fillStyle = "rgba(223,255,238,0.5)";
    mapContext.font = "14px ui-sans-serif, sans-serif";
    mapContext.textAlign = "center";
    mapContext.fillText("等待机器人遥测上报…", width / 2, height / 2);
    return;
  }
  const minX = Math.min(...points.map((p) => p[0])) - 0.6;
  const maxX = Math.max(...points.map((p) => p[0])) + 0.6;
  const minY = Math.min(...points.map((p) => p[1])) - 0.6;
  const maxY = Math.max(...points.map((p) => p[1])) + 0.6;
  const scale = Math.min(width / (maxX - minX), height / (maxY - minY));
  const toX = (x) => (x - minX) * scale;
  const toY = (y) => height - (y - minY) * scale;

  // World grid (0.2 m).
  mapContext.strokeStyle = "rgba(143,255,196,0.07)";
  mapContext.lineWidth = 1;
  for (let gx = Math.floor(minX / 0.2) * 0.2; gx <= maxX; gx += 0.2) {
    mapContext.beginPath();
    mapContext.moveTo(toX(gx), 0);
    mapContext.lineTo(toX(gx), height);
    mapContext.stroke();
  }
  for (let gy = Math.floor(minY / 0.2) * 0.2; gy <= maxY; gy += 0.2) {
    mapContext.beginPath();
    mapContext.moveTo(0, toY(gy));
    mapContext.lineTo(width, toY(gy));
    mapContext.stroke();
  }

  // Fused occupancy layer (translucent cells from the base64 payload).
  if (global.cells && global.width > 0 && global.height > 0) {
    let cells = global.cells;
    if (typeof cells === "string") {
      try {
        const binary = atob(cells);
        cells = Uint8Array.from(binary, (char) => char.charCodeAt(0));
      } catch (_) {
        cells = [];
      }
    }
    if (cells.length >= global.width * global.height) {
      const view = { minX, minY, scale, height };
      for (let iy = 0; iy < global.height; iy += 1) {
        for (let ix = 0; ix < global.width; ix += 1) {
          const value = cells[iy * global.width + ix];
          if (value <= 0) continue;
          const alpha = Math.min(0.4, 0.06 + (value / 100) * 0.3);
          mapContext.fillStyle = value >= 100 ? `rgba(255,120,90,${alpha})` : `rgba(255,200,120,${alpha})`;
          mapContext.fillRect(...worldGridCellRect(global, ix, iy, view));
        }
      }
    }
  }

  const robotColors = ["#8fffc4", "#6bb5ff", "#ffb86b", "#d18fff"];
  const robots = global.robots || [];
  const robotColor = new Map();
  robots.forEach((robot, index) => robotColor.set(robot.robotId, robotColors[index % robotColors.length]));

  // Trajectories under everything.
  robots.forEach((robot, index) => {
    const color = robotColors[index % robotColors.length];
    const trajectory = robot.trajectory || [];
    if (trajectory.length > 1) {
      mapContext.strokeStyle = color;
      mapContext.globalAlpha = 0.5;
      mapContext.lineWidth = 1.5;
      mapContext.beginPath();
      trajectory.forEach((point, pointIndex) => {
        const px = toX(point[0]);
        const py = toY(point[1]);
        if (pointIndex === 0) mapContext.moveTo(px, py);
        else mapContext.lineTo(px, py);
      });
      mapContext.stroke();
      mapContext.globalAlpha = 1;
    }
  });

  // Static furniture and objects from fused entities.
  for (const entity of global.entities || []) {
    const x = toX(entity.pose[0] ?? 0);
    const y = toY(entity.pose[1] ?? 0);
    if (entity.category === "work_surface") {
      mapContext.fillStyle = "rgba(90,69,48,0.35)";
      mapContext.strokeStyle = "#5a4530";
      const w = 0.84 * scale;
      const h = 0.78 * scale;
      mapContext.fillRect(x - w / 2, y - h / 2, w, h);
      mapContext.strokeRect(x - w / 2, y - h / 2, w, h);
      continue;
    }
    if (entity.category === "storage_bin" || entity.category === "delivery_tray") {
      mapContext.fillStyle = "rgba(255,184,107,0.25)";
      mapContext.strokeStyle = entityColor(entity);
      const s = 0.22 * scale;
      mapContext.fillRect(x - s / 2, y - s / 2, s, s);
      mapContext.strokeRect(x - s / 2, y - s / 2, s, s);
      mapContext.fillStyle = "#dfffee";
      mapContext.font = `${labelSize}px ui-sans-serif, sans-serif`;
      mapContext.textAlign = "center";
      mapContext.fillText(entityLabel(entity), x, y - s / 2 - 4);
      continue;
    }
    if (entity.category === "environment" || entity.category === "robot") continue;
    // Objects: filled circle with color + label.
    mapContext.fillStyle = entityColor(entity);
    mapContext.beginPath();
    mapContext.arc(x, y, Math.max(6, 0.07 * scale), 0, Math.PI * 2);
    mapContext.fill();
    mapContext.strokeStyle = "#07120f";
    mapContext.lineWidth = 1;
    mapContext.stroke();
    mapContext.fillStyle = "#dfffee";
    mapContext.font = `${labelSize}px ui-sans-serif, sans-serif`;
    mapContext.textAlign = "center";
    mapContext.fillText(entityLabel(entity), x, y - Math.max(10, 0.07 * scale) - 2);
  }

  // Robots: heading triangle + held object + activity label.
  robots.forEach((robot, index) => {
    const color = robotColors[index % robotColors.length];
    const pose = robot.pose || [];
    const x = toX(pose[0] ?? 0);
    const y = toY(pose[1] ?? 0);
    const yaw = pose[3] || 0;
    mapContext.save();
    mapContext.translate(x, y);
    mapContext.rotate(-yaw);
    mapContext.fillStyle = "rgba(143,255,196,0.15)";
    mapContext.strokeStyle = color;
    mapContext.lineWidth = 2;
    mapContext.fillRect(-22, -14, 44, 28);
    mapContext.strokeRect(-22, -14, 44, 28);
    mapContext.beginPath();
    mapContext.moveTo(22, 0);
    mapContext.lineTo(12, -8);
    mapContext.lineTo(12, 8);
    mapContext.closePath();
    mapContext.fillStyle = color;
    mapContext.fill();
    mapContext.restore();
    const held = robot.held ? ` · 抓取 ${robot.held}` : "";
    mapContext.fillStyle = color;
    mapContext.font = `bold ${userView ? labelSize + 2 : 11}px ui-sans-serif, sans-serif`;
    mapContext.textAlign = "center";
    mapContext.fillText(
      userView ? robot.robotId.replace(/^robot-(\d+)$/, "$1 号机器人") + (robot.held ? " · 持有物体" : "")
        : `${robot.robotId}${robot.activity ? ` · ${robot.activity}` : ""}${held}`,
      x,
      y - 24,
    );
    // Held object rendered at the robot's gripper side.
    if (robot.held) {
      const hx = x + Math.sin(yaw) * 0.18 * scale;
      const hy = y - Math.cos(yaw) * 0.18 * scale;
      mapContext.fillStyle = "#ff6b6b";
      mapContext.beginPath();
      mapContext.arc(hx, hy, 5, 0, Math.PI * 2);
      mapContext.fill();
    }
  });

  if (!robots.length) {
    mapContext.fillStyle = "rgba(223,255,238,0.5)";
    mapContext.font = "14px ui-sans-serif, sans-serif";
    mapContext.textAlign = "center";
    mapContext.fillText("等待机器人遥测上报…", width / 2, height / 2);
  }
}

async function pollFleetTasks() {
  try {
    const response = await fleetAPI("/v1/tasks");
    if (!response.ok) return;
    const tasks = await response.json();
    renderFleetTasks(tasks || []);
    if (selectedFleetTask) {
      const current = (tasks || []).find((task) => task.id === selectedFleetTask.id);
      if (current) await fleetSelectTask(current);
    }
  } catch (_) {
    // best-effort
  }
}

function renderFleetTasks(tasks) {
  const list = $("#fleet-tasks");
  list.replaceChildren();
  for (const task of tasks) {
    const item = document.createElement("li");
    const content = document.createElement("div");
    const title = document.createElement("strong");
    title.textContent = globalThis.TangyingConsoleUI?.taskPresentation(task.state).label || `${task.state} · ${task.id.slice(0, 8)}`;
    const detail = document.createElement("p");
    const robots = task.intent?.sequence?.length
      ? task.intent.sequence.map((intent) => intent.robotId || "any").join(" → ")
      : (task.intent?.robotId || "any");
    detail.textContent = `${task.request}  [${robots}]`;
    const identity = document.createElement("code");
    identity.setAttribute("data-developer", "");
    identity.textContent = task.id;
    content.append(title, detail, identity);
    const open = document.createElement("button");
    open.type = "button";
    open.append(content);
    item.append(open);
    open.addEventListener("click", () => {
      globalThis.TangyingConsoleUI?.navigate("workspace");
      void fleetSelectTask(task);
    });
    list.append(item);
  }
}

async function fleetSelectTask(task) {
  const selectionChanged = selectedFleetTask?.id !== task.id;
  selectedFleetTask = task;
  globalThis.TangyingConsoleUI?.update({ task });
  if ($("#fleet-cancel")) $("#fleet-cancel").disabled = ["SUCCEEDED", "CANCELLED", "FAILED"].includes(task.state);
  const selectionGeneration = ++fleetTaskSelectionGeneration;
  if (selectionChanged) {
    fleetPendingTaskRevision = null;
    fleetTaskUpdateAllowed = false;
    $("#fleet-update-preview").hidden = true;
    $("#fleet-update-preview").replaceChildren();
    $("#fleet-revision-confirm").disabled = true;
    $("#fleet-revision-edit").disabled = true;
    $("#fleet-task-experience-status").textContent = "正在同步所选任务的最新说明。";
  }
  $("#fleet-task-id").textContent = `任务 ${task.id} · ${task.state}`;
  $("#fleet-approve").disabled = task.approved || ["SUCCEEDED", "CANCELLED", "FAILED"].includes(task.state);
  try {
    const response = await fleetAPI(`/v1/tasks/${task.id}/intents`);
    if (response.ok) renderFleetIntents(await response.json());
  } catch (_) {
    // best-effort
  }
  if (selectionGeneration !== fleetTaskSelectionGeneration) return;
  await loadFleetTaskExperience(task.id, { selectionGeneration });
}

function renderFleetIntents(snapshot) {
  const list = $("#fleet-intents");
  list.replaceChildren();
  const statusColor = {
    PENDING: "var(--muted)",
    READY: "var(--mint)",
    RUNNING: "var(--accent, #ffb86b)",
    SUCCEEDED: "var(--mint)",
    FAILED: "var(--danger)",
  };
  for (const intent of snapshot.intents || []) {
    const item = document.createElement("li");
    const status = document.createElement("span");
    status.className = "intent-status";
    status.textContent = intent.status;
    status.style.color = statusColor[intent.status] || "var(--muted)";
    const label = document.createElement("span");
    label.textContent = `#${intent.index} ${intent.action || "task"} @ ${intent.robotId || intent.claimed || "any"}`;
    item.append(status, label);
    list.append(item);
  }
  const verified = [...(snapshot.intents || [])]
    .reverse()
    .find((intent) => intent.harnessStatus);
  $("#fleet-harness-verdict").textContent = verified
    ? `${verified.harnessStatus} · ${verified.harnessReason || "已记录物理证据"}`
    : "等待 Harness 证据";
  $("#fleet-intents-robots").textContent = `机器人群组: ${(snapshot.robots || []).join(", ") || "—"}`;
  $("#fleet-task-state").textContent = `任务状态: ${snapshot.state || "—"}`;
}

const fleetArgumentLabels = {
  targetRef: "目标位置",
  objectId: "物品",
  destinationId: "放置位置",
  resourceId: "任务资源",
};

const fleetReferenceLabels = {
  "red-cup": "红色杯子", "blue-cup": "蓝色杯子", "green-cup": "绿色杯子",
  "red-bottle": "红色瓶子", "blue-bottle": "蓝色瓶子", "green-bottle": "绿色瓶子",
  "red-block": "红色方块",
  "blue-block": "蓝色方块", "green-block": "绿色方块",
  "right-bin": "右侧收纳盒", "left-bin": "左侧收纳盒", "front-tray": "前方交付托盘",
  "storage_bin/right_side": "右侧收纳盒", "storage_bin/left_side": "左侧收纳盒",
  "delivery_tray/front_side": "前方交付托盘",
  "storage_bin": "收纳盒", "delivery_tray": "交付托盘", "right_side": "右侧", "left_side": "左侧", "front_side": "前方",
  "robot-local": "当前机器人", "xlerobot-mujoco-tabletop": "当前机器人",
  "handoff-zone": "交接区",
  "right-target-zone": "右侧目标区",
  "left-target-zone": "左侧目标区",
};

function missionReferenceLabel(value) {
  return String(value).replace(/[a-z][a-z0-9_-]*(?:\/[a-z0-9_-]+)?/gi, (token) => Object.hasOwn(fleetReferenceLabels, token) ? fleetReferenceLabels[token] : token);
}

function taskExperienceDecisionForState(experience, currentState) {
  const incoming = {
    taskId: String(experience?.taskId || ""),
    revision: Number(experience?.revision || 0),
    aggregateVersion: Number(experience?.aggregateVersion || 0),
    cursor: Number(experience?.cursor || 0),
  };
  if (!incoming.taskId || !Number.isInteger(incoming.revision) || incoming.revision < 1 ||
      !Number.isFinite(incoming.aggregateVersion) || incoming.aggregateVersion < 0) return "invalid";
  const current = currentState || { taskId: "", revision: 0, aggregateVersion: 0, cursor: 0 };
  if (!current.taskId || current.taskId !== incoming.taskId) return "accept";
  if (incoming.revision < current.revision) return "stale";
  if (incoming.revision > current.revision + 1) return "gap";
  if (incoming.revision === current.revision) {
    if (incoming.aggregateVersion < current.aggregateVersion) return "stale";
    // HTTP Experience snapshots currently have no cursor, and task execution
    // does not increment the revision aggregate. Refresh those full snapshots;
    // keep ordering checks when an event cursor is actually available.
    if (incoming.aggregateVersion === current.aggregateVersion &&
        (incoming.cursor > 0 || current.cursor > 0) && incoming.cursor <= current.cursor) return "stale";
  }
  return "accept";
}

function taskExperienceDecision(experience) {
  return taskExperienceDecisionForState(experience, fleetTaskExperienceState);
}

function makeTextElement(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  element.textContent = String(text || "");
  return element;
}

function revisionStatusText(status) {
  switch (status) {
    case "PROPOSED": return "已经理解更新，等待你确认";
    case "WAITING_APPROVAL": return "请确认这次任务变化";
    case "WAITING_SAFE_POINT": return "机器人会先完成手上的安全动作，再按新任务继续";
    case "ACTIVE": return "当前版本正在执行";
    case "SUPERSEDED": return "这个版本已被更新任务替换";
    case "REJECTED": return "这次更新没有生效";
    default: return "任务状态正在同步";
  }
}

function renderMissionSteps(steps) {
  const list = $("#fleet-step-ribbon");
  list.replaceChildren();
  for (const [index, step] of (steps || []).entries()) {
    const item = document.createElement("li");
    item.className = `mission-step ${String(step.status || "pending").toLowerCase()}`;
    item.dataset.stepId = String(step.stepId || "");
    item.append(
      makeTextElement("strong", "", `${index + 1}. ${step.statusText || "等待执行"} · ${step.explanation || "机器人执行当前步骤"}`),
      makeTextElement("p", "mission-step-meta", [step.assignedRobot, step.capabilityLabel].filter(Boolean).join(" · ") || "系统正在安排机器人"),
    );
    if (step.evidenceText) item.append(makeTextElement("span", "mission-evidence", step.evidenceText));
    list.append(item);
  }
  if (!(steps || []).length) list.append(makeTextElement("li", "mission-step", "等待系统拆解任务步骤"));
}

function renderMissionActivities(activities, professionalActivities, professionalStepEvidence) {
  const list = $("#fleet-tool-activities");
  const professional = $("#fleet-professional-activities");
  list.replaceChildren();
  professional.replaceChildren();
  const latestByStep = new Map();
  for (const activity of activities || []) {
    const key = [
      activity.stepId || activity.robotId || "robot",
      activity.displayName || "capability",
    ].join(":");
    latestByStep.set(key, activity);
  }
  for (const activity of latestByStep.values()) {
    const card = document.createElement("article");
    card.className = `mission-tool-card ${String(activity.status || "waiting").toLowerCase()}`;
    card.append(
      makeTextElement("span", "mission-tool-status", `${activity.robotId || "机器人"} · ${activity.statusText || "等待机器人反馈"}`),
      makeTextElement("strong", "", activity.displayName || "机器人能力"),
      makeTextElement("p", "", activity.purpose || "机器人正在执行当前步骤"),
    );
	if (activity.controlMethod) {
	  const control = document.createElement("div");
	  control.className = "mission-control-method";
	  control.append(
	    makeTextElement("span", "", `控制方式：${activity.controlMethod}`),
	    makeTextElement("span", "mission-control-stage", activity.controlStage || "准备环境信息"),
	  );
	  card.append(control);
	}
    const argumentsList = document.createElement("div");
    argumentsList.className = "mission-safe-arguments";
    for (const [name, value] of Object.entries(activity.safeArguments || {})) {
      if (/password|secret|token|bearer|credential|private|api[_-]?key/i.test(name)) continue;
      argumentsList.append(makeTextElement("span", "", `${fleetArgumentLabels[name] || "任务信息"}：${missionReferenceLabel(value)}`));
    }
    if (argumentsList.children.length) card.append(argumentsList);
    if (activity.evidenceText) card.append(makeTextElement("span", "mission-evidence", activity.evidenceText));
    list.append(card);
  }
  if (!latestByStep.size) list.append(makeTextElement("p", "", "任务开始后，这里会说明机器人调用了什么能力，以及结果是否被环境确认。"));
  for (const activity of professionalActivities || []) {
    const code = document.createElement("code");
	code.textContent = JSON.stringify(sanitizeProfessionalActivity(activity), null, 2);
    professional.append(code);
  }
  for (const evidence of professionalStepEvidence || []) {
    const code = document.createElement("code");
    code.textContent = JSON.stringify({ kind: "harness_evidence", ...evidence }, null, 2);
    professional.append(code);
  }
  if (!(professionalActivities || []).length && !(professionalStepEvidence || []).length) {
    professional.append(makeTextElement("p", "", "暂无专业活动记录"));
  }
}

function sanitizeProfessionalActivity(activity) {
  const sanitized = { ...(activity || {}) };
  delete sanitized.action_chunk;
  delete sanitized.actionChunk;
  delete sanitized.actions;
  if (sanitized.arguments && typeof sanitized.arguments === "object") {
    sanitized.arguments = { ...sanitized.arguments };
    delete sanitized.arguments.action_chunk;
    delete sanitized.arguments.actionChunk;
    delete sanitized.arguments.actions;
  }
  return sanitized;
}

function renderMissionRecovery(recovery) {
  const panel = $("#fleet-mission-recovery");
  const content = $("#fleet-recovery-content");
  content.replaceChildren();
  panel.hidden = !recovery;
  if (!recovery) return;
  content.append(
    makeTextElement("p", "", recovery.knownState || "系统正在确认最后可信状态"),
    makeTextElement("p", "", recovery.robotSafetyState || "机器人保持安全状态"),
    makeTextElement("p", "", recovery.automaticAction || "系统正在自动恢复"),
  );
	if ((recovery.timeline || []).length) {
	  content.append(makeTextElement("strong", "mission-recovery-title", "恢复过程"));
	  const timeline = document.createElement("ol");
	  timeline.className = "mission-recovery-timeline";
	  for (const item of recovery.timeline) {
	    const attempt = item.attempt ? ` · 第 ${item.attempt}${item.maxAttempts ? `/${item.maxAttempts}` : ""} 次` : "";
	    const row = document.createElement("li");
	    row.append(
	      makeTextElement("strong", "", `${item.knownState || "系统正在恢复"}${attempt}`),
	      makeTextElement("span", "", item.action || "系统正在自动处理"),
	    );
	    timeline.append(row);
	  }
	  content.append(timeline);
	}
  if ((recovery.userActions || []).length) {
    const list = document.createElement("ul");
    for (const action of recovery.userActions) list.append(makeTextElement("li", "", action));
    content.append(list);
  }
}

function localGoalEvidenceRecord(step) {
  const match = /^intent-(\d+)\/[a-f0-9]+$/.exec(step.stepId || "");
  if (!match || step.status !== "SATISFIED") return null;
  const index = Number(match[1]);
  const goalCount = activeTask?.intent?.sequence?.length || (activeTask?.intent ? 1 : 0);
  if (index >= goalCount) return null;
  // Local Runner uses this stable manipulation step prefix. Require the actual
  // verified event and saved capture as well; task success alone is no proof.
  const logicalStep = `${goalCount > 1 ? `task${String(index + 1).padStart(2, "0")}-` : ""}verify_place`;
  const revision = Number(activeTask.currentRevision || localTaskExperienceState.revision || 1);
  const events = [...localEvents.values()].filter(event => {
    const payload = event.payload || {};
    const id = payload.stepId || event.stepId || "";
    return event.type === "TOOL_ACTIVITY" && payload.toolName === "verify_placement" && payload.activityStatus === "CONFIRMED"
      && Number(payload.taskRevision || 1) === revision && (id === logicalStep || id.startsWith(`${logicalStep}/resume-read/`));
  }).sort((a, b) => b.sequence - a.sequence);
  for (const event of events) {
    const stepId = event.payload.stepId || event.stepId;
    const executionStep = revision > 1 ? `revision-${revision}/${stepId}` : stepId;
    const record = localEvidenceRecords.find(item => item.taskId === activeTask.id && Number(item.taskRevision || 1) === revision && item.stepId === executionStep && Array.isArray(event.payload.evidenceIds) && event.payload.evidenceIds.includes(item.captureId));
    if (record) return record;
  }
  return null;
}

function localEvidenceButton(record) {
  const button = makeTextElement("button", "secondary evidence-link", "回看当时观测");
  button.type = "button";
  button.addEventListener("click", () => {
    void selectLocalEvidence(record.id);
    $("#local-evidence-panel").scrollIntoView?.({ behavior: "smooth", block: "start" });
    $("#local-evidence-select").focus();
  });
  return button;
}

function evidenceRenderKey(record) {
  return record ? [record.id, record.expired, record.snapshotSha256, localEvidenceIsCommandObservation(record), localEvidenceSourceLabel(record), localNavigationVerification(record)?.map_receipt?.completion_source] : null;
}

function renderLocalMissionSteps(steps) {
  localMissionSteps = steps || [];
  if (!localStepRibbon) return;
  const list = localStepRibbon;
  const renderKey = JSON.stringify([activeTask?.id, localTaskExperienceState.revision, steps, (steps || []).map(step => evidenceRenderKey(localGoalEvidenceRecord(step)))]);
  if (list.dataset.renderKey === renderKey) return;
  list.dataset.renderKey = renderKey;
  list.replaceChildren();
  const entries = steps || [];
  for (const [index, step] of entries.entries()) {
    const item = document.createElement("li");
    item.className = `mission-step ${String(step.status || "pending").toLowerCase()}`;
    item.dataset.stepId = String(step.stepId || "");
    item.append(
      makeTextElement("strong", "", `${index + 1}. ${step.statusText || "等待执行"} · ${missionReferenceLabel(step.explanation || "机器人正在执行")}`),
      makeTextElement("p", "mission-step-meta", [missionReferenceLabel(step.assignedRobot || "当前机器人"), step.capabilityLabel].filter(Boolean).join(" · ")),
    );
    const observation = localGoalEvidenceRecord(step);
    if (observation) {
      item.append(makeTextElement("span", "mission-evidence", observation.expired ? "动作观测记录已保存，图像已清理" : "动作观测可回看"));
      item.append(localEvidenceButton(observation));
    } else if (step.evidenceText) item.append(makeTextElement("span", "mission-evidence", step.status === "SATISFIED" && step.evidenceText === "等待环境证据" ? "此记录尚未附带观测证据" : step.evidenceText));
    list.append(item);
  }
  if (!entries.length) {
    list.append(makeTextElement("li", "mission-step", "任务尚未拆解，执行中逐步显示"));
  }
}

function renderLocalMissionActivities(activities) {
  localMissionActivities = activities || [];
  if (!localToolActivities) return;
  const list = localToolActivities;
  const renderKey = JSON.stringify([activeTask?.id, localTaskExperienceState.revision, activities, (activities || []).map(activity => evidenceRenderKey(localActivityEvidenceRecord(activity)))]);
  if (list.dataset.renderKey === renderKey) return;
  list.dataset.renderKey = renderKey;
  list.replaceChildren();
  const latestByStep = new Map();
  for (const activity of activities || []) {
    const key = [activity.stepId || activity.robotId || "robot", activity.displayName || "capability"].join(":");
    latestByStep.set(key, activity);
  }
  for (const activity of latestByStep.values()) {
    const record = localActivityEvidenceRecord(activity);
    const navigation = localNavigationVerification(record);
    const card = document.createElement("article");
    card.className = `mission-tool-card ${String(activity.status || "waiting").toLowerCase()}`;
    card.append(
      makeTextElement("span", "mission-tool-status", `${missionReferenceLabel(activity.robotId || "当前机器人")} · ${activity.status === "CONFIRMED" ? "执行完成" : activity.statusText || "等待反馈"}`),
      makeTextElement("strong", "", localActivityDisplayName(activity) || "机器人能力"),
      makeTextElement("p", "", navigation ? navigationCompletionText(navigation) : activity.purpose || "机器人正在执行相关步骤。"),
    );
    const target = localTargetDescription(activity.safeArguments);
    if (target) card.append(makeTextElement("p", "mission-target", target));
    const argumentLine = makeTextElement("div", "mission-safe-arguments", "");
    for (const [name, value] of Object.entries(activity.safeArguments || {})) {
      if (/password|secret|token|bearer|credential|private|api[_-]?key/i.test(name)) continue;
      argumentLine.append(makeTextElement("span", "", `${fleetArgumentLabels[name] || "任务信息"}：${missionReferenceLabel(value)}`));
    }
    card.append(argumentLine);
    if (["CONFIRMED", "FAILED"].includes(activity.status)) {
      if (record) card.append(makeTextElement("span", "mission-evidence", localEvidenceIsCommandObservation(record) ? `已保存${localEvidenceSourceLabel(record)}` : "已保存执行后观测"));
    } else if (activity.evidenceText) card.append(makeTextElement("span", "mission-evidence", activity.evidenceText));
    if (record) card.append(localEvidenceButton(record));
    list.append(card);
  }
  if (!latestByStep.size) {
    list.append(makeTextElement("p", "", "执行开始后，任务中的每个能力会显示对应调用状态。"));
  }
  renderLocalEvidenceChoices();
  renderLocalEvidenceMetadata(localEvidenceRecords.find(record => record.id === localEvidenceSelectedId));
}

function renderLocalTaskExperience(experience, options = {}) {
  if (experience?.schemaVersion !== "task.experience.v1") return false;
  if (activeTask?.id !== experience.taskId) return false;
  let decision = taskExperienceDecisionForState(experience, localTaskExperienceState);
  if (decision === "gap" && Number(options.resyncRevision) === Number(experience.revision)) decision = "accept";
  if (decision === "gap") {
    if (localUnderstanding) localUnderstanding.textContent = "任务版本有跳跃，正在同步最新任务说明…";
    return false;
  }
  if (decision !== "accept") return false;
  localExperienceLoadStatus = "available";
  localTaskExperienceState = {
    taskId: String(experience.taskId),
    revision: Number(experience.revision),
    aggregateVersion: Number(experience.aggregateVersion || 0),
    cursor: Number(experience.cursor || 0),
  };
  if (localUnderstanding) {
    localUnderstanding.textContent = missionReferenceLabel(experience.understanding || experience.originalRequest || "系统正在理解任务");
  }
  renderLocalMissionSteps(experience.steps);
  renderLocalMissionActivities(experience.activities);
  localRecoveryGuidance = experience.recovery || null;
  renderLocalRecovery(localRecovery);
  renderLocalProfessional(experience);
  return true;
}

function renderLocalProfessional(experience) {
  if (experience?.taskId !== activeTask?.id) return;
  const professional = experience.professional || {};
  const seen = new Set();
  const activities = [];
  for (const item of professional.activities || []) {
    const entry = {
      toolName: item.toolName, commandId: item.commandId, catalogRevision: item.catalogRevision,
      taskRevision: item.taskRevision, aggregateVersion: item.aggregateVersion, evidenceIds: item.evidenceIds || [],
      policy: item.policy ? { policyId: item.policy.policyId, framework: item.policy.framework, inferenceId: item.policy.inferenceId, observationId: item.policy.observationId, manifestRevision: item.policy.manifestRevision } : undefined,
    };
    const key = JSON.stringify(entry);
    if (!seen.has(key)) { seen.add(key); activities.push(entry); }
  }
  $("#local-trace-version").textContent = `任务版本 ${experience.revision || "—"} · 聚合版本 ${experience.aggregateVersion || "—"}`;
  $("#local-professional-trace").textContent = JSON.stringify({
    taskId: experience.taskId, revision: experience.revision, aggregateVersion: experience.aggregateVersion,
    stepEvidence: (professional.stepEvidence || []).map(item => ({ stepId: item.stepId, evidenceIds: item.evidenceIds || [] })),
    activities,
  }, null, 2);
}

async function loadLocalTaskExperience(taskId, options = {}) {
  if (!taskId || activeTask?.id !== taskId) return false;
  const requestGeneration = ++localExperienceRequestGeneration;
  const isCurrent = () => requestGeneration === localExperienceRequestGeneration && activeTask?.id === taskId;
  try {
    const response = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}/experience`);
    if (!isCurrent()) return false;
    if (!response.ok) {
      if (localTaskExperienceState.revision === 0) {
        localExperienceLoadStatus = response.status === 404 ? "unavailable" : "failed";
        renderPlanSteps(activeTask.plan?.plans || [], activeTask);
      }
      return false;
    }
    const experience = await response.json();
    if (!isCurrent() || experience.taskId !== taskId) return false;
    const decision = taskExperienceDecisionForState(experience, localTaskExperienceState);
    if (decision === "gap" && options.resyncRevision == null) {
      const history = await fetch(`/v1/tasks/${encodeURIComponent(taskId)}/revisions`);
      if (!history.ok || !isCurrent()) return false;
      const record = await history.json();
      if (!isCurrent()) return false;
      return loadLocalTaskExperience(taskId, { resyncRevision: Number(record.currentRevision || 0) });
    }
    return renderLocalTaskExperience(experience, options);
  } catch (_) {
    if (isCurrent() && localUnderstanding && localTaskExperienceState.revision === 0) {
      localExperienceLoadStatus = "failed";
      renderPlanSteps(activeTask.plan?.plans || [], activeTask);
      localUnderstanding.textContent = "任务说明暂时无法同步，前端先展示原始计划。";
    }
    return false;
  }
}

function renderTaskExperience(experience, options = {}) {
  if (experience?.schemaVersion !== "task.experience.v1") return false;
  let decision = taskExperienceDecision(experience);
  if (decision === "gap" && Number(options.resyncRevision) === Number(experience.revision)) decision = "accept";
  if (decision === "gap") {
    $("#fleet-task-experience-status").textContent = "发现任务版本跳跃，正在重新同步完整记录。";
    return false;
  }
  if (decision !== "accept") return false;
  fleetTaskExperienceState = {
    taskId: String(experience.taskId), revision: Number(experience.revision),
    aggregateVersion: Number(experience.aggregateVersion || 0), cursor: Number(experience.cursor || 0),
  };
  globalThis.TangyingConsoleUI?.update({ task: { ...selectedFleetTask, id: experience.taskId, revision: experience.revision } });
  $("#fleet-mission-headline").textContent = experience.headline || "当前任务";
  $("#fleet-mission-revision").textContent = `第 ${experience.revision} 版`;
  $("#fleet-mission-understanding").textContent = experience.understanding || experience.originalRequest || "系统正在理解任务";
  const terminalStatus = experience.updateStatus === "ACTIVE" && selectedFleetTask?.id === experience.taskId
    ? { SUCCEEDED: "任务已完成", FAILED: "任务未完成，请查看原因", CANCELLED: "任务已取消" }[selectedFleetTask.state]
    : "";
  const statusText = terminalStatus || revisionStatusText(experience.updateStatus);
  $("#fleet-mission-update-state").textContent = statusText;
  $("#fleet-task-experience-status").textContent = statusText;
  const journey = $("#fleet-update-journey");
  journey.replaceChildren();
  for (const message of experience.updateJourney || []) {
    journey.append(makeTextElement("li", "", message));
  }
  journey.hidden = !(experience.updateJourney || []).length;
  renderMissionSteps(experience.steps);
  renderMissionActivities(
    experience.activities,
    experience.professional?.activities,
    experience.professional?.stepEvidence,
  );
  renderMissionRecovery(experience.recovery);
  fleetTaskUpdateAllowed = (experience.allowedActions || []).includes("update");
  updateFleetRevisionControls();
  return true;
}

async function loadFleetTaskExperience(taskId, options = {}) {
  if (!taskId) return false;
  const requestGeneration = ++fleetExperienceRequestGeneration;
  const selectionGeneration = Number(options.selectionGeneration ?? fleetTaskSelectionGeneration);
  const isCurrent = () => requestGeneration === fleetExperienceRequestGeneration &&
    selectionGeneration === fleetTaskSelectionGeneration;
  if (!isCurrent()) return false;
  try {
    const response = await fleetAPI(`/v1/tasks/${encodeURIComponent(taskId)}/experience`);
    if (!isCurrent()) return false;
    if (response.status === 401) {
      fleetLogout();
      return false;
    }
    if (!response.ok) return false;
    const experience = await response.json();
    if (!isCurrent()) return false;
    let decision = taskExperienceDecision(experience);
    if (decision === "gap" && Number(options.resyncRevision) === Number(experience.revision)) decision = "accept";
    if (decision === "gap" && !fleetTaskExperienceResyncing) {
      fleetTaskExperienceResyncing = true;
      $("#fleet-task-experience-status").textContent = "正在补齐任务更新记录，请稍候。";
      try {
        const history = await fleetAPI(`/v1/tasks/${encodeURIComponent(taskId)}/revisions`);
        if (!history.ok || !isCurrent()) return false;
        const record = await history.json();
        if (!isCurrent()) return false;
        return await loadFleetTaskExperience(taskId, {
          resyncRevision: Number(record.currentRevision || 0), selectionGeneration,
        });
      } finally {
        fleetTaskExperienceResyncing = false;
      }
    }
    return renderTaskExperience(experience, { resyncRevision: options.resyncRevision });
  } catch (_) {
    if (isCurrent()) $("#fleet-task-experience-status").textContent = "任务说明暂时无法同步，机器人安全状态不受影响。";
    return false;
  }
}

function renderTaskRevisionPreview(experience) {
  const preview = $("#fleet-update-preview");
  preview.replaceChildren();
  preview.hidden = false;
  preview.append(makeTextElement("strong", "", `机器人对新要求的理解：${experience.understanding || experience.headline || "等待确认"}`));
  const groups = [
    ["retained", "保持不变"], ["changed", "会改变"], ["added", "会新增"], ["paused", "会暂停"],
  ];
  for (const [key, label] of groups) {
    const values = experience.changePreview?.[key] || [];
    if (!values.length) continue;
    const group = document.createElement("div");
    group.className = "mission-change-group";
    group.append(makeTextElement("strong", "", label));
    for (const value of values) {
      const chip = makeTextElement("span", `mission-change-chip ${key}`, value);
      chip.dataset.changeKind = key;
      group.append(chip);
    }
    preview.append(group);
  }
}

function createIdempotencyKey() {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = Array.from({ length: 16 }, () => Math.floor(Math.random() * 256));
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = bytes.map((value) => value.toString(16).padStart(2, "0")).join("");
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

function updateFleetRevisionControls() {
  const request = $("#fleet-update-request").value.trim();
  const canUpdate = Boolean(selectedFleetTask && fleetTaskExperienceState.taskId === selectedFleetTask.id);
  $("#fleet-revision-preview").disabled = !fleetTaskUpdateAllowed || !canUpdate || !request;
}

function editFleetTaskRevision() {
  fleetPendingTaskRevision = null;
  $("#fleet-update-preview").hidden = true;
  $("#fleet-update-preview").replaceChildren();
  $("#fleet-revision-confirm").disabled = true;
  $("#fleet-revision-edit").disabled = true;
  $("#fleet-update-request").focus();
  updateFleetRevisionControls();
}

async function proposeFleetTaskRevision() {
  const task = selectedFleetTask;
  const request = $("#fleet-update-request").value.trim();
  if (!task || !request || fleetTaskExperienceState.taskId !== task.id) return false;
  const status = $("#fleet-task-experience-status");
  status.textContent = "正在理解新的要求，不会立即改变机器人动作。";
  try {
    const response = await fleetAPI(`/v1/tasks/${encodeURIComponent(task.id)}/revisions`, {
      method: "POST",
      body: JSON.stringify({ expectedRevision: fleetTaskExperienceState.revision, request, idempotencyKey: createIdempotencyKey() }),
    });
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 409 && body.code === "REVISION_CONFLICT") {
        status.textContent = "任务已被其他操作更新，请查看最新变化。你的文字已保留。";
        return false;
      }
      status.textContent = body.message || "暂时无法预览任务更新，请稍后重试。";
      return false;
    }
    fleetPendingTaskRevision = {
      taskId: task.id,
      revision: Number(body.proposal?.revision?.revision || body.experience?.revision || 0),
      expectedCurrentRevision: Number(body.currentRevision || fleetTaskExperienceState.revision),
    };
    renderTaskRevisionPreview(body.experience || {});
    $("#fleet-revision-confirm").disabled = false;
    $("#fleet-revision-edit").disabled = false;
    $("#fleet-revision-preview").disabled = true;
    status.textContent = "请核对哪些内容保持不变、改变、新增或暂停，再确认更新。";
    $("#fleet-revision-confirm").focus();
    return true;
  } catch (_) {
    status.textContent = "任务更新预览暂时不可用，你的文字已保留。";
    return false;
  }
}

async function confirmFleetTaskRevision() {
  const pending = fleetPendingTaskRevision;
  if (!pending || !selectedFleetTask || pending.taskId !== selectedFleetTask.id) return false;
  const status = $("#fleet-task-experience-status");
  $("#fleet-revision-confirm").disabled = true;
  try {
    const response = await fleetAPI(`/v1/tasks/${encodeURIComponent(pending.taskId)}/revisions/${pending.revision}/confirm`, {
      method: "POST",
      body: JSON.stringify({ expectedCurrentRevision: pending.expectedCurrentRevision, idempotencyKey: createIdempotencyKey() }),
    });
    const body = await response.json();
    if (!response.ok) {
      if (response.status === 409 && body.code === "REVISION_CONFLICT") {
        status.textContent = "任务已被其他操作更新，请查看最新变化。这次预览没有执行。";
      } else {
        status.textContent = body.message || "确认更新失败，机器人仍按当前任务安全运行。";
      }
      $("#fleet-revision-confirm").disabled = false;
      return false;
    }
    const waiting = body.revision?.status === "WAITING_SAFE_POINT";
    status.textContent = waiting
      ? "机器人会先完成手上的安全动作，再按新任务继续"
      : "任务更新已生效，正在同步机器人执行进度。";
    fleetPendingTaskRevision = null;
    $("#fleet-update-preview").hidden = true;
    $("#fleet-revision-edit").disabled = true;
    await loadFleetTaskExperience(pending.taskId);
    if (waiting) status.textContent = "机器人会先完成手上的安全动作，再按新任务继续";
    return true;
  } catch (_) {
    status.textContent = "确认结果暂时未知。系统不会重复执行，请刷新任务状态后再操作。";
    $("#fleet-revision-confirm").disabled = false;
    return false;
  }
}

async function createFleetTask() {
  const createButton = $("#fleet-create");
  if (createButton.disabled) return;
  const request = $("#fleet-request").value.trim();
  const message = $("#fleet-task-id");
  if (!request) {
    message.textContent = "请输入任务描述";
    globalThis.TangyingConsoleUI?.feedback("请先描述希望机器人完成的任务。", true);
    return;
  }
  createButton.disabled = true;
  createButton.textContent = "正在创建…";
  try {
    const response = await fleetAPI("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({ request, adapter: fleetExecutionAdapter }),
    });
    const task = await response.json();
    if (!response.ok) {
      message.textContent = `${task.code || "ERROR"}: ${task.message || "创建失败"}`;
      globalThis.TangyingConsoleUI?.feedback(task.message || "任务没有创建成功，请检查描述后重试。", true);
      return;
    }
    message.textContent = `已创建 ${task.id}，等待审批`;
    globalThis.TangyingConsoleUI?.feedback("任务已创建，正在请求开始执行。");
    await fleetSelectTask(task);
    const approved = await fleetTaskAction("approve", task);
    if (!approved) {
      $("#fleet-task-experience-status").textContent = "任务已经创建，但机器人暂时不能开始。请检查机器人在线和安全状态后重试批准。";
    }
    await pollFleetTasks();
  } catch (_) {
    message.textContent = "创建任务失败";
    globalThis.TangyingConsoleUI?.feedback("未能连接服务，任务创建结果尚未确认。请查看任务记录后再重试。", true);
  } finally {
    createButton.disabled = false;
    createButton.textContent = "创建并开始任务";
  }
}

async function fleetTaskAction(action, taskOverride = null) {
  const task = taskOverride || selectedFleetTask;
  if (!task) return false;
  try {
    const response = await fleetAPI(`/v1/tasks/${task.id}/${action}`, { method: "POST" });
    if (!response.ok) {
      $("#fleet-approve").disabled = false;
      globalThis.TangyingConsoleUI?.feedback(action === "cancel" ? "取消尚未得到服务确认，请检查任务状态。" : "暂时不能开始，请检查机器人连接与安全状态。", true);
      return false;
    }
    selectedFleetTask = await response.json();
    globalThis.TangyingConsoleUI?.feedback(action === "cancel" ? "取消请求已确认，请留意机器人是否停止。" : "任务已开始，可在下方查看进展。");
    $("#fleet-approve").disabled = true;
    await fleetSelectTask(selectedFleetTask);
    return true;
  } catch (_) {
    $("#fleet-approve").disabled = false;
    globalThis.TangyingConsoleUI?.feedback(action === "cancel"
      ? "连接中断，取消结果尚未确认。请查看任务状态；如有危险，请使用实体急停。"
      : "连接中断，开始执行的结果尚未确认。请查看任务记录后再操作。", true);
    void pollFleetTasks();
    return false;
  }
}

async function pollFleetTelemetry() {
  const robotID = $("#fleet-telemetry-robot").value;
  try {
    const response = await fleetAPI(`/v1/telemetry?robot_id=${encodeURIComponent(robotID)}&limit=20`);
    if (!response.ok) return;
    const payload = await response.json();
    if (robotID === "") {
      const select = $("#fleet-telemetry-robot");
      const current = select.value;
      select.replaceChildren();
      const all = document.createElement("option");
      all.value = "";
      all.textContent = "全部";
      select.append(all);
      for (const id of payload.robots || []) {
        const option = document.createElement("option");
        option.value = id;
        option.textContent = id;
        select.append(option);
      }
      select.value = current || "";
      $("#fleet-telemetry-meta").textContent = `${(payload.robots || []).length} 台机器人上报遥测`;
      return;
    }
    const latest = payload.latest;
    $("#fleet-telemetry-meta").textContent = `${payload.robotId} · 轨迹 ${(payload.trajectory || []).length} 点`;
    $("#fleet-telemetry").textContent = latest
      ? JSON.stringify({
          robotId: latest.robotId,
          observedAt: latest.observedAt,
          pose: latest.pose,
          activity: latest.activity,
          emergencyStopped: latest.emergencyStopped,
          anomalies: latest.anomalies,
          entities: latest.entities || [],
          occupancy: latest.occupancy
            ? `${latest.occupancy.width}×${latest.occupancy.height} @ ${latest.occupancy.cellSizeM}m`
            : null,
          state: latest.state || {},
        }, null, 2)
      : "该机器人尚无遥测";
  } catch (_) {
    // best-effort
  }
}

function startLocalMode() {
  localModeStarted = true;
  cameraPageWasVisible = scenePageVisible();
  globalThis.TangyingConsoleUI?.update({ mode: "local" });
  pollTelemetry();
  pollMetrics();
  pollRuntime();
  loadLLMConfig();
  void loadLocalTasks({ openLatest: true });
  void pollLocalWorld();
  setInterval(pollTelemetry, 1000);
  setInterval(pollRuntime, 3000);
  setInterval(pollMetrics, 5000);
  setInterval(pollLocalTask, 2000);
  setInterval(() => { if (pageVisible("tasks")) void loadLocalTasks(); }, 5000);
  setInterval(pollLocalWorld, 3000);
  setInterval(() => { if (scenePageVisible()) renderSceneFrameStats(); }, 250);
}

function renderServiceRequired(url) {
  const entry = $("#open-service-console");
  entry.href = url;
  entry.hidden = false;
  $("#scene-frame-message").textContent = `Runtime/Fleet 数据由 HTTP 服务提供。请打开 ${url}`;
  $("#connection-text").textContent = "SERVICE REQUIRED";
  sceneLiveState.textContent = "SERVICE REQUIRED";
  sceneLiveState.className = "scene-state stale";
  sceneStage.className = "scene-stage stale";
  document.body.classList.add("service-required");
}

// Resolve the deployment mode before starting either polling loop.  Fleet
// routes require an operator token, so Local Brain requests must never leak
// into a cloud page during the login window.
async function bootApplication() {
  if (location.protocol === "file:") {
    renderServiceRequired("http://127.0.0.1:18080/");
    return "file";
  }
  if (await detectFleetMode()) {
    await initFleetMode();
    return "fleet";
  }
  startLocalMode();
  return "local";
}

void bootApplication();
