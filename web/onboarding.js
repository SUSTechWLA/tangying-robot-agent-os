// "Can I use the robot yet?" — the one screen a non-expert needs.
//
// Someone who has never calibrated a robot or built a map does not need metrics,
// they need to know what is missing and what to do about it. So this module turns
// whatever the runtime currently reports into a short ordered list of items, each
// with a plain-language state, one concrete next action, and a button that goes
// there. It never invents a status: an input it does not have is reported as
// unknown rather than assumed good, because telling somebody "可以开始" on a guess
// is the one failure this screen must not have.
//
// Loaded as a classic script under the console's CSP, so no `export`.

const READY = "ready";
const ACTION = "action";
const WAITING = "waiting";
const UNKNOWN = "unknown";

// Ordered by what a user must actually do first: you cannot calibrate a robot you
// have not connected, and a map is worthless before the cameras are calibrated.
const ITEM_ORDER = ["connection", "safety", "calibration", "cameras", "map"];

const STATE_TEXT = {
  [READY]: "已完成",
  [ACTION]: "需要处理",
  [WAITING]: "进行中",
  [UNKNOWN]: "状态未知",
};

const STATE_RANK = { [ACTION]: 0, [WAITING]: 1, [UNKNOWN]: 2, [READY]: 3 };

function item(id, title, state, situation, action, target, options = {}) {
  return {
    id, title, state, stateText: STATE_TEXT[state], situation, action,
    target: target || "", detail: options.detail || "",
    // A confirm control is an item the operator clears by doing something in the
    // world rather than by fixing a fault. It is separate from "去处理" because
    // the two are different promises: one navigates, the other records that a
    // person checked something.
    confirm: options.confirm || "",
    confirmLabel: options.confirmLabel || "",
  };
}

function calibrationReadiness(serviceResult, robotState = {}) {
  if (serviceResult?.revision) {
    const documentCameras = serviceResult.document?.cameras;
    const documentCameraCount = documentCameras && typeof documentCameras === "object"
      ? Object.keys(documentCameras).length : null;
    const cameraCount = serviceResult.cameraCount != null && Number.isFinite(Number(serviceResult.cameraCount))
      ? Number(serviceResult.cameraCount) : documentCameraCount;
    const ready = serviceResult.available !== false && serviceResult.session?.status !== "failed";
    return {
      revision: serviceResult.revision,
      ready,
      cameraCount,
      camerasMeasured: serviceResult.camerasMeasured === true
        || (ready && Number(cameraCount) > 0),
    };
  }
  const published = robotState.calibration || {};
  const revision = published.revision || robotState.calibration_revision;
  if (!revision) return null;
  const publishedCount = published.cameraCount ?? robotState.calibration_camera_count;
  const cameraCount = publishedCount == null ? null : Number(publishedCount);
  return {
    revision,
    ready: published.valid !== false,
    cameraCount: Number.isFinite(cameraCount) ? cameraCount : null,
    camerasMeasured: published.camerasMeasured === true || robotState.calibration_cameras_measured === true,
  };
}

function connectionItem(input) {
  const connection = input.connection || "";
  if (!connection) {
    return item("connection", "连接机器人", UNKNOWN,
      "还没有收到机器人的状态。",
      "确认机器人已通电、USB 线已插好，然后点“重试连接”。", "devices");
  }
  if (connection === "LIVE" || connection === "CONNECTED") {
    return item("connection", "连接机器人", READY,
      "机器人已连接，状态正常。", "", "");
  }
  const detail = input.connectionDetail || "";
  return item("connection", "连接机器人", ACTION,
    "现在联系不上机器人。",
    "检查电源、USB 线和网线，确认机器人本体已开机，然后点“重试连接”。", "devices", { detail });
}

function safetyItem(input) {
  if (input.emergencyStopped === true) {
    return item("safety", "安全确认", ACTION,
      "机器人处于急停状态，不会执行任何动作。",
      "先确认现场安全，再按机器人上的复位步骤解除急停，然后回来刷新。", "devices");
  }
  if (input.safetyAcknowledged === true) {
    return item("safety", "安全确认", READY, "已确认现场安全。", "", "");
  }
  if (input.emergencyStopped == null) {
    return item("safety", "安全确认", UNKNOWN,
      "还没有读到急停状态。",
      "等连接稳定后再刷新；连接不稳定时不要下指令。", "devices");
  }
  return item("safety", "安全确认", ACTION,
    "开始前需要你确认一次现场安全。",
    "确认机器人一个手臂范围内没有人、没有杂物，实体急停开关在手边。", "",
    // It used to have no control at all, so the checklist could never reach
    // "ready" and the first thing a new user met was a blocker they could not
    // clear.
    { confirm: "safety", confirmLabel: "我已确认现场安全" });
}

function calibrationItem(input) {
  const calibration = input.calibration || null;
  if (!calibration || !calibration.revision) {
    return item("calibration", "整机标定", ACTION,
      "这台机器人还没有标定过，标定之前不要让它抓东西。",
      "打开标定向导，跟着提示一步步做，大约十几分钟。", "calibration");
  }
  const revision = String(calibration.revision).slice(0, 8);
  if (calibration.ready !== false) {
    return item("calibration", "整机标定", READY,
      `标定参数已就绪（版本 ${revision}）。`, "", "calibration");
  }
  return item("calibration", "整机标定", ACTION,
    `标定参数还不能使用（版本 ${revision}）。`,
    "打开整机标定，选择机器人标定服务或录入完整结果。", "calibration");
}

function cameraItem(input) {
  const calibration = input.calibration || null;
  if (!calibration || calibration.cameraCount == null) {
    return item("cameras", "相机标定", UNKNOWN,
      "还没有读到相机参数。", "连接稳定后刷新；相机标定需要单独做一次。", "calibration");
  }
  if (calibration.cameraCount >= 1 && calibration.camerasMeasured === true && calibration.ready !== false) {
    return item("cameras", "相机标定", READY,
      `${calibration.cameraCount} 个相机已完成标定。`, "", "");
  }
  if (calibration.cameraCount >= 1) {
    return item("cameras", "相机标定", ACTION,
      `已有 ${calibration.cameraCount} 个相机参数，但整机标定尚未就绪。`,
      "打开整机标定，检查内参、外参与整机几何。", "calibration");
  }
  return item("cameras", "相机标定", ACTION,
    "还没有相机参数，机器人看不清东西。",
    "先做相机标定，再做整机标定。", "calibration");
}

function mapItem(input) {
  const map = input.map || null;
  if (!map || map.ready == null) {
    return item("map", "场景地图", UNKNOWN,
      "还没有读到地图状态。",
      "点“重新检查”读一次；如果仍然没有地图，请打开 SLAM 建图。", "mapping");
  }
  if (map.ready === true) {
    return item("map", "场景地图", READY,
      map.summary || "地图已覆盖房间，可以开始执行任务。", "", "mapping");
  }
  const missing = Array.isArray(map.problems) ? map.problems : [];
  const next = Array.isArray(map.nextTargets) ? map.nextTargets[0] : null;
  const action = next && next.instruction
    ? `${missing[0] || "地图还没建完"}。${next.instruction}。`
    : (missing[0] || "地图还没建完，请让机器人把没走过的区域走一遍。");
  return item("map", "场景地图", ACTION,
    map.summary || "地图还不完整，跨房间的任务会规划失败。",
    action, "mapping");
}

/**
 * Turn runtime state into the ordered checklist.
 *
 * Returns one entry per prerequisite plus a headline: whether the robot can be
 * used, and if not, the single thing to do first.
 */
// The checks this module computes from what the browser can see.
//
// The server computes its own set, and the two are not the same thing: the server
// knows the robot's fault report, whether any action's outcome is unknown, and
// whether an agent is watching; the browser knows calibration and camera detail
// the server does not publish. Rather than merge two opinions about the same
// question, each row has exactly one owner and the server's rows are added only
// where the client has none.
const CLIENT_CHECK_IDS = new Set(["connection", "safety", "calibration", "cameras", "map"]);

const SERVER_STATE = { ready: READY, action: ACTION, unknown: UNKNOWN };

/**
 * Rows from the authoritative readiness report.
 *
 * The report is what the server will act on and what it tells support, so a
 * blocking row in it has to appear here even though this module did not compute
 * it. Ignoring it is how a panel comes to say "ready" while the server says the
 * robot has a blocking fault.
 */
function serverReadinessItems(server) {
  if (!server || !Array.isArray(server.checks)) return [];
  const rows = [];
  for (const check of server.checks) {
    if (!check || CLIENT_CHECK_IDS.has(check.id)) continue;
    rows.push(item(
      `server:${check.id}`,
      check.title || check.id,
      SERVER_STATE[check.state] || UNKNOWN,
      check.situation || "",
      check.state === READY ? "" : (check.action || "打开诊断信息并联系支持人员。"),
      "",
      { detail: check.detail || "" },
    ));
  }
  return rows;
}

function buildReadiness(input = {}) {
  const items = [
    connectionItem(input),
    safetyItem(input),
    calibrationItem(input),
    cameraItem(input),
    mapItem(input),
    ...serverReadinessItems(input.server),
  ].sort((left, right) => {
    const rank = STATE_RANK[left.state] - STATE_RANK[right.state];
    return rank !== 0 ? rank : ITEM_ORDER.indexOf(left.id) - ITEM_ORDER.indexOf(right.id);
  });

  const blockers = items.filter(entry => entry.state === ACTION);
  const unknown = items.filter(entry => entry.state === UNKNOWN);
  const ready = blockers.length === 0 && unknown.length === 0;
  const next = blockers[0] || unknown[0] || null;

  return {
    items,
    ready,
    completed: items.filter(entry => entry.state === READY).length,
    total: items.length,
    next,
    headline: ready
      ? "前置工作已全部完成，可以直接给机器人下指令了。"
      : (next ? `下一步：${next.title} —— ${next.action}` : "正在检查…"),
    // The server's own sentence, kept alongside the per-item headline so the
    // panel can quote the same verdict the API returns. Two different summaries
    // of one state is how a console and its own backend come to disagree.
    serverSummary: input.server && input.server.summary ? input.server.summary : "",
    language: input.server && input.server.language ? input.server.language : null,
  };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

/** Build the screen as a DOM subtree, so no server string is ever injected. */
function renderReadinessNodes(readiness) {
  if (!readiness) return null;
  const root = element("div", "onboarding");
  root.append(element("p", `onboarding-headline ${readiness.ready ? "ready" : "blocked"}`, readiness.headline));

  const progress = element("div", "onboarding-progress");
  const bar = element("div", "onboarding-bar");
  const fill = element("div", "onboarding-fill");
  fill.style.width = `${Math.round((readiness.completed / readiness.total) * 100)}%`;
  bar.append(fill);
  progress.append(bar, element("span", "onboarding-count",
    `${readiness.completed} / ${readiness.total} 项已完成`));
  root.append(progress);

  // What the system can currently understand.
  //
  // It is not a checklist item, because a robot that understands a fixed
  // vocabulary still works. It is shown because "it did not understand me" and
  // "no model is configured" look identical from the outside, and only one of
  // them is something the owner can fix.
  if (readiness.language && readiness.language.modelConfigured === false) {
    const note = element("p", "onboarding-language", readiness.language.note || "");
    if (readiness.language.vocabulary) {
      note.textContent += ` 现在能听懂：${readiness.language.vocabulary}。`;
    }
    root.append(note);
  }

  const list = element("ol", "onboarding-items");
  for (const entry of readiness.items) {
    const row = element("li", `onboarding-item ${entry.state}`);
    const header = element("div", "onboarding-item-head");
    header.append(
      element("span", `onboarding-state ${entry.state}`, entry.stateText),
      element("strong", "", entry.title),
    );
    row.append(header, element("p", "onboarding-situation", entry.situation));
    if (entry.action) row.append(element("p", "onboarding-action", entry.action));
    if (entry.detail) row.append(element("p", "onboarding-detail", entry.detail));
    if (entry.confirm) {
      // The operator's acknowledgement is recorded by the console, not decided
      // here: this layer only knows how to ask, and a module that could mark
      // itself satisfied would be able to claim readiness nobody gave.
      const button = element("button", "primary onboarding-confirm", entry.confirmLabel || "确认");
      button.type = "button";
      button.dataset.confirm = entry.confirm;
      button.addEventListener("click", () => {
        globalThis.dispatchEvent?.(new globalThis.CustomEvent("tangying:onboarding-confirm", {
          detail: { id: entry.confirm },
        }));
      });
      row.append(button);
    }
    if (entry.target) {
      const button = element("button", "secondary onboarding-go", "去处理");
      button.type = "button";
      button.dataset.target = entry.target;
      button.addEventListener("click", () => {
        if (globalThis.TangyingConsoleUI?.navigate) globalThis.TangyingConsoleUI.navigate(entry.target);
        else if (globalThis.location) globalThis.location.hash = `#${entry.target}`;
      });
      row.append(button);
    }
    list.append(row);
  }
  root.append(list);
  return root;
}

// Published last, once every declaration exists: index.html loads this as a
// classic script and app.js reads the object.
globalThis.TangyingOnboarding = {
  buildReadiness,
  calibrationReadiness,
  renderReadinessNodes,
  STATE_TEXT,
};
