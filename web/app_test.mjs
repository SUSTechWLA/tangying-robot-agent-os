import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const appSource = await readFile(new URL("./app.js", import.meta.url), "utf8");

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

class FakeElement {
  constructor(id = "") {
    this.id = id;
    this.value = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.attributes = new Map();
    this.listeners = new Map();
    this.style = {};
    this.width = id === "scene-canvas" ? 1200 : 0;
    this.height = id === "scene-canvas" ? 560 : 0;
    this.classList = {
      add() {},
      remove() {},
      toggle() {},
    };
  }

  addEventListener(name, callback) {
    this.listeners.set(name, callback);
  }

  append(...children) {
    this.children.push(...children);
  }

  replaceChildren(...children) {
    this.children = [...children];
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "src") this._src = "";
  }

  set src(value) {
    this._src = String(value);
    this.attributes.set("src", this._src);
  }

  get src() {
    return this._src || "";
  }

  getContext() {
    const noOp = () => {};
    return {
      beginPath: noOp,
      clearRect: noOp,
      closePath: noOp,
      fill: noOp,
      fillRect: noOp,
      fillText: noOp,
      lineTo: noOp,
      moveTo: noOp,
      restore: noOp,
      rotate: noOp,
      save: noOp,
      stroke: noOp,
      strokeRect: noOp,
      translate: noOp,
      arc: noOp,
    };
  }
}

function createHarness() {
  const elements = new Map();
  const element = (selector) => {
    const id = selector.startsWith("#") ? selector.slice(1) : selector;
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  element("adapter").value = "mujoco";
  const createdURLs = [];
  const revokedURLs = [];
  let fetchImplementation = async () => ({ ok: false, status: 503 });
  const context = vm.createContext({
    AbortController,
    Blob,
    Date,
    JSON,
    Map,
    Math,
    Number,
    Promise,
    Set,
    String,
    URL: {
      createObjectURL(blob) {
        const url = `blob:${blob.label}`;
        createdURLs.push(url);
        return url;
      },
      revokeObjectURL(url) {
        revokedURLs.push(url);
      },
    },
    WebSocket: class {
      addEventListener() {}
      close() {}
    },
    console,
    document: {
      createElement: (tag) => new FakeElement(tag),
      querySelector: element,
    },
    fetch: (...arguments_) => fetchImplementation(...arguments_),
    location: { host: "127.0.0.1:8787", protocol: "http:" },
    queueMicrotask,
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout,
    clearTimeout,
  });
  const boot = appSource.lastIndexOf("\npollTelemetry();");
  assert.notEqual(boot, -1, "app boot marker missing");
  const source = `${appSource.slice(0, boot)}\n;globalThis.__hooks = { pollTelemetry, drawScene, trails, adapterInput, sceneFrame };`;
  vm.runInContext(source, context, { filename: "app.js" });
  return {
    hooks: context.__hooks,
    elements,
    createdURLs,
    revokedURLs,
    setFetch(implementation) {
      fetchImplementation = implementation;
    },
  };
}

function snapshot(observedAt, adapter = "mujoco") {
  return {
    adapter,
    observedAt,
    activity: "IDLE",
    entities: [{ entityId: "xlerobot", category: "robot", pose: [0, 0, 0, 1, 0, 0, 0] }],
    robotState: { joint_positions: { left: 0 } },
  };
}

test("a deferred old frame cannot overwrite or leak past a newer telemetry generation", async () => {
  const harness = createHarness();
  const oldBlob = deferred();
  const oldBlobStarted = deferred();
  const telemetryPayloads = [
    { adapters: ["mujoco"], latest: snapshot("2026-08-19T01:00:00Z") },
    { adapters: ["mujoco"], latest: snapshot("2026-08-19T01:00:01Z") },
    { adapters: ["mujoco"], latest: snapshot("2026-08-19T01:00:00Z") },
  ];
  let telemetryCalls = 0;
  let frameCalls = 0;
  const signals = [];
  harness.setFetch(async (url, options = {}) => {
    signals.push(options.signal);
    if (url.startsWith("/v1/telemetry")) {
      const payload = telemetryPayloads[telemetryCalls++];
      return { ok: true, status: 200, json: async () => payload };
    }
    frameCalls += 1;
    if (frameCalls === 1) {
      return {
        ok: true,
        status: 200,
        blob: async () => {
          oldBlobStarted.resolve();
          return oldBlob.promise;
        },
      };
    }
    return { ok: true, status: 200, blob: async () => ({ label: "new" }) };
  });

  const first = harness.hooks.pollTelemetry();
  await oldBlobStarted.promise;
  const firstSignal = signals[0];
  const second = harness.hooks.pollTelemetry();
  await second;
  assert.equal(firstSignal.aborted, true, "new generation must abort the old telemetry/frame chain");
  assert.equal(harness.hooks.sceneFrame.src, "blob:new");

  oldBlob.resolve({ label: "old" });
  await first;
  await Promise.resolve();
  assert.deepEqual(harness.createdURLs, ["blob:new"], "stale blob must be rejected before URL creation");
  assert.equal(harness.hooks.sceneFrame.src, "blob:new", "stale blob must never be written to img.src");

  await harness.hooks.pollTelemetry();
  assert.equal(frameCalls, 2, "older observedAt must not trigger another frame request");
  assert.deepEqual(harness.revokedURLs, ["blob:new"], "superseded pending blob URL must be revoked immediately");
});

test("semantic trails are removed as soon as an entity disappears", () => {
  const harness = createHarness();
  const entity = { entityId: "red-cup", category: "cup", attributes: { color: "red" }, pose: [0.1, 0.2] };
  harness.hooks.drawScene([entity], {}, snapshot("2026-08-19T01:00:00Z"));
  assert.equal(harness.hooks.trails.has("red-cup"), true);

  harness.hooks.drawScene([], {}, snapshot("2026-08-19T01:00:01Z"));
  assert.equal(harness.hooks.trails.has("red-cup"), false);
});
