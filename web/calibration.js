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

// Published last, once every declaration exists.
globalThis.TangyingCalibration = {
  buildCalibrationFlow,
  buildParameters,
  renderCalibrationNodes,
  renderParameters,
};
