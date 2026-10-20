// A workspace is an independent edge agent, never a mutable process-wide robot.
// Full document navigation disposes polling, sockets and pending UI callbacks.
(() => {
  const root = globalThis;
  const nativeFetch = root.fetch?.bind(root);
  function routePrefix(pathname = "/") {
    if (!pathname.startsWith("/robots/")) return "";
    const match = pathname.match(/^\/robots\/([a-zA-Z0-9_-]+)\//);
    if (!match) throw new Error("无效的机器人工作空间地址。");
    return `/robots/${match[1]}`;
  }
  const prefix = routePrefix(root.location?.pathname || "/");
  let identity = null, current = null, entries = [], fleet = false;

  function url(path) {
    const value = String(path);
    if (!prefix) return value;
    if (/^\/(?:v1\/|assets\/|healthz(?:\?|$))/.test(value)) return prefix + value;
    if (value.startsWith(prefix + "/")) return value;
    throw new Error("请求不能离开当前机器人工作空间。");
  }
  async function request(path, options = {}) {
    const method = String(options.method || "GET").toUpperCase();
    if (!fleet && !identity && !["GET", "HEAD"].includes(method)) {
      throw new Error("机器人身份尚未确认，请刷新连接后再操作。");
    }
    const headers = new Headers(options.headers);
    if (identity?.robotId) headers.set("X-Tangying-Robot-ID", identity.robotId);
    return nativeFetch(url(path), { ...options, headers, credentials: "same-origin" });
  }
  function storageKey(key) {
    return `${key}:${prefix || "local"}:${identity?.robotId || "unverified"}`;
  }
  function scopedStorage(storage) {
    return storage && {
      getItem: key => storage.getItem(storageKey(key)),
      setItem: (key, value) => storage.setItem(storageKey(key), value),
      removeItem: key => storage.removeItem(storageKey(key)),
    };
  }
  function rememberTask(taskId) {
    if (!identity) return;
    try { scopedStorage(root.sessionStorage)?.setItem("last-task", taskId); } catch (_) { /* optional */ }
  }
  function previousTask() {
    if (!identity) return "";
    try { return scopedStorage(root.sessionStorage)?.getItem("last-task") || ""; } catch (_) { return ""; }
  }
  function taskCounts(tasks = []) {
    const result = { total: tasks.length, active: 0, attention: 0, succeeded: 0, cancelled: 0 };
    for (const task of tasks) {
      if (task.state === "SUCCEEDED") result.succeeded++;
      else if (task.state === "CANCELLED") result.cancelled++;
      else if (["FAILED", "FAILED_SAFE", "RECOVERABLE_FAILURE", "SAFETY_STOPPED", "BLOCKED", "WAITING_USER"].includes(task.state)) result.attention++;
      else result.active++;
    }
    return result;
  }
  const $ = selector => root.document?.querySelector(selector);
  function text(selector, value) { const node = $(selector); if (node) node.textContent = value; }
  function status(message, tone = "neutral") {
    text("#edge-identity-state", message);
    const node = $("#edge-identity-state"); if (node) node.dataset.tone = tone;
  }
  function renderRegistry() {
    const selector = $("#edge-workspace-select");
    if (!selector) return;
    selector.replaceChildren();
    for (const item of entries) {
      const option = document.createElement("option");
      option.value = item.href;
      option.textContent = `${item.label} · ${item.robotId || "身份待确认"}`;
      option.selected = item.id === current?.id;
      selector.append(option);
    }
    selector.disabled = entries.length < 2;
    text("#edge-current-robot", current?.robotId || "身份待确认");
    text("#edge-directory-hint", entries.length > 1
      ? "每台机器人由自己的边缘 Agent 执行任务。切换查看不会暂停或转移正在执行的任务。"
      : "当前只有一个工作空间。部署其他边缘 Agent 后，在本机连接配置中注册即可切换。");
    const list = $("#edge-workspace-list");
    if (!list) return;
    list.replaceChildren();
    for (const item of entries) {
      const li = document.createElement("li");
      const link = document.createElement("a");
      link.href = item.href + "#devices";
      link.className = "edge-workspace-link";
      if (item.id === current?.id) link.setAttribute("aria-current", "true");
      const info = document.createElement("span");
      const name = document.createElement("strong"); name.textContent = item.label;
      const id = document.createElement("span"); id.textContent = item.robotId || "身份待确认";
      info.append(name, id);
      const tag = document.createElement("span");
      tag.className = "status-pill"; tag.textContent = item.id === current?.id ? "正在查看" : "进入工作空间";
      link.append(info, tag); li.append(link); list.append(li);
    }
  }
  async function init() {
    fleet = false;
    const control = $("#edge-workspace-control"); if (control) control.hidden = false;
    try {
      // The directory belongs to the host workbench, not to a proxied agent.
      const response = await nativeFetch("/v1/workspaces", { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(5000) });
      if (!response.ok) throw new Error("工作空间目录暂不可用");
      const registry = await response.json();
      if (registry.schemaVersion !== "edge.workspaces.v1" || !Array.isArray(registry.workspaces)) throw new Error("工作空间目录版本不匹配");
      entries = registry.workspaces.filter(item => typeof item.robotId === "string" && typeof item.label === "string"
        && (item.id === "local" ? item.href === "/" : /^[a-zA-Z0-9_-]+$/.test(item.id) && item.href === `/robots/${item.id}/`));
      current = entries.find(item => item.href === (prefix ? `${prefix}/` : "/"));
      if (!current) throw new Error("当前机器人未注册");
      renderRegistry();
      const identityResponse = await nativeFetch(url("/v1/workspace"), { cache: "no-store", credentials: "same-origin", signal: AbortSignal.timeout(5000) });
      if (!identityResponse.ok) throw new Error("边缘 Agent 暂不可达，恢复连接后可读取历史");
      const value = await identityResponse.json();
      if (value.schemaVersion !== "edge.workspace.v1" || !value.robotId || value.robotId !== current.robotId) {
        throw new Error("机器人身份与连接配置不一致，已阻止操作");
      }
      identity = value;
      text("#edge-current-robot", identity.robotId);
      text("#edge-history-scope", `当前记录来自 ${identity.robotId}。回放中的观测属于采集当时，实时状态请查看“我的机器人”。`);
      status("Agent 已连接", "good");
      return true;
    } catch (error) {
      identity = null;
      status("身份未确认", "warning");
      text("#edge-connection-detail", error.message);
      text("#local-history-state", "当前工作空间不可读取。恢复连接后刷新；不会显示其他机器人的记录。");
      root.TangyingConsoleUI?.update({ historyAvailable: false, runtimeConnected: false });
      root.TangyingConsoleUI?.feedback(`${current?.robotId ? current.robotId + "：" : ""}${error.message}。可在顶部切换其他机器人，或在“我的机器人”中刷新连接。`, true);
      return false;
    }
  }
  function runtime(snapshot) {
    const robotId = snapshot?.RobotID || snapshot?.robotId;
    if (snapshot && (!identity || robotId !== identity.robotId)) {
      identity = null;
      root.TangyingConsoleUI?.update({ runtimeConnected: false });
      status("机器人身份不匹配", "danger");
      return false;
    }
    root.TangyingConsoleUI?.update({ runtimeConnected: Boolean(snapshot) });
    status(snapshot ? "Runtime 已连接" : "Runtime 未连接", snapshot ? "good" : "warning");
    text("#edge-connection-detail", snapshot
      ? `已核对 ${robotId} 的运行时。动作能否执行仍以准备检查和 Harness 判定为准。`
      : "实时状态待确认。边缘 Agent 可达时，仍可查看该机器人的任务与历史观测。");
    text("#edge-runtime-checked", `最近检查 ${new Date().toLocaleTimeString()}`);
    return true;
  }
  function history(tasks) {
    if (!Array.isArray(tasks)) {
      for (const key of ["total", "active", "attention", "succeeded", "cancelled"]) text(`#edge-task-${key}`, "—");
      return;
    }
    const counts = taskCounts(tasks);
    for (const [key, value] of Object.entries(counts)) text(`#edge-task-${key}`, value);
  }
  root.TangyingEdgeWorkspace = Object.freeze({
    routePrefix, prefix, url, fetch: request, storageKey, scopedStorage,
    init, runtime, history, taskCounts, rememberTask, previousTask,
    useFleet() {
      if (prefix) throw new Error("机器人工作空间只能连接边缘 Agent。");
      fleet = true;
    },
  });
  const selector = $("#edge-workspace-select");
  selector?.addEventListener("change", () => {
    const target = entries.find(item => item.href === selector.value);
    if (!target || target.id === current?.id) return;
    // A full page boundary also prevents delayed A responses being rendered as B.
    root.location.assign(target.href + (root.location.hash || "#workspace"));
  });
  $("#edge-workspace-retry")?.addEventListener("click", () => root.location.reload());
})();
