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
let frameRequest = 0;
const discoveredAdapters = new Set();
const trails = new Map();

document.querySelector("#create").addEventListener("click", createTask);
approveButton.addEventListener("click", () => taskAction("approve"));
cancelButton.addEventListener("click", () => taskAction("cancel"));
adapterInput.addEventListener("change", () => {
  latestTelemetry = null;
  clearSceneFrame("已切换适配器，等待新观测");
  renderTelemetry(null);
  void pollTelemetry();
});
$("#save-llm").addEventListener("click", saveLLMConfig);

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
  try {
    const response = await fetch(`/v1/telemetry?adapter=${encodeURIComponent(adapter)}&limit=20`);
    if (!response.ok) {
      handleTelemetryFailure(`遥测请求失败（HTTP ${response.status}）`);
      return;
    }
    const payload = await response.json();
    const selected = syncAdapters(payload.adapters || [], adapter);
    if (selected !== adapter) {
      void pollTelemetry();
      return;
    }
    $("#adapter-label").textContent = `adapter: ${selected || "—"}`;
    if (payload.latest) renderTelemetry(payload.latest);
    else if (!latestTelemetry) {
      clearSceneFrame(`${selected || "Robot Runtime"} 已连接，尚无场景观测`);
      renderTelemetry(null);
    }
  } catch (_) {
    handleTelemetryFailure("遥测连接中断");
  }
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
  if (snapshot) updateSceneFrame(snapshot);
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

async function updateSceneFrame(snapshot) {
  const requestedAdapter = adapterInput.value;
  if (snapshot.adapter !== requestedAdapter) return;
  const requestID = ++frameRequest;
  const observedAt = Date.parse(snapshot.observedAt || "");
  const age = Number.isFinite(observedAt) ? Date.now() - observedAt : Infinity;
  const identity = sceneIdentity(snapshot, findRobotEntity(snapshot.entities || []));
  setSceneVisualState(
    age <= 3000 ? "LIVE" : "STALE",
    age <= 3000 ? `${identity.adapter} 实时场景画面` : `显示 ${identity.adapter} 最近一次场景画面`,
  );
  try {
    const response = await fetch(`/v1/scene/frame?adapter=${encodeURIComponent(requestedAdapter)}&t=${Date.now()}`, { cache: "no-store" });
    if (requestID !== frameRequest || requestedAdapter !== adapterInput.value) return;
    if (!response.ok) {
      clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧不可用，已切换语义俯视图`);
      return;
    }
    const blob = await response.blob();
    const nextURL = URL.createObjectURL(blob);
    sceneFrame.onload = () => {
      if (requestID !== frameRequest) {
        URL.revokeObjectURL(nextURL);
        return;
      }
      if (frameObjectURL) URL.revokeObjectURL(frameObjectURL);
      frameObjectURL = nextURL;
      sceneFrame.hidden = false;
      canvas.hidden = true;
    };
    sceneFrame.onerror = () => {
      URL.revokeObjectURL(nextURL);
      clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧解码失败，已切换语义俯视图`);
    };
    sceneFrame.src = nextURL;
  } catch (_) {
    if (requestID === frameRequest) clearSceneFrame(`${identity.robot} / ${identity.adapter} 场景帧连接失败，已切换语义俯视图`);
  }
}

function clearSceneFrame(message) {
  frameRequest += 1;
  sceneFrame.onload = null;
  sceneFrame.onerror = null;
  sceneFrame.removeAttribute("src");
  sceneFrame.hidden = true;
  canvas.hidden = false;
  if (frameObjectURL) URL.revokeObjectURL(frameObjectURL);
  frameObjectURL = null;
  setSceneVisualState("UNAVAILABLE", message);
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

function drawScene(entities, robotState, snapshot) {
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const bounds = { minX: -0.72, maxX: 0.72, minY: -0.18, maxY: 0.86 };
  const toX = (x) => ((x - bounds.minX) / (bounds.maxX - bounds.minX)) * width;
  const toY = (y) => ((bounds.maxY - y) / (bounds.maxY - bounds.minY)) * height;

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
    trail.push({ x, y, t: Date.now() });
    while (trail.length && Date.now() - trail[0].t > 4000) trail.shift();
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
