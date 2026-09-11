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
function buildCalibrationFlow(snapshot) {
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

  return {
    kind: finished ? FLOW_DONE : FLOW_RUNNING,
    reason: "",
    steps,
    completed,
    total,
    percent: total > 0 ? Math.round((completed / total) * 100) : 0,
    current,
    summary: String(snapshot.summary || ""),
    headline: finished ? "标定已完成" : (current ? `第 ${current.index} / ${total} 步：${current.title}` : "标定进行中"),
    hint: current
      ? current.instruction
      : (finished ? "参数已经保存，可以开始建图或执行任务了。" : "按终端里的提示操作，这里会同步显示进度。"),
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

// Published last, once every declaration exists.
globalThis.TangyingCalibration = {
  buildCalibrationFlow,
  renderCalibrationNodes,
};
