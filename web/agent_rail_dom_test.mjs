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
    parent: null,
    listeners: new Map(),
    append(...nodes) {
      // The parent link exists so remove() can work. Without it a test could not
      // tell "the status line was replaced by the result" from "the status line
      // is still there and the result was added next to it".
      for (const node of nodes) {
        if (node && typeof node === "object") node.parent = this;
      }
      this.children.push(...nodes);
    },
    replaceChildren(...nodes) {
      for (const node of nodes) {
        if (node && typeof node === "object") node.parent = this;
      }
      this.children = [...nodes];
    },
    addEventListener(name, callback) {
      this.listeners.set(name, callback);
    },
    click() {
      // The return value is passed through so an async handler can be awaited;
      // otherwise a test would have to guess when the work finished.
      return this.listeners.get("click")?.();
    },
    remove() {
      if (!this.parent) return;
      const index = this.parent.children.indexOf(this);
      if (index !== -1) this.parent.children.splice(index, 1);
      this.parent = null;
    },
    // Only the one selector shape the renderer uses: a list of class names.
    querySelectorAll(selector) {
      const wanted = String(selector).split(",").map((part) => part.trim().replace(/^\./, ""));
      return this.children.filter((child) => {
        const classes = String(child?.className || "").split(/\s+/);
        return wanted.some((name) => classes.includes(name));
      });
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
  // An `async function` slice that began at the word `function` would drop the
  // `async` and fail to parse the moment the body used `await` — a SyntaxError
  // that names the harness, not the missing keyword that caused it.
  if (app.slice(Math.max(0, start - 6), start) === "async ") start -= 6;
  // A binding with no braces of its own — `const visibleLimit = 5;` — has to end
  // at its semicolon. Brace counting would otherwise run on to the next braced
  // binding and swallow it, and the duplicate declaration then fails as a
  // SyntaxError that says nothing about the real cause.
  const firstBrace = app.indexOf("{", start);
  const firstSemicolon = app.indexOf(";", start);
  if (firstSemicolon !== -1 && (firstBrace === -1 || firstSemicolon < firstBrace)) {
    return app.slice(start, firstSemicolon + 1);
  }
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
//
// fetchImpl is injected rather than stubbed globally so the execution tests can
// assert on the request body the console actually sends — the path and the three
// ids are part of the contract with POST /v1/recovery/execute.
function defaultFetchStub() {
  throw new Error("this test did not install a fetch stub");
}

function loadAlertRenderer(nodes, currentTaskId = "", fetchImpl = defaultFetchStub) {
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
  // Every binding the renderer reaches for has to be pulled in by name: the
  // harness runs one function, not the file, so a missing helper is a
  // ReferenceError rather than a silently different result.
  const body = [
    extractFunction("makeTextElement"),
    extractFunction("agentAlertSeverityLabels"),
    extractFunction("agentAlertCodeLabels"),
    extractFunction("agentAlertTitle"),
    extractFunction("agentAlertVisibleLimit"),
    extractFunction("agentAlertSeverityRank"),
    extractFunction("rankAgentAlerts"),
    extractFunction("scopeAgentAlerts"),
    extractFunction("agentAlertNode"),
    extractFunction("recoveryTrailKindLabels"),
    extractFunction("recoveryExecutionTrailLabels"),
    extractFunction("renderRecoveryExecution"),
    extractFunction("runRecoveryStep"),
    extractFunction("recoveryFailure"),
    extractFunction("renderRecovery"),
    extractFunction("renderAgentAlerts"),
    "return renderAgentAlerts;",
  ].join("\n");
  return new Function(
    "document", "makeTextElement", "$", "localEventTaskId", "fetch", body,
  )(document, makeTextElement, $, currentTaskId, fetchImpl);
}

// renderAlerts feeds the banner the way the console does: an array of alerts from
// /v1/agent/alerts. It is separate from render() above because that one models an
// agent EVENT (topic/summary), while the banner consumes alerts (code/severity/
// recovery). Using the wrong fixture shape was a mistake made here once already.
function renderAlerts(alerts, options = {}) {
  const dom = alertDom(options.taskId || "", options.fetch);
  dom.render({
    alerts,
    supervision: { enabled: true, observing: true },
    runnerAlerts: options.runnerAlerts || [],
  });
  return dom;
}

function alertDom(currentTaskId = "", fetchImpl) {
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
    render: loadAlertRenderer(nodes, currentTaskId, fetchImpl),
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

// --- approving a step and running it ---------------------------------------

// A fetch recorder: it answers with a canned response and remembers what was
// asked. The request body is asserted on because the three ids are the contract
// with POST /v1/recovery/execute — sending the wrong planId would execute a real
// action under a plan nobody approved.
function fetchRecorder(response, ok = true) {
  const calls = [];
  const impl = async (url, init) => {
    calls.push({ url, method: init?.method, body: JSON.parse(init?.body || "{}") });
    return {
      ok,
      status: ok ? 200 : Number(response?.status || 400),
      json: async () => response,
    };
  };
  impl.calls = calls;
  return impl;
}

function runButtonFor(dom, index) {
  const steps = byClass(dom.list, "agent-recovery-step");
  assert.ok(steps[index], `no step ${index}`);
  const [button] = byClass(steps[index], "agent-recovery-run-button");
  assert.ok(button, `step ${index} has no run button`);
  return button;
}

test("every step gets its own approval control, labelled by what it needs", () => {
  // One button per step, not one per plan: approving a plan approves whatever the
  // plan later turns out to contain.
  const dom = renderAlerts([alertWithPlan()]);
  const steps = byClass(dom.list, "agent-recovery-step");
  assert.equal(byClass(dom.list, "agent-recovery-run-button").length, 2);
  assert.match(textOf(steps[0]), /执行这一步/);
  assert.match(textOf(steps[1]), /批准并执行这一步/);
});

test("clicking a step posts that action, its plan and its task", async () => {
  const calls = fetchRecorder({ actionId: "arm.home", executed: true, verified: false, reason: "已下发" });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls, taskId: "task-a" });
  await runButtonFor(dom, 1).click();

  assert.equal(calls.calls.length, 1);
  assert.equal(calls.calls[0].url, "/v1/recovery/execute");
  assert.equal(calls.calls[0].method, "POST");
  assert.deepEqual(calls.calls[0].body, {
    actionId: "arm.home",
    planId: "plan-trail-task-a-ANOMALY_UNVERIFIED_MUTATION@execution",
    taskId: "task-a",
  });
});

test("an executed but unverified action is not drawn as a recovery", async () => {
  // The closed-loop contract, where the operator is looking: "we ran something"
  // and "it is fixed" must not look the same.
  const calls = fetchRecorder({ actionId: "arm.home", executed: true, verified: false, reason: "已下发" });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  await runButtonFor(dom, 1).click();

  const [box] = byClass(dom.list, "agent-recovery-execution");
  assert.ok(box, "no execution result was rendered");
  assert.equal(box.dataset.executed, "true");
  assert.equal(box.dataset.verified, "false");
  assert.match(textOf(box), /已执行，但没有确认结果/);
  assert.match(textOf(box), /系统不会自动重试/);
});

test("a verified action says so", async () => {
  const calls = fetchRecorder({ actionId: "arm.home", executed: true, verified: true, verification: "姿态已回零" });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  await runButtonFor(dom, 1).click();

  const [box] = byClass(dom.list, "agent-recovery-execution");
  assert.equal(box.dataset.verified, "true");
  assert.match(textOf(box), /已执行并复验通过/);
  assert.match(textOf(box), /姿态已回零/);
  assert.doesNotMatch(textOf(box), /系统不会自动重试/);
});

test("a refusal is shown with the server's own sentence", async () => {
  // Rewording a catalog refusal here would be a second answer to "is this
  // allowed", and the two would drift.
  const calls = fetchRecorder(
    { code: "RECOVERY_ACTION_REFUSED", message: "复位急停要人确认现场安全", status: 409 }, false);
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  await runButtonFor(dom, 1).click();

  const [failure] = byClass(dom.list, "agent-recovery-execution-failure");
  assert.ok(failure, "a refusal rendered nothing");
  assert.equal(failure.dataset.code, "RECOVERY_ACTION_REFUSED");
  assert.match(textOf(failure), /没有执行/);
  assert.match(textOf(failure), /复位急停要人确认现场安全/);
  assert.deepEqual(byClass(dom.list, "agent-recovery-execution"), []);
});

test("the button cannot be pressed twice after the action has run", async () => {
  // A double-click must not become a second physical action.
  const calls = fetchRecorder({ actionId: "arm.home", executed: true, verified: false });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  const button = runButtonFor(dom, 1);
  await button.click();

  assert.equal(button.disabled, true, "the button stayed armed after a physical action ran");
  assert.match(button.textContent, /已执行/);
});

test("a refused action leaves the button usable again", async () => {
  // Nothing happened, so nothing is at risk from pressing again once the cause is
  // fixed. Disabling here would strand the operator with no way forward.
  const calls = fetchRecorder({ code: "RECOVERY_EXECUTION_UNAVAILABLE", message: "没有配置" }, false);
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  const button = runButtonFor(dom, 1);
  await button.click();

  assert.equal(button.disabled, false);
  assert.match(button.textContent, /批准并执行这一步/);
});

test("a run that called nothing is never drawn as an execution", async () => {
  // The bug this pins: a blocked run (no model configured) returned from the
  // server without an error, and the console drew "已执行，但没有确认结果" and
  // disabled the button — telling an operator a robot had moved when nothing had
  // been called at all.
  const calls = fetchRecorder({
    actionId: "arm.home", executed: false, verified: false,
    reason: "没有配置决策器，无法选择恢复动作",
    trail: [
      { name: "tools.resolved", detail: "recover_to_safe_pose" },
      { name: "not-executed", detail: "没有配置决策器，无法选择恢复动作" },
      { name: "verification.not-applicable", detail: "没有执行任何动作，因此没有可复验的结果" },
    ],
  });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  const button = runButtonFor(dom, 1);
  await button.click();

  const text = textOf(dom.list);
  assert.doesNotMatch(text, /已执行/, "a run that called nothing was reported as executed");
  assert.match(text, /没有执行任何动作/);
  assert.match(text, /无可复验的结果/);
  // Nothing happened, so pressing again is safe once the cause is fixed.
  assert.equal(button.disabled, false);
});

test("the execution trail is shown with the checkpoints a reviewer looks for", async () => {
  const calls = fetchRecorder({
    actionId: "arm.home", executed: true, verified: false,
    trail: [
      { name: "tools.resolved", detail: "recover_to_safe_pose" },
      { name: "approval.operator", detail: "操作者本人发起了这次执行" },
      { name: "executed", detail: "已下发" },
      { name: "verification.unavailable" },
    ],
  });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  await runButtonFor(dom, 1).click();

  const [trail] = byClass(dom.list, "agent-recovery-execution-trail");
  assert.ok(trail, "the execution trail was not rendered");
  const text = textOf(trail);
  assert.match(text, /工具已解析/);
  assert.match(text, /操作者批准/);
  assert.match(text, /没有配置复验/);
});

test("the decision rounds show what was chosen from", async () => {
  // "Chose the only option" and "chose one of nine" are different decisions, and
  // a record that dropped the alternatives would make them look the same.
  const calls = fetchRecorder({
    actionId: "arm.home", executed: true, verified: false,
    rounds: [{
      round: 1, tool: "recover_to_safe_pose", verdict: "CALLED", reason: "退出未知姿态",
      candidates: ["recover_to_safe_pose", "telemetry.read", "runtime.reconnect"],
    }],
  });
  const dom = renderAlerts([alertWithPlan()], { fetch: calls });
  await runButtonFor(dom, 1).click();

  const [rounds] = byClass(dom.list, "agent-recovery-rounds");
  assert.ok(rounds, "the decision rounds were not rendered");
  assert.match(textOf(rounds), /recover_to_safe_pose/);
  assert.match(textOf(rounds), /退出未知姿态/);
  assert.match(textOf(rounds), /可选：/);
});

test("a failed request does not leave the status line behind", async () => {
  // The "正在执行" line is a claim about right now. Leaving it after the call
  // settles would tell the operator something is still running.
  const impl = async () => { throw new Error("connection refused"); };
  const dom = renderAlerts([alertWithPlan()], { fetch: impl });
  await runButtonFor(dom, 1).click();

  assert.deepEqual(byClass(dom.list, "agent-recovery-run-status"), []);
  const [failure] = byClass(dom.list, "agent-recovery-execution-failure");
  assert.match(textOf(failure), /connection refused/);
});

test("the execution renderer places text as text, never as markup", () => {
  const start = app.indexOf("function renderRecoveryExecution(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  assert.doesNotMatch(body, /innerHTML/);
  assert.doesNotMatch(body, /insertAdjacentHTML/);
});

// --- the banner has to stay readable ---------------------------------------

// The banner is the first thing on the page, so an uncapped list of findings is
// not a cosmetic problem: the workspace was measured with 102 active findings, a
// banner 24,496 px tall, and the task input at y=24,729 — the thing the user came
// for was below a wall of history.
function manyAlerts(count, overrides = {}) {
  return Array.from({ length: count }, (_entry, index) => ({
    ...criticalAlert,
    id: `ANOMALY_ACTION_FAILED@component-${index}`,
    code: "ANOMALY_ACTION_FAILED",
    severity: "warning",
    taskId: `task-${index}`,
    ...overrides,
  }));
}

test("a hundred findings do not become a hundred rendered rows", () => {
  const dom = renderAlerts(manyAlerts(100));
  const rows = dom.list.children.filter(child => child.className === "agent-alert");
  assert.ok(rows.length <= 5, `the banner rendered ${rows.length} rows for 100 findings`);
  // The count is still honest: the summary says how many were not rendered.
  assert.match(textOf(dom.list), /本页还有 95 项/);
  assert.match(textOf(dom.list), /展开全部/);
});

test("the banner keeps a way to see everything", () => {
  const dom = renderAlerts(manyAlerts(12));
  const button = dom.list.children
    .flatMap(child => child.children || [])
    .find(child => String(child.className).includes("agent-alert-expand"));
  assert.ok(button, "the capped list offered no way to expand");
  button.click();
  const rows = dom.list.children.filter(child => child.className === "agent-alert");
  assert.equal(rows.length, 12, "expanding did not show every finding");
  assert.match(textOf(dom.list), /已显示全部/);
});

test("a robot-wide fault leads, ahead of task findings", () => {
  const dom = renderAlerts(
    manyAlerts(3, { severity: "critical" }),
    { runnerAlerts: [{ ...criticalAlert, id: "ANOMALY_SAFETY_STOP@estop", code: "ANOMALY_SAFETY_STOP", severity: "warning", taskId: "" }] },
  );
  const first = dom.list.children.find(child => child.className === "agent-alert");
  assert.match(textOf(first), /机器人处于急停/,
    "a fault on the robot itself must not be listed below findings about old tasks");
});

test("findings about another task are counted, not dropped", () => {
  const dom = renderAlerts(
    [
      { ...criticalAlert, id: "about-mine", taskId: "task-mine" },
      { ...criticalAlert, id: "about-other", taskId: "task-other" },
    ],
    { taskId: "task-mine" },
  );
  assert.match(textOf(dom.list), /其他任务还有 1 项/);
  assert.doesNotMatch(textOf(dom.list), /about-other/);
});

test("the finding is named in words, and the code stays available", () => {
  const dom = renderAlerts([{ ...criticalAlert, code: "ANOMALY_UNVERIFIED_MUTATION" }]);
  const text = textOf(dom.list);
  assert.match(text, /有个动作的结果没能确认/,
    "the banner still opens with a rule name instead of what happened");
  // Reachable for whoever is debugging, without being the headline.
  assert.match(text, /ANOMALY_UNVERIFIED_MUTATION/);
});

test("a finding nobody has a label for is still shown", () => {
  // Dropping it would be the one failure worse than an ugly label.
  const dom = renderAlerts([{ ...criticalAlert, code: "ANOMALY_SOMETHING_NEW" }]);
  assert.match(textOf(dom.list), /ANOMALY_SOMETHING_NEW/);
});

test("the list keeps a stable order across refreshes", () => {
  // The banner refreshes every five seconds; a list that reshuffles itself under
  // the reader's cursor is a list they cannot click.
  const alerts = manyAlerts(6, { severity: "critical" });
  const first = textOf(renderAlerts(alerts).list);
  const second = textOf(renderAlerts([...alerts].reverse()).list);
  assert.equal(first, second, "the same findings rendered in a different order");
});

// --- discovered robots, and pairing one --------------------------------------

// The panel an owner uses once, when the robot is new. Its promise: a robot that
// announced itself appears with a way to join it, and the way it fails says which
// failure it was — because "the robot refused me" and "the robot was never
// offering" send an owner to two different places.

function loadDiscoveryRenderer(nodes) {
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
    extractFunction("pairingStateLabels"),
    extractFunction("renderDiscoveredRobots"),
    "return renderDiscoveredRobots;",
  ].join("\n");
  return new Function("document", "makeTextElement", "$", body)(document, makeTextElement, $);
}

function discoveryDom() {
  const nodes = new Map([
    ["#discovered-panel", createElement("section")],
    ["#discovered-list", createElement("ul")],
    ["#discovered-hint", createElement("p")],
  ]);
  nodes.get("#discovered-panel").hidden = true;
  return {
    nodes,
    panel: nodes.get("#discovered-panel"),
    list: nodes.get("#discovered-list"),
    hint: nodes.get("#discovered-hint"),
    render: loadDiscoveryRenderer(nodes),
  };
}

const announcedRobot = {
  robotId: "xlerobot-0001",
  hostname: "xlerobot.local",
  address: "192.168.50.73:50051",
  adapter: "xlerobot",
  sourceIp: "192.168.50.73",
  pairingState: "open",
  needsPairing: true,
  pairingOpen: true,
  capabilityCount: 12,
};

test("a robot that announced itself is shown with a way to join it", () => {
  const dom = discoveryDom();
  dom.render({ listening: true, robots: [announcedRobot], unreadable: 0, mismatched: 0 });
  assert.equal(dom.panel.hidden, false);
  const text = textOf(dom.list);
  assert.match(text, /xlerobot-0001/);
  assert.match(text, /正在等待配对/);
  const button = dom.list.children
    .flatMap(child => child.children || [])
    .flatMap(child => child.children || [])
    .find(child => child.tagName === "BUTTON");
  assert.ok(button, "the panel offered no way to pair");
});

test("a robot that is not offering pairing says so before the attempt", () => {
  const dom = discoveryDom();
  dom.render({
    listening: true,
    robots: [{ ...announcedRobot, pairingState: "unpaired", pairingOpen: false }],
  });
  assert.match(textOf(dom.list), /没有开放配对窗口/);
});

test("an already paired robot is not offered a pairing form", () => {
  const dom = discoveryDom();
  dom.render({
    listening: true,
    robots: [{ ...announcedRobot, pairingState: "paired", needsPairing: false, pairingOpen: false }],
  });
  assert.match(textOf(dom.list), /已配对，可以使用/);
  assert.doesNotMatch(textOf(dom.list), /配对码/);
});

test("nothing found and nothing to explain stays out of the way", () => {
  // A device list that is always present and always empty is noise for the many
  // owners whose robot is already paired and working.
  const dom = discoveryDom();
  dom.render({ listening: true, robots: [] });
  assert.equal(dom.panel.hidden, true);
});

test("a robot this build cannot read is reported as a version problem", () => {
  const dom = discoveryDom();
  dom.render({ listening: true, robots: [], unreadable: 0, mismatched: 2 });
  assert.equal(dom.panel.hidden, false);
  assert.match(dom.hint.textContent, /版本读不了/);
});

test("not listening is not the same answer as nothing found", () => {
  const dom = discoveryDom();
  dom.render({ listening: false, robots: [] });
  assert.match(dom.hint.textContent, /没有在监听/);
});

test("the address and the identity are both shown, because they can disagree", () => {
  // The announced address is what the robot believes; the source is what the
  // network says. Showing one of them would make a disagreement invisible.
  const dom = discoveryDom();
  dom.render({ listening: true, robots: [announcedRobot] });
  assert.match(textOf(dom.list), /192\.168\.50\.73:50051/);
  assert.match(textOf(dom.list), /xlerobot\.local/);
});
