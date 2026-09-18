import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

// The problems page is exercised by running the real source against a stub DOM.
//
// A regex over the source would catch a function that stopped mentioning a field
// and nothing else: not a card that renders empty, not a filter that filters
// nothing, not an empty state that appears where problems should be. This page is
// the one an operator uses to decide what to do, so "it renders" is not enough to
// assert.

const source = await readFile(new URL("./problems.js", import.meta.url), "utf8");

function createElement(tag) {
  return {
    tagName: String(tag).toUpperCase(),
    className: "",
    textContent: "",
    dataset: {},
    attributes: {},
    children: [],
    hidden: false,
    disabled: false,
    listeners: new Map(),
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren(...nodes) { this.children = [...nodes]; },
    addEventListener(name, callback) { this.listeners.set(name, callback); },
    click() { return this.listeners.get("click")?.(); },
    setAttribute(name, value) { this.attributes[name] = String(value); },
    getAttribute(name) { return this.attributes[name]; },
    scrollIntoView() {},
  };
}

function classesOf(node) {
  return String(node.className || "").split(/\s+/).filter(Boolean);
}

function byClass(node, className) {
  const found = [];
  const walk = (current) => {
    if (!current || typeof current !== "object") return;
    if (classesOf(current).includes(className)) found.push(current);
    for (const child of current.children || []) walk(child);
  };
  walk(node);
  return found;
}

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

// loadPage builds the page against a stub document with the elements index.html
// provides, and hands back the renderer.
function loadPage() {
  const nodes = new Map([
    ["#problems-list", createElement("div")],
    ["#problems-filters", createElement("div")],
    ["#problems-headline", createElement("span")],
    ["#problems-subline", createElement("p")],
    ["#local-task-id", createElement("input")],
  ]);
  const document = {
    createElement,
    querySelector: (selector) => nodes.get(selector) || null,
  };
  const window = {
    tangyingAlertLabels: {
      codes: { ANOMALY_ABNORMAL_TASK: "有任务没能正常结束" },
      severities: { critical: "严重", warning: "注意" },
      handling: {
        automatic: { text: "系统可自行处理" },
        approval: { text: "需要你批准" },
        human: { text: "需要你判断" },
      },
    },
    tangyingRenderRecovery: () => null,
  };
  // The module is an IIFE that publishes itself on window.
  new Function("window", "document", "fetch", "location", "CSS", source)(
    window, document, () => { throw new Error("no fetch in this test"); },
    { hash: "" }, { escape: (value) => value },
  );
  return { page: window.tangyingProblems, nodes };
}

function group(overrides = {}) {
  return {
    identity: "ANOMALY_ABNORMAL_TASK@task",
    code: "ANOMALY_ABNORMAL_TASK",
    severity: "warning",
    handling: "human",
    message: "有任务异常结束",
    why: "没有可自动执行的恢复动作",
    active: true,
    count: 34,
    taskCount: 3,
    tasks: ["task-a", "task-b", "task-c"],
    ...overrides,
  };
}

function payload(groups, activeCount) {
  return {
    groups,
    activeCount: activeCount ?? groups.reduce((total, entry) => total + entry.count, 0),
  };
}

test("a problem is one card, however many reports it took", () => {
  const { page, nodes } = loadPage();
  page.render(payload([group({ count: 34, taskCount: 21 })]));
  const cards = byClass(nodes.get("#problems-list"), "problem-card");
  assert.equal(cards.length, 1, "34 reports became more than one card");
  assert.match(textOf(cards[0]), /影响 21 个任务/);
  assert.match(textOf(cards[0]), /共 34 次报告/);
});

test("the headline shows problems and the subline shows reports", () => {
  // Both numbers, because their difference is the finding: a few problems
  // described by hundreds of reports is a system that is noisy.
  const { page, nodes } = loadPage();
  page.render(payload([
    group({ identity: "A@x", count: 20, taskCount: 10 }),
    group({ identity: "B@y", count: 5, taskCount: 2, handling: "automatic" }),
  ]));
  assert.equal(nodes.get("#problems-headline").textContent, "2 个问题");
  const subline = nodes.get("#problems-subline").textContent;
  assert.match(subline, /1 个需要你判断/);
  assert.match(subline, /1 个系统可自行处理/);
  assert.match(subline, /共 25 条报告/);
});

test("the handling verdict is on the card, not hidden behind a click", () => {
  // It is the answer to the question the operator opened the page with.
  const { page, nodes } = loadPage();
  page.render(payload([group({ handling: "approval" })]));
  const [card] = byClass(nodes.get("#problems-list"), "problem-card");
  assert.equal(card.dataset.handling, "approval");
  assert.match(textOf(card), /需要你批准/);
});

test("resolved problems are not listed", () => {
  const { page, nodes } = loadPage();
  page.render(payload([group({ active: false })]));
  assert.deepEqual(byClass(nodes.get("#problems-list"), "problem-card"), []);
});

test("filtering keeps the reader on the page and counts what is behind each filter", () => {
  const { page, nodes } = loadPage();
  page.render(payload([
    group({ identity: "A@x", handling: "human" }),
    group({ identity: "B@y", handling: "automatic" }),
  ]));
  const filters = byClass(nodes.get("#problems-filters"), "problems-filter");
  assert.equal(filters.length, 4, "one chip per handling plus all");
  assert.match(textOf(filters[0]), /全部 2/);
  assert.match(textOf(filters[1]), /需要我判断 1/);
  assert.match(textOf(filters[3]), /系统可自行处理 1/);

  filters[3].click();
  const shown = byClass(nodes.get("#problems-list"), "problem-card");
  assert.equal(shown.length, 1, "the filter did not filter");
  assert.equal(shown[0].dataset.handling, "automatic");
});

test("a filter with nothing behind it is disabled rather than hidden", () => {
  // Hiding it makes the set of categories change as problems come and go, and a
  // reader cannot then learn where things go.
  const { page, nodes } = loadPage();
  page.render(payload([group({ handling: "human" })]));
  const filters = byClass(nodes.get("#problems-filters"), "problems-filter");
  const automatic = filters.find((chip) => chip.dataset.filter === "automatic");
  assert.ok(automatic, "the chip disappeared instead of being disabled");
  assert.equal(automatic.disabled, true);
  assert.match(textOf(automatic), /系统可自行处理 0/);
});

test("no problems reads as a state, not as a blank page", () => {
  const { page, nodes } = loadPage();
  page.render(payload([]));
  const empty = byClass(nodes.get("#problems-list"), "problems-empty");
  assert.equal(empty.length, 1);
  assert.match(textOf(empty[0]), /当前没有未处理的问题/);
  assert.equal(nodes.get("#problems-headline").textContent, "没有问题");
});

test("a filter that hides everything explains itself", () => {
  const { page, nodes } = loadPage();
  page.render(payload([group({ identity: "A@x", handling: "human" })]));
  const automatic = byClass(nodes.get("#problems-filters"), "problems-filter")
    .find((chip) => chip.dataset.filter === "automatic");
  // Disabled chips cannot be clicked, so drive the renderer the way a stale
  // selection would: filter, then re-render with a set that has none of it.
  page.render(payload([group({ identity: "A@x", handling: "automatic" })]));
  automatic.click();
  page.render(payload([group({ identity: "A@x", handling: "human" })]));
  const empty = byClass(nodes.get("#problems-list"), "problems-empty");
  assert.equal(empty.length, 1);
  assert.match(textOf(empty[0]), /其他类别里还有/);
});

test("the detail is collapsed until asked for, and then shows the reasoning", () => {
  const { page, nodes } = loadPage();
  page.render(payload([group({ recommendedActions: ["先观测机器人当前姿态"] })]));
  const [collapsed] = byClass(nodes.get("#problems-list"), "problem-detail");
  assert.equal(collapsed.hidden, true, "the detail was open before anybody asked");

  const toggle = byClass(nodes.get("#problems-list"), "problem-actions")[0].children[0];
  toggle.click();
  const [opened] = byClass(nodes.get("#problems-list"), "problem-detail");
  assert.equal(opened.hidden, false);
  assert.match(textOf(opened), /先观测机器人当前姿态/);
});

test("the retry prohibition is stated where the problem is", () => {
  // It is the one piece of advice that must never be softened or buried.
  const { page, nodes } = loadPage();
  page.render(payload([group({ automaticRetryForbidden: true })]));
  assert.match(textOf(nodes.get("#problems-list")), /禁止自动重试/);
});

test("a render failure is stated on the page, not left blank", () => {
  // A page that silently renders nothing looks exactly like a system with no
  // problems, which is the one outcome an alerting surface must never produce.
  const { page, nodes } = loadPage();
  page.renderError(new Error("Cannot read properties of undefined"));
  const empty = byClass(nodes.get("#problems-list"), "problems-empty");
  assert.equal(empty.length, 1, "a render failure left the page blank");
  assert.match(textOf(empty[0]), /渲染失败/);
  assert.match(textOf(empty[0]), /Cannot read properties of undefined/);
  assert.match(nodes.get("#problems-headline").textContent, /失败/);
  // The line that matters most: a blank surface reads as a healthy system, so the
  // failure has to say out loud that it is not one.
  assert.match(nodes.get("#problems-subline").textContent, /这不代表没有问题/);
});

test("a failure does not leave the previous problems on screen", () => {
  // Stale cards under a failure banner would be read as current.
  const { page, nodes } = loadPage();
  page.render(payload([group()]));
  assert.equal(byClass(nodes.get("#problems-list"), "problem-card").length, 1);
  page.renderError(new Error("boom"));
  assert.deepEqual(byClass(nodes.get("#problems-list"), "problem-card"), []);
});
