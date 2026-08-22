const $ = (id) => document.querySelector(id);
const requestInput = $("#request");
const adapterInput = $("#adapter");
const stateLabel = $("#state");
const taskLabel = $("#task-id");
const eventList = $("#events");
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
let frameObjectURL = null;
let pendingFrameObjectURL = null;
let telemetryGeneration = 0;
let telemetryController = null;
let sceneViewMode = "live";
const sceneCamera = { yaw: 0.6, pitch: 0.42, distance: 2.4, target: [0, 0.4, 0.7] };
let orbitDragging = false;
let orbitLastX = 0;
let orbitLastY = 0;
const lastObservedAtByAdapter = new Map();
const discoveredAdapters = new Set();
const trails = new Map();
let fleetAcceptanceFrameSamplingStarted = false;

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
adapterInput.addEventListener("change", () => {
  invalidateTelemetryPolling();
  lastObservedAtByAdapter.delete(adapterInput.value);
  latestTelemetry = null;
  clearSceneFrame("已切换适配器，等待新观测");
  renderTelemetry(null);
  void pollTelemetry();
});
$("#save-llm").addEventListener("click", saveLLMConfig);
$("#view-live").addEventListener("click", () => setSceneViewMode("live"));
$("#view-orbit").addEventListener("click", () => setSceneViewMode("orbit"));
$("#reset-view").addEventListener("click", resetSceneCamera);
canvas.addEventListener("pointerdown", (event) => {
  orbitDragging = true;
  orbitLastX = event.clientX;
  orbitLastY = event.clientY;
  canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener("pointermove", (event) => {
  if (!orbitDragging || sceneViewMode !== "orbit") return;
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
  if (sceneViewMode !== "orbit") return;
  event.preventDefault();
  sceneCamera.distance = Math.min(5, Math.max(0.6, sceneCamera.distance + event.deltaY * 0.001));
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
  const response = await fetch("/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request: requestInput.value, adapter: adapterInput.value }),
  });
  const body = await response.json();
  if (!response.ok) {
    stateLabel.textContent = `${body.code || "ERROR"}: ${body.message || "请求失败"}`;
    return;
  }
  activeTask = body;
  renderTask(body);
  connectEvents(body.id);
}

async function taskAction(action) {
  if (!activeTask) return;
  const response = await fetch(`/v1/tasks/${activeTask.id}/${action}`, { method: "POST" });
  if (!response.ok) return;
  activeTask = await response.json();
  renderTask(activeTask);
}

function connectEvents(taskId) {
  if (socket) socket.close();
  eventList.replaceChildren();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/v1/tasks/${taskId}/events/ws`);
  socket.addEventListener("open", () => setConnection(true));
  socket.addEventListener("message", (message) => {
    const event = JSON.parse(message.data);
    appendEvent(event);
    if (event.type === "STATE_CHANGED") refreshTask(taskId);
    if (event.type === "LOCAL_RUN_SUCCEEDED" && event.payload?.completedSteps) {
      $("#completed-count").textContent = event.payload.completedSteps.length;
    }
  });
  socket.addEventListener("close", () => setConnection(false));
}

function setConnection(online) {
  connectionLabel.textContent = online ? "实时连接" : "连接关闭";
  connectionLabel.classList.toggle("online", online);
}

function setRuntimeConnection(online) {
  $("#connection-dot").classList.toggle("online", online);
  $("#connection-text").textContent = online ? "Runtime 在线" : "Runtime 离线";
}

function appendEvent(event) {
  const item = document.createElement("li");
  const time = document.createElement("time");
  time.textContent = event.sequence ?? "–";
  const content = document.createElement("div");
  const title = document.createElement("strong");
  title.textContent = event.type || event.code || "EVENT";
  const detail = document.createElement("p");
  detail.textContent = event.message || event.stepId || "";
  content.append(title, detail);
  item.append(time, content);
  eventList.append(item);
  eventList.scrollTop = eventList.scrollHeight;
}

async function refreshTask(taskId) {
  const response = await fetch(`/v1/tasks/${taskId}`);
  if (response.ok) {
    activeTask = await response.json();
    renderTask(activeTask);
  }
}

function renderTask(task) {
  stateLabel.textContent = task.state;
  taskLabel.textContent = task.id;
  approveButton.disabled = task.approved || ["SUCCEEDED", "CANCELLED", "FAILED"].includes(task.state);
  cancelButton.disabled = ["SUCCEEDED", "CANCELLED", "FAILED"].includes(task.state);
  const source = task.plan?.source || "deterministic";
  $("#plan-source").textContent = source === "llm_consensus" ? "LLM consensus" : source;
  const intents = task.intent?.sequence?.length ? task.intent.sequence : task.intent ? [task.intent] : [];
  $("#subtask-count").textContent = intents.length;
  renderPlanSteps(task.plan?.plans || []);
}

function renderPlanSteps(plans) {
  const chips = [];
  plans.forEach((plan, planIndex) => {
    plan.steps?.forEach((step) => {
      chips.push(`${planIndex + 1}.${step.id} ${step.skill}`);
    });
  });
  if (!chips.length) return;
}

async function pollTelemetry() {
  const adapter = adapterInput.value;
  const poll = beginTelemetryPoll(adapter);
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
      const previousObservedAt = lastObservedAtByAdapter.get(selected);
      if (previousObservedAt != null && observedAt <= previousObservedAt) return;
      lastObservedAtByAdapter.set(selected, observedAt);
      poll.observedAt = observedAt;
      if (!isCurrentTelemetryPoll(poll)) return;
      renderTelemetry(payload.latest);
      await updateSceneFrame(payload.latest, poll);
      if (!isCurrentTelemetryPoll(poll)) return;
    }
    else if (!latestTelemetry) {
      clearSceneFrame(`${selected || "Robot Runtime"} 已连接，尚无场景观测`);
      renderTelemetry(null);
    }
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentTelemetryPoll(poll)) return;
    handleTelemetryFailure("遥测连接中断");
  }
}

function beginTelemetryPoll(adapter) {
  invalidateTelemetryPolling();
  telemetryGeneration += 1;
  telemetryController = new AbortController();
  return {
    adapter,
    controller: telemetryController,
    generation: telemetryGeneration,
    observedAt: null,
  };
}

function invalidateTelemetryPolling() {
  telemetryGeneration += 1;
  if (telemetryController) telemetryController.abort();
  telemetryController = null;
  discardPendingFrame();
}

function isCurrentTelemetryPoll(poll) {
  if (!poll || poll.controller.signal.aborted) return false;
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
  if (latestTelemetry) {
    setSceneVisualState("STALE", `${message}，保留最后一次场景观测`);
    return;
  }
  clearSceneFrame(`${message}，尚无可用场景观测`);
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

function renderTelemetry(snapshot) {
  latestTelemetry = snapshot;
  $("#telemetry-time").textContent = snapshot ? new Date(snapshot.observedAt).toLocaleString() : "等待遥测";
  $("#activity").textContent = snapshot?.activity || "—";
  $("#mode").textContent = snapshot?.mode || "—";
  $("#robot-id").textContent = snapshot?.robotId || "—";
  $("#telemetry-adapter").textContent = snapshot?.adapter || "—";
  $("#software-version").textContent = snapshot?.softwareVersion || "—";
  $("#runtime-activity").textContent = `activity: ${snapshot?.activity || "—"}`;
  const estop = $("#estop-state");
  estop.textContent = snapshot?.emergencyStopped ? "已锁存" : "安全";
  estop.style.color = snapshot?.emergencyStopped ? "var(--danger)" : "var(--mint)";
  const anomalies = snapshot?.anomalies || [];
  $("#anomalies").textContent = anomalies.length ? `异常: ${anomalies.join(" / ")}` : "";
  const robotState = snapshot?.robotState || {};
  const robot = findRobotEntity(snapshot?.entities || []);
  updateSceneIdentity(snapshot, robot);
  $("#held-object").textContent = robotState.held || "—";
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
          robotState: snapshot.robotState || {},
          entities: snapshot.entities || [],
        },
        null,
        2,
      )
    : "等待 Local Agent 上报遥测…";
  renderScene(snapshot);
}

function renderScene(snapshot) {
  const entities = snapshot?.entities || [];
  $("#entity-count").textContent = `${entities.length} entities`;
  const list = $("#entity-list");
  list.replaceChildren();
  entities.forEach((entity) => {
    const chip = document.createElement("span");
    chip.className = "entity-chip";
    chip.textContent = `${entity.category}:${entity.attributes?.color || entity.entityId || "?"}`;
    list.append(chip);
  });
  drawScene(entities, snapshot?.robotState || {}, snapshot);
}

async function updateSceneFrame(snapshot, poll) {
  const requestedAdapter = poll.adapter;
  if (snapshot.adapter !== requestedAdapter || !isCurrentTelemetryPoll(poll)) return;
  // In orbit mode the live PNG is intentionally hidden; do not let a late
  // frame callback force the view back to the realtime image.
  if (sceneViewMode !== "live") return;
  const observedAt = Date.parse(snapshot.observedAt || "");
  const age = Number.isFinite(observedAt) ? Date.now() - observedAt : Infinity;
  const identity = sceneIdentity(snapshot, findRobotEntity(snapshot.entities || []));
  setSceneVisualState(
    age <= 3000 ? "LIVE" : "STALE",
    age <= 3000 ? `${identity.adapter} 实时场景画面` : `显示 ${identity.adapter} 最近一次场景画面`,
  );
  try {
    const response = await fetch(`/v1/scene/frame?adapter=${encodeURIComponent(requestedAdapter)}&t=${Date.now()}`, {
      cache: "no-store",
      signal: poll.controller.signal,
    });
    if (!isCurrentTelemetryPoll(poll)) return;
    if (!response.ok) {
      clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧不可用，已切换语义俯视图`);
      return;
    }
    const blob = await response.blob();
    if (!isCurrentTelemetryPoll(poll)) return;
    const nextURL = URL.createObjectURL(blob);
    if (!isCurrentTelemetryPoll(poll)) {
      URL.revokeObjectURL(nextURL);
      return;
    }
    pendingFrameObjectURL = nextURL;
    sceneFrame.onload = () => {
      if (!isCurrentTelemetryPoll(poll) || pendingFrameObjectURL !== nextURL) {
        releasePendingFrame(nextURL);
        return;
      }
      if (frameObjectURL) URL.revokeObjectURL(frameObjectURL);
      frameObjectURL = nextURL;
      pendingFrameObjectURL = null;
      sceneFrame.hidden = false;
      canvas.hidden = true;
    };
    sceneFrame.onerror = () => {
      releasePendingFrame(nextURL);
      if (isCurrentTelemetryPoll(poll)) {
        clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧解码失败，已切换语义俯视图`);
      }
    };
    sceneFrame.src = nextURL;
  } catch (error) {
    if (error?.name === "AbortError" || !isCurrentTelemetryPoll(poll)) return;
    clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧连接失败，已切换语义俯视图`);
  }
}

function clearSceneFrame(message) {
  discardPendingFrame();
  sceneFrame.onload = null;
  sceneFrame.onerror = null;
  sceneFrame.removeAttribute("src");
  sceneFrame.hidden = true;
  canvas.hidden = false;
  if (frameObjectURL) URL.revokeObjectURL(frameObjectURL);
  frameObjectURL = null;
  setSceneVisualState("UNAVAILABLE", message);
}

function discardPendingFrame() {
  if (!pendingFrameObjectURL) return;
  const pendingURL = pendingFrameObjectURL;
  pendingFrameObjectURL = null;
  sceneFrame.onload = null;
  sceneFrame.onerror = null;
  if (sceneFrame.src === pendingURL) {
    if (frameObjectURL) sceneFrame.src = frameObjectURL;
    else sceneFrame.removeAttribute("src");
  }
  URL.revokeObjectURL(pendingURL);
}

function releasePendingFrame(url) {
  if (pendingFrameObjectURL !== url) return;
  pendingFrameObjectURL = null;
  if (sceneFrame.src === url) sceneFrame.removeAttribute("src");
  URL.revokeObjectURL(url);
}

function setSceneVisualState(state, message) {
  const normalized = state.toLowerCase();
  sceneLiveState.textContent = state;
  sceneLiveState.className = `scene-state ${normalized}`;
  sceneStage.className = `scene-stage ${normalized}`;
  $("#scene-frame-message").textContent = message;
}

function shortRevision(revision) {
  if (!revision) return "—";
  return String(revision).slice(0, 10);
}

function findRobotEntity(entities) {
  return entities.find((entity) => entity.category === "robot")
    || entities.find((entity) => entity.entityId === "xlerobot");
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
  $("#scene-source-label").textContent = `${identity.adapter} / 1 HZ`;
  sceneFrame.alt = `${identity.robot} 通过 ${identity.adapter} 提供的实时场景画面`;
  canvas.setAttribute("aria-label", `${identity.robot} 通过 ${identity.adapter} 提供的语义场景俯视图`);
}

function setSceneViewMode(mode) {
  sceneViewMode = mode;
  $("#view-live").classList.toggle("active", mode === "live");
  $("#view-orbit").classList.toggle("active", mode === "orbit");
  if (mode === "orbit") {
    sceneFrame.hidden = true;
    canvas.hidden = false;
    setSceneVisualState("LIVE", "自由视角 3D：拖拽旋转、滚轮缩放");
    if (latestTelemetry) renderScene(latestTelemetry);
  } else if (latestTelemetry) {
    renderScene(latestTelemetry);
    void updateSceneFrame(latestTelemetry, { adapter: latestTelemetry.adapter, controller: { signal: new AbortController().signal }, generation: telemetryGeneration, observedAt: latestTelemetry.observedAt });
  }
}

function resetSceneCamera() {
  Object.assign(sceneCamera, { yaw: 0.6, pitch: 0.42, distance: 2.4, target: [0, 0.4, 0.7] });
  if (sceneViewMode === "orbit" && latestTelemetry) renderScene(latestTelemetry);
}

function drawScene(entities, robotState, snapshot) {
  if (sceneViewMode === "orbit") {
    drawScene3D(entities, robotState, snapshot);
    return;
  }
  drawScene2D(entities, robotState, snapshot);
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

function drawScene3D(entities, robotState, snapshot) {
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  context.fillStyle = "#07120f";
  context.fillRect(0, 0, width, height);
  context.strokeStyle = "rgba(143,255,196,0.12)";
  context.lineWidth = 1;
  for (let i = -8; i <= 8; i += 1) {
    const a = projectScenePoint([i * 0.1, -0.5, 0], width, height);
    const b = projectScenePoint([i * 0.1, 1.4, 0], width, height);
    if (a && b) { context.beginPath(); context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.stroke(); }
  }
  for (let i = -5; i <= 14; i += 1) {
    const a = projectScenePoint([-0.8, i * 0.1, 0], width, height);
    const b = projectScenePoint([0.8, i * 0.1, 0], width, height);
    if (a && b) { context.beginPath(); context.moveTo(a[0], a[1]); context.lineTo(b[0], b[1]); context.stroke(); }
  }

  // Table and robot chassis as simple semantic bodies.
  drawBox3D([0, 0.65, 0.68], [0.84, 0.78, 0.1], "#5a4530", width, height);
  drawBox3D([0, 0.2, 0.35], [0.45, 0.45, 0.5], "#335a4a", width, height);
  // IKEA RÅSKOG cart version: official XLeRobot is mounted on the cart.
  drawBox3D([0, 0, 0.05], [0.5, 0.72, 0.06], "#c9c9c9", width, height);
  drawBox3D([0, 0, -0.25], [0.44, 0.64, 0.05], "#b3b3b3", width, height);
  drawBox3D([0, 0, -0.5], [0.4, 0.56, 0.05], "#9e9e9e", width, height);
  drawBox3D([-0.21, -0.32, -0.25], [0.04, 0.04, 1.0], "#a0a0a0", width, height);
  drawBox3D([0.21, -0.32, -0.25], [0.04, 0.04, 1.0], "#a0a0a0", width, height);
  drawBox3D([-0.21, 0.32, -0.25], [0.04, 0.04, 1.0], "#a0a0a0", width, height);
  drawBox3D([0.21, 0.32, -0.25], [0.04, 0.04, 1.0], "#a0a0a0", width, height);
  drawHeadCameraMarker(width, height);
  drawCartDepthCameraMarker(width, height);

  const robot = findRobotEntity(entities);
  for (const entity of entities) {
    if (entity.category === "environment" || entity === robot) continue;
    const position = entity.pose && entity.pose.length >= 3 ? entity.pose : [0, 0.5, 0.8];
    const color = entityColor(entity);
    const size = entity.category === "block" ? 0.07 : 0.08;
    drawBox3D(position, [size, size, entity.category === "bottle" ? 0.16 : 0.12], color, width, height);
  }
  if (robot?.pose?.length >= 3) drawBox3D(robot.pose, [0.5, 0.5, 0.55], "#4aa3df", width, height);
  context.fillStyle = "#8fffc4";
  context.font = "bold 12px ui-monospace, monospace";
  context.fillText("自由视角 3D · 官方 XLeRobot + IKEA RÅSKOG 置物推车", 16, 24);
}

function drawBox3D(center, size, color, width, height) {
  const [cx, cy, cz] = center;
  const [sx, sy, sz] = size;
  const corners = [
    [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
    [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
  ].map((c) => [cx + c[0] * sx / 2, cy + c[1] * sy / 2, cz + c[2] * sz / 2]);
  const projected = corners.map((point) => projectScenePoint(point, width, height));
  if (projected.some((point) => point === null)) return;
  const edges = [
    [0, 1], [1, 2], [2, 3], [3, 0],
    [4, 5], [5, 6], [6, 7], [7, 4],
    [0, 4], [1, 5], [2, 6], [3, 7],
  ];
  context.strokeStyle = color;
  context.lineWidth = 1.5;
  for (const [a, b] of edges) {
    context.beginPath();
    context.moveTo(projected[a][0], projected[a][1]);
    context.lineTo(projected[b][0], projected[b][1]);
    context.stroke();
  }
  context.fillStyle = color;
  context.globalAlpha = 0.25;
  context.beginPath();
  corners.forEach((_c, index) => {
    const point = projected[index];
    if (index === 0) context.moveTo(point[0], point[1]);
    else context.lineTo(point[0], point[1]);
  });
  context.closePath();
  context.fill();
  context.globalAlpha = 1;
}

function drawHeadCameraMarker(width, height) {
  // Real XLeRobot head/depth camera is above the chassis and looks forward.
  const head = projectScenePoint([-0.1, 0.15, 1.05], width, height);
  if (!head) return;
  context.fillStyle = "#ffb86b";
  context.beginPath();
  context.arc(head[0], head[1], 5, 0, Math.PI * 2);
  context.fill();
  context.strokeStyle = "#ffb86b";
  context.globalAlpha = 0.4;
  context.beginPath();
  context.moveTo(head[0], head[1]);
  context.lineTo(head[0], head[1] - 24);
  context.stroke();
  context.globalAlpha = 1;
}

function drawCartDepthCameraMarker(width, height) {
  // IKEA RÅSKOG cart version can mount another depth camera on the top
  // cart platform looking down at the front tray and robot workspace.
  const cart = projectScenePoint([0, -0.05, 0.35], width, height);
  if (!cart) return;
  context.fillStyle = "#ffb86b";
  context.beginPath();
  context.arc(cart[0], cart[1], 6, 0, Math.PI * 2);
  context.fill();
  context.strokeStyle = "#ffb86b";
  context.globalAlpha = 0.5;
  context.beginPath();
  context.moveTo(cart[0], cart[1]);
  context.lineTo(cart[0], cart[1] + 32);
  context.stroke();
  context.globalAlpha = 1;
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

  const robot = findRobotEntity(entities);
  for (const entity of entities) {
    if (entity === robot || entity.category === "environment") continue;
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

  if (robot) drawRobotFootprint(robot, toX, toY, robotState, snapshot);
  else drawEmptyRobotState(snapshot);
}

function drawRobotFootprint(entity, toX, toY, robotState, snapshot) {
  const pose = entity.pose || [];
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
  const held = robotState.held ? ` · ${robotState.held}` : "";
  context.fillText(`${robotIdentity(snapshot, entity)}${held}`, centerX, centerY - 28);
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
const fleetWorldVisibility = { models: true, bounds: false, labels: true, path: true };

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
    yaw: 0.76, pitch: 0.62, distance: 6.8, target: [2.75, -1.5, 0.4],
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
  $("#fleet-godview-webgl").hidden = !enabled;
  $("#fleet-godview-canvas").hidden = enabled;
  $("#fleet-world-label-layer").hidden = !enabled;
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
  if (fleetWorldWebGLRenderer && !$("#fleet-godview-webgl").hidden) {
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
    bindFleetWorldToolbar();
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

function initFleetMode() {
  fleetMode = true;
  document.body.classList.add("fleet-mode");
  const view = $("#fleet-view");
  view.hidden = false;
  $("#fleet-login-button").addEventListener("click", fleetLogin);
  $("#fleet-logout").addEventListener("click", fleetLogout);
  $("#fleet-create").addEventListener("click", createFleetTask);
  $("#fleet-approve").addEventListener("click", () => fleetTaskAction("approve"));
  $("#fleet-revision-preview").addEventListener("click", proposeFleetTaskRevision);
  $("#fleet-revision-confirm").addEventListener("click", confirmFleetTaskRevision);
  $("#fleet-revision-edit").addEventListener("click", editFleetTaskRevision);
  $("#fleet-update-request").addEventListener("input", updateFleetRevisionControls);
  $("#fleet-telemetry-robot").addEventListener("change", pollFleetTelemetry);
  renderFleetAuth();
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
  fleetSessionGeneration += 1;
  const socket = fleetWorldSocket;
  fleetWorldSocket = null;
  fleetToken = "";
  fleetOperator = "";
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
    for (const text of cells) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.append(cell);
    }
    row.classList.toggle("offline", !device.online);
    body.append(row);
  }
  $("#fleet-devices-status").textContent = `${(devices || []).length} 台设备`;
}

const fleetFrameURLs = new Map();
let fleetFrameGeneration = 0;

async function pollFleetFrames() {
  try {
    const response = await fleetAPI("/v1/scene/frames");
    if (!response.ok) return;
    const payload = await response.json();
    const frames = payload.frames || [];
    const live = new Set(frames.map((frame) => frame.robotId));
    fleetFrameGeneration += 1;
    const generation = fleetFrameGeneration;
    for (const frame of frames) {
      const image = document.querySelector(`#fleet-frame-${frame.robotId}`);
      if (!image) continue;
      // Fetch the frame bytes as a blob URL; a stale generation is revoked.
      try {
        const frameResponse = await fleetAPI(`/v1/scene/frames/${encodeURIComponent(frame.robotId)}?t=${Date.now()}`, { cache: "no-store" });
        if (!frameResponse.ok || generation !== fleetFrameGeneration) continue;
        const blob = await frameResponse.blob();
        if (generation !== fleetFrameGeneration) {
          URL.revokeObjectURL(blob);
          continue;
        }
        const url = URL.createObjectURL(blob);
        const previous = fleetFrameURLs.get(frame.robotId);
        if (previous) URL.revokeObjectURL(previous);
        fleetFrameURLs.set(frame.robotId, url);
        image.src = url;
        image.classList.remove("stale");
      } catch (_) {
        // best-effort frame refresh
      }
    }
    const selector = [...fleetFrameURLs.keys()];
    for (const [robotID, url] of fleetFrameURLs) {
      if (!live.has(robotID)) {
        URL.revokeObjectURL(url);
        fleetFrameURLs.delete(robotID);
      }
    }
    const status = document.querySelector("#fleet-godview-status");
    if (status) {
      status.textContent = selector.length
        ? `${selector.join(", ")} 实时画面 · ${frames.length ? `${frames.length} 路` : ""}`
        : "等待实时画面…";
    }
  } catch (_) {
    // best-effort
  }
}

async function pollFleetMap() {
  try {
    const response = await fleetAPI("/v1/maps/global");
    if (!response.ok) return;
    const global = await response.json();
    drawFleetMap(global);
    $("#fleet-map-meta").textContent =
      `${(global.robots || []).length} 机器人 · ${(global.entities || []).length} 实体 · ${(global.width || 0)}×${(global.height || 0)} 栅格 @${(global.cellSizeM || 0.1).toFixed(2)}m`;
  } catch (_) {
    // best-effort
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

function drawFleetMap(global) {
  const mapCanvas = document.querySelector("#fleet-map-canvas");
  const mapContext = mapCanvas.getContext("2d");
  const width = mapCanvas.width;
  const height = mapCanvas.height;
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
      mapContext.font = "10px ui-monospace, monospace";
      mapContext.textAlign = "center";
      mapContext.fillText(entity.entityId, x, y - s / 2 - 4);
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
    mapContext.font = "9px ui-monospace, monospace";
    mapContext.textAlign = "center";
    mapContext.fillText(entity.entityId, x, y - Math.max(10, 0.07 * scale) - 2);
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
    mapContext.font = "bold 11px ui-monospace, monospace";
    mapContext.textAlign = "center";
    mapContext.fillText(
      `${robot.robotId}${robot.activity ? ` · ${robot.activity}` : ""}${held}`,
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
    title.textContent = `${task.state} · ${task.id.slice(0, 8)}`;
    const detail = document.createElement("p");
    const robots = task.intent?.sequence?.length
      ? task.intent.sequence.map((intent) => intent.robotId || "any").join(" → ")
      : (task.intent?.robotId || "any");
    detail.textContent = `${task.request}  [${robots}]`;
    content.append(title, detail);
    item.append(content);
    item.addEventListener("click", () => fleetSelectTask(task));
    list.append(item);
  }
}

async function fleetSelectTask(task) {
  const selectionChanged = selectedFleetTask?.id !== task.id;
  selectedFleetTask = task;
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
  "red-block": "红色方块",
  "handoff-zone": "交接区",
  "right-target-zone": "右侧目标区",
  "left-target-zone": "左侧目标区",
};

function missionReferenceLabel(value) {
  return fleetReferenceLabels[String(value)] || String(value);
}

function taskExperienceDecision(experience) {
  const incoming = {
    taskId: String(experience?.taskId || ""),
    revision: Number(experience?.revision || 0),
    aggregateVersion: Number(experience?.aggregateVersion || 0),
    cursor: Number(experience?.cursor || 0),
  };
  if (!incoming.taskId || !Number.isInteger(incoming.revision) || incoming.revision < 1 ||
      !Number.isFinite(incoming.aggregateVersion) || incoming.aggregateVersion < 0) return "invalid";
  const current = fleetTaskExperienceState;
  if (!current.taskId || current.taskId !== incoming.taskId) return "accept";
  if (incoming.revision < current.revision) return "stale";
  if (incoming.revision > current.revision + 1) return "gap";
  if (incoming.revision === current.revision) {
    if (incoming.aggregateVersion < current.aggregateVersion) return "stale";
    if (incoming.aggregateVersion === current.aggregateVersion && incoming.cursor <= current.cursor) return "stale";
  }
  return "accept";
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

function renderMissionActivities(activities, professionalActivities) {
  const list = $("#fleet-tool-activities");
  const professional = $("#fleet-professional-activities");
  list.replaceChildren();
  professional.replaceChildren();
  for (const activity of activities || []) {
    const card = document.createElement("article");
    card.className = `mission-tool-card ${String(activity.status || "waiting").toLowerCase()}`;
    card.append(
      makeTextElement("span", "mission-tool-status", `${activity.robotId || "机器人"} · ${activity.statusText || "等待机器人反馈"}`),
      makeTextElement("strong", "", activity.displayName || "机器人能力"),
      makeTextElement("p", "", activity.purpose || "机器人正在执行当前步骤"),
    );
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
  if (!(activities || []).length) list.append(makeTextElement("p", "", "任务开始后，这里会说明机器人调用了什么能力，以及结果是否被环境确认。"));
  for (const activity of professionalActivities || []) {
    const code = document.createElement("code");
    code.textContent = JSON.stringify(activity, null, 2);
    professional.append(code);
  }
  if (!(professionalActivities || []).length) professional.append(makeTextElement("p", "", "暂无专业活动记录"));
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
  if ((recovery.userActions || []).length) {
    const list = document.createElement("ul");
    for (const action of recovery.userActions) list.append(makeTextElement("li", "", action));
    content.append(list);
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
  $("#fleet-mission-headline").textContent = experience.headline || "当前任务";
  $("#fleet-mission-revision").textContent = `第 ${experience.revision} 版`;
  $("#fleet-mission-understanding").textContent = experience.understanding || experience.originalRequest || "系统正在理解任务";
  $("#fleet-mission-update-state").textContent = revisionStatusText(experience.updateStatus);
  $("#fleet-task-experience-status").textContent = revisionStatusText(experience.updateStatus);
  renderMissionSteps(experience.steps);
  renderMissionActivities(experience.activities, experience.professional?.activities);
  renderMissionRecovery(experience.recovery);
  fleetTaskUpdateAllowed = (experience.allowedActions || []).includes("update");
  updateFleetRevisionControls();
  return true;
}

async function loadFleetTaskExperience(taskId, options = {}) {
  if (!taskId) return false;
  const selectionGeneration = Number(options.selectionGeneration || 0);
  if (selectionGeneration && selectionGeneration !== fleetTaskSelectionGeneration) return false;
  try {
    const response = await fleetAPI(`/v1/tasks/${encodeURIComponent(taskId)}/experience`);
    if (selectionGeneration && selectionGeneration !== fleetTaskSelectionGeneration) return false;
    if (response.status === 401) {
      fleetLogout();
      return false;
    }
    if (!response.ok) return false;
    const experience = await response.json();
    if (selectionGeneration && selectionGeneration !== fleetTaskSelectionGeneration) return false;
    let decision = taskExperienceDecision(experience);
    if (decision === "gap" && Number(options.resyncRevision) === Number(experience.revision)) decision = "accept";
    if (decision === "gap" && !fleetTaskExperienceResyncing) {
      fleetTaskExperienceResyncing = true;
      $("#fleet-task-experience-status").textContent = "正在补齐任务更新记录，请稍候。";
      try {
        const history = await fleetAPI(`/v1/tasks/${encodeURIComponent(taskId)}/revisions`);
        if (!history.ok) return false;
        const record = await history.json();
        return await loadFleetTaskExperience(taskId, {
          resyncRevision: Number(record.currentRevision || 0), selectionGeneration,
        });
      } finally {
        fleetTaskExperienceResyncing = false;
      }
    }
    return renderTaskExperience(experience, { resyncRevision: options.resyncRevision });
  } catch (_) {
    $("#fleet-task-experience-status").textContent = "任务说明暂时无法同步，机器人安全状态不受影响。";
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
  const request = $("#fleet-request").value.trim();
  const message = $("#fleet-task-id");
  if (!request) {
    message.textContent = "请输入任务描述";
    return;
  }
  try {
    const response = await fleetAPI("/v1/tasks", {
      method: "POST",
      body: JSON.stringify({ request, adapter: fleetExecutionAdapter }),
    });
    const task = await response.json();
    if (!response.ok) {
      message.textContent = `${task.code || "ERROR"}: ${task.message || "创建失败"}`;
      return;
    }
    message.textContent = `已创建 ${task.id}，等待审批`;
    await fleetSelectTask(task);
    const approved = await fleetTaskAction("approve", task);
    if (!approved) {
      $("#fleet-task-experience-status").textContent = "任务已经创建，但机器人暂时不能开始。请检查机器人在线和安全状态后重试批准。";
    }
    await pollFleetTasks();
  } catch (_) {
    message.textContent = "创建任务失败";
  }
}

async function fleetTaskAction(action, taskOverride = null) {
  const task = taskOverride || selectedFleetTask;
  if (!task) return false;
  try {
    const response = await fleetAPI(`/v1/tasks/${task.id}/${action}`, { method: "POST" });
    if (!response.ok) {
      $("#fleet-approve").disabled = false;
      return false;
    }
    selectedFleetTask = await response.json();
    $("#fleet-approve").disabled = true;
    await fleetSelectTask(selectedFleetTask);
    return true;
  } catch (_) {
    $("#fleet-approve").disabled = false;
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
  pollTelemetry();
  pollMetrics();
  pollRuntime();
  loadLLMConfig();
  setInterval(pollTelemetry, 1000);
  setInterval(pollRuntime, 3000);
  setInterval(pollMetrics, 5000);
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
    initFleetMode();
    return "fleet";
  }
  startLocalMode();
  return "local";
}

void bootApplication();
