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
      body: new FakeElement("body"),
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
  const boot = appSource.lastIndexOf("\nvoid bootApplication();");
  assert.notEqual(boot, -1, "app boot marker missing");
  const source = `${appSource.slice(0, boot)}\n;globalThis.__hooks = { bootApplication, pollTelemetry, drawScene, trails, adapterInput, sceneFrame, noteFleetWorldUpdate, checkFleetWorldFreshness, worldGridCellRect, renderFleetWorld, renderFleetIntents, renderFleetDevices, createFleetTask, describeFleetWorldEntity, isFleetWorldClick, fleetExecutionAdapter: () => fleetExecutionAdapter };`;
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

test("fleet world stream becomes stale when updates freeze", () => {
  const harness = createHarness();
  harness.hooks.noteFleetWorldUpdate(1_000);
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "LIVE");

  harness.hooks.checkFleetWorldFreshness(4_001);
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "STALE");
});

test("RoboCasa world and Harness evidence stay visible in the operator rail", () => {
  const harness = createHarness();
  harness.hooks.renderFleetWorld({
    revision: 42,
    eventCursor: "world:42",
    entities: {
      "robot-1": {
        attributes: {
          adapter: "robocasa",
          scene_id: "robocasa-handoff-v1",
          model_hash: "0123456789abcdef0123456789abcdef",
        },
      },
    },
    resources: {},
    sources: {},
    health: {},
  });
  harness.hooks.renderFleetIntents({
    state: "SUCCEEDED",
    robots: ["robot-1", "robot-2"],
    intents: [{
      index: 1,
      status: "SUCCEEDED",
      harnessStatus: "SATISFIED",
      harnessReason: "PHYSICAL_POSTCONDITIONS_SATISFIED",
    }],
  });

  assert.match(harness.elements.get("fleet-world-model").textContent, /robocasa-handoff-v1/);
  assert.match(harness.elements.get("fleet-world-model").textContent, /0123456789ab/);
  assert.match(harness.elements.get("fleet-harness-verdict").textContent, /SATISFIED/);
  assert.match(harness.elements.get("fleet-harness-verdict").textContent, /PHYSICAL_POSTCONDITIONS_SATISFIED/);
});

test("MuJoCo fixtures and selected scene evidence stay visible in the operator rail", () => {
  const harness = createHarness();
  const fixture = {
    entityId: "fixture-sink",
    category: "sink",
    pose: [1.5, -1.2, 0.8],
    attributes: {
      bounds: "1,-1.5,0.5,2,-0.9,1.1",
      label: "水槽",
      model_source: "mujoco",
      static: "true",
    },
    relations: { fixed_in: "kitchen" },
  };

  harness.hooks.renderFleetWorld({
    revision: 7,
    entities: { "fixture-sink": fixture },
    resources: {},
    sources: {},
    health: {},
  });

  assert.match(harness.elements.get("fleet-world-fixtures").textContent, /1/);
  assert.match(harness.hooks.describeFleetWorldEntity(fixture), /水槽/);
  assert.match(harness.hooks.describeFleetWorldEntity(fixture), /sink/);
  assert.match(harness.hooks.describeFleetWorldEntity(fixture), /1\.50/);
});

test("cancelled pointers never select a world entity", () => {
  const harness = createHarness();

  assert.equal(harness.hooks.isFleetWorldClick({ button: 0, moved: 2 }, false), true);
  assert.equal(harness.hooks.isFleetWorldClick({ button: 0, moved: 2 }, true), false);
  assert.equal(harness.hooks.isFleetWorldClick({ button: 2, moved: 0 }, false), false);
});

test("online RoboCasa devices select the RoboCasa execution adapter", () => {
  const harness = createHarness();

  harness.hooks.renderFleetDevices([
    { robotId: "robot-1", adapter: "robocasa", online: true },
    { robotId: "robot-2", adapter: "robocasa", online: true },
  ]);

  assert.equal(harness.hooks.fleetExecutionAdapter(), "robocasa");
});

test("occupancy cells use the same world-to-canvas transform as entities", () => {
  const harness = createHarness();
  const rect = harness.hooks.worldGridCellRect(
    { originX: 10, originY: 20, cellSizeM: 0.5 },
    0,
    0,
    { minX: 9, minY: 19, scale: 100, height: 500 },
  );

  assert.deepEqual(Array.from(rect), [100, 350, 50, 50]);
});

test("cloud mode detection does not start Local Brain polling", async () => {
  const harness = createHarness();
  const requests = [];
  harness.setFetch(async (url) => {
    requests.push(url);
    if (url === "/healthz") {
      return { ok: true, status: 200, json: async () => ({ mode: "fleet" }) };
    }
    return { ok: false, status: 401 };
  });

  await harness.hooks.bootApplication();

  assert.deepEqual(requests, ["/healthz"]);
  assert.equal(harness.elements.get("fleet-view").hidden, false);
});

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

test("a newest snapshot without a frame finishes unavailable rather than live", async () => {
  const harness = createHarness();
  harness.setFetch(async (url) => {
    if (url.startsWith("/v1/telemetry")) {
      return {
        ok: true,
        status: 200,
        json: async () => ({ adapters: ["mujoco"], latest: snapshot(new Date().toISOString()) }),
      };
    }
    return { ok: false, status: 404 };
  });

  await harness.hooks.pollTelemetry();

  assert.equal(harness.elements.get("scene-live-state").textContent, "UNAVAILABLE");
  assert.equal(harness.hooks.sceneFrame.src, "");
  assert.equal(harness.hooks.sceneFrame.hidden, true);
});
