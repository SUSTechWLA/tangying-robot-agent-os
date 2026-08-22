import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const appSource = await readFile(new URL("./app.js", import.meta.url), "utf8");
const worldViewSource = await readFile(new URL("./world_view.js", import.meta.url), "utf8");

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
    this.dataset = {};
    this.className = "";
    this.open = false;
    this.focused = false;
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

  emit(name, event = {}) {
    const payload = { currentTarget: this, target: this, ...event };
    return this.listeners.get(name)?.(payload);
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

  focus() {
    this.focused = true;
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

function createHarness(options = {}) {
  const elements = new Map();
  const element = (selector) => {
    const id = selector.startsWith("#") ? selector.slice(1) : selector;
    if (!elements.has(id)) elements.set(id, new FakeElement(id));
    return elements.get(id);
  };
  element("adapter").value = "mujoco";
  element("fleet-godview-webgl").hidden = true;
  const createdURLs = [];
  const revokedURLs = [];
  const fetches = [];
  const webSockets = [];
  let webSocketCount = 0;
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
    URLSearchParams,
    WebSocket: class {
      constructor(url) {
        this.url = url;
        this.listeners = new Map();
        this.closed = false;
        webSocketCount += 1;
        webSockets.push(this);
      }
      addEventListener(name, callback) {
        if (!this.listeners.has(name)) this.listeners.set(name, []);
        this.listeners.get(name).push(callback);
      }
      emit(name, event = {}) {
        for (const callback of this.listeners.get(name) || []) callback(event);
      }
      close() { this.closed = true; }
    },
    console,
    document: {
      body: new FakeElement("body"),
      documentElement: new FakeElement("html"),
      createElement: (tag) => new FakeElement(tag),
      querySelector: element,
    },
    fetch: (...arguments_) => {
      fetches.push(arguments_[0]);
      return fetchImplementation(...arguments_);
    },
    location: { host: "127.0.0.1:8787", protocol: options.protocol || "http:", href: `${options.protocol || "http:"}//127.0.0.1:8787/`, search: options.search || "" },
    queueMicrotask,
    requestAnimationFrame: options.requestAnimationFrame,
    setInterval: () => 1,
    clearInterval: () => {},
    setTimeout: options.setTimeout || setTimeout,
    clearTimeout: options.clearTimeout || clearTimeout,
    TangyingWebGL: options.TangyingWebGL,
    TangyingWorld: options.TangyingWorld,
  });
  const boot = appSource.lastIndexOf("\nvoid bootApplication();");
  assert.notEqual(boot, -1, "app boot marker missing");
  const source = `${appSource.slice(0, boot)}\n;globalThis.__hooks = { bootApplication, pollTelemetry, drawScene, trails, adapterInput, sceneFrame, noteFleetWorldUpdate, checkFleetWorldFreshness, worldGridCellRect, renderFleetWorld, renderFleetIntents, renderFleetDevices, createFleetTask, describeFleetWorldEntity, isFleetWorldClick, fleetExecutionAdapter: () => fleetExecutionAdapter, createFleetWorldRenderer: (...args) => typeof createFleetWorldRenderer === "function" ? createFleetWorldRenderer(...args) : Promise.reject(new Error("createFleetWorldRenderer missing")), retryFleetWorldVisual: (...args) => typeof retryFleetWorldVisual === "function" ? retryFleetWorldVisual(...args) : Promise.reject(new Error("retryFleetWorldVisual missing")), bindFleetWorldToolbar, fleetLogout, startFleetWorld, fleetSelectTask, renderTaskExperience, loadFleetTaskExperience, proposeFleetTaskRevision, confirmFleetTaskRevision, taskExperienceState: () => ({ ...fleetTaskExperienceState }), pendingTaskRevision: () => fleetPendingTaskRevision, installSelectedFleetTask: (task) => { selectedFleetTask = task; }, fleetLogout, installFleetWorldTestState: (renderer, camera) => { fleetWorldRenderer = renderer; fleetWorldCamera = camera; }, setFleetTokenForTest: (token) => { fleetToken = token; }, activeFleetWorldClient: () => fleetWorldClient, latestFleetWorldSnapshot: () => fleetWorldLatestSnapshot, fleetWorldMessageQueueForTest: () => fleetWorldMessageQueue, activeFleetWebGLRenderer: () => fleetWorldWebGLRenderer, activeFleetWebGLInteraction: () => fleetWorldWebGLInteraction };`;
  vm.runInContext(source, context, { filename: "app.js" });
  return {
    hooks: context.__hooks,
    elements,
    element,
    createdURLs,
    revokedURLs,
    fetches,
    webSockets,
    webSocketCount: () => webSocketCount,
    setFetch(implementation) {
      fetchImplementation = implementation;
    },
  };
}

test("server episode nonce becomes a visible task and revision acceptance marker", () => {
  const nonce = "fedcba9876543210".repeat(4);
  const harness = createHarness({ search: "?acceptance_task=task-review-1" });

  harness.hooks.renderFleetWorld({ ...visualSnapshot(42), acceptanceNonce: nonce });

  const marker = harness.element("fleet-acceptance-marker");
  assert.equal(marker.hidden, false);
  assert.equal(marker.dataset.nonce, nonce);
  assert.equal(marker.dataset.taskId, "task-review-1");
  assert.equal(marker.dataset.worldRevision, "42");
  assert.equal(marker.textContent, `ACCEPT ${nonce} · TASK task-review-1 · REV 42`);
  assert.deepEqual(
    JSON.parse(harness.element("fleet-acceptance-world-snapshot").textContent),
    { ...visualSnapshot(42), acceptanceNonce: nonce },
  );

  harness.hooks.renderFleetWorld(visualSnapshot(43));
  assert.equal(marker.hidden, false);
  assert.equal(marker.dataset.nonce, nonce);
  assert.equal(marker.dataset.worldRevision, "43");
  assert.equal(
    JSON.parse(harness.element("fleet-acceptance-world-snapshot").textContent).acceptanceNonce,
    nonce,
  );

  harness.hooks.bindFleetWorldToolbar();
  const fallback = harness.element("fleet-acceptance-fallback");
  assert.equal(fallback.hidden, false);
  fallback.emit("click");
  assert.equal(harness.element("fleet-world-connection").textContent, "");
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL DEGRADED");
  assert.equal(harness.element("fleet-godview-webgl").hidden, true);
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
});

test("acceptance page exposes actual requestAnimationFrame timestamps", () => {
  let frame = 0;
  const harness = createHarness({
    search: "?acceptance_task=task-review-1",
    requestAnimationFrame(callback) {
      frame += 1;
      callback(1000 + frame * 16.6 + (frame % 5) * 0.031);
      return frame;
    },
  });

  harness.hooks.renderFleetWorld({
    ...visualSnapshot(42),
    acceptanceNonce: "fedcba9876543210".repeat(4),
  });

  const samples = JSON.parse(harness.element("fleet-acceptance-raf-samples").textContent);
  assert.equal(samples.length, 600);
  assert.ok(samples.every((value, index) => index === 0 || value > samples[index - 1]));
  assert.ok(new Set(samples.slice(1).map((value, index) => value - samples[index])).size > 1);
  assert.ok(Number.isFinite(Number(harness.element("fleet-acceptance-raf-samples").dataset.timeOriginMs)));
});

function visualSnapshot(revision = 1) {
  return {
    revision,
    worldId: "robocasa-handoff-v1",
    entities: {
      kitchen: {
        entityId: "kitchen",
        attributes: { static: "true", scene_id: "robocasa-handoff-v1", model_hash: "a".repeat(64) },
      },
    },
    robots: {},
    resources: {},
    sources: {},
    health: {},
  };
}

async function createConnectedFleetHarness(options = {}) {
  const worldContext = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
  vm.runInContext(worldViewSource, worldContext, { filename: "world_view.js" });
  const rendered = [];
  const scheduledReconnects = [];
  let worldRequests = 0;
  const harness = createHarness({
    TangyingWorld: worldContext.globalThis.TangyingWorld,
    setTimeout(callback, delay) {
      scheduledReconnects.push({ callback, delay });
      return scheduledReconnects.length;
    },
    clearTimeout() {},
    TangyingWebGL: {
      AssetRegistry: class { async load() { return {}; } },
      WebGLSceneRenderer: {
        create() {
          return {
            status: { state: "READY", code: "WEBGL_READY" },
            render() { return true; }, select() {}, setVisibility() {}, setWorldCamera() {}, dispose() {},
          };
        },
      },
    },
  });
  harness.hooks.installFleetWorldTestState({
    render(snapshot) { rendered.push(snapshot.revision); },
    setVisibility() {},
  }, new worldContext.globalThis.TangyingWorld.WorldCamera());
  harness.hooks.setFleetTokenForTest("same-token");
  harness.setFetch(async (url) => {
    if (url === "/v1/world") {
      worldRequests += 1;
      if (worldRequests === 1) {
        return { ok: true, status: 200, json: async () => visualSnapshot(1) };
      }
      const snapshot = options.resyncSnapshot
        ? await options.resyncSnapshot(worldRequests)
        : visualSnapshot(worldRequests);
      return { ok: true, status: 200, json: async () => snapshot };
    }
    if (url === "/v1/auth/ws-ticket") {
      return { ok: true, status: 200, json: async () => ({ ticket: "ticket-1" }) };
    }
    return { ok: false, status: 404 };
  });
  await harness.hooks.startFleetWorld();
  assert.equal(harness.webSockets.length, 1);
  return {
    harness,
    rendered,
    scheduledReconnects,
    worldRequestCount: () => worldRequests,
    socket: harness.webSockets[0],
  };
}

function fakeWorldAPI() {
  return {
    WorldCamera: class {
      constructor() { this.target = [0, 0, 0]; }
      basis() { return { position: [2, -2, 2] }; }
      drag() {}
      zoomAt() {}
      applyPreset() { return true; }
      toJSON() { return { yaw: 0, pitch: 0.6, distance: 4, target: [...this.target] }; }
    },
  };
}

test("file pages explain the service entry and make no API request", async () => {
  const harness = createHarness({ protocol: "file:" });

  assert.equal(await harness.hooks.bootApplication(), "file");
  assert.equal(harness.fetches.length, 0);
  assert.match(harness.elements.get("scene-frame-message").textContent, /127\.0\.0\.1:18080/);
  assert.equal(harness.elements.get("open-service-console").href, "http://127.0.0.1:18080/");
  assert.equal(harness.elements.get("open-service-console").hidden, false);
  assert.equal(harness.elements.get("connection-text").textContent, "SERVICE REQUIRED");
});

test("matching visual assets promote WebGL without pausing the Canvas world", async () => {
  const loaded = deferred();
  const webglRevisions = [];
  const webglRenderer = {
    status: { state: "READY", code: "WEBGL_READY" },
    render(snapshot) { webglRevisions.push(snapshot.revision); return true; },
    setVisibility() {}, setWorldCamera() {}, select() {}, dispose() {},
  };
  const harness = createHarness({
    TangyingWorld: fakeWorldAPI(),
    TangyingWebGL: {
      AssetRegistry: class { load() { return loaded.promise; } },
      WebGLSceneRenderer: {
        create() { return webglRenderer; },
        bindInteraction() { return { setFollow() {}, dispose() {} }; },
      },
    },
  });
  harness.element("fleet-godview-canvas").getContext = () => ({ clearRect() {}, fillRect() {} });

  const pending = harness.hooks.createFleetWorldRenderer(visualSnapshot());
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL LOADING");
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
  harness.hooks.renderFleetWorld(visualSnapshot(2));
  assert.equal(harness.element("fleet-world-revision").textContent, "REV 2");
  loaded.resolve({ scene: { clone() {} }, robotTemplate: { clone() {} }, binding: {} });
  assert.equal(await pending, webglRenderer);

  assert.equal(harness.element("fleet-godview-webgl").hidden, false);
  assert.equal(harness.element("fleet-godview-canvas").hidden, true);
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL LIVE");
  assert.deepEqual(webglRevisions, [2]);
});

test("a newer model identity degrades immediately and retry reuses that latest snapshot", async () => {
  const loadedRevisions = [];
  const webglRenderer = {
    bundle: { modelHash: "a".repeat(64), manifest: { sceneId: "robocasa-handoff-v1" } },
    status: { state: "READY", code: "WEBGL_READY" },
    render() { return true; }, setVisibility() {}, setWorldCamera() {}, select() {}, dispose() {},
  };
  const harness = createHarness({
    TangyingWorld: fakeWorldAPI(),
    TangyingWebGL: {
      AssetRegistry: class {
        async load(snapshot) {
          loadedRevisions.push(snapshot.revision);
          const identity = snapshot.entities.kitchen.attributes;
          if (identity.model_hash !== "a".repeat(64)) {
            throw Object.assign(new Error("mismatch"), { code: "VISUAL_MODEL_MISMATCH" });
          }
          return webglRenderer.bundle;
        }
      },
      WebGLSceneRenderer: {
        create() { return webglRenderer; },
        bindInteraction() { return { setFollow() {}, dispose() {} }; },
      },
    },
  });
  harness.hooks.noteFleetWorldUpdate(1000);
  await harness.hooks.createFleetWorldRenderer(visualSnapshot(1));
  const changed = visualSnapshot(2);
  changed.entities.kitchen.attributes.model_hash = "b".repeat(64);

  harness.hooks.renderFleetWorld(changed);
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL DEGRADED");
  assert.match(harness.element("fleet-visual-detail").textContent, /VISUAL_MODEL_MISMATCH/);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD LIVE");

  await harness.hooks.retryFleetWorldVisual();
  assert.deepEqual(loadedRevisions, [1, 2]);
  assert.equal(harness.fetches.length, 0, "visual retry must not request or reconnect world state");
});

test("visual mismatch and context loss fall back without changing WORLD LIVE", async () => {
  let shouldReject = true;
  const loadedRevisions = [];
  const webglRenderer = {
    status: { state: "READY", code: "WEBGL_READY" },
    render() { return true; }, setVisibility() {}, setWorldCamera() {}, select() {}, dispose() {},
  };
  const harness = createHarness({
    TangyingWorld: fakeWorldAPI(),
    TangyingWebGL: {
      AssetRegistry: class {
        async load() {
          loadedRevisions.push(arguments[0].revision);
          if (shouldReject) throw Object.assign(new Error("mismatch"), { code: "VISUAL_MODEL_MISMATCH" });
          return { scene: { clone() {} }, robotTemplate: { clone() {} }, binding: {} };
        }
      },
      WebGLSceneRenderer: {
        create() { return webglRenderer; },
        bindInteraction() { return { setFollow() {}, dispose() {} }; },
      },
    },
  });
  harness.hooks.noteFleetWorldUpdate(1000);

  await harness.hooks.createFleetWorldRenderer(visualSnapshot());
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "WORLD LIVE");
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL DEGRADED");
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
  assert.match(harness.element("fleet-visual-detail").textContent, /VISUAL_MODEL_MISMATCH/);

  shouldReject = false;
  await harness.hooks.createFleetWorldRenderer(visualSnapshot(2));
  assert.deepEqual(loadedRevisions, [1, 2], "retry must validate the latest supplied snapshot");
  harness.element("fleet-godview-webgl").emit("webglcontextlost", { preventDefault() {} });
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "WORLD LIVE");
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL DEGRADED");
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
});

test("all four visual layers toggle aria-pressed independently", () => {
  const harness = createHarness();
  harness.hooks.bindFleetWorldToolbar();
  const ids = [
    "fleet-world-models-toggle",
    "fleet-world-fixtures-toggle",
    "fleet-world-labels-toggle",
    "fleet-world-path-toggle",
  ];
  for (const id of ids) {
    const button = harness.element(id);
    const before = button.attributes.get("aria-pressed");
    button.emit("click");
    assert.notEqual(button.attributes.get("aria-pressed"), before, `${id} must toggle its pressed state`);
  }
});

test("WebGL select and render exceptions degrade visually without rejecting world receive", async () => {
  const worldContext = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
  vm.runInContext(worldViewSource, worldContext, { filename: "world_view.js" });
  const { WorldRealtimeClient } = worldContext.globalThis.TangyingWorld;

  for (const failure of ["select", "render"]) {
    const canvasRevisions = [];
    let throwRuntimeError = false;
    let disposed = 0;
    let resyncs = 0;
    const renderer = {
      status: { state: "READY", code: "WEBGL_READY" },
      select() {
        if (throwRuntimeError && failure === "select") {
          throw Object.assign(new Error("select failed"), { code: "WEBGL_SELECT_FAILED" });
        }
        return "";
      },
      render(snapshot) {
        if (throwRuntimeError && failure === "render") {
          throw Object.assign(new Error("render failed"), { code: "WEBGL_RENDER_FAILED" });
        }
        return Boolean(snapshot);
      },
      setVisibility() {}, setWorldCamera() {},
      dispose() { disposed += 1; },
    };
    const harness = createHarness({
      TangyingWorld: fakeWorldAPI(),
      TangyingWebGL: {
        AssetRegistry: class { async load() { return {}; } },
        WebGLSceneRenderer: {
          create() { return renderer; },
          bindInteraction() { return { dispose() {}, setFollow() {} }; },
        },
      },
    });
    harness.hooks.installFleetWorldTestState({
      render(snapshot) { canvasRevisions.push(snapshot.revision); },
      setVisibility() {},
    }, new (fakeWorldAPI().WorldCamera)());
    harness.hooks.noteFleetWorldUpdate(1000);
    await harness.hooks.createFleetWorldRenderer(visualSnapshot(1));
    const client = new WorldRealtimeClient({
      render: harness.hooks.renderFleetWorld,
      requestSnapshot: async () => { resyncs += 1; return visualSnapshot(99); },
    });
    client.acceptSnapshot(visualSnapshot(1));
    throwRuntimeError = true;

    await client.receive(visualSnapshot(2));

    assert.equal(client.revision, 2, `${failure} exception must not reject world revision`);
    assert.equal(harness.element("fleet-world-connection").textContent, "WORLD LIVE");
    assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL DEGRADED");
    assert.equal(harness.element("fleet-godview-canvas").hidden, false);
    assert.equal(canvasRevisions.at(-1), 2, "Canvas must hold the newest authoritative snapshot");
    assert.equal(resyncs, 0);
    assert.equal(harness.fetches.length, 0);
    assert.equal(harness.webSocketCount(), 0);
    assert.equal(disposed, 1);
  }
});

test("logout invalidates a world request before it can start a visual renderer", async () => {
  const worldPayload = deferred();
  let createCalls = 0;
  const worldContext = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
  vm.runInContext(worldViewSource, worldContext, { filename: "world_view.js" });
  const harness = createHarness({
    TangyingWorld: worldContext.globalThis.TangyingWorld,
    TangyingWebGL: {
      AssetRegistry: class { async load() { return {}; } },
      WebGLSceneRenderer: {
        create() { createCalls += 1; return { render() { return true; }, dispose() {} }; },
      },
    },
  });
  harness.setFetch(async (url) => {
    if (url === "/v1/world") {
      return { ok: true, status: 200, json: async () => worldPayload.promise };
    }
    return { ok: false, status: 401 };
  });

  const start = harness.hooks.startFleetWorld();
  await Promise.resolve();
  harness.hooks.fleetLogout();
  worldPayload.resolve(visualSnapshot(1));
  await start;
  await Promise.resolve();

  assert.equal(createCalls, 0);
  assert.equal(harness.hooks.activeFleetWebGLRenderer(), null);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD CONNECTING");
  assert.equal(harness.element("fleet-godview-webgl").hidden, true);
});

test("logout makes every callback from the old socket inert even with the same token", async () => {
  const { harness, scheduledReconnects, socket } = await createConnectedFleetHarness();
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD LIVE");

  harness.hooks.fleetLogout();
  harness.hooks.setFleetTokenForTest("same-token");
  socket.emit("open");
  socket.emit("error", new Error("old transport"));
  socket.emit("close");

  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD CONNECTING");
  assert.equal(harness.webSocketCount(), 1);
  assert.equal(scheduledReconnects.length, 0);
});

test("an old socket message after logout cannot fetch, render, or restore world state", async () => {
  const state = await createConnectedFleetHarness();
  const { harness, rendered, scheduledReconnects, socket } = state;
  harness.hooks.fleetLogout();
  const fetchesAfterLogout = harness.fetches.length;
  const rendersAfterLogout = rendered.length;

  socket.emit("message", { data: JSON.stringify({ type: "RESYNC_REQUIRED" }) });
  await harness.hooks.fleetWorldMessageQueueForTest();

  assert.equal(harness.fetches.length, fetchesAfterLogout);
  assert.equal(rendered.length, rendersAfterLogout);
  assert.equal(harness.hooks.latestFleetWorldSnapshot(), null);
  assert.equal(harness.hooks.activeFleetWorldClient(), null);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD CONNECTING");
  assert.equal(harness.webSocketCount(), 1);
  assert.equal(scheduledReconnects.length, 0);
});

test("logout fences a receive whose resync snapshot resolves after the session ends", async () => {
  const pendingResync = deferred();
  const state = await createConnectedFleetHarness({
    resyncSnapshot: async () => pendingResync.promise,
  });
  const { harness, rendered, scheduledReconnects, socket } = state;

  socket.emit("message", { data: JSON.stringify({ revision: 3, snapshot: visualSnapshot(3) }) });
  for (let count = 0; count < 5 && state.worldRequestCount() < 2; count += 1) await Promise.resolve();
  assert.equal(state.worldRequestCount(), 2, "the old client must already be awaiting resync");
  const oldMessageChain = harness.hooks.fleetWorldMessageQueueForTest();
  harness.hooks.fleetLogout();
  harness.hooks.setFleetTokenForTest("same-token");
  const rendersAfterLogout = rendered.length;
  pendingResync.resolve(visualSnapshot(3));
  await oldMessageChain;

  assert.equal(state.worldRequestCount(), 2, "logout must not start a second resync fetch");
  assert.equal(rendered.length, rendersAfterLogout);
  assert.equal(harness.hooks.latestFleetWorldSnapshot(), null);
  assert.equal(harness.hooks.activeFleetWorldClient(), null);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD CONNECTING");
  assert.equal(harness.webSocketCount(), 1);
  assert.equal(scheduledReconnects.length, 0);
});

test("a replacement socket applies rev2 before the previous socket's resync settles", async () => {
  const pendingResync = deferred();
  const state = await createConnectedFleetHarness({
    resyncSnapshot: async () => pendingResync.promise,
  });
  const { harness, rendered, scheduledReconnects, socket } = state;
  socket.emit("message", { data: JSON.stringify({ revision: 3, snapshot: visualSnapshot(3) }) });
  for (let count = 0; count < 5 && state.worldRequestCount() < 2; count += 1) await Promise.resolve();
  assert.equal(state.worldRequestCount(), 2);
  const oldMessageChain = harness.hooks.fleetWorldMessageQueueForTest();

  socket.emit("close");
  assert.equal(scheduledReconnects.length, 1);
  scheduledReconnects[0].callback();
  for (let count = 0; count < 20 && harness.webSockets.length < 2; count += 1) await Promise.resolve();
  assert.equal(harness.webSockets.length, 2, "the live session must reconnect normally");
  const rendersBeforeOldResync = rendered.length;
  harness.webSockets[1].emit("message", { data: JSON.stringify(visualSnapshot(2)) });
  for (let count = 0; count < 10 && rendered.at(-1) !== 2; count += 1) await Promise.resolve();
  const replacementAppliedBeforeRelease = rendered.at(-1) === 2;
  const stateBeforeOldResync = harness.element("fleet-world-connection").textContent;

  pendingResync.resolve(visualSnapshot(3));
  await oldMessageChain;
  await harness.hooks.fleetWorldMessageQueueForTest();

  assert.equal(replacementAppliedBeforeRelease, true, "the new socket must not wait on the old queue");
  assert.equal(stateBeforeOldResync, "WORLD LIVE");
  assert.equal(rendered.length, rendersBeforeOldResync + 1);
  assert.equal(harness.hooks.latestFleetWorldSnapshot()?.revision, 2);
  assert.equal(harness.hooks.activeFleetWorldClient()?.revision, 2);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD LIVE");
});

test("messages remain in order within one socket while its resync is pending", async () => {
  const pendingResync = deferred();
  const state = await createConnectedFleetHarness({
    resyncSnapshot: async () => pendingResync.promise,
  });
  const { harness, rendered, socket } = state;
  socket.emit("message", { data: JSON.stringify({ type: "RESYNC_REQUIRED" }) });
  socket.emit("message", { data: JSON.stringify(visualSnapshot(2)) });
  for (let count = 0; count < 5 && state.worldRequestCount() < 2; count += 1) await Promise.resolve();
  assert.equal(state.worldRequestCount(), 2);
  for (let count = 0; count < 5; count += 1) await Promise.resolve();
  assert.equal(harness.hooks.activeFleetWorldClient()?.revision, 1);
  assert.equal(rendered.at(-1), 1, "rev2 must wait behind the same socket's resync");

  pendingResync.resolve(visualSnapshot(1));
  await harness.hooks.fleetWorldMessageQueueForTest();

  assert.deepEqual(rendered.slice(-2), [1, 2]);
  assert.equal(harness.hooks.activeFleetWorldClient()?.revision, 2);
  assert.equal(harness.element("fleet-world-connection").textContent, "WORLD LIVE");
});

test("logout invalidates a pending renderer and disposes it instead of promoting it", async () => {
  const createStarted = deferred();
  const pendingRenderer = deferred();
  let rendererDisposals = 0;
  let interactionBinds = 0;
  const candidate = {
    status: { state: "READY", code: "WEBGL_READY" },
    render() { return true; }, select() {}, setVisibility() {}, setWorldCamera() {},
    dispose() { rendererDisposals += 1; },
  };
  const harness = createHarness({
    TangyingWorld: fakeWorldAPI(),
    TangyingWebGL: {
      AssetRegistry: class { async load() { return {}; } },
      WebGLSceneRenderer: {
        create() { createStarted.resolve(); return pendingRenderer.promise; },
        bindInteraction() { interactionBinds += 1; return { dispose() {} }; },
      },
    },
  });

  const pending = harness.hooks.createFleetWorldRenderer(visualSnapshot(1));
  await createStarted.promise;
  harness.hooks.fleetLogout();
  pendingRenderer.resolve(candidate);
  await pending;

  assert.equal(rendererDisposals, 1);
  assert.equal(interactionBinds, 0);
  assert.equal(harness.hooks.activeFleetWebGLRenderer(), null);
  assert.equal(harness.element("fleet-godview-webgl").hidden, true);
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL LOADING");

  harness.element("fleet-godview-webgl").emit("webglcontextlost", { preventDefault() {} });
  assert.equal(
    harness.element("fleet-visual-state").textContent,
    "VISUAL LOADING",
    "a stale context event must not revive a logged-out visual lifecycle",
  );
});

test("logout tears down the active renderer, interaction, and projected labels", async () => {
  let rendererDisposals = 0;
  let interactionDisposals = 0;
  const renderer = {
    status: { state: "READY", code: "WEBGL_READY" },
    render() { return true; }, select() {}, setVisibility() {}, setWorldCamera() {},
    dispose() { rendererDisposals += 1; },
  };
  const harness = createHarness({
    TangyingWorld: fakeWorldAPI(),
    TangyingWebGL: {
      AssetRegistry: class { async load() { return {}; } },
      WebGLSceneRenderer: {
        create() {
          harness.element("fleet-world-label-layer").append(new FakeElement("candidate-label"));
          return renderer;
        },
        bindInteraction() { return { dispose() { interactionDisposals += 1; } }; },
      },
    },
  });
  harness.hooks.installFleetWorldTestState(null, new (fakeWorldAPI().WorldCamera)());
  await harness.hooks.createFleetWorldRenderer(visualSnapshot(1));
  assert.equal(harness.element("fleet-world-label-layer").children.length, 1);

  harness.hooks.fleetLogout();

  assert.equal(rendererDisposals, 1);
  assert.equal(interactionDisposals, 1);
  assert.equal(harness.hooks.activeFleetWebGLRenderer(), null);
  assert.equal(harness.hooks.activeFleetWebGLInteraction(), null);
  assert.equal(harness.element("fleet-world-label-layer").children.length, 0);
  assert.equal(harness.element("fleet-godview-webgl").hidden, true);
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
  assert.equal(harness.element("fleet-visual-state").textContent, "VISUAL LOADING");
});

test("Canvas fallback keeps the semantic kitchen when debug bounds are off", () => {
  const worldContext = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
  vm.runInContext(worldViewSource, worldContext, { filename: "world_view.js" });
  const { WorldCamera, WorldRenderer } = worldContext.globalThis.TangyingWorld;
  const canvas = new FakeElement("fleet-godview-canvas");
  canvas.width = 1400;
  canvas.height = 700;
  const renderer = new WorldRenderer(canvas, new WorldCamera({ target: [1, 0, 0] }));
  const fixture = {
    entityId: "counter-main",
    category: "counter",
    pose: [1, 0, 0.45],
    attributes: { bounds: "0,-0.5,0,2,0.5,0.9", static: "true" },
  };
  renderer.setVisibility({ models: true, bounds: false });
  renderer.render({ revision: 1, entities: { "counter-main": fixture }, robots: {}, resources: {} });
  const point = renderer.camera.project(fixture.pose, canvas.width, canvas.height);

  assert.equal(renderer.pick(point[0], point[1])?.entityId, "counter-main");
});

test("Canvas fallback keeps models, bounds, labels, path, and hit evidence independent", () => {
  const worldContext = vm.createContext({ globalThis: {}, Math, Number, structuredClone });
  vm.runInContext(worldViewSource, worldContext, { filename: "world_view.js" });
  const { WorldCamera, WorldRenderer } = worldContext.globalThis.TangyingWorld;
  const labels = [];
  const drawing = {
    fills: 0, dashes: 0,
    beginPath() {}, clearRect() {}, closePath() {}, lineTo() {}, moveTo() {}, restore() {}, save() {},
    stroke() {}, strokeRect() {}, fillRect() {}, arc() {},
    fill() { this.fills += 1; },
    fillText(text) { labels.push(String(text)); },
    setLineDash() { this.dashes += 1; },
  };
  const canvas = new FakeElement("fleet-godview-canvas");
  canvas.width = 1400;
  canvas.height = 700;
  canvas.getContext = () => drawing;
  const camera = new WorldCamera({ target: [1, 0, 0] });
  const renderer = new WorldRenderer(canvas, camera);
  const fixture = {
    entityId: "counter-main", category: "counter", pose: [1, 0, 0.45],
    attributes: { bounds: "0,-0.5,0,2,0.5,0.9", static: "true" },
  };
  const block = { entityId: "red-block", category: "block", pose: [0.2, 0.1, 0.2] };
  const robot = { robotId: "robot-1", pose: [-0.3, 0, 0], freshness: "FRESH" };
  const zones = {
    "left-start-zone": { entityId: "left-start-zone", pose: [0, 1, 0] },
    "handoff-zone": { entityId: "handoff-zone", pose: [1, 1, 0] },
    "right-target-zone": { entityId: "right-target-zone", pose: [2, 1, 0] },
  };
  const snapshot = {
    revision: 1,
    entities: { "counter-main": fixture, "red-block": block, ...zones },
    robots: { "robot-1": robot }, resources: {},
  };

  renderer.setVisibility({ models: false, bounds: false, labels: true, path: false });
  renderer.render(snapshot);
  assert.equal(drawing.fills, 0, "labels-only view must not draw model faces or glyphs");
  assert.ok(labels.some((label) => label.includes("counter-main")));
  assert.ok(labels.some((label) => label.includes("red-block")));
  assert.ok(labels.some((label) => label.includes("robot-1")));
  for (const entity of [fixture, block, robot]) {
    const point = camera.project(entity.pose, canvas.width, canvas.height);
    assert.equal(renderer.pick(point[0], point[1])?.entityId, entity.entityId || entity.robotId);
  }

  labels.length = 0;
  drawing.fills = 0;
  drawing.dashes = 0;
  renderer.setVisibility({ models: false, bounds: true, labels: false, path: true });
  renderer.render(snapshot);
  assert.equal(drawing.fills, 0, "bounds-only view must not fill fixture models");
  assert.equal(labels.some((label) => /counter-main|red-block|robot-1/.test(label)), false);
  assert.ok(drawing.dashes > 0, "path remains independent from models and labels");
});

test("fleet world stream becomes stale when updates freeze", () => {
  const harness = createHarness();
  harness.hooks.noteFleetWorldUpdate(1_000);
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "WORLD LIVE");

  harness.hooks.checkFleetWorldFreshness(4_001);
  assert.equal(harness.elements.get("fleet-world-connection").textContent, "WORLD STALE");
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

function taskExperience(overrides = {}) {
  return {
    schemaVersion: "task.experience.v1",
    taskId: "task-1",
    revision: 2,
    aggregateVersion: 8,
    headline: "把红色方块交给 2 号机器人并送到目标区",
    originalRequest: "先交接，再送到右侧目标区",
    understanding: "1 号机器人先把红色方块放到交接区，2 号机器人接力送到右侧目标区。",
    updateStatus: "ACTIVE",
    changePreview: { retained: ["保留已完成的拿取"], changed: [], added: [], paused: [] },
    steps: [
      { stepId: "step-1", status: "SATISFIED", statusText: "已经完成", explanation: "1 号机器人把红色方块放到交接区", assignedRobot: "robot-1", capabilityLabel: "放下物品", evidenceText: "环境已经确认动作结果" },
      { stepId: "step-2", status: "RUNNING", statusText: "正在执行", explanation: "2 号机器人把方块送到右侧目标区", assignedRobot: "robot-2", capabilityLabel: "移动到指定位置" },
    ],
    activities: [
      { displayName: "移动到指定位置", purpose: "让机器人安全到达任务位置", status: "RUNNING", statusText: "机器人正在执行", robotId: "robot-2", stepId: "step-2", safeArguments: { targetRef: "right-target-zone", secretToken: "must-not-render" } },
    ],
    recovery: null,
    allowedActions: ["update", "view_professional_details"],
    professional: { activities: [{ toolName: "navigation.navigate", commandId: "cmd-2", taskRevision: 2, aggregateVersion: 8 }] },
    ...overrides,
  };
}

function descendantText(element) {
  return [element.textContent, ...element.children.map(descendantText)].filter(Boolean).join(" ");
}

test("mission rail explains natural language, steps, tools, evidence, and hides technical details by default", () => {
  const harness = createHarness();

  assert.equal(harness.hooks.renderTaskExperience(taskExperience()), true);

  assert.equal(harness.element("fleet-mission-headline").textContent, "把红色方块交给 2 号机器人并送到目标区");
  assert.match(harness.element("fleet-mission-understanding").textContent, /1 号机器人先/);
  assert.match(descendantText(harness.element("fleet-step-ribbon")), /已经完成/);
  assert.match(descendantText(harness.element("fleet-step-ribbon")), /环境已经确认/);
  assert.match(descendantText(harness.element("fleet-tool-activities")), /机器人正在执行/);
  assert.match(descendantText(harness.element("fleet-tool-activities")), /目标位置.*右侧目标区/);
  assert.doesNotMatch(descendantText(harness.element("fleet-tool-activities")), /right-target-zone/);
  assert.doesNotMatch(descendantText(harness.element("fleet-tool-activities")), /must-not-render/);
  assert.doesNotMatch(descendantText(harness.element("fleet-tool-activities")), /navigation\.navigate|commandId/);
  assert.match(descendantText(harness.element("fleet-professional-activities")), /navigation\.navigate/);
  assert.equal(harness.element("fleet-mission-professional").open, false);
});

test("task experience rejects stale facts and resyncs a skipped revision", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("operator-token");
  assert.equal(harness.hooks.renderTaskExperience(taskExperience()), true);
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ revision: 1, aggregateVersion: 99, headline: "旧任务" })), false);
  assert.equal(harness.element("fleet-mission-headline").textContent, "把红色方块交给 2 号机器人并送到目标区");
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ aggregateVersion: 7, headline: "旧聚合" })), false);

  const requests = [];
  harness.setFetch(async (url) => {
    requests.push(url);
    if (url.endsWith("/revisions")) return { ok: true, status: 200, json: async () => ({ currentRevision: 4 }) };
    if (url.endsWith("/experience")) return { ok: true, status: 200, json: async () => taskExperience({ revision: 4, aggregateVersion: 12, headline: "同步后的任务" }) };
    return { ok: false, status: 404, json: async () => ({}) };
  });
  await harness.hooks.loadFleetTaskExperience("task-1");

  assert.deepEqual(requests, ["/v1/tasks/task-1/experience", "/v1/tasks/task-1/revisions", "/v1/tasks/task-1/experience"]);
  assert.equal(harness.element("fleet-mission-headline").textContent, "同步后的任务");
  assert.equal(harness.hooks.taskExperienceState().revision, 4);
});

test("server allowed actions cannot be re-enabled by typing", () => {
  const harness = createHarness();
  harness.hooks.installSelectedFleetTask({ id: "task-1", state: "EXECUTING" });
  harness.element("fleet-update-request").value = "试图更新";

  harness.hooks.renderTaskExperience(taskExperience({ allowedActions: [] }));

  assert.equal(harness.element("fleet-revision-preview").disabled, true);
});

test("a late experience response from the previous selection cannot replace the current task", async () => {
  const harness = createHarness();
  const oldExperience = deferred();
  harness.hooks.setFleetTokenForTest("operator-token");
  harness.setFetch(async (url) => {
    if (url === "/v1/tasks/task-old/intents" || url === "/v1/tasks/task-new/intents") {
      return { ok: true, status: 200, json: async () => ({ intents: [], robots: [] }) };
    }
    if (url === "/v1/tasks/task-old/experience") return oldExperience.promise;
    if (url === "/v1/tasks/task-new/experience") {
      return { ok: true, status: 200, json: async () => taskExperience({ taskId: "task-new", revision: 1, headline: "现在选择的新任务" }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });

  const oldSelection = harness.hooks.fleetSelectTask({ id: "task-old", state: "EXECUTING" });
  await new Promise((resolve) => setTimeout(resolve, 0));
  await harness.hooks.fleetSelectTask({ id: "task-new", state: "EXECUTING" });
  oldExperience.resolve({ ok: true, status: 200, json: async () => taskExperience({ taskId: "task-old", revision: 9, headline: "迟到的旧任务" }) });
  await oldSelection;

  assert.equal(harness.element("fleet-mission-headline").textContent, "现在选择的新任务");
  assert.equal(harness.hooks.taskExperienceState().taskId, "task-new");
});

test("task updates preserve user text on conflict and require preview confirmation", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("operator-token");
  harness.hooks.installSelectedFleetTask({ id: "task-1", state: "EXECUTING" });
  harness.hooks.renderTaskExperience(taskExperience());
  harness.element("fleet-update-request").value = "改成让 2 号机器人送到左侧区域";
  let conflict = true;
  const requests = [];
  harness.setFetch(async (url, options = {}) => {
    requests.push({ url, body: options.body ? JSON.parse(options.body) : null });
    if (url.endsWith("/revisions") && options.method === "POST" && conflict) {
      return { ok: false, status: 409, json: async () => ({ code: "REVISION_CONFLICT", current: { currentRevision: 3 } }) };
    }
    if (url.endsWith("/revisions") && options.method === "POST") {
      return { ok: true, status: 201, json: async () => ({
        schemaVersion: "task.revision-preview.v1",
        currentRevision: 2,
        proposal: { revision: { revision: 3 } },
        experience: taskExperience({ revision: 3, aggregateVersion: 8, updateStatus: "WAITING_APPROVAL", changePreview: { retained: ["保留步骤 1"], changed: ["步骤 2 改为左侧区域"], added: [], paused: [] } }),
      }) };
    }
    if (url.includes("/revisions/3/confirm")) {
      return { ok: true, status: 200, json: async () => ({ revision: { status: "WAITING_SAFE_POINT" } }) };
    }
    if (url.endsWith("/experience")) {
      return { ok: true, status: 200, json: async () => taskExperience({ revision: 3, aggregateVersion: 9, updateStatus: "WAITING_SAFE_POINT" }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });

  assert.equal(await harness.hooks.proposeFleetTaskRevision(), false);
  assert.equal(harness.element("fleet-update-request").value, "改成让 2 号机器人送到左侧区域");
  assert.match(harness.element("fleet-task-experience-status").textContent, /任务已被其他操作更新/);

  conflict = false;
  assert.equal(await harness.hooks.proposeFleetTaskRevision(), true);
  assert.match(descendantText(harness.element("fleet-update-preview")), /保留步骤 1/);
  assert.match(descendantText(harness.element("fleet-update-preview")), /改为左侧区域/);
  assert.equal(harness.element("fleet-revision-confirm").disabled, false);
  assert.equal(harness.element("fleet-revision-confirm").focused, true);
  assert.equal(harness.hooks.taskExperienceState().revision, 2, "preview must not replace active task facts");

  assert.equal(await harness.hooks.confirmFleetTaskRevision(), true);
  assert.match(harness.element("fleet-task-experience-status").textContent, /完成手上的安全动作/);
  assert.equal(harness.hooks.taskExperienceState().revision, 3);
  assert.equal(requests.at(-1).url, "/v1/tasks/task-1/experience");
});

test("a created task stays selected and explained when physical approval is temporarily unavailable", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("operator-token");
  harness.element("fleet-request").value = "让1号机器人先把红色方块放到交接区";
  harness.setFetch(async (url, options = {}) => {
    if (url === "/v1/tasks" && options.method === "POST") {
      return { ok: true, status: 201, json: async () => ({ id: "task-created", state: "READY", approved: false }) };
    }
    if (url === "/v1/tasks/task-created/intents") {
      return { ok: true, status: 200, json: async () => ({ state: "READY", intents: [], robots: [] }) };
    }
    if (url === "/v1/tasks/task-created/experience") {
      return { ok: true, status: 200, json: async () => taskExperience({ taskId: "task-created", revision: 1, headline: "先把红色方块放到交接区" }) };
    }
    if (url === "/v1/tasks/task-created/approve") {
      return { ok: false, status: 409, json: async () => ({ message: "robot offline" }) };
    }
    return { ok: false, status: 404, json: async () => ({}) };
  });

  await harness.hooks.createFleetTask();

  assert.equal(harness.element("fleet-mission-headline").textContent, "先把红色方块放到交接区");
  assert.match(harness.element("fleet-task-experience-status").textContent, /任务已经创建.*暂时不能开始/);
  assert.match(harness.element("fleet-task-id").textContent, /task-created/);
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
