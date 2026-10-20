import test from "node:test";
import assert from "node:assert/strict";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./edge_workspace.js", import.meta.url), "utf8");
const entries = [
  { id: "local", label: "本机", robotId: "local-1", href: "/" },
  { id: "ugv", label: "无人车", robotId: "agv-1", href: "/robots/ugv/" },
  { id: "uav", label: "无人机", robotId: "uav-1", href: "/robots/uav/" },
];
const response = (value, status = 200) => ({ ok: status === 200, status, json: async () => value });
const memoryStorage = () => {
  const values = new Map();
  return { getItem: key => values.get(key) ?? null, setItem: (key, value) => values.set(key, String(value)), removeItem: key => values.delete(key) };
};
function shell(path = "/robots/ugv/", options = {}) {
  const calls = [], navigations = [], nodes = new Map();
  function element() {
    return { dataset: {}, children: [], listeners: {}, textContent: "", hidden: false,
      replaceChildren() { this.children = []; }, append(...nodes) { this.children.push(...nodes); },
      setAttribute(key, value) { this[key] = value; },
      addEventListener(name, callback) { this.listeners[name] = callback; },
    };
  }
  const node = selector => { if (!nodes.has(selector)) nodes.set(selector, element()); return nodes.get(selector); };
  const context = vm.createContext({
    Headers, AbortSignal, console,
    location: { pathname: path, hash: "#tasks", assign: value => navigations.push(value), reload() {} },
    sessionStorage: options.storage || memoryStorage(),
    document: { querySelector: node, createElement: element },
    fetch: async (url, init) => {
      calls.push({ url, init });
      if (url === "/v1/workspaces") return response({ schemaVersion: "edge.workspaces.v1", workspaces: options.entries || entries });
      if (url.endsWith("/v1/workspace")) return response({ schemaVersion: "edge.workspace.v1", robotId: options.robotId || (path === "/" ? "local-1" : path.includes("uav") ? "uav-1" : "agv-1") }, options.identityStatus);
      return response([]);
    },
  });
  vm.runInContext(source, context);
  return { api: context.TangyingEdgeWorkspace, calls, nodes, node, navigations };
}

test("every scoped API call pins the intended robot without changing global fetch", async () => {
  const { api, calls } = shell();
  assert.equal(await api.init(), true);
  await api.fetch("/v1/tasks/task-same/approve", { method: "POST" });
  const last = calls.at(-1);
  assert.equal(last.url, "/robots/ugv/v1/tasks/task-same/approve");
  assert.equal(last.init.headers.get("X-Tangying-Robot-ID"), "agv-1");
  assert.equal(last.init.credentials, "same-origin");
  assert.equal(calls[0].url, "/v1/workspaces", "directory is always read from the host");
});

test("unknown and mismatched robot identities cannot send writes", async () => {
  for (const input of [{ robotId: "uav-1" }, { identityStatus: 502 }]) {
    const { api, calls, node } = shell("/robots/ugv/", input);
    assert.equal(await api.init(), false);
    const count = calls.length;
    await assert.rejects(api.fetch("/v1/robot/services", { method: "POST" }), /身份尚未确认/);
    assert.equal(calls.length, count);
    assert.equal(node("#edge-workspace-select").disabled, false, "offline view can still select another agent");
  }
  const { api } = shell("/robots/missing/");
  assert.equal(await api.init(), false);
});

test("initial root writes wait for identity; only explicit Fleet detection opens Fleet auth", async () => {
  const { api, calls } = shell("/");
  await assert.rejects(api.fetch("/v1/tasks", { method: "POST" }), /身份尚未确认/);
  assert.equal(calls.length, 0);
  api.useFleet();
  await api.fetch("/v1/auth/login", { method: "POST" });
  assert.equal(calls.at(-1).url, "/v1/auth/login");
  assert.throws(() => shell().api.useFleet(), /只能连接边缘/);
});

test("read paths for sockets, evidence images, maps and scene assets stay in one scope", () => {
  const { api } = shell();
  for (const path of ["/healthz", "/v1/tasks/t/events/ws", "/v1/tasks/t/observations/o/rgb", "/v1/maps/m/artifact/slam_session", "/assets/scenes/scene/manifest.json"]) {
    assert.equal(api.url(path), "/robots/ugv" + path);
  }
  assert.throws(() => api.url("/robots/uav/v1/tasks"), /离开/);
  assert.throws(() => api.url("https://example.invalid/v1/tasks"), /离开/);
  assert.throws(() => api.routePrefix("/robots/../"), /无效/);
});

test("separate tabs keep independent routes even with identical task and map IDs", async () => {
  const storage = memoryStorage();
  const a = shell("/robots/ugv/", { storage }), b = shell("/robots/uav/", { storage });
  await a.api.init(); await b.api.init();
  a.api.rememberTask("task-a"); b.api.rememberTask("task-b");
  a.api.scopedStorage(storage).setItem("map-same", "A");
  b.api.scopedStorage(storage).setItem("map-same", "B");
  assert.equal(a.api.previousTask(), "task-a");
  assert.equal(b.api.previousTask(), "task-b");
  assert.equal(a.api.scopedStorage(storage).getItem("map-same"), "A");
  assert.equal(b.api.scopedStorage(storage).getItem("map-same"), "B");
  assert.notEqual(a.api.storageKey("safety"), b.api.storageKey("safety"));
  await a.api.fetch("/v1/tasks/task-same/cancel", { method: "POST" });
  await b.api.fetch("/v1/tasks/task-same/cancel", { method: "POST" });
  assert.match(a.calls.at(-1).url, /^\/robots\/ugv\//);
  assert.match(b.calls.at(-1).url, /^\/robots\/uav\//);
});

test("switch performs full navigation without cancelling either robot task", async () => {
  const { api, calls, node, navigations } = shell();
  await api.init();
  const selector = node("#edge-workspace-select");
  selector.value = "/robots/uav/";
  const before = calls.length;
  selector.listeners.change();
  assert.deepEqual(navigations, ["/robots/uav/#tasks"]);
  assert.equal(calls.length, before);
  selector.value = "https://attacker.invalid/";
  selector.listeners.change();
  assert.equal(navigations.length, 1);
});

test("root workspace and a changed robot binding never reuse old browser memory", async () => {
  const storage = memoryStorage();
  const a = shell("/", { storage }); await a.api.init(); a.api.rememberTask("old");
  const b = shell("/", { storage, robotId: "replacement", entries: [{ ...entries[0], robotId: "replacement" }] });
  await b.api.init();
  assert.equal(b.api.previousTask(), "");
  assert.equal(b.api.url("/v1/tasks"), "/v1/tasks");
});

test("Runtime connection is not reported as readiness, offline preserves history", async () => {
  const { api, node } = shell(); await api.init();
  assert.equal(api.runtime({ RobotID: "other" }), false);
  assert.match(node("#edge-identity-state").textContent, /不匹配/);
  api.runtime(null);
  assert.match(node("#edge-connection-detail").textContent, /仍可查看/);
  await api.init();
  api.runtime({ RobotID: "agv-1" });
  assert.match(node("#edge-connection-detail").textContent, /Harness/);
  api.history([{ state: "SUCCEEDED" }, { state: "RUNNING" }, { state: "WAITING_USER" }, { state: "CANCELLED" }]);
  assert.equal(node("#edge-task-total").textContent, 4);
  assert.equal(node("#edge-task-attention").textContent, 1);
});
