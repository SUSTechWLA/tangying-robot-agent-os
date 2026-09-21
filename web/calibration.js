// The calibration card flow.
//
// The Python wizard owns the plan, the wording and the progress; this renders
// them. It deliberately does not hold a second copy of the steps, because two
// definitions of "what the operator is asked to do" will drift, and the one that
// drifts is the one on screen.
//
// Loaded as a classic script under the console's CSP, so no `export`.

const FLOW_UNAVAILABLE = "unavailable";
const FLOW_DONE = "done";
const FLOW_RUNNING = "running";

function stepState(step) {
  const status = String(step.status || "pending");
  if (status === "done") return { key: "done", text: "已完成" };
  if (status === "current") return { key: "current", text: "现在做这一步" };
  return { key: "pending", text: "稍后" };
}

/**
 * Turn the wizard snapshot into what the screen shows.
 *
 * An unavailable snapshot is a normal state, not a failure: most robots have
 * never opened the wizard, and saying "还没有开始标定" is more useful than an
 * empty panel or a red error.
 */
//: What each group of motors is called on screen, and in what order.
const MOTOR_GROUPS = [
  ["left", "左臂"], ["right", "右臂"], ["shared", "头部与底盘"],
];

function motorGroupOf(name) {
  if (name.startsWith("left_arm_")) return "left";
  if (name.startsWith("right_arm_")) return "right";
  return "shared";
}

function jointLabel(name) {
  const joint = name.includes("_arm_") ? name.split("_arm_")[1] : name;
  return JOINT_LABELS[joint] || joint;
}

const JOINT_LABELS = {
  shoulder_pan: "肩部旋转", shoulder_lift: "肩部抬升", elbow_flex: "肘部",
  wrist_flex: "腕部俯仰", wrist_roll: "腕部旋转", gripper: "夹爪",
  head_motor_1: "头部旋转", head_motor_2: "头部俯仰",
  base_left_wheel: "左驱动轮", base_right_wheel: "右驱动轮",
};

/**
 * The measured numbers, grouped the way a person finds them.
 *
 * The page used to report "16 servos, 2 cameras" and nothing else, which is a
 * sentence about a calibration rather than the calibration. These are the values
 * themselves, so somebody whose own procedure disagrees can see where and change it.
 */
function buildParameters(document) {
  if (!document || typeof document !== "object") return null;
  const motors = document.motors || {};
  const groups = MOTOR_GROUPS.map(([id, label]) => ({
    id, label,
    motors: Object.keys(motors).filter(name => motorGroupOf(name) === id).sort().map(name => ({
      name, label: jointLabel(name),
      servoId: motors[name].id,
      homingOffset: motors[name].homing_offset,
      rangeMin: motors[name].range_min,
      rangeMax: motors[name].range_max,
    })),
  })).filter(group => group.motors.length > 0);

  const cameras = Object.keys(document.cameras || {}).sort().map(name => {
    const camera = document.cameras[name] || {};
    const k = camera.intrinsics || {};
    const e = camera.extrinsics || {};
    return {
      name, width: camera.width, height: camera.height,
      fx: k.fx, fy: k.fy, cx: k.cx, cy: k.cy,
      parentLink: e.parentLink,
      xyz: (e.xyz || []).join(", "), rpy: (e.rpy || []).join(", "),
      distortion: (camera.distortion || {}).model || "none",
    };
  });

  return {
    motorCount: Object.keys(motors).length,
    groups, cameras,
    source: document.source || "unknown",
    revision: String(document.hash || "").slice(0, 12),
  };
}

/**
 * Copy the wizard's guide for a step, field by field.
 *
 * The wizard decides how a step is shown; this only carries that decision to the
 * animation. Nothing is added here, because a field invented on this side would
 * be a second opinion about what the operator has to do, and the two would drift.
 */
function stepGuide(raw) {
  if (!raw || typeof raw !== "object") return null;
  const list = value => (Array.isArray(value) ? value : []);
  return {
    kind: String(raw.kind || ""),
    motor: String(raw.motor || ""),
    side: String(raw.side || ""),
    joint: String(raw.joint || ""),
    camera: String(raw.camera || ""),
    views: Number(raw.views || 0),
    poses: Number(raw.poses || 0),
    ports: list(raw.ports).map(port => ({
      id: String(port?.id || ""), port: String(port?.port || ""), label: String(port?.label || ""),
    })),
    checks: list(raw.checks).map(check => ({
      id: String(check?.id || ""), label: String(check?.label || ""),
    })),
    cameras: list(raw.cameras).map(name => String(name)),
    joints: list(raw.joints).map(name => String(name)),
  };
}

function buildCalibrationFlow(snapshot, document) {
  if (!snapshot || snapshot.available !== true) {
    return {
      kind: FLOW_UNAVAILABLE,
      reason: (snapshot && snapshot.reason) || "还没有标定记录。",
      steps: [],
      completed: 0,
      total: 0,
      percent: 0,
      current: null,
      summary: "",
      headline: "还没有开始标定",
      hint: "打开标定向导后，这里会显示每一步要做什么、做到哪了。",
    };
  }

  const steps = (snapshot.steps || []).map(step => {
    const state = stepState(step);
    return {
      id: String(step.id || ""),
      index: Number(step.index || 0),
      total: Number(step.total || 0),
      group: String(step.group || ""),
      title: String(step.title || ""),
      instruction: String(step.instruction || ""),
      detail: String(step.detail || ""),
      state: state.key,
      stateText: state.text,
      guide: stepGuide(step.guide),
    };
  });
  const completed = Number(snapshot.completed || 0);
  const total = Number(snapshot.total || steps.length);
  const current = steps.find(step => step.state === "current") || null;
  const finished = current === null && total > 0 && completed >= total;
  const parameters = finished ? buildParameters(document) : null;

  return {
    kind: finished ? FLOW_DONE : FLOW_RUNNING,
    reason: "",
    steps,
    completed,
    total,
    percent: total > 0 ? Math.round((completed / total) * 100) : 0,
    current,
    parameters,
    summary: finished ? "" : String(snapshot.summary || ""),
    headline: finished ? "标定已完成" : (current ? `第 ${current.index} / ${total} 步：${current.title}` : "标定进行中"),
    hint: current
      ? current.instruction
      : (finished
        ? (parameters
          ? "下面是这台机器人测得的全部参数，可以按你自己的方法修改。"
          : "参数已经保存，可以开始建图或执行任务了。")
        : "按终端里的提示操作，这里会同步显示进度。"),
  };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

/** Build the card flow as a DOM subtree; server strings are never injected. */
function renderCalibrationNodes(flow) {
  if (!flow) return null;
  const root = element("div", `calibration-flow ${flow.kind}`);

  if (flow.kind === FLOW_UNAVAILABLE) {
    root.append(element("p", "calibration-headline", flow.headline));
    root.append(element("p", "calibration-reason", flow.reason));
    root.append(element("p", "hint", flow.hint));
    return root;
  }

  root.append(element("p", `calibration-headline ${flow.kind}`, flow.headline));
  root.append(element("p", "calibration-instruction", flow.hint));
  if (flow.summary) root.append(element("p", "hint", flow.summary));

  const progress = element("div", "calibration-progress");
  const bar = element("div", "calibration-bar");
  const fill = element("div", "calibration-fill");
  fill.style.width = `${flow.percent}%`;
  bar.append(fill);
  progress.append(bar, element("span", "calibration-count", `${flow.completed} / ${flow.total} 步`));
  root.append(progress);

  // A finished calibration shows its parameters, not its steps. Listing the last two
  // cards and then "20 steps completed" underneath said the same thing twice and left
  // the numbers - the reason anybody opens this page - off the screen entirely.
  if (flow.kind === FLOW_DONE && flow.parameters) {
    root.append(renderParameters(flow.parameters));
    return root;
  }

  // Only the current and the next few steps: twenty cards at once is a wall, and
  // the operator only ever needs the one in front of them.
  const currentIndex = flow.steps.findIndex(step => step.state === "current");
  const window = currentIndex < 0 ? flow.steps.slice(-2) : flow.steps.slice(currentIndex, currentIndex + 3);

  const list = element("ol", "calibration-steps");
  for (const step of window) {
    const row = element("li", `calibration-step ${step.state}`);
    const head = element("div", "calibration-step-head");
    head.append(
      element("span", `calibration-state ${step.state}`, step.stateText),
      element("strong", "", `${step.index}. ${step.title}`),
    );
    row.append(head);
    if (step.instruction) row.append(element("p", "calibration-step-instruction", step.instruction));
    if (step.detail) row.append(element("p", "calibration-step-detail", step.detail));
    list.append(row);
  }
  root.append(list);

  const done = element("details", "calibration-done");
  done.append(element("summary", "", `已完成的 ${flow.completed} 步`));
  const doneList = element("ol", "calibration-done-list");
  for (const step of flow.steps.filter(entry => entry.state === "done")) {
    doneList.append(element("li", "", `${step.index}. ${step.title}`));
  }
  done.append(doneList);
  if (flow.completed > 0) root.append(done);

  return root;
}

/** The measured numbers, editable, grouped the way the robot is laid out. */
function renderParameters(parameters) {
  const root = element("div", "calibration-parameters");

  for (const group of parameters.groups) {
    root.append(element("h3", "calibration-group", `${group.label}（${group.motors.length} 个舵机）`));
    const table = element("table", "calibration-table");
    const head = element("tr");
    for (const title of ["关节", "舵机 ID", "零点偏移", "最小", "最大"]) {
      head.append(element("th", "", title));
    }
    table.append(head);
    for (const motor of group.motors) {
      const row = element("tr");
      row.append(element("td", "", motor.label));
      for (const [field, value] of [["servoId", motor.servoId], ["homingOffset", motor.homingOffset],
        ["rangeMin", motor.rangeMin], ["rangeMax", motor.rangeMax]]) {
        const cell = element("td");
        const input = element("input", "calibration-input");
        input.type = "number";
        input.value = String(value);
        input.dataset.motor = motor.name;
        input.dataset.field = field;
        input.setAttribute("aria-label", `${motor.label} ${field}`);
        cell.append(input);
        row.append(cell);
      }
      table.append(row);
    }
    root.append(table);
  }

  root.append(element("h3", "calibration-group", `相机（${parameters.cameras.length} 个）`));
  for (const camera of parameters.cameras) {
    const card = element("div", "calibration-camera");
    card.append(element("strong", "", camera.name));
    const rows = [
      ["分辨率", `${camera.width} × ${camera.height}`],
      ["内参 fx / fy", `${camera.fx} / ${camera.fy}`],
      ["主点 cx / cy", `${camera.cx} / ${camera.cy}`],
      ["畸变模型", camera.distortion],
      ["安装于", camera.parentLink],
      ["位置 xyz", camera.xyz],
      ["姿态 rpy", camera.rpy],
    ];
    const list = element("dl", "calibration-camera-fields");
    for (const [label, value] of rows) {
      list.append(element("dt", "", label), element("dd", "", value));
    }
    card.append(list);
    root.append(card);
  }

  root.append(element("p", "hint",
    `来源：${parameters.source}　版本号：${parameters.revision}　`
    + `共 ${parameters.motorCount} 个舵机、${parameters.cameras.length} 个相机。`));
  return root;
}

// --- the picture for the current step ---------------------------------------
//
// The wizard says what a step asks for; TangyingWebGL knows how to draw it. This
// is the seam between them: one panel, created once and re-shown as the current
// step moves. A WebGL context per render would leak one context per poll, and a
// browser stops handing them out long before the wizard is finished.
//
// An animation the page cannot draw is not a failure. The instruction above the
// canvas is the wizard's own sentence, so the step stays doable with the picture
// missing — which is what a robot without WebGL, or a step this build has no
// animation for, both look like.

const GUIDE_PANEL_ID = "calibration-guide";
const GUIDE_CANVAS_ID = "calibration-guide-canvas";
const GUIDE_CAPTION_ID = "calibration-guide-caption";
const GUIDE_NOTE_ID = "calibration-guide-note";
const GUIDE_LEGEND_ID = "calibration-guide-legend";
const GUIDE_BODY_ID = "calibration-body";

//: What each animation shows. These describe the drawing, not the step: the step
//: and its wording are the wizard's, and this panel never restates them.
const GUIDE_CAPTIONS = {
  connect: "示意图：机器人身上要接的几处——电源、数据线和相机。轮到哪一处，哪一处就亮起来。",
  preflight: "示意图：开始前先绕机器看一圈，周围清空、急停放在手边。",
  zero: "示意图：把高亮的这一节用手转到刻度圈的中间位置。",
  travel: "示意图：扶着这条手臂，在两端挡块之间慢慢地来回移动。",
  intrinsics: "示意图：把标定板举在相机前面，按提示换角度、换距离。",
  handeye: "示意图：板子固定在桌上不动，由手臂带着相机换姿态。",
  review: "示意图：参数已经测好，确认后保存。",
};

const GUIDE_NO_PICTURE = "这一步没有示意图，照上面的文字做就可以。";
const GUIDE_NO_WEBGL = "这台设备暂时不能显示动画，照上面的文字做这一步就可以。";

let guideView = null;
let guideLegend = null;

function guideElement(id) {
  return document.getElementById ? document.getElementById(id) : null;
}

/**
 * A counter the legend can rewrite in place as the animation advances.
 *
 * The camera names are the wizard's, and they are worth repeating here: on a
 * robot with two cameras the step is about one of them, and "第 3 / 12 个视角"
 * without a camera beside it is a sentence about nothing in particular.
 */
function guideCounter(guide) {
  const cameras = guide.kind === "intrinsics" ? [guide.camera].filter(Boolean) : (guide.cameras || []);
  const prefix = cameras.length > 0 ? `${cameras.join("、")} · ` : "";
  if (guide.kind === "intrinsics") return { noun: "视角", count: guide.views || 12, prefix };
  if (guide.kind === "handeye") return { noun: "姿态", count: guide.poses || 8, prefix };
  return null;
}

function guideLegendLabels(guide) {
  if (guide.kind === "connect") return guide.ports.map(port => port.label).filter(Boolean);
  if (guide.kind === "preflight") return guide.checks.map(check => check.label).filter(Boolean);
  return [];
}

/** The legend repeats the wizard's own labels: the picture is never the only clue. */
function renderGuideLegend(guide) {
  const legend = guideElement(GUIDE_LEGEND_ID);
  if (!legend) return;
  const labels = guideLegendLabels(guide);
  const counter = guideCounter(guide);
  guideLegend = { labels, counter, nodes: [] };
  legend.replaceChildren();
  for (const label of labels) {
    const item = element("li", "calibration-guide-item", label);
    legend.append(item);
    guideLegend.nodes.push(item);
  }
  if (counter) {
    const item = element("li", "calibration-guide-item", counterText(counter, 0));
    legend.append(item);
    guideLegend.nodes.push(item);
  }
  legend.hidden = labels.length === 0 && !counter;
  highlightGuideLegend({ index: 0 });
}

function counterText(counter, index) {
  return `${counter.prefix || ""}第 ${clampIndex(index, counter.count) + 1} / ${counter.count} 个${counter.noun}`;
}

function clampIndex(index, count) {
  return Math.max(0, Math.min(Number.isFinite(index) ? index : 0, Math.max(0, count - 1)));
}

/** Follow the animation: whichever item is on screen is the one marked current. */
function highlightGuideLegend(progress) {
  if (!guideLegend) return;
  const index = Number(progress?.index);
  guideLegend.labels.forEach((_, position) => {
    const node = guideLegend.nodes[position];
    if (!node) return;
    const active = position === index;
    node.className = active ? "calibration-guide-item current" : "calibration-guide-item";
    if (active) node.setAttribute?.("aria-current", "step");
    else node.removeAttribute?.("aria-current");
  });
  if (guideLegend.counter) {
    const node = guideLegend.nodes[guideLegend.nodes.length - 1];
    if (node) node.textContent = counterText(guideLegend.counter, index);
  }
}

/**
 * Show the current step's guide, reusing the panel that is already running.
 *
 * Returns the running view, or null when the page has nowhere to draw one; the
 * caller needs no more than that, since a missing picture leaves the words.
 */
function syncCalibrationGuide(flow) {
  const panel = guideElement(GUIDE_PANEL_ID);
  const canvas = guideElement(GUIDE_CANVAS_ID);
  if (!panel || !canvas) return null;
  const step = flow && flow.current ? flow.current : null;
  if (!step || !step.guide || !step.guide.kind) {
    releaseGuideView();
    panel.hidden = true;
    return null;
  }

  const guide = step.guide;
  panel.hidden = false;
  const caption = guideElement(GUIDE_CAPTION_ID);
  if (caption) caption.textContent = GUIDE_CAPTIONS[guide.kind] || GUIDE_NO_PICTURE;
  // The canvas is what a screen reader gets instead of the animation, so it
  // carries the step's own title rather than a description of the drawing.
  canvas.setAttribute?.("aria-label", `标定步骤示意图：${step.title}`);

  const Guide = globalThis.TangyingWebGL && globalThis.TangyingWebGL.CalibrationGuide;
  if (typeof Guide !== "function") {
    releaseGuideView();
    renderGuideLegend(guide);
    setGuideNote(GUIDE_NO_WEBGL);
    canvas.hidden = true;
    return null;
  }

  if (guideView && guideView.canvas === canvas) {
    guideView.show(guide);
  } else {
    releaseGuideView();
    try {
      guideView = new Guide(canvas, guide, { onProgress: highlightGuideLegend });
    } catch (_) {
      // The guide says it cannot draw; the panel says so in words and moves on.
      guideView = null;
    }
  }
  const unavailable = !guideView || guideView.status?.state === "UNAVAILABLE";
  canvas.hidden = unavailable;
  renderGuideLegend(guide);
  setGuideNote(unavailable ? GUIDE_NO_WEBGL : (GUIDE_CAPTIONS[guide.kind] ? "" : GUIDE_NO_PICTURE));
  if (guideView && guideView.progress) highlightGuideLegend(guideView.progress);
  else highlightGuideLegend({ index: 0 });
  return guideView;
}

function setGuideNote(text) {
  const note = guideElement(GUIDE_NOTE_ID);
  if (!note) return;
  note.textContent = text || "";
  note.hidden = !text;
}

/** Give the WebGL context back; idempotent, so a teardown may call it twice. */
function releaseGuideView() {
  guideView?.dispose?.();
  guideView = null;
}

/**
 * Give the WebGL context back, e.g. when the operator navigates away.
 *
 * Clearing the render key is the whole point of doing this here rather than in
 * `releaseGuideView`. That key means "this is what #calibration-body currently
 * shows", and tearing the animation down makes it untrue — leave it set and coming
 * back to this page rebuilds nothing, because the card flow still looks unchanged
 * and the render is skipped as a duplicate.
 */
function disposeCalibrationGuide() {
  releaseGuideView();
  const body = guideElement(GUIDE_BODY_ID);
  if (body?.dataset) delete body.dataset.renderKey;
  const panel = guideElement(GUIDE_PANEL_ID);
  if (panel) panel.hidden = true;
}

// Published last, once every declaration exists.
globalThis.TangyingCalibration = {
  buildCalibrationFlow,
  buildParameters,
  renderCalibrationNodes,
  renderParameters,
  stepGuide,
  syncCalibrationGuide,
  disposeCalibrationGuide,
};
