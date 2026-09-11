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

function item(id, title, state, situation, action, target, detail) {
  return { id, title, state, stateText: STATE_TEXT[state], situation, action, target: target || "", detail: detail || "" };
}

function connectionItem(input) {
  const connection = input.connection || "";
  if (!connection) {
    return item("connection", "连接机器人", UNKNOWN,
      "还没有收到机器人的状态。",
      "确认机器人已通电、USB 线已插好，然后点“重试连接”。", "devices");
  }
  if (connection === "LIVE" || connection === "CONNECTED") {
    const adapter = input.adapter ? `（${input.adapter}）` : "";
    return item("connection", "连接机器人", READY,
      `机器人已连接${adapter}，状态正常。`, "", "");
  }
  const detail = input.connectionDetail || "";
  return item("connection", "连接机器人", ACTION,
    "现在联系不上机器人。",
    "检查电源、USB 线和网线，确认机器人本体已开机，然后点“重试连接”。", "devices", detail);
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
    "确认机器人一个手臂范围内没有人、没有杂物，实体急停开关在手边。", "");
}

function calibrationItem(input) {
  const calibration = input.calibration || null;
  if (!calibration || !calibration.revision) {
    return item("calibration", "整机标定", ACTION,
      "这台机器人还没有标定过，标定之前不要让它抓东西。",
      "打开标定向导，跟着提示一步步做，大约十几分钟。", "calibration");
  }
  const source = calibration.source || "unknown";
  const revision = String(calibration.revision).slice(0, 8);
  if (source === "measured") {
    return item("calibration", "整机标定", READY,
      `已经用实测数据标定过（版本 ${revision}）。`, "", "calibration");
  }
  if (source === "simulation") {
    return item("calibration", "整机标定", ACTION,
      `当前用的是仿真推导的参数（版本 ${revision}），不是这台机器测出来的。`,
      "在真机上跑一次标定向导，用实测参数替换它。", "calibration");
  }
  return item("calibration", "整机标定", ACTION,
    `标定参数来自默认值（版本 ${revision}）。`,
    "在真机上跑一次标定向导。", "calibration");
}

function cameraItem(input) {
  const calibration = input.calibration || null;
  if (!calibration || calibration.cameraCount == null) {
    return item("cameras", "相机标定", UNKNOWN,
      "还没有读到相机参数。", "连接稳定后刷新；相机标定需要单独做一次。", "calibration");
  }
  if (calibration.cameraCount >= 1 && calibration.camerasMeasured === true) {
    return item("cameras", "相机标定", READY,
      `${calibration.cameraCount} 个相机已完成标定。`, "", "");
  }
  if (calibration.cameraCount >= 1) {
    return item("cameras", "相机标定", ACTION,
      `已有 ${calibration.cameraCount} 个相机的参数，但还没在本机测过。`,
      "用标定板在相机前采集一组画面完成内参标定；如果两台相机没有共同可见区域，用底盘运动法求它们之间的外参。", "calibration");
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
      "点“重新检查”读一次；如果仍然读不到，说明导航或建图还没启动，请先去“开发诊断”。", "diagnostics");
  }
  if (map.ready === true) {
    return item("map", "场景地图", READY,
      map.summary || "地图已覆盖房间，可以开始执行任务。", "", "diagnostics");
  }
  const missing = Array.isArray(map.problems) ? map.problems : [];
  const next = Array.isArray(map.nextTargets) ? map.nextTargets[0] : null;
  const action = next && next.instruction
    ? `${missing[0] || "地图还没建完"}。${next.instruction}。`
    : (missing[0] || "地图还没建完，请让机器人把没走过的区域走一遍。");
  return item("map", "场景地图", ACTION,
    map.summary || "地图还不完整，跨房间的任务会规划失败。",
    action, "diagnostics");
}

/**
 * Turn runtime state into the ordered checklist.
 *
 * Returns one entry per prerequisite plus a headline: whether the robot can be
 * used, and if not, the single thing to do first.
 */
function buildReadiness(input = {}) {
  const items = [
    connectionItem(input),
    safetyItem(input),
    calibrationItem(input),
    cameraItem(input),
    mapItem(input),
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
    if (entry.target) {
      const button = element("button", "secondary onboarding-go", "去处理");
      button.type = "button";
      button.dataset.target = entry.target;
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
  renderReadinessNodes,
  STATE_TEXT,
};
