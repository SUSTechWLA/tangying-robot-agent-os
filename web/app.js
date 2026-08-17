const requestInput = document.querySelector("#request");
const adapterInput = document.querySelector("#adapter");
const stateLabel = document.querySelector("#state");
const taskLabel = document.querySelector("#task-id");
const eventList = document.querySelector("#events");
const connectionLabel = document.querySelector("#connection");
const approveButton = document.querySelector("#approve");
const cancelButton = document.querySelector("#cancel");

let activeTask = null;
let socket = null;

document.querySelector("#create").addEventListener("click", async () => {
  const response = await fetch("/v1/tasks", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ request: requestInput.value, adapter: adapterInput.value }),
  });
  const body = await response.json();
  if (!response.ok) {
    stateLabel.textContent = `${body.code}: ${body.message}`;
    return;
  }
  activeTask = body;
  renderTask(body);
  connectEvents(body.id);
});

approveButton.addEventListener("click", () => taskAction("approve"));
cancelButton.addEventListener("click", () => taskAction("cancel"));

async function taskAction(action) {
  if (!activeTask) return;
  const response = await fetch(`/v1/tasks/${activeTask.id}/${action}`, { method: "POST" });
  const body = await response.json();
  if (response.ok) {
    activeTask = body;
    renderTask(body);
  }
}

function connectEvents(taskId) {
  if (socket) socket.close();
  eventList.replaceChildren();
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  socket = new WebSocket(`${protocol}://${location.host}/v1/tasks/${taskId}/events/ws`);
  socket.addEventListener("open", () => {
    connectionLabel.textContent = "实时连接";
    connectionLabel.classList.add("online");
  });
  socket.addEventListener("message", (message) => {
    const event = JSON.parse(message.data);
    const item = document.createElement("li");
    item.innerHTML = `<time>${event.sequence ?? "–"}</time><div><strong>${event.type ?? event.code}</strong><p>${event.message ?? event.stepId ?? ""}</p></div>`;
    eventList.append(item);
    if (event.type === "STATE_CHANGED") refreshTask(taskId);
  });
  socket.addEventListener("close", () => {
    connectionLabel.textContent = "连接关闭";
    connectionLabel.classList.remove("online");
  });
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
}
