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

let activeTask = null;
let socket = null;
let latestTelemetry = null;
const trails = new Map();

document.querySelector("#create").addEventListener("click", createTask);
approveButton.addEventListener("click", () => taskAction("approve"));
cancelButton.addEventListener("click", () => taskAction("cancel"));
adapterInput.addEventListener("change", () => {
  latestTelemetry = null;
  renderTelemetry(null);
});

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
  $("#connection-dot").classList.toggle("online", online);
  $("#connection-text").textContent = online ? "在线" : "离线";
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
    if (!response.ok) return;
    const payload = await response.json();
    $("#adapter-label").textContent = `adapter: ${adapter}`;
    if (payload.latest) renderTelemetry(payload.latest);
    else if (!latestTelemetry) renderTelemetry(null);
  } catch (_) {
    // telemetry is best-effort; keep the console usable during network blips
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
  drawScene(entities, snapshot?.robotState || {});
}

function drawScene(entities, robotState) {
  const width = canvas.width;
  const height = canvas.height;
  context.clearRect(0, 0, width, height);
  const bounds = { minX: -0.72, maxX: 0.72, minY: -0.45, maxY: 0.45 };
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

  context.fillStyle = "rgba(143,255,196,0.06)";
  context.strokeStyle = "rgba(143,255,196,0.25)";
  context.fillRect(toX(-0.3), toY(0.22), toX(0.3) - toX(-0.3), toY(-0.22) - toY(0.22));
  context.strokeRect(toX(-0.3), toY(0.22), toX(0.3) - toX(-0.3), toY(-0.22) - toY(0.22));

  for (const entity of entities) {
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
    context.beginPath();
    context.arc(toX(x), toY(y), 9, 0, Math.PI * 2);
    context.fill();
    context.fillStyle = "#07120f";
    context.font = "bold 9px ui-monospace, monospace";
    context.textAlign = "center";
    context.fillText(entity.entityId.slice(0, 5), toX(x), toY(y) + 3);
  }

  context.strokeStyle = "#8fffc4";
  context.fillStyle = "#8fffc4";
  context.beginPath();
  context.arc(toX(0), toY(0), 7, 0, Math.PI * 2);
  context.fill();
  context.fillStyle = "#07120f";
  context.font = "bold 9px ui-monospace, monospace";
  context.fillText("ROBOT", toX(0), toY(0) - 12);
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
setInterval(pollTelemetry, 1000);
setInterval(pollMetrics, 5000);
