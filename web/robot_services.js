// Environment-neutral calibration and mapping workflows backed by registered
// Robot Runtime services. Loaded as a classic script under the console CSP.
(() => {
  let requestSequence = 0;

  class RobotServiceError extends Error {
    constructor(code, message, status = 0) {
      super(message ? `${code}: ${message}` : code);
      this.name = "RobotServiceError";
      this.code = code || "SERVICE_ERROR";
      this.status = status;
    }
  }

  function requestId(prefix = "console") {
    if (globalThis.crypto?.randomUUID) return `${prefix}-${globalThis.crypto.randomUUID()}`;
    requestSequence += 1;
    return `${prefix}-${Date.now().toString(36)}-${requestSequence.toString(36)}`;
  }

  async function readPayload(response) {
    try { return await response.json(); } catch (_) { return {}; }
  }

  function errorFrom(payload, response) {
    const legacy = payload?.error;
    const code = payload?.code || legacy?.code || (typeof legacy === "string" ? legacy : "")
      || `HTTP_${response?.status || 0}`;
    const message = payload?.message || legacy?.message || (typeof legacy === "string" ? legacy : "")
      || "机器人服务没有返回可用结果。";
    return new RobotServiceError(code, message, Number(response?.status || 0));
  }

  function createServiceClient(fetchImpl = globalThis.fetch?.bind(globalThis)) {
    if (!fetchImpl) throw new RobotServiceError("FETCH_UNAVAILABLE", "浏览器不能连接机器人服务。");
    return {
      async list() {
        const response = await fetchImpl("/v1/robot/services", { cache: "no-store" });
        const payload = await readPayload(response);
        if (!response.ok) throw errorFrom(payload, response);
        return payload;
      },
      async call(name, parameters = {}) {
        const body = { name, requestId: requestId(name.replaceAll(".", "-")), parameters };
        const response = await fetchImpl("/v1/robot/services", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        const payload = await readPayload(response);
        if (!response.ok || payload?.ok !== true) throw errorFrom(payload, response);
        return payload.result || {};
      },
    };
  }

  function clone(value) { return value == null ? value : JSON.parse(JSON.stringify(value)); }

  function calibrationSaveParameters(documentValue, expectedRevision, algorithm = "") {
    const document = clone(documentValue);
    document.source = "manual";
    const parameters = { document, expectedRevision: String(expectedRevision || "") };
    if (String(algorithm).trim()) parameters.algorithm = String(algorithm).trim();
    return parameters;
  }

  function acceptCalibrationSnapshot(state, result) {
    const next = { ...state, session: result?.session || state.session || null };
    if (state.dirty) {
      next.remoteRevision = String(result?.revision || "");
      return next;
    }
    next.document = clone(result?.document || null);
    next.revision = String(result?.revision || "");
    next.remoteRevision = "";
    return next;
  }

  function mapSelectionAfterStatus(previous, status) {
    return status?.state === "completed" && status?.mapId ? String(status.mapId) : String(previous || status?.activeMap?.mapId || "");
  }

  function mappingCanFinish(status, mode) {
    if (status?.state === "recording" && mode === "manual") return true;
    // An automatic survey saves its own legs; the button would only race it.
    return status?.state === "failed"
      && Number(status.frameCount || 0) >= 3
      && Number(status.travelledM || 0) >= 0.15;
  }

  function serviceMap(payload) {
    return new Map((payload?.services || []).map(service => [service.name, service]));
  }

  const API = { RobotServiceError, createServiceClient, requestId, calibrationSaveParameters, acceptCalibrationSnapshot, mapSelectionAfterStatus, mappingCanFinish, serviceMap };
  globalThis.TangyingRobotServices = API;
  if (typeof document === "undefined") return;

  const $ = selector => document.querySelector(selector);
  const all = selector => [...document.querySelectorAll(selector)];
  const client = createServiceClient();
  const state = {
    route: "", services: new Map(), robotId: "", timer: 0,
    calibration: { document: null, revision: "", remoteRevision: "", session: null, dirty: false },
    mapping: null, mappingMode: "", mappingMapId: "", busy: false, jsonSyncing: false,
  };

  function setText(selector, value) {
    const node = $(selector);
    if (node) node.textContent = String(value ?? "");
  }

  function announce(selector, message, tone = "neutral") {
    const node = $(selector);
    if (!node) return;
    node.textContent = message;
    node.dataset.tone = tone;
    node.hidden = !message;
  }

  function serviceAvailable(name) { return state.services.get(name)?.available === true; }

  function formatError(error) {
    const code = error?.code || "SERVICE_ERROR";
    const message = error?.message || "机器人服务暂时不可用。";
    return `${code}：${message}`;
  }

  async function loadCatalogue() {
    const payload = await client.list();
    state.services = serviceMap(payload);
    state.robotId = String(payload?.robotId || "");
    setText("#workflow-robot-id", state.robotId || "等待机器人");
    globalThis.TangyingConsoleUI?.update?.({ robotId: state.robotId || null, serviceConnected: true });
    updateAvailability();
    return payload;
  }

  function updateAvailability() {
    const calibrationReady = serviceAvailable("calibration.get");
    const run = $("#calibration-run");
    const manual = $("#calibration-manual");
    if (run) run.disabled = state.busy || state.calibration.dirty || !serviceAvailable("calibration.run");
    if (manual) manual.disabled = state.busy || !calibrationReady || !state.calibration.document;
    const save = $("#calibration-save");
    if (save) save.disabled = state.busy || !state.calibration.dirty || !serviceAvailable("calibration.save");

    const mappingReady = serviceAvailable("mapping.start")
      && ["idle", "completed", "failed", "cancelled"].includes(state.mapping?.state);
    for (const button of all("[data-mapping-start]")) button.disabled = state.busy || !mappingReady;
    for (const button of all("[data-move-action]")) button.disabled = state.busy || !serviceAvailable("mapping.move") || !manualMoveReady();
    const stop = $("#mapping-stop-motion");
    if (stop) stop.disabled = state.busy || !serviceAvailable("mapping.stop_motion") || state.mapping?.state !== "moving";
    const finish = $("#mapping-finish");
    if (finish) finish.disabled = state.busy || !serviceAvailable("mapping.finish") || !mappingCanFinish(state.mapping, state.mappingMode);
    const cancel = $("#mapping-cancel");
    if (cancel) cancel.disabled = state.busy || !serviceAvailable("mapping.cancel") || !mappingActive();
    const activate = $("#activate-saved-map");
    if (activate) activate.disabled = state.busy || mappingActive() || !serviceAvailable("mapping.activate") || !$("#saved-map-select")?.value;
  }

  function setBusy(busy) {
    state.busy = busy;
    document.body.dataset.workflowBusy = String(busy);
    updateAvailability();
  }

  function pathLabel(path) {
    const labels = {
      id: "舵机 ID", drive_mode: "驱动模式", homing_offset: "零点偏移",
      range_min: "最小行程", range_max: "最大行程", sourceId: "数据源",
      width: "宽度", height: "高度", fx: "焦距 fx", fy: "焦距 fy",
      cx: "主点 cx", cy: "主点 cy", model: "畸变模型", coefficients: "畸变系数",
      parentLink: "父连杆", xyz: "位置 xyz", rpy: "姿态 rpy",
      openM: "张开距离", closedM: "闭合距离", maxRelativeTargetDeg: "单次关节最大角度",
      maxActionChunkLength: "动作分段上限", maxLinearSpeedMPerS: "最大线速度",
      maxAngularSpeedRadPerS: "最大角速度",
    };
    return labels[path.at(-1)] || path.at(-1);
  }

  function isLeaf(value) {
    return value == null || typeof value !== "object" || Array.isArray(value);
  }

  function setDocumentPath(path, value) {
    let target = state.calibration.document;
    for (let index = 0; index < path.length - 1; index += 1) target = target[path[index]];
    target[path.at(-1)] = value;
    state.calibration.dirty = true;
    renderCalibrationMeta();
    updateAvailability();
  }

  function editableInput(path, value, accessibleName = pathLabel(path)) {
    const input = document.createElement(Array.isArray(value) ? "textarea" : "input");
    input.dataset.calibrationPath = JSON.stringify(path);
    input.setAttribute("aria-label", accessibleName);
    if (Array.isArray(value)) {
      input.rows = 2;
      input.value = JSON.stringify(value);
    } else {
      input.type = typeof value === "number" ? "number" : "text";
      if (typeof value === "number") input.step = Number.isInteger(value) ? "1" : "any";
      input.value = String(value ?? "");
    }
    input.addEventListener("input", () => {
      try {
        const next = Array.isArray(value) ? JSON.parse(input.value)
          : typeof value === "number" ? Number(input.value) : input.value;
        if (typeof value === "number" && !Number.isFinite(next)) throw new Error("请输入有效数字");
        if (Array.isArray(value) && !Array.isArray(next)) throw new Error("请输入 JSON 数组");
        input.setCustomValidity("");
        setDocumentPath(path, next);
      } catch (error) {
        input.setCustomValidity(error.message);
      }
    });
    return input;
  }

  function inputFor(path, value) {
    const label = document.createElement("label");
    label.className = "calibration-field";
    const title = document.createElement("span");
    title.textContent = pathLabel(path);
    const input = editableInput(path, value);
    label.append(title, input);
    return label;
  }

  function renderObjectFields(root, value, path = []) {
    for (const [key, entry] of Object.entries(value || {})) {
      const nextPath = [...path, key];
      if (isLeaf(entry)) {
        root.append(inputFor(nextPath, entry));
        continue;
      }
      const group = document.createElement("fieldset");
      group.className = "calibration-field-group";
      const legend = document.createElement("legend");
      legend.textContent = key;
      const fields = document.createElement("div");
      fields.className = "calibration-field-grid";
      renderObjectFields(fields, entry, nextPath);
      group.append(legend, fields);
      root.append(group);
    }
  }

  function renderMotorTable(section, motors) {
    const columns = ["id", "drive_mode", "homing_offset", "range_min", "range_max"];
    const wrap = document.createElement("div");
    wrap.className = "calibration-motor-table-wrap";
    const table = document.createElement("table");
    table.className = "calibration-motor-table";
    const head = document.createElement("thead");
    const headRow = document.createElement("tr");
    for (const label of ["电机", "ID", "驱动", "零位", "Min", "Max"]) {
      const cell = document.createElement("th");
      cell.scope = "col";
      cell.textContent = label;
      headRow.append(cell);
    }
    head.append(headRow);
    const body = document.createElement("tbody");
    for (const [name, motor] of Object.entries(motors || {})) {
      const row = document.createElement("tr");
      const identity = document.createElement("th");
      identity.scope = "row";
      identity.textContent = name;
      identity.title = name;
      row.append(identity);
      for (const field of columns) {
        const cell = document.createElement("td");
        if (Object.hasOwn(motor || {}, field)) {
          cell.append(editableInput(["motors", name, field], motor[field], `${name} ${pathLabel([field])}`));
        } else {
          cell.textContent = "—";
        }
        row.append(cell);
      }
      body.append(row);
    }
    table.append(head, body);
    wrap.append(table);
    section.append(wrap);
  }

  function renderCameraCards(section, cameras) {
    const grid = document.createElement("div");
    grid.className = "calibration-camera-editor-grid";
    for (const [name, camera] of Object.entries(cameras || {})) {
      const card = document.createElement("details");
      card.className = "calibration-camera-editor";
      const summary = document.createElement("summary");
      const resolution = camera?.width && camera?.height ? `${camera.width} × ${camera.height}` : "待检查尺寸";
      summary.textContent = `${name} · ${resolution}`;
      const fields = document.createElement("div");
      fields.className = "calibration-field-grid";
      renderObjectFields(fields, camera, ["cameras", name]);
      card.append(summary, fields);
      grid.append(card);
    }
    section.append(grid);
  }

  function renderCalibrationEditor() {
    const root = $("#calibration-structured-fields");
    if (!root || !state.calibration.document) return;
    root.replaceChildren();
    for (const key of ["motors", "cameras", "geometry", "safety"]) {
      const section = document.createElement("section");
      section.className = `calibration-document-section calibration-document-${key}`;
      const heading = document.createElement("h3");
      heading.textContent = ({ motors: "电机与零位", cameras: "相机内参与外参", geometry: "整机几何", safety: "安全限值" })[key];
      section.append(heading);
      if (key === "motors") renderMotorTable(section, state.calibration.document[key]);
      else if (key === "cameras") renderCameraCards(section, state.calibration.document[key]);
      else renderObjectFields(section, state.calibration.document[key], [key]);
      root.append(section);
    }
    const editor = $("#calibration-json");
    if (editor && !state.jsonSyncing) editor.value = JSON.stringify(state.calibration.document, null, 2);
  }

  function renderCalibrationMeta() {
    const calibration = state.calibration;
    setText("#calibration-revision", calibration.revision ? calibration.revision.slice(0, 12) : "尚未保存");
    const motorCount = Object.keys(calibration.document?.motors || {}).length;
    const cameraCount = Object.keys(calibration.document?.cameras || {}).length;
    setText("#calibration-summary", calibration.document ? `${motorCount} 个电机 · ${cameraCount} 个相机` : "等待参数");
    const status = calibration.session?.status || (calibration.document ? "idle" : "unavailable");
    setText("#calibration-session-status", ({ idle: "可标定", running: "标定进行中", completed: "标定已完成", unavailable: "等待服务" })[status] || status);
    setText("#calibration-session-message", calibration.session?.message || "选择机器人标定服务，或自行录入完整参数。");
    const dirty = $("#calibration-dirty");
    if (dirty) {
      dirty.hidden = !calibration.dirty;
      dirty.textContent = calibration.remoteRevision && calibration.remoteRevision !== calibration.revision
        ? "有未保存修改；服务端版本也已更新，保存时会检查冲突。" : "有未保存修改";
    }
    updateAvailability();
  }

  async function refreshCalibration({ polling = false } = {}) {
    if (!serviceAvailable("calibration.get")) {
      if (!polling) announce("#calibration-feedback", "CALIBRATION_UNAVAILABLE：机器人没有提供标定读取服务。", "danger");
      return;
    }
    try {
      const result = await client.call("calibration.get", {});
      state.calibration = acceptCalibrationSnapshot(state.calibration, result);
      renderCalibrationMeta();
      if (!state.calibration.dirty) renderCalibrationEditor();
      globalThis.dispatchEvent(new CustomEvent("tangying:calibration-state", { detail: result }));
      if (!polling) announce("#calibration-feedback", "已加载机器人当前标定参数。", "good");
    } catch (error) {
      announce("#calibration-feedback", formatError(error), "danger");
    }
  }

  async function runCalibration() {
    setBusy(true);
    announce("#calibration-feedback", "正在启动机器人标定服务…", "neutral");
    try {
      const result = await client.call("calibration.run", {});
      state.calibration = acceptCalibrationSnapshot({ ...state.calibration, dirty: false }, result);
      renderCalibrationMeta();
      renderCalibrationEditor();
      announce("#calibration-feedback", result?.session?.message || "标定服务已启动。", "good");
      globalThis.dispatchEvent(new CustomEvent("tangying:calibration-state", { detail: result }));
    } catch (error) { announce("#calibration-feedback", formatError(error), "danger"); }
    finally { setBusy(false); }
  }

  function showManualCalibration() {
    const editor = $("#calibration-manual-editor");
    if (editor) editor.hidden = false;
    renderCalibrationEditor();
    editor?.querySelector("input, textarea")?.focus();
  }

  function setCalibrationView(view) {
    const selected = view === "json" ? "json" : "structured";
    for (const pane of all("[data-calibration-pane]")) pane.hidden = pane.dataset.calibrationPane !== selected;
    for (const button of all("[data-calibration-view]")) button.setAttribute("aria-pressed", String(button.dataset.calibrationView === selected));
    $(selected === "json" ? "#calibration-json" : "#calibration-structured-fields")?.focus?.();
  }

  function importCalibrationJSON() {
    const editor = $("#calibration-json");
    try {
      const value = JSON.parse(editor.value);
      if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("JSON 顶层必须是对象");
      state.calibration.document = value;
      state.calibration.dirty = true;
      state.jsonSyncing = true;
      renderCalibrationEditor();
      state.jsonSyncing = false;
      renderCalibrationMeta();
      announce("#calibration-feedback", "已导入 JSON，请检查字段后保存。", "good");
    } catch (error) {
      announce("#calibration-feedback", `INVALID_JSON：${error.message}`, "danger");
      editor.focus();
    }
  }

  function exportCalibrationJSON() {
    const editor = $("#calibration-json");
    if (!editor || !state.calibration.document) return;
    editor.value = JSON.stringify(state.calibration.document, null, 2);
    editor.focus();
    editor.select();
    announce("#calibration-feedback", "完整标定 JSON 已更新，可复制或另存。", "good");
  }

  async function saveCalibration() {
    const form = $("#calibration-manual-editor");
    if (!form?.reportValidity() || !state.calibration.document) return;
    const algorithm = $("#calibration-algorithm")?.value.trim();
    const parameters = calibrationSaveParameters(state.calibration.document, state.calibration.revision, algorithm);
    setBusy(true);
    announce("#calibration-feedback", "正在校验并保存…", "neutral");
    try {
      const result = await client.call("calibration.save", parameters);
      state.calibration = acceptCalibrationSnapshot({ ...state.calibration, dirty: false }, result);
      renderCalibrationMeta();
      renderCalibrationEditor();
      announce("#calibration-feedback", "标定已保存并应用，版本已更新。", "good");
      globalThis.dispatchEvent(new CustomEvent("tangying:calibration-state", { detail: result }));
    } catch (error) { announce("#calibration-feedback", formatError(error), "danger"); }
    finally { setBusy(false); }
  }

  function mappingActive() {
    return ["recording", "moving", "exploring", "finalizing"].includes(state.mapping?.state);
  }

  function manualMoveReady() {
    return state.mapping?.state === "recording" && state.mappingMode === "manual";
  }

  function renderMapping() {
    const status = state.mapping || { state: "idle" };
    const labels = { idle: "等待开始", recording: "正在采集", moving: "机器人移动中", exploring: "自动探索中", finalizing: "正在生成地图", completed: "地图已完成", failed: "建图失败", cancelled: "已取消" };
    setText("#mapping-state", labels[status.state] || status.state || "状态未知");
    setText("#mapping-message", status.message || (status.state === "idle" ? "选择自动探索、自动巡检或手动移动开始建图。" : "等待机器人更新。"));
    const explored = status.exploration;
    if (explored && Number.isFinite(Number(explored.unknownFraction))) {
      setText("#mapping-coverage", `${((1 - Number(explored.unknownFraction)) * 100).toFixed(1)}%`);
    } else {
      setText("#mapping-coverage", "—");
    }
    setText("#mapping-frame-count", Number(status.frameCount || 0).toLocaleString());
    setText("#mapping-point-count", Number(status.pointCount || 0).toLocaleString());
    setText("#mapping-travelled", `${Number(status.travelledM || 0).toFixed(2)} m`);
    setText("#mapping-registration-count", Number(status.registrationCount || 0).toLocaleString());
    setText("#mapping-loop-closures", Number(status.loopClosures || 0).toLocaleString());
    setText("#mapping-session-id", status.sessionId || "—");
    const stateNode = $("#mapping-state");
    if (stateNode) stateNode.dataset.tone = status.state === "completed" ? "good" : status.state === "failed" ? "danger" : mappingActive() ? "pending" : "neutral";
    paintMappingPreview($("#mapping-preview"), status);
    const active = status.activeMap;
    setText("#saved-map-active", active?.mapId ? `当前地图：${active.mapId}` : "尚未激活地图");
    updateAvailability();
  }

  function paintMappingPreview(canvas, status) {
    const context = canvas?.getContext?.("2d");
    if (!context) return false;
    const width = Math.max(320, Math.floor(canvas.clientWidth || 640));
    const height = Math.max(220, Math.floor(canvas.clientHeight || 360));
    canvas.width = width; canvas.height = height;
    context.fillStyle = "#182528"; context.fillRect(0, 0, width, height);
    const points = Array.isArray(status?.preview?.points) ? status.preview.points : [];
    const trajectory = Array.isArray(status?.trajectory) ? status.trajectory : [];
    const allPoints = [...points, ...trajectory].filter(point => Array.isArray(point) && point.length >= 2 && point.slice(0, 2).every(Number.isFinite));
    if (!allPoints.length) {
      context.fillStyle = "#91a4a1"; context.font = "14px system-ui"; context.textAlign = "center";
      context.fillText("采集开始后显示实测点与轨迹", width / 2, height / 2);
      return false;
    }
    const xs = allPoints.map(point => point[0]), ys = allPoints.map(point => point[1]);
    const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys);
    const scale = Math.min((width - 32) / Math.max(.2, maxX - minX), (height - 32) / Math.max(.2, maxY - minY));
    const project = point => [16 + (point[0] - minX) * scale, height - 16 - (point[1] - minY) * scale];
    const colors = Array.isArray(status?.preview?.colors) ? status.preview.colors : [];
    for (let index = Math.max(0, points.length - 12000); index < points.length; index += 1) {
      const point = points[index];
      const color = colors[index];
      context.fillStyle = Array.isArray(color) && color.length >= 3 && color.slice(0, 3).every(Number.isFinite)
        ? `rgb(${color[0]} ${color[1]} ${color[2]} / .78)` : "rgba(170, 210, 204, .7)";
      const [x, y] = project(point);
      context.fillRect(x, y, 1.4, 1.4);
    }
    if (trajectory.length) {
      context.strokeStyle = "#45b5a5"; context.lineWidth = 2; context.beginPath();
      trajectory.forEach((point, index) => { const [x, y] = project(point); index ? context.lineTo(x, y) : context.moveTo(x, y); });
      context.stroke();
    }
    return true;
  }

  async function refreshMapping({ polling = false } = {}) {
    if (!serviceAvailable("mapping.status")) {
      if (!polling) announce("#mapping-feedback", "MAPPING_UNAVAILABLE：机器人没有提供建图状态服务。", "danger");
      return;
    }
    try {
      const result = await client.call("mapping.status", {});
      const selected = mapSelectionAfterStatus(state.mappingMapId, result);
      const newlyCompleted = selected && selected !== state.mappingMapId;
      state.mapping = result;
      state.mappingMapId = selected;
      renderMapping();
      refreshMappingCamera();
      if (newlyCompleted) {
        announce("#mapping-feedback", `地图 ${selected} ${result.state === "completed" ? "已生成" : "已恢复"}，正在加载保存结果。`, "good");
        await globalThis.TangyingWorkflowMap?.selectSavedMap?.(selected, result.activeMap || null);
      }
    } catch (error) { announce("#mapping-feedback", formatError(error), "danger"); }
  }

  async function mappingCommand(name, parameters = {}) {
    setBusy(true);
    announce("#mapping-feedback", "命令已发送，等待机器人确认…", "neutral");
    try {
      if (name === "mapping.start") state.mappingMode = String(parameters.mode || "");
      const result = await client.call(name, parameters);
      const selected = mapSelectionAfterStatus(state.mappingMapId, result);
      state.mapping = result;
      if (["idle", "completed", "failed", "cancelled"].includes(result.state)) state.mappingMode = "";
      renderMapping();
      if (selected && selected !== state.mappingMapId) {
        state.mappingMapId = selected;
        await globalThis.TangyingWorkflowMap?.selectSavedMap?.(selected, result.activeMap || null);
      }
      announce("#mapping-feedback", result.message || "机器人已接受命令。", "good");
    } catch (error) { announce("#mapping-feedback", formatError(error), "danger"); }
    finally { setBusy(false); }
  }

  function refreshMappingCamera() {
    if (state.route !== "mapping") return;
    const image = $("#mapping-camera");
    if (!image) return;
    image.src = `/v1/scene/frame?t=${Date.now()}`;
  }

  async function activateSelectedMap() {
    const mapId = $("#saved-map-select")?.value;
    if (!mapId) return;
    await mappingCommand("mapping.activate", { mapId });
    await globalThis.TangyingWorkflowMap?.selectSavedMap?.(mapId, state.mapping?.activeMap || null);
  }

  async function enterRoute(route) {
    state.route = route;
    if (state.timer) { clearInterval(state.timer); state.timer = 0; }
    if (![/^(calibration|mapping)$/].some(pattern => pattern.test(route))) return;
    if (globalThis.location?.protocol === "file:") {
      announce(route === "calibration" ? "#calibration-feedback" : "#mapping-feedback",
        "请通过机器人控制台服务打开此页面后再使用该功能。", "danger");
      return;
    }
    try { await loadCatalogue(); }
    catch (error) {
      globalThis.TangyingConsoleUI?.update?.({ serviceConnected: false });
      announce(route === "calibration" ? "#calibration-feedback" : "#mapping-feedback", formatError(error), "danger");
      return;
    }
    if (state.route !== route) return;
    if (route === "calibration") await refreshCalibration();
    if (route === "mapping") await refreshMapping();
    if (state.route !== route) return;
    state.timer = setInterval(() => {
      if (state.route === "calibration") void refreshCalibration({ polling: true });
      if (state.route === "mapping") void refreshMapping({ polling: true });
    }, 1500);
  }

  API.enter = enterRoute;
  API.refreshCalibration = refreshCalibration;
  API.refreshMapping = refreshMapping;

  $("#refresh-calibration")?.addEventListener("click", () => void refreshCalibration());
  $("#calibration-run")?.addEventListener("click", () => void runCalibration());
  $("#calibration-manual")?.addEventListener("click", showManualCalibration);
  $("#calibration-view-structured")?.addEventListener("click", () => setCalibrationView("structured"));
  $("#calibration-view-json")?.addEventListener("click", () => setCalibrationView("json"));
  $("#calibration-json-import")?.addEventListener("click", importCalibrationJSON);
  $("#calibration-json-export")?.addEventListener("click", exportCalibrationJSON);
  $("#calibration-save")?.addEventListener("click", () => void saveCalibration());
  for (const button of all("[data-mapping-start]")) button.addEventListener("click", () => {
    const name = $("#mapping-name")?.value.trim();
    void mappingCommand("mapping.start", { ...(name ? { name } : {}), mode: button.dataset.mappingStart });
  });
  for (const button of all("[data-move-action]")) button.addEventListener("click", () => {
    const action = button.dataset.moveAction;
    const parameters = { action };
    const input = $(action.startsWith("turn_") ? "#mapping-angle" : "#mapping-distance");
    if (input && !input.reportValidity()) { input.focus(); return; }
    if (action.startsWith("turn_")) parameters.angleRad = Number(input?.value || .35);
    else parameters.distanceM = Number(input?.value || .25);
    void mappingCommand("mapping.move", parameters);
  });
  $("#mapping-stop-motion")?.addEventListener("click", () => void mappingCommand("mapping.stop_motion", {}));
  $("#mapping-finish")?.addEventListener("click", () => void mappingCommand("mapping.finish", {}));
  $("#mapping-cancel")?.addEventListener("click", () => void mappingCommand("mapping.cancel", {}));
  $("#refresh-mapping")?.addEventListener("click", () => void refreshMapping());
  $("#activate-saved-map")?.addEventListener("click", () => void activateSelectedMap());
  $("#saved-map-select")?.addEventListener("change", updateAvailability);
  $("#mapping-camera")?.addEventListener("load", event => {
    event.currentTarget.hidden = false;
    $("#mapping-camera-empty").hidden = true;
  });
  $("#mapping-camera")?.addEventListener("error", event => {
    event.currentTarget.hidden = true;
    $("#mapping-camera-empty").hidden = false;
  });
  globalThis.addEventListener("tangying:page-change", () => void enterRoute(document.body.dataset.page || ""));
  globalThis.addEventListener("hashchange", () => void enterRoute(String(location.hash || "").slice(1)));
  API.controller = { enterRoute, refreshCalibration, refreshMapping, state };
  void enterRoute(document.body.dataset.page || String(location.hash || "").slice(1));
})();
