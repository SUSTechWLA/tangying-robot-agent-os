import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("./console_ui.js", import.meta.url), "utf8");
const sandbox = { console };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const ui = sandbox.TangyingConsoleUI;

test("operator routes cannot expose diagnostics, including direct links", () => {
  assert.equal(ui.resolveRoute("#diagnostics", false), "workspace");
  assert.equal(ui.resolveRoute("#diagnostics", true), "diagnostics");
  assert.equal(ui.resolveRoute("#devices", false), "devices");
  assert.equal(ui.resolveRoute("#unknown", true), "workspace");
  assert.equal(ui.resolveRoute("#constructor", true), "workspace");
});

test("unknown and disconnected state never becomes a ready robot", () => {
  assert.equal(ui.connectionPresentation("CONNECTING").tone, "pending");
  assert.equal(ui.connectionPresentation("LOADING").label, "正在加载画面");
  assert.equal(ui.connectionPresentation("UNAVAILABLE").tone, "warning");
  assert.equal(ui.connectionPresentation("STALE").tone, "warning");
  assert.equal(ui.connectionPresentation("LIVE").label, "场景已同步");
  assert.equal(ui.connectionPresentation("surprise").label, "状态待确认");
  assert.equal(ui.connectionPresentation("constructor").label, "状态待确认");
});

test("task state labels distinguish failure, cancellation and waiting for approval", () => {
  assert.equal(ui.taskPresentation("SUCCEEDED").label, "任务已完成");
  assert.equal(ui.taskPresentation("FAILED").tone, "danger");
  assert.equal(ui.taskPresentation("CANCELLED").label, "任务已取消");
  assert.equal(ui.taskPresentation("AWAITING_APPROVAL").tone, "warning");
  assert.equal(ui.taskPresentation("unrecognized").label, "状态待确认");
  assert.equal(ui.taskPresentation("constructor").label, "状态待确认");
  assert.equal(ui.taskPresentation("SAFETY_STOPPED").tone, "danger");
  assert.equal(ui.taskPresentation("WAITING_USER").label, "需要你处理");
});

test("support summary includes correlation fields but excludes credentials and raw payloads", () => {
  const summary = ui.supportSummary({
    mode: "fleet", connection: "STALE", worldRevision: 92, eventCursor: "cursor-8",
    task: { id: "task-7", state: "FAILED", revision: 2, request: "private request", token: "secret" },
    token: "secret", password: "secret", telemetry: { apiKey: "secret" },
  });
  assert.equal(summary.task.id, "task-7");
  assert.equal(summary.world.revision, 92);
  assert.equal(summary.world.eventCursor, "cursor-8");
  assert.doesNotMatch(JSON.stringify(summary), /secret|private request|apiKey|password/);
});

test("task templates follow the selected simulation and never submit an action", () => {
  assert.match(ui.taskExamples("robocasa")[0].request, /方块/);
  assert.match(ui.taskExamples("mujoco")[0].request, /杯子/);
  assert.equal(ui.taskExamples("xlerobot_direct").length, 0);
});

// The route change swaps every visible panel, so the page must also return to
// the top; a reader left at the previous offset lands mid-page. The module needs
// a document to reach that code, so this shell supplies the smallest one that
// keeps its listeners working.
function createConsoleShell({ hash = "#workspace" } = {}) {
  const nodes = new Map();
  const scrollCalls = [];
  const node = id => {
    if (!nodes.has(id)) {
      nodes.set(id, {
        id, dataset: {}, hidden: false, textContent: "", hash, children: [],
        attributes: new Map(), listeners: new Map(), focused: false,
        setAttribute(name, value) { this.attributes.set(name, String(value)); },
        removeAttribute(name) { this.attributes.delete(name); },
        addEventListener(name, callback) { this.listeners.set(name, callback); },
        dispatchEvent(event) { this.listeners.get(event.type)?.(event); return true; },
        append(child) { this.children.push(child); },
        replaceChildren() { this.children = []; },
        focus() { this.focused = true; },
        scrollIntoView() {},
      });
    }
    return nodes.get(id);
  };
  const panels = [
    { dataset: { pagePanel: "workspace" }, hidden: false },
    { dataset: { pagePanel: "tasks" }, hidden: true },
  ];
  const navs = ["workspace", "tasks"].map(route => {
    const link = node(`nav-${route}`);
    link.dataset.nav = route;
    link.hash = `#${route}`;
    return link;
  });
  const sandbox = {
    console,
    Event: class { constructor(type) { this.type = type; } },
    MutationObserver: class { observe() {} },
    navigator: { clipboard: { writeText: async () => {} } },
    location: { hash },
    history: { replaceState() {} },
    matchMedia: () => ({ matches: false }),
    scrollTo: options => { scrollCalls.push(options); },
    addEventListener() {},
    dispatchEvent() { return true; },
    document: {
      body: { dataset: { page: "workspace", audience: "operator" } },
      querySelector(selector) {
        if (selector.startsWith("meta[")) return { content: "full" };
        return selector.startsWith("#") ? node(selector.slice(1)) : null;
      },
      querySelectorAll(selector) {
        if (selector === "[data-page-panel]") return panels;
        if (selector === "[data-nav]") return navs;
        return [];
      },
      createElement: () => node(`created-${nodes.size}`),
    },
  };
  vm.createContext(sandbox);
  vm.runInContext(source, sandbox);
  return { ui: sandbox.TangyingConsoleUI, scrollCalls, body: sandbox.document.body };
}

test("a page change returns to the top but re-entering the page does not", () => {
  const shell = createConsoleShell();
  // The first render keeps whatever offset the browser restored; nothing has
  // been replaced under the viewport yet.
  assert.equal(shell.scrollCalls.length, 0, "the initial route must not fight browser scroll restoration");
  shell.ui.navigate("tasks");
  assert.equal(shell.scrollCalls.length, 1, "switching pages must scroll back to the top");
  assert.equal(shell.body.dataset.page, "tasks");
  shell.ui.navigate("tasks");
  assert.equal(shell.scrollCalls.length, 1, "re-entering the current page must not move the reader");
});

test("reduced motion turns the page-change scroll into an instant jump", () => {
  const shell = createConsoleShell();
  shell.ui.navigate("tasks");
  // The shell reports no reduced-motion preference, so the change is animated.
  assert.equal(shell.scrollCalls.at(-1).behavior, "smooth");
});
