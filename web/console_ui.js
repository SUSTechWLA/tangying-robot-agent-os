/* Presentation only. Task admission and authorization remain server-owned. */
(() => {
  const pages = {
    workspace: ["工作台", "描述任务，查看进展，让机器人有条不紊地完成工作。"],
    tasks: ["任务记录", "查看任务结果，回到工作台继续跟进。"],
    devices: ["我的机器人", "查看连接状态和机器人看到的现场。"],
    diagnostics: ["开发诊断", "从任务、命令和世界观测，回溯问题发生的过程。"],
    // Setup is its own place, not a section of the workbench. One screen holding a
    // readiness list, a calibration wizard and a point cloud viewer is where a
    // first-time user gets lost; each of these is a task with a beginning and an end.
    calibration: ["整机标定", "使用机器人标定服务，或录入自己的算法结果。参数验证后应用。"],
    mapping: ["SLAM 建图", "建立并检查场景地图：覆盖率、占据栅格与稠密点云。"],
  };
  function resolveRoute(hash, developer) {
    const route = String(hash || "").replace(/^#/, "");
    return Object.hasOwn(pages, route) && (route !== "diagnostics" || developer) ? route : "workspace";
  }
  function connectionPresentation(state) {
    const states = {
      LIVE: { label: "场景已同步", tone: "good", detail: "正在显示最近收到的场景观测。" },
      STALE: { label: "场景更新延迟", tone: "warning", detail: "当前保留最后一次观测，请确认连接后再继续操作。" },
      UNAVAILABLE: { label: "场景暂不可用", tone: "warning", detail: "请检查机器人和服务连接；有新观测后会自动恢复。" },
      CONNECTING: { label: "正在连接", tone: "pending", detail: "连接成功后，场景和机器人状态会自动显示。" },
      LOADING: { label: "正在加载画面", tone: "pending", detail: "正在读取所选相机画面，采集时间和来源会随画面一起更新。" },
      RESYNCING: { label: "正在重新同步", tone: "pending", detail: "正在获取最新场景，请稍候。" },
    };
    return Object.hasOwn(states, state) ? states[state] : { label: "状态待确认", tone: "pending", detail: "等待服务返回可确认的状态。" };
  }
  function topConnectionPresentation(route, state) {
    if (!["calibration", "mapping"].includes(route)) return connectionPresentation(state.connection);
    if (state.serviceConnected === true) return { label: state.robotId ? "机器人已连接" : "服务已连接", tone: "good", detail: "机器人服务可以使用。" };
    if (state.serviceConnected === false) return { label: "机器人服务不可用", tone: "warning", detail: "请检查机器人和控制台服务连接。" };
    return { label: "正在连接机器人", tone: "pending", detail: "正在读取机器人服务。" };
  }
  function taskPresentation(state) {
    if (!state) return { label: "准备新任务", tone: "neutral" };
    const labels = {
      CREATED: ["任务已创建", "pending"], PENDING: ["等待安排", "pending"],
      PLANNING: ["正在理解任务", "pending"], READY: ["等待开始", "pending"],
      AWAITING_APPROVAL: ["等待你确认", "warning"], WAITING_APPROVAL: ["等待你确认", "warning"],
      RUNNING: ["正在执行", "good"], EXECUTING: ["正在执行", "good"],
      PAUSED: ["任务已暂停", "warning"], BLOCKED: ["需要处理", "warning"],
      SUCCEEDED: ["任务已完成", "good"], FAILED: ["任务未完成", "danger"],
      CANCELLED: ["任务已取消", "neutral"],
      OBSERVING: ["正在查看现场", "pending"], WAITING_FOR_OBSERVATION: ["等待现场更新", "warning"],
      RECOVERING: ["正在恢复", "warning"], FAILED_SAFE: ["已安全停止", "danger"],
      VERIFYING: ["正在确认结果", "pending"], RECOVERABLE_FAILURE: ["需要恢复处理", "warning"],
      SAFE_RECOVERY: ["正在安全恢复", "warning"], WAITING_USER: ["需要你处理", "warning"],
      SAFETY_STOPPED: ["已触发安全停止", "danger"],
    };
    const [label, tone] = Object.hasOwn(labels, state) ? labels[state] : ["状态待确认", "pending"];
    return { label, tone };
  }
  function supportSummary(state) {
    return {
      schemaVersion: 1, consoleMode: state.mode || "detecting", connection: state.connection || "CONNECTING",
      task: { id: state.task?.id || null, state: state.task?.state || null, revision: state.task?.revision || state.task?.currentRevision || null, eventSequence: state.taskEventSequence ?? null },
      world: { revision: state.worldRevision ?? null, eventCursor: state.eventCursor || null },
      robot: { id: state.robotId || null, adapter: state.adapter || null, emergencyStopped: state.emergencyStopped ?? null },
      visualState: state.visualState || null,
      observation: { mode: state.observation?.mode || null, sourceId: state.observation?.sourceId || null, observationId: state.observation?.observationId || null, observedAt: state.observation?.observedAt || null },
      recovery: { reasonCode: state.recovery?.reasonCode || null, requiresReconciliation: state.recovery?.requiresReconciliation ?? null },
    };
  }
  function taskExamples() {
    return [
      { label: "收好杯子", request: "把杯子放进收纳盘" },
      { label: "厨房收纳并返回", request: "从客厅出发，去厨房拿杯子，放进收纳盘，然后回到客厅" },
      { label: "巡检家庭环境", request: "从客厅出发，巡检卧室和卫生间，最后回到客厅" },
    ];
  }
  function sceneGuidance(view, visualState, code = "") {
    if (view === "simple") return "简洁视图：查看物体位置和任务路径；可随时切回三维场景。";
    if (view === "cameras") return "画面用于核对现场；连接中断时会标记最后收到的画面。";
    if (view === "map") return "地图来自机器人上报的位置、轨迹和环境占用信息。";
    if (visualState === "LIVE") return "三维场景已加载。拖动查看，也可选择俯视或机器人视角。";
    if (visualState === "DEGRADED") return code === "VISUAL_MODEL_MISMATCH"
      ? "场景模型与当前环境版本不一致，暂用简洁视图。可切换机器人画面，或重新加载。"
      : "三维场景暂不可用，正在使用简洁视图。其他展示方式仍可切换。";
    return "正在加载三维场景，先显示物体位置。也可以切换到机器人画面。";
  }
  const api = { resolveRoute, connectionPresentation, topConnectionPresentation, taskPresentation, supportSummary, taskExamples, sceneGuidance };
  globalThis.TangyingConsoleUI = api;
  if (typeof document === "undefined") return;

  const $ = (selector) => document.querySelector(selector);
  const all = (selector) => [...document.querySelectorAll(selector)];
  const state = { mode: "detecting", connection: "CONNECTING", task: null };
  // A deployment can omit developer navigation through this explicit edition.
  // This is presentation configuration, never an authorization boundary.
  const operatorEdition = $("meta[name='tangying-console-edition']")?.content === "operator";
  let developer = false;
  let examplesKey = "";
  function text(selector, value) {
    const node = $(selector);
    if (node && node.textContent !== String(value)) node.textContent = String(value);
  }
  function navigate(hash = location.hash, focus = false) {
    const route = resolveRoute(hash, developer);
    const previousRoute = document.body.dataset.page;
    document.body.dataset.page = route;
    for (const node of all("[data-page-panel]")) node.hidden = !node.dataset.pagePanel.split(" ").includes(route);
    for (const node of all("[data-nav]")) {
      if (node.dataset.nav === route) node.setAttribute("aria-current", "page");
      else node.removeAttribute("aria-current");
    }
    text("#page-title", pages[route][0]);
    text("#page-description", pages[route][1]);
    if ((location.hash || focus) && location.hash !== `#${route}`) history.replaceState(null, "", `#${route}`);
    if (focus) $("#page-title")?.focus({ preventScroll: true });
    // A page change replaces everything under the viewport, so keeping the
    // previous offset drops the reader into the middle of a page they have not
    // seen. Only a real route change scrolls: re-entering the current page must
    // not yank the view away from what is being read.
    if (previousRoute && previousRoute !== route) {
      const reduced = globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches;
      globalThis.scrollTo?.({ top: 0, behavior: reduced ? "auto" : "smooth" });
    }
    render();
    globalThis.dispatchEvent(new Event("resize"));
    globalThis.dispatchEvent(new Event("tangying:page-change"));
  }
  api.navigate = (route) => { navigate(`#${route}`, true); };
  function render() {
    const connection = topConnectionPresentation(document.body.dataset.page, state);
    text("#workspace-connection", connection.label);
    $("#workspace-connection").dataset.tone = connection.tone;
    text("#workspace-scene-status", connection.label);
    text("#visual-guidance", sceneGuidance(state.sceneView, state.visualState, state.visualCode));
    text("#connection-guidance", connection.detail);
    document.body.dataset.hasTask = String(Boolean(state.task?.id));
    const task = taskPresentation(state.task?.state);
    text("#workspace-task-status", task.label);
    $("#workspace-task-status").dataset.tone = task.tone;
    const adapter = String(state.adapter || "");
    text("#environment-description", adapter ? "已连接；正在读取可用服务与状态" : "连接后读取可用服务与状态");
    const warning = state.emergencyStopped ? "机器人已停止。请先检查现场，再由负责人完成安全复位。" : state.anomalyCount ? "机器人报告了异常，请查看机器人状态并联系维护人员。" : "";
    text("#workspace-safety-alert", warning);
    $("#workspace-safety-alert").hidden = !warning;
    text("#diagnostic-summary", JSON.stringify(supportSummary(state), null, 2));
    text("#diagnostic-task", state.task?.id || "尚未选择任务");
    text("#diagnostic-revision", state.worldRevision ?? "等待观测");
    text("#diagnostic-cursor", state.eventCursor || "等待事件");
    if (state.mode === "local") {
      text("#local-task-history", state.task?.request ? `当前打开：${state.task.request}` : "选择已保存的任务可查看步骤、结果和恢复说明。");
    }
    const key = `${state.mode}:${adapter}`;
    if (key !== examplesKey) {
      examplesKey = key;
      for (const node of all("[data-task-examples]")) {
        node.replaceChildren();
        for (const example of taskExamples(adapter)) {
          const button = document.createElement("button");
          button.type = "button";
          button.className = "example-chip";
          button.textContent = example.label;
          button.addEventListener("click", () => {
            const input = $(node.dataset.taskExamples);
            input.value = example.request;
            input.dispatchEvent(new Event("input", { bubbles: true }));
            input.focus();
          });
          node.append(button);
        }
      }
    }
  }
  api.update = (patch) => { Object.assign(state, patch); render(); };
  api.feedback = (message, error = false) => {
    text("#console-feedback", message);
    $("#console-feedback").hidden = !message;
    $("#console-feedback").dataset.tone = error ? "danger" : "good";
  };
  for (const link of all("[data-nav]")) link.addEventListener("click", (event) => {
    event.preventDefault(); navigate(link.hash, true);
  });
  globalThis.addEventListener("hashchange", () => navigate(location.hash, true));
  $("#audience-toggle").hidden = operatorEdition;
  $("#audience-toggle").addEventListener("click", () => {
    if (operatorEdition) return;
    developer = !developer;
    document.body.dataset.audience = developer ? "developer" : "operator";
    $("#audience-toggle").setAttribute("aria-pressed", String(developer));
    text("#audience-label", developer ? "返回用户模式" : "开发模式");
    text("#audience-description", developer ? "已展开诊断工具" : "简洁的日常操作界面");
    navigate(developer ? "#diagnostics" : "#workspace", true);
  });
  function filterDiagnostics() {
    const query = $("#diagnostic-search").value.trim().toLowerCase();
    for (const list of all("[data-diagnostic-list]")) for (const item of list.children) item.hidden = !item.textContent.toLowerCase().includes(query);
  }
  $("#diagnostic-search").addEventListener("input", filterDiagnostics);
  const diagnosticObserver = new MutationObserver(filterDiagnostics);
  for (const list of all("[data-diagnostic-list]")) diagnosticObserver.observe(list, { childList: true, subtree: true, characterData: true });
  $("#copy-diagnostics").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("#diagnostic-summary").textContent);
      text("#diagnostic-copy-status", "排查摘要已复制，包含任务编号和事件游标。");
    } catch (_) {
      text("#diagnostic-copy-status", "浏览器限制了复制，请选中下方摘要手动复制。");
    }
  });
  $("#start-new-task").addEventListener("click", () => {
    navigate("#workspace", true);
    const input = $(state.mode === "fleet" ? "#fleet-request" : "#request");
    input.focus(); input.scrollIntoView({ block: "center", behavior: "instant" });
  });
  navigate(); render();
})();
