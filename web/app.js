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

pollTelemetry();
pollMetrics();
pollRuntime();
loadLLMConfig();
setInterval(pollTelemetry, 1000);
setInterval(pollRuntime, 3000);
setInterval(pollMetrics, 5000);
