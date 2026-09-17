import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The rail renderer is exercised by running the real source, not by matching
// patterns in it.
//
// The rest of the frontend suite asserts on source text, which cannot catch a
// renderer that builds the wrong DOM: it only catches one that stopped mentioning
// a field. That gap is not hypothetical — during this session two wiring bugs
// survived a green frontend suite (a missing event sink and an unattributed
// event), because the suite never executed the code.
//
// So this harness provides the three DOM calls the renderer actually uses and
// runs the function for real. It is deliberately minimal: a stub that grew into a
// DOM implementation would be a second thing to get wrong.

const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

// --- a DOM small enough to reason about -----------------------------------

function createElement(tag) {
  return {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    dataset: {},
    children: [],
    hidden: false,
    append(...nodes) {
      this.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      this.children = [...nodes];
    },
  };
}

// extractTopLevel returns one top-level binding's source by brace counting, so
// the file's boot sequence (which talks to the network and the page) never runs.
//
// It handles both `function name(` and `const name =`, because a renderer may
// depend on a module-level constant (the severity labels do) and pulling in the
// function alone would fail with a bare ReferenceError.
function extractTopLevel(name) {
  let start = app.indexOf(`function ${name}(`);
  if (start === -1) start = app.indexOf(`const ${name} =`);
  assert.notEqual(start, -1, `${name} is missing from app.js`);
  let depth = 0;
  for (let index = app.indexOf("{", start); index < app.length; index += 1) {
    if (app[index] === "{") depth += 1;
    if (app[index] === "}") {
      depth -= 1;
      if (depth === 0) return app.slice(start, index + 1);
    }
  }
  throw new Error(`unbalanced braces in ${name}`);
}

// extractFunction keeps the original call sites readable.
const extractFunction = extractTopLevel;

function loadRenderer(container) {
  const document = {
    createElement,
    querySelector: (selector) => (selector === "#fleet-agent-events" ? container : null),
  };
  const makeTextElement = (tag, className, text) => {
    const element = createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text || "");
    return element;
  };
  // `$` in app.js is the one-line query helper the whole file uses.
  const $ = (selector) => document.querySelector(selector);
  const body = [
    extractFunction("makeTextElement"),
    extractFunction("renderMissionAgentEvents"),
    "return renderMissionAgentEvents;",
  ].join("\n");
  return new Function("document", "makeTextElement", "$", body)(document, makeTextElement, $);
}

function render(agentEvents) {
  const container = createElement("div");
  const renderer = loadRenderer(container);
  renderer(agentEvents);
  return container;
}

// Flattens the rendered tree so assertions read as "what would a person see".
function textOf(node) {
  const parts = [];
  const walk = (current) => {
    if (!current || typeof current !== "object") return;
    if (current.textContent) parts.push(String(current.textContent));
    for (const child of current.children || []) walk(child);
  };
  walk(node);
  return parts.join(" | ");
}

function byClass(node, className) {
  const found = [];
  const walk = (current) => {
    if (!current || typeof current !== "object") return;
    if (current.className && String(current.className).split(/\s+/).includes(className)) {
      found.push(current);
    }
    for (const child of current.children || []) walk(child);
  };
  walk(node);
  return found;
}

// --- the cases -------------------------------------------------------------

const unverifiedMutation = {
  sequence: 1,
  topic: "ops.anomaly_detected",
  agent: "ops",
  severity: "critical",
  code: "ANOMALY_UNVERIFIED_MUTATION",
  summary: "有物理动作已下发但没有确认结果，必须先对账再决定下一步",
  evidenceIds: ["task-a/pick"],
};

const hypothesisWithAdvice = {
  sequence: 2,
  topic: "ops.root_cause_hypothesis",
  agent: "ops",
  confidence: 1,
  recommendedActions: [
    "先观测机器人当前姿态与目标物体位置，确认这个动作到底有没有发生",
    "用观测结果对账后再决定继续、重做还是交给人工",
  ],
  automaticRetryForbidden: true,
  missingEvidence: ["步骤的执行记录没有证据编号，无法指名要复核的观测"],
};

test("a finding renders with its code, severity and evidence as text", () => {
  const view = render([unverifiedMutation]);
  const text = textOf(view);
  assert.match(text, /ANOMALY_UNVERIFIED_MUTATION/);
  assert.match(text, /观察 agent/);
  assert.match(text, /ops\.anomaly_detected/);
  assert.match(text, /task-a\/pick/, "evidence must be shown so a reader can check the claim");
  const [card] = byClass(view, "mission-agent-event");
  assert.ok(card, "no finding card was rendered");
  assert.match(card.className, /critical/);
});

test("the recommended actions are rendered as an ordered list a person can follow", () => {
  const view = render([hypothesisWithAdvice]);
  const [advice] = byClass(view, "mission-agent-advice");
  assert.ok(advice, "the advice block was not rendered; a diagnosis without advice is a log line");
  assert.match(textOf(advice), /建议动作/);
  const items = advice.children.find((child) => child.tagName === "OL");
  assert.ok(items, "the actions were not rendered as a list");
  assert.equal(items.children.length, 2);
  assert.match(items.children[0].textContent, /先观测机器人当前姿态/);
  // Order is the advice: the first action is the one to try first.
  assert.match(items.children[1].textContent, /再决定继续/);
});

test("the retry prohibition is rendered as its own warning, not a list item", () => {
  const view = render([hypothesisWithAdvice]);
  const [warning] = byClass(view, "mission-agent-forbidden");
  assert.ok(
    warning,
    "the retry prohibition was not rendered as a warning band; a reader skimming must not miss it",
  );
  assert.match(warning.textContent, /禁止自动重试/);
  // It must stand outside the action list, so it cannot be dismissed as one
  // suggestion among several.
  const [advice] = byClass(view, "mission-agent-advice");
  assert.ok(!advice || !advice.children.includes(warning));
});

test("missing evidence is rendered so an incomplete diagnosis says what is absent", () => {
  const view = render([hypothesisWithAdvice]);
  const [gaps] = byClass(view, "mission-agent-missing");
  assert.ok(gaps, "the missing-evidence block was not rendered");
  assert.match(textOf(gaps), /还缺什么/);
  assert.match(textOf(gaps), /无法指名要复核的观测/);
});

test("a finding with no advice renders without an empty advice block", () => {
  // An observation carries no recommendation; the explanation does. An empty
  // "建议动作" heading would tell a reader there is advice and then show none.
  const view = render([unverifiedMutation]);
  assert.deepEqual(byClass(view, "mission-agent-advice"), []);
  assert.deepEqual(byClass(view, "mission-agent-forbidden"), []);
  assert.deepEqual(byClass(view, "mission-agent-missing"), []);
});

test("an empty replay explains itself instead of rendering a blank card", () => {
  const view = render([]);
  assert.match(textOf(view), /这里会列出执行 agent 与观察 agent 的事件/);
  assert.deepEqual(byClass(view, "mission-agent-event"), []);
});

test("a missing agent name falls back to the raw name rather than disappearing", () => {
  // A future agent must be visible before someone adds a label for it.
  const view = render([{ ...unverifiedMutation, agent: "escalation" }]);
  assert.match(textOf(view), /escalation/);
});

test("several findings each get their own card, in the order given", () => {
  const view = render([unverifiedMutation, hypothesisWithAdvice]);
  const cards = byClass(view, "mission-agent-event");
  assert.equal(cards.length, 2);
  assert.match(textOf(cards[0]), /ANOMALY_UNVERIFIED_MUTATION/);
  assert.match(textOf(cards[1]), /建议动作/);
});

test("severity drives the card class the stylesheet keys on", () => {
  for (const severity of ["critical", "warning", "info"]) {
    const view = render([{ ...unverifiedMutation, severity }]);
    const [card] = byClass(view, "mission-agent-event");
    assert.match(card.className, new RegExp(severity), `severity ${severity} did not reach the card`);
  }
});

test("rendering twice does not append to the previous result", () => {
  const container = createElement("div");
  const renderer = loadRenderer(container);
  renderer([unverifiedMutation]);
  const first = container.children.length;
  renderer([unverifiedMutation]);
  assert.equal(container.children.length, first, "the rail accumulated cards instead of replacing them");
});

// --- the alert banner ------------------------------------------------------

// The banner is driven by server-side state through /v1/agent/alerts, so it is
// loaded the same way: by running the real function against a stub DOM.
function loadAlertRenderer(nodes) {
  const document = {
    createElement,
    querySelector: (selector) => nodes.get(selector) || null,
  };
  const makeTextElement = (tag, className, text) => {
    const element = createElement(tag);
    if (className) element.className = className;
    element.textContent = String(text || "");
    return element;
  };
  const $ = (selector) => document.querySelector(selector);
  const body = [
    extractFunction("makeTextElement"),
    extractFunction("agentAlertSeverityLabels"),
    extractFunction("recoveryTrailKindLabels"),
    extractFunction("renderRecovery"),
    extractFunction("renderAgentAlerts"),
    "return renderAgentAlerts;",
  ].join("\n");
  return new Function("document", "makeTextElement", "$", body)(document, makeTextElement, $);
}

// renderAlerts feeds the banner the way the console does: an array of alerts from
// /v1/agent/alerts. It is separate from render() above because that one models an
// agent EVENT (topic/summary), while the banner consumes alerts (code/severity/
// recovery). Using the wrong fixture shape was a mistake made here once already.
function renderAlerts(alerts) {
  const dom = alertDom();
  dom.render({ alerts, supervision: { enabled: true, observing: true } });
  return dom;
}

function alertDom() {
  const nodes = new Map([
    ["#agent-alert-banner", createElement("section")],
    ["#agent-alert-list", createElement("ul")],
    ["#agent-alert-count", createElement("span")],
    ["#agent-alert-supervision", createElement("p")],
  ]);
  const banner = nodes.get("#agent-alert-banner");
  const supervision = nodes.get("#agent-alert-supervision");
  // The renderer reads `.hidden` to decide whether the banner must show, so the
  // stub starts in the same state the markup does.
  banner.hidden = true;
  supervision.hidden = true;
  return {
    nodes,
    banner,
    list: nodes.get("#agent-alert-list"),
    count: nodes.get("#agent-alert-count"),
    supervision,
    render: loadAlertRenderer(nodes),
  };
}

const criticalAlert = {
  id: "ANOMALY_UNVERIFIED_MUTATION@execution",
  taskId: "task-a",
  agent: "ops",
  topic: "ops.anomaly_detected",
  code: "ANOMALY_UNVERIFIED_MUTATION",
  severity: "critical",
  message: "有物理动作已下发但没有确认结果，必须先对账再决定下一步",
  active: true,
  recommendedActions: ["先观测机器人当前姿态与目标物体位置"],
  automaticRetryForbidden: true,
};

test("a critical finding raises the banner with the warning icon", () => {
  const dom = alertDom();
  dom.render({ alerts: [criticalAlert], supervision: { enabled: true, observing: true } });
  assert.equal(dom.banner.hidden, false, "the banner stayed hidden with a critical finding active");
  assert.equal(dom.banner.dataset.tone, "danger");
  assert.match(textOf(dom.list), /有物理动作已下发但没有确认结果/);
  assert.match(textOf(dom.list), /禁止自动重试/);
  assert.match(textOf(dom.list), /先观测机器人当前姿态/);
  assert.match(dom.count.textContent, /1 项需要处理/);
});

test("a resolved finding does not raise the banner", () => {
  const dom = alertDom();
  dom.render({
    alerts: [{ ...criticalAlert, active: false }],
    supervision: { enabled: true, observing: true },
  });
  assert.equal(dom.banner.hidden, true, "a resolved finding still raised the banner");
  assert.deepEqual(dom.list.children, []);
});

test("a system with no supervision says so even with no findings", () => {
  // An unwatched system looks exactly like a healthy one. This is the only place
  // that difference can be surfaced.
  const dom = alertDom();
  dom.render({
    alerts: [],
    supervision: { enabled: false, observing: false, reason: "agent runtime is not running" },
  });
  assert.equal(dom.supervision.hidden, false, "supervision was off and the banner said nothing");
  assert.match(dom.supervision.textContent, /没有监督 agent/);
  assert.equal(dom.banner.hidden, false, "the banner hid a missing supervisor");
});

test("an observing agent that is switched off is reported as not observing", () => {
  const dom = alertDom();
  dom.render({
    alerts: [],
    supervision: { enabled: true, observing: false, agents: ["task"] },
  });
  assert.equal(dom.supervision.hidden, false);
  assert.match(dom.supervision.textContent, /没有监督 agent/);
});

test("a healthy supervised system shows nothing at all", () => {
  const dom = alertDom();
  dom.render({ alerts: [], supervision: { enabled: true, observing: true, agents: ["task", "ops"] } });
  assert.equal(dom.banner.hidden, true);
  assert.equal(dom.supervision.hidden, true);
});

test("several findings are listed with the most severe banner tone", () => {
  const dom = alertDom();
  dom.render({
    alerts: [criticalAlert, { ...criticalAlert, id: "w1", severity: "warning", code: "ANOMALY_STEP_LATENCY" }],
    supervision: { enabled: true, observing: true },
  });
  assert.equal(dom.banner.dataset.tone, "danger");
  assert.equal(dom.list.children.length, 2);
  assert.match(dom.count.textContent, /2 项需要处理/);
});

test("re-rendering replaces the list instead of accumulating it", () => {
  const dom = alertDom();
  dom.render({ alerts: [criticalAlert], supervision: { enabled: true, observing: true } });
  dom.render({ alerts: [criticalAlert], supervision: { enabled: true, observing: true } });
  assert.equal(dom.list.children.length, 1, "the banner accumulated findings across polls");
});

test("alert text is placed as text, never as markup", () => {
  const start = app.indexOf("function renderAgentAlerts(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  assert.doesNotMatch(body, /innerHTML/);
  assert.doesNotMatch(body, /insertAdjacentHTML/);
});

// --- the recovery plan and its investigation --------------------------------

// Builds an alert carrying a plan, so the panel is exercised through the same
// path the console uses rather than by calling the renderer directly.
function alertWithPlan(overrides = {}) {
  return {
    ...criticalAlert,
    recovery: {
      planId: "plan-trail-task-a-ANOMALY_UNVERIFIED_MUTATION@execution",
      verdict: "PLAN",
      diagnosis: "有物理动作结果未知，需先对账",
      confidence: 0.9,
      source: "deterministic",
      steps: [
        { order: 1, action: "execution.read-history", summary: "读取执行记录", risk: "read_only", requiresApproval: false, why: "先确认现状" },
        { order: 2, action: "arm.home", summary: "机械臂回零", risk: "bounded_write", requiresApproval: true, why: "退出未知姿态" },
      ],
      refused: [{ action: "estop.release", summary: "复位急停", reason: "复位急停要人确认现场安全" }],
      ...overrides.recovery,
    },
    investigation: {
      trailId: "trail-task-a", trigger: "ANOMALY_UNVERIFIED_MUTATION",
      stepCount: 2,
      catalogSize: 16, neverAutomatic: 3,
      steps: [
        { sequence: 1, kind: "decision", name: "catalog.read", summary: "读取恢复动作目录", rows: 16 },
        {
          sequence: 2, kind: "query", name: "step_runs.read",
          summary: "读取该任务的执行记录",
          source: "sqlite:step_runs (task_id = task-a)",
          sourceDetail: { kind: "sqlite", table: "step_runs", query: "task_id = task-a" },
          rows: 3,
        },
        { sequence: 3, kind: "query", name: "tasks.read", summary: "读取任务状态", error: "database is locked" },
      ],
      ...overrides.investigation,
    },
  };
}

test("the panel shows which database and which table were read", () => {
  const dom = renderAlerts([alertWithPlan()]);
  const text = textOf(dom.list);
  // The requirement is explicit: an operator must see what was consulted. The
  // parts are rendered separately so they can be read without parsing a string.
  assert.match(text, /判断过程/, "the investigation is not shown at all");
  assert.match(text, /来源：sqlite/);
  assert.match(text, /表：step_runs/);
  assert.match(text, /条件：task_id = task-a/);
  assert.match(text, /记录数：3/);
  // The kind of each step is labelled, so a rule is distinguishable from a read.
  assert.match(text, /查询/);
  assert.match(text, /判断/);
});

test("the panel shows a failed step rather than hiding it", () => {
  const dom = renderAlerts([alertWithPlan()]);
  assert.match(textOf(dom.list), /该步失败：database is locked/);
});

test("approval is stated per step, not once for the plan", () => {
  const dom = renderAlerts([alertWithPlan()]);
  const steps = byClass(dom.list, "agent-recovery-step");
  assert.equal(steps.length, 2);
  assert.equal(steps[0].dataset.approval, "none");
  assert.match(textOf(steps[0]), /只读，无需批准/);
  assert.equal(steps[1].dataset.approval, "required");
  assert.match(textOf(steps[1]), /需要批准/);
});

test("the panel says where the proposal came from", () => {
  // A reader must be able to tell a table lookup from a model's reasoning.
  const deterministic = textOf(renderAlerts([alertWithPlan()]).list);
  assert.match(deterministic, /来自规则/);
  assert.match(deterministic, /把握 90%/);

  const withModel = textOf(renderAlerts([alertWithPlan({
    recovery: { source: "deterministic+model" },
  })]).list);
  assert.match(withModel, /来自规则 \+ 模型/);
});

test("an escalation is rendered as the answer, not as a missing plan", () => {
  const dom = renderAlerts([alertWithPlan({
    recovery: {
      verdict: "ESCALATE", steps: [],
      escalateReason: "工作台高度与标定不符，需要人重新标定工作台",
    },
  })]);
  const [section] = byClass(dom.list, "agent-recovery");
  assert.ok(section, "no recovery panel");
  assert.equal(section.dataset.verdict, "escalate");
  assert.match(textOf(dom.list), /需要人工处理/);
  assert.match(textOf(dom.list), /交给人工：工作台高度与标定不符/);
});

test("the actions the system will never take are shown with their reasons", () => {
  // The boundary must be visible: an operator sees which actions are refused and
  // why, rather than inferring the limit from a missing step.
  const dom = renderAlerts([alertWithPlan()]);
  const [refused] = byClass(dom.list, "agent-recovery-refused");
  assert.ok(refused, "the refused actions are not shown");
  assert.match(textOf(refused), /系统不会自动执行/);
  assert.match(textOf(refused), /estop\.release：复位急停要人确认现场安全/);
});

test("an alert with no plan renders no recovery panel", () => {
  const dom = renderAlerts([criticalAlert]);
  assert.deepEqual(byClass(dom.list, "agent-recovery"), []);
});

test("recovery text is placed as text, never as markup", () => {
  const start = app.indexOf("function renderRecovery(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  assert.doesNotMatch(body, /innerHTML/);
  assert.doesNotMatch(body, /insertAdjacentHTML/);
});
