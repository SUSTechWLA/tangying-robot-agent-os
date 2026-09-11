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

  appendChild(child) {
    this.append(child);
    return child;
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
    this.drawCalls ||= [];
    const noOp = () => {};
    const drawing = {
      beginPath: noOp,
      clearRect: noOp,
      closePath: noOp,
      fill: noOp,
      fillRect: (...args) => this.drawCalls.push(["rect", ...args, drawing.fillStyle]),
      fillText: (...args) => this.drawCalls.push(["text", ...args, drawing.font]),
      lineTo: noOp,
      moveTo: noOp,
      restore: noOp,
      rotate: noOp,
      save: noOp,
      stroke: noOp,
      strokeRect: noOp,
      translate: noOp,
      arc: (...args) => this.drawCalls.push(["point", ...args, drawing.fillStyle]),
    };
    return drawing;
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
    atob,
    Blob,
    Date: options.Date || Date,
    JSON,
    Map,
    Math,
    Number,
    Promise,
    Set,
    String,
    URL: {
      createObjectURL(blob) {
        const url = `blob:${blob.label ?? `camera-${createdURLs.length}`}`;
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
      createElement: (tag) => {
        const element = new FakeElement(tag);
        if (tag === "img" && options.decodeImage) element.decode = () => options.decodeImage(element);
        return element;
      },
      querySelector: element,
      querySelectorAll: (selector) => {
        const attribute = selector.match(/^\[data-([a-z-]+)\]$/)?.[1];
        const key = attribute?.replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
        return key ? [...elements.values()].filter(el => key in el.dataset) : [];
      },
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
    TangyingConsoleUI: options.TangyingConsoleUI,
    TangyingNavigationView: options.TangyingNavigationView,
  });
  const boot = appSource.lastIndexOf("\nvoid bootApplication();");
  assert.notEqual(boot, -1, "app boot marker missing");
  const source = `${appSource.slice(0, boot)}\n;globalThis.__hooks = { bootApplication, pollTelemetry, drawScene, trails, adapterInput, sceneFrame, noteFleetWorldUpdate, checkFleetWorldFreshness, worldGridCellRect, renderFleetWorld, renderFleetIntents, renderFleetDevices, createFleetTask, describeFleetWorldEntity, isFleetWorldClick, fleetExecutionAdapter: () => fleetExecutionAdapter, createFleetWorldRenderer: (...args) => typeof createFleetWorldRenderer === "function" ? createFleetWorldRenderer(...args) : Promise.reject(new Error("createFleetWorldRenderer missing")), retryFleetWorldVisual: (...args) => typeof retryFleetWorldVisual === "function" ? retryFleetWorldVisual(...args) : Promise.reject(new Error("retryFleetWorldVisual missing")), bindFleetWorldToolbar, fleetLogout, startFleetWorld, fleetSelectTask, renderTaskExperience, loadFleetTaskExperience, proposeFleetTaskRevision, confirmFleetTaskRevision, taskExperienceState: () => ({ ...fleetTaskExperienceState }), pendingTaskRevision: () => fleetPendingTaskRevision, installSelectedFleetTask: (task) => { selectedFleetTask = task; }, fleetLogout, installFleetWorldTestState: (renderer, camera) => { fleetWorldRenderer = renderer; fleetWorldCamera = camera; }, setFleetTokenForTest: (token) => { fleetToken = token; }, activeFleetWorldClient: () => fleetWorldClient, latestFleetWorldSnapshot: () => fleetWorldLatestSnapshot, fleetWorldMessageQueueForTest: () => fleetWorldMessageQueue, activeFleetWebGLRenderer: () => fleetWorldWebGLRenderer, activeFleetWebGLInteraction: () => fleetWorldWebGLInteraction };`;
  vm.runInContext(source, context, { filename: "app.js" });
  vm.runInContext(`Object.assign(__hooks, {
    renderTask, renderLocalTaskExperience, loadLocalTaskExperience, refreshTask, connectEvents,
    selectLocalTask: (task) => { activeTask = task; renderTask(task); },
    localExperienceState: () => ({ ...localTaskExperienceState }),
    robotEntitiesFromSnapshot, setFleetSceneView, pollFleetFrames,
    perceptionPresentation, missionReferenceLabel, renderLocalRecovery, loadLocalRecovery,
    loadLocalTasks, openLocalTask, renderLocalProfessional, renderTelemetry,
    taskAction, drawScene2D, setSceneViewMode,
    loadLocalEvidence: (...args) => loadLocalEvidence(...args),
    selectLocalEvidence: (...args) => selectLocalEvidence(...args),
    renderLocalMissionActivities,
    pointCloudViewData: (...args) => pointCloudViewData(...args),
    currentSceneView: () => sceneViewMode,
    currentSceneCamera: () => ({ ...sceneCamera }),
    setSceneCamera: value => Object.assign(sceneCamera, value),
    projectScenePoint, drawObservedCloud,
    selectSceneCamera: (...args) => selectSceneCamera(...args),
    displayedTelemetry: () => latestTelemetry,
    pngDataURLBlob: (...args) => pngDataURLBlob(...args),
    setAudience: value => { document.body.dataset.audience = value; },
    pendingSceneImage: () => pendingSceneImage,
    sizeSceneCanvas,
    renderSceneFrameStats: (...args) => renderSceneFrameStats(...args),
    handlePageVisibility: (...args) => handlePageVisibility(...args),
    setPage: page => { document.body.dataset.page = page; },
    setDocumentHidden: hidden => { document.hidden = hidden; },
    renderLocalMissionSteps, pollLocalTask, pollMetrics, pollLocalWorld,
    openLocalTaskById,
    setLocalTaskFilter: value => { localTaskFilter = value; localTaskLimit = LOCAL_TASK_PAGE_SIZE; },
    setLocalTaskLimit: value => { localTaskLimit = value; },
    localTaskWindow: () => ({ filter: localTaskFilter, limit: localTaskLimit }),
    localTaskLookupState: () => $("#local-task-lookup-state").textContent,
  });`, context);
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

test("local perception provenance distinguishes RGBD from undeclared simulation", () => {
  const { hooks } = createHarness();
  const legacy = hooks.perceptionPresentation({ mode: "SIMULATION", robotState: { simulation: true } });
  assert.equal(legacy.mode, "undeclared");
  assert.match(legacy.detail, /未声明/);
  const rgbd = hooks.perceptionPresentation({
    observedAt: new Date().toISOString(),
    robotState: { perception: { mode: "rgbd", source_id: "head-rgbd", camera: "head_depth" } },
    reconstruction: { observationId: "obs-camera-3", sourceId: "head-rgbd", frameId: "world", points: [[0, 0, 1]] },
  });
  assert.equal(rgbd.mode, "rgbd");
  assert.equal(rgbd.sourceId, "head-rgbd");
  assert.equal(rgbd.observationId, "obs-camera-3");
  assert.equal(rgbd.pointCount, 1);
  assert.match(rgbd.label, /RGB-D/);
});

test("local task descriptions translate known scene references without inventing unknown names", () => {
  const { hooks } = createHarness();
  assert.equal(hooks.missionReferenceLabel("机器人把red-cup放到storage_bin/right_side"), "机器人把红色杯子放到右侧收纳盒");
  assert.equal(hooks.missionReferenceLabel("blue-bottle"), "蓝色瓶子");
  assert.equal(hooks.missionReferenceLabel("custom-object-17"), "custom-object-17");
});

test("local recovery never enables resume for unconfirmed physical outcomes", () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "RECOVERABLE_FAILURE" });
  harness.hooks.renderLocalRecovery({ taskId: "task-1", canResume: true, requiresReconciliation: true, uncertainStepIds: ["pick"], reason: "夹爪动作结果尚未确认" });
  assert.equal(harness.element("resume").disabled, true);
  assert.match(descendantText(harness.element("local-recovery-content")), /夹爪动作结果尚未确认/);
  harness.hooks.renderLocalRecovery({ taskId: "task-1", canResume: true, requiresReconciliation: false, completedStepIds: ["observe"], reason: "可从中断处继续" });
  assert.equal(harness.element("resume").disabled, false);
  harness.hooks.renderLocalRecovery({ taskId: "task-other", canResume: false });
  assert.equal(harness.element("resume").disabled, false, "another task cannot replace current permissions");
});

test("local recovery request rejects a response after task selection changes", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "PAUSED" });
  const response = deferred();
  harness.setFetch(async () => ({ ok: true, json: () => response.promise }));
  const pending = harness.hooks.loadLocalRecovery("task-1");
  await Promise.resolve();
  harness.hooks.selectLocalTask({ id: "task-2", state: "READY" });
  response.resolve({ taskId: "task-1", canResume: true });
  assert.equal(await pending, false);
  assert.equal(harness.element("resume").disabled, true);
});

test("local pause accepts the recovery acknowledgement and refreshes the task", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "EXECUTING", approved: true });
  harness.hooks.renderLocalRecovery({ taskId: "task-1", state: "EXECUTING", canPause: true });
  const requests = [];
  harness.setFetch(async (url, options = {}) => {
    requests.push([url, options.method || "GET"]);
    if (url.endsWith("/pause") || url.endsWith("/recovery")) return { ok: true, json: async () => ({ taskId: "task-1", state: "PAUSED", canResume: true, reason: "已安全暂停" }) };
    if (url === "/v1/tasks/task-1") return { ok: true, json: async () => ({ id: "task-1", state: "PAUSED", approved: true }) };
    return { ok: false, status: 404 };
  });
  await harness.hooks.taskAction("pause");
  assert.deepEqual(requests.filter(([, method]) => method === "POST"), [["/v1/tasks/task-1/pause", "POST"]]);
  assert.equal(harness.element("state").textContent, "PAUSED");
  assert.equal(harness.element("resume").disabled, false);
});

test("a task with no explanation record stops being asked for one on every poll", async () => {
  const harness = createHarness();
  const task = { id: "task-legacy", request: "把红色杯子放进收纳盒", state: "SUCCEEDED", events: [] };
  let experienceRequests = 0;
  harness.setFetch(async url => {
    if (url === "/v1/tasks/task-legacy") return { ok: true, json: async () => task };
    if (url.endsWith("/experience")) {
      experienceRequests += 1;
      return { ok: false, status: 404 };
    }
    if (url.endsWith("/recovery")) return { ok: true, json: async () => ({ taskId: task.id, canResume: false }) };
    return { ok: false, status: 404 };
  });
  await harness.hooks.openLocalTask("task-legacy");
  assert.equal(experienceRequests, 1);
  // The task is terminal, so the record will never appear; polling must not
  // keep hammering an endpoint that has already answered.
  await harness.hooks.loadLocalTaskExperience("task-legacy");
  await harness.hooks.loadLocalTaskExperience("task-legacy");
  assert.equal(experienceRequests, 1);

  // A developer asking for a fresh read is still allowed to retry.
  await harness.hooks.loadLocalTaskExperience("task-legacy", { force: true });
  assert.equal(experienceRequests, 2);
});

test("local task completion does not manufacture tool evidence or expose stale resume permission", () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, activities: [{ stepId: "pick", displayName: "拿稳物品", status: "AWAITING_EVIDENCE", statusText: "正在确认动作结果" }] }));
  harness.hooks.renderLocalRecovery({ taskId: "task-1", canResume: true });
  assert.match(descendantText(harness.element("local-tool-activities")), /正在确认动作结果/);
  assert.equal(harness.element("resume").disabled, true);
});

test("local persisted history opens task events without approving or replaying actions", async () => {
  const harness = createHarness();
  const task = { id: "task-old", request: "收好红色杯子", state: "PAUSED", events: [{ sequence: 1, type: "TASK_CREATED" }] };
  const methods = [];
  harness.setFetch(async (url, options = {}) => {
    methods.push(options.method || "GET");
    if (url === "/v1/tasks") return { ok: true, json: async () => [task] };
    if (url === "/v1/tasks/task-old") return { ok: true, json: async () => task };
    if (url.endsWith("/recovery")) return { ok: true, json: async () => ({ taskId: task.id, canResume: true }) };
    return { ok: false, status: 404 };
  });
  await harness.hooks.loadLocalTasks();
  assert.match(descendantText(harness.element("local-task-list")), /收好红色杯子/);
  await harness.hooks.openLocalTask("task-old");
  assert.equal(harness.element("task-id").textContent, "task-old");
  assert.equal(harness.element("events").children.length, 1);
  assert.ok(methods.every(method => method === "GET"));
});

test("local history shows the task id and narrows to failures on request", async () => {
  const harness = createHarness();
  const tasks = [
    { id: "task-ok", request: "递送蓝色瓶子", state: "SUCCEEDED", updatedAt: "2026-09-08T12:00:00Z" },
    { id: "task-broken", request: "把绿色杯子放进左侧收纳盒", state: "RECOVERABLE_FAILURE", updatedAt: "2026-09-08T11:00:00Z" },
    { id: "task-dropped", request: "整理桌面", state: "CANCELLED", updatedAt: "2026-09-08T10:00:00Z" },
  ];
  harness.setFetch(async url => {
    if (url === "/v1/tasks") return { ok: true, json: async () => tasks };
    return { ok: false, status: 404 };
  });

  await harness.hooks.loadLocalTasks();
  const listed = descendantText(harness.element("local-task-list"));
  // The id is what a log line quotes back, so every row has to carry it.
  assert.match(listed, /task-broken/);
  assert.equal(harness.element("local-task-list").children.length, 3);

  harness.hooks.setLocalTaskFilter("failed");
  await harness.hooks.loadLocalTasks();
  const failures = harness.element("local-task-list").children;
  assert.equal(failures.length, 1);
  const failure = descendantText(failures[0]);
  assert.match(failure, /绿色杯子/);
  assert.match(failure, /task-broken/);
  assert.doesNotMatch(failure, /task-ok/);
  assert.match(harness.element("local-history-state").textContent, /筛选后 1 个/);
});

test("local history lookup replays a task that is outside the visible window", async () => {
  const recent = { id: "task-recent", request: "收拾桌面", state: "SUCCEEDED", updatedAt: "2026-09-08T12:00:00Z" };
  const buried = {
    id: "task-8db2fd74468200b1082bf860", request: "把绿色杯子放进左侧收纳盒", state: "RECOVERABLE_FAILURE",
    updatedAt: "2026-09-01T09:00:00Z", events: [{ sequence: 1, type: "TASK_CREATED" }],
  };
  const harness = createHarness();
  const requested = [];
  harness.setFetch(async url => {
    requested.push(url);
    if (url === "/v1/tasks") return { ok: true, json: async () => [recent] };
    if (url === `/v1/tasks/${buried.id}`) return { ok: true, json: async () => buried };
    if (url.endsWith("/recovery")) return { ok: true, json: async () => ({ taskId: buried.id, canResume: true }) };
    return { ok: false, status: 404 };
  });

  await harness.hooks.loadLocalTasks();
  // The buried task is genuinely unreachable from the list, which is why the
  // lookup has to work on its own.
  assert.equal(harness.element("local-task-list").children.length, 1);
  assert.doesNotMatch(descendantText(harness.element("local-task-list")), /8db2fd74468200b1082bf860/);

  assert.equal(await harness.hooks.openLocalTaskById(`  ${buried.id}  `), true);
  assert.equal(harness.element("task-id").textContent, buried.id);
  assert.ok(requested.includes(`/v1/tasks/${buried.id}`));
  assert.match(harness.hooks.localTaskLookupState(), new RegExp(`已打开 ${buried.id}`));
});

test("local history lookup separates a missing task from an unreachable service", async () => {
  const harness = createHarness();
  harness.setFetch(async url => {
    if (url === "/v1/tasks") return { ok: true, json: async () => [] };
    return { ok: false, status: 404 };
  });

  await harness.hooks.loadLocalTasks();
  assert.equal(await harness.hooks.openLocalTaskById("task-does-not-exist"), false);
  const missing = harness.hooks.localTaskLookupState();
  assert.match(missing, /找不到任务编号 task-does-not-exist/);
  assert.doesNotMatch(missing, /服务连接/);

  // An empty box is a prompt, not a failed request.
  assert.equal(await harness.hooks.openLocalTaskById("   "), false);
  assert.match(harness.hooks.localTaskLookupState(), /请先粘贴一个任务编号/);
});

test("local history pages past the window instead of hiding the remainder", async () => {
  const tasks = Array.from({ length: 3 }, (_, index) => ({
    id: `task-${index}`, request: `任务 ${index}`, state: "SUCCEEDED",
    updatedAt: `2026-09-08T1${index}:00:00Z`,
  }));
  const harness = createHarness();
  harness.setFetch(async url => {
    if (url === "/v1/tasks") return { ok: true, json: async () => tasks };
    return { ok: false, status: 404 };
  });
  harness.hooks.setLocalTaskLimit(2);

  await harness.hooks.loadLocalTasks();
  const window = harness.element("local-task-list").children;
  assert.equal(window.length, 3, "two rows plus the control that reveals the rest");
  assert.match(descendantText(window[2]), /显示更多（还有 1 个）/);

  window[2].children[0].emit("click");
  const expanded = harness.element("local-task-list").children;
  assert.equal(expanded.length, 3);
  assert.doesNotMatch(descendantText(expanded[2]), /显示更多/);
  assert.match(descendantText(expanded[2]), /task-0/);
});

test("the history filters partition every state instead of leaving gaps", async () => {
  const states = ["SUCCEEDED", "FAILED", "FAILED_SAFE", "RECOVERABLE_FAILURE", "SAFETY_STOPPED",
    "BLOCKED", "WAITING_USER", "CANCELLED", "RUNNING", "AWAITING_APPROVAL", "PAUSED"];
  const tasks = states.map((state, index) => ({
    id: `task-${state.toLowerCase()}`, request: `${state} 任务`, state,
    updatedAt: `2026-09-08T${String(index).padStart(2, "0")}:00:00Z`,
  }));
  const harness = createHarness();
  harness.setFetch(async url => {
    if (url === "/v1/tasks") return { ok: true, json: async () => tasks };
    return { ok: false, status: 404 };
  });

  const listed = async filter => {
    harness.hooks.setLocalTaskFilter(filter);
    await harness.hooks.loadLocalTasks();
    return harness.element("local-task-list").children
      .filter(item => !item.children.some(child => child.className === "local-task-more"))
      .map(item => descendantText(item));
  };
  const showsId = (rows, state) => rows.some(row => row.includes(`task-${state.toLowerCase()}`));
  await harness.hooks.loadLocalTasks();
  assert.equal(harness.element("local-task-list").children.length, states.length, "全部 must show every task");

  const failed = await listed("failed");
  // A safety stop or a blocked task is exactly what someone filtering for
  // "went wrong" is looking for; missing one would hide the task they need.
  for (const state of ["FAILED", "FAILED_SAFE", "RECOVERABLE_FAILURE", "SAFETY_STOPPED", "BLOCKED", "WAITING_USER"]) {
    assert.ok(showsId(failed, state), `${state} must be listed as a failure`);
  }
  assert.equal(failed.length, 6);

  const active = await listed("active");
  assert.deepEqual(active.map(row => row.match(/task-[a-z_]+/)[0]).sort(),
    ["task-awaiting_approval", "task-paused", "task-running"]);
  // The four buckets together account for every stored task.
  const succeeded = await listed("succeeded");
  const cancelled = await listed("cancelled");
  assert.equal(succeeded.length + cancelled.length + failed.length + active.length, states.length);
});

test("local event backfill and live replay keep one ordered event with correlation IDs", () => {
  const harness = createHarness();
  const event = { sequence: 1, type: "TOOL_ACTIVITY", stepId: "pick", occurredAt: "2026-09-08T11:14:00Z", payload: { commandId: "cmd-pick", evidenceIds: ["rgbd-41"], arguments: { apiKey: "never-display" } } };
  const task = { id: "task-1", state: "EXECUTING", events: [event] };
  harness.hooks.selectLocalTask(task);
  harness.hooks.connectEvents(task.id);
  const socket = harness.webSockets.at(-1);
  socket.emit("message", { data: JSON.stringify(event) });
  assert.equal(harness.element("events").children.length, 1);
  harness.hooks.renderTask({ ...task, events: [event, { sequence: 2, type: "LOCAL_RUN_SUCCEEDED" }] });
  assert.equal(harness.element("events").children.length, 2);
  const text = descendantText(harness.element("events"));
  assert.match(text, /cmd-pick/);
  assert.match(text, /rgbd-41/);
  assert.doesNotMatch(text, /never-display/);
});

test("local professional trace includes evidence but excludes raw action parameters", () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.hooks.renderLocalProfessional({ taskId: "task-1", revision: 2, professional: { activities: [{ commandId: "cmd-9", evidenceIds: ["obs-9"], arguments: { secretToken: "never-display" }, action_chunk: { raw: true } }] } });
  const trace = harness.element("local-professional-trace").textContent;
  assert.match(trace, /cmd-9/);
  assert.match(trace, /obs-9/);
  assert.doesNotMatch(trace, /never-display|secretToken|action_chunk/);
});

test("local task refresh preserves evidence-confirmed progress and understanding", () => {
  const harness = createHarness();
  const task = { id: "task-1", request: "先交接", state: "RUNNING" };
  harness.hooks.selectLocalTask(task);
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1 }));

  harness.hooks.renderTask({ ...task, state: "SUCCEEDED" });

  assert.equal(harness.element("local-understanding").textContent, taskExperience().understanding);
  assert.match(descendantText(harness.element("local-step-ribbon")), /已经完成.*红色方块/);
  assert.equal(harness.element("local-step-ribbon").children[0].className, "mission-step satisfied");
});

test("local capabilities display all safe arguments without secrets", () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
  const experience = taskExperience({ revision: 1 });
  experience.activities[0].safeArguments = {
    secretToken: "must-not-render", objectId: "red-block", targetRef: "right-target-zone",
  };
  harness.hooks.renderLocalTaskExperience(experience);

  const text = descendantText(harness.element("local-tool-activities"));
  assert.match(text, /红色方块/);
  assert.match(text, /右侧目标区/);
  assert.doesNotMatch(text, /must-not-render/);
});

test("local task selection fences a late experience response", async () => {
  const harness = createHarness();
  const response = deferred();
  harness.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
  harness.setFetch(async () => ({ ok: true, json: () => response.promise }));
  const pending = harness.hooks.loadLocalTaskExperience("task-1");
  await Promise.resolve();
  harness.hooks.selectLocalTask({ id: "task-2", request: "新的任务", state: "READY" });
  response.resolve(taskExperience());

  assert.equal(await pending, false);
  assert.equal(harness.hooks.localExperienceState().taskId, "task-2");
  assert.equal(harness.element("local-understanding").textContent, "新的任务");
});

test("local task selection fences late task refresh and obsolete socket events", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
  harness.hooks.connectEvents("task-1");
  const oldSocket = harness.webSockets.at(-1);
  const response = deferred();
  harness.setFetch(async () => ({ ok: true, json: () => response.promise }));
  const pending = harness.hooks.refreshTask("task-1");
  await Promise.resolve();
  harness.hooks.selectLocalTask({ id: "task-2", state: "READY" });
  harness.hooks.connectEvents("task-2");
  const fetchCount = harness.fetches.length;
  oldSocket.emit("message", { data: JSON.stringify({ type: "STATE_CHANGED", sequence: 1 }) });
  oldSocket.emit("close");
  response.resolve({ id: "task-1", state: "SUCCEEDED" });
  await pending;

  assert.equal(harness.element("task-id").textContent, "task-2");
  assert.equal(harness.element("events").children.length, 0);
  assert.equal(harness.fetches.length, fetchCount);
});

test("local task revision gaps resync from server history instead of freezing progress", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1 }));
  harness.setFetch(async (url) => ({
    ok: true,
    json: async () => url.endsWith("/revisions")
      ? { currentRevision: 3 }
      : taskExperience({ revision: 3, aggregateVersion: 12, understanding: "改送到新的目标" }),
  }));

  assert.equal(await harness.hooks.loadLocalTaskExperience("task-1"), true);
  assert.equal(harness.hooks.localExperienceState().revision, 3);
  assert.equal(harness.element("local-understanding").textContent, "改送到新的目标");
  assert.deepEqual(harness.fetches, [
    "/v1/tasks/task-1/experience", "/v1/tasks/task-1/revisions", "/v1/tasks/task-1/experience",
  ]);
});

test("local robot state supplies the reporting robot without moving another robot", () => {
  const harness = createHarness();
  const other = { entityId: "robot-2", category: "robot", pose: [2, 1, 0, 1, 0, 0, 0] };
  const entities = [other];
  const robots = harness.hooks.robotEntitiesFromSnapshot(
    entities, { base_pose: [0.4, 0.8, 0, 1, 0, 0, 0] }, { robotId: "robot-1" },
  );
  assert.equal(robots.length, 2);
  assert.deepEqual(Array.from(robots.find((robot) => robot.entityId === "robot-1").pose), [0.4, 0.8, 0, 1, 0, 0, 0]);
  assert.equal(robots.find((robot) => robot.entityId === "robot-2"), other);
  assert.equal(entities.length, 1, "rendering must not modify the observation");
});

test("local robot fallback repairs only its own missing pose and ignores invalid state", () => {
  const harness = createHarness();
  const own = { entityId: "robot-1", category: "robot" };
  const missingOther = { entityId: "robot-2", category: "robot" };
  const result = harness.hooks.robotEntitiesFromSnapshot(
    [own, missingOther], { base_pose: [0.4, 0.8, 0, 1, 0, 0, 0] }, { robotId: "robot-1" },
  );
  assert.equal(result.length, 1, "unobserved robot must not borrow another robot's position");
  assert.equal(result[0].entityId, "robot-1");
  assert.deepEqual(Array.from(result[0].pose), [0.4, 0.8, 0, 1, 0, 0, 0]);
  assert.equal(own.pose, undefined);
  assert.equal(harness.hooks.robotEntitiesFromSnapshot([], { base_pose: [null, 0, 0] }, { robotId: "robot-1" }).length, 0);
});

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

test("full task snapshots without cursors refresh progress within the same revision", () => {
  const harness = createHarness();
  const initial = taskExperience();
  assert.equal(harness.hooks.renderTaskExperience(initial), true);
  harness.hooks.installSelectedFleetTask({ id: "task-1", state: "SUCCEEDED" });
  const finished = taskExperience({ steps: initial.steps.map(step => ({ ...step, status: "SATISFIED", statusText: "已完成" })) });
  assert.equal(harness.hooks.renderTaskExperience(finished), true);
  assert.equal(harness.element("fleet-step-ribbon").children[1].className, "mission-step satisfied");
  assert.equal(harness.element("fleet-task-experience-status").textContent, "任务已完成");
  assert.equal(harness.element("fleet-mission-update-state").textContent, "任务已完成");
});

test("task snapshots with cursors still reject duplicate and out-of-order evidence", () => {
  const harness = createHarness();
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ cursor: 5 })), true);
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ cursor: 5, headline: "duplicate" })), false);
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ cursor: 4, headline: "older" })), false);
  assert.equal(harness.hooks.renderTaskExperience(taskExperience({ cursor: 6, headline: "newer" })), true);
  assert.equal(harness.element("fleet-mission-headline").textContent, "newer");
});

for (const mode of ["Local", "Fleet"]) {
  test(`${mode} full snapshots cannot be rolled back by an older overlapping request`, async () => {
    const harness = createHarness();
    if (mode === "Local") harness.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
    else harness.hooks.installSelectedFleetTask({ id: "task-1", state: "RUNNING" });
    const oldResponse = deferred();
    const firstReading = deferred();
    let calls = 0;
    harness.setFetch(async () => ({ ok: true, status: 200, json: () => {
      if (++calls === 1) { firstReading.resolve(); return oldResponse.promise; }
      return taskExperience({ revision: 1, understanding: "已完成的新观测" });
    } }));
    const load = harness.hooks[`load${mode}TaskExperience`];
    const pending = load("task-1");
    await firstReading.promise;
    assert.equal(await load("task-1"), true);
    oldResponse.resolve(taskExperience({ revision: 1, understanding: "旧的准备状态" }));
    assert.equal(await pending, false);
    assert.equal(harness.element(mode === "Local" ? "local-understanding" : "fleet-mission-understanding").textContent, "已完成的新观测");
  });
}

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
    colorFrameAvailable: true,
    entities: [{ entityId: "xlerobot", category: "robot", pose: [0, 0, 0, 1, 0, 0, 0] }],
    robotState: { joint_positions: { left: 0 } },
  };
}

test("a deferred old frame cannot overwrite or leak past a resumed page's newer telemetry generation", async () => {
  const harness = createHarness();
  const oldBlob = deferred();
  const oldBlobStarted = deferred();
  const telemetryPayloads = [
    { adapters: ["mujoco"], latest: snapshot(new Date(Date.now() - 1000).toISOString()) },
    { adapters: ["mujoco"], latest: snapshot(new Date(Date.now() - 500).toISOString()) },
    { adapters: ["mujoco"], latest: snapshot(new Date(Date.now() - 1000).toISOString()) },
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
  harness.hooks.setDocumentHidden(true); harness.hooks.handlePageVisibility();
  harness.hooks.setDocumentHidden(false); harness.hooks.handlePageVisibility();
  const second = harness.hooks.pollTelemetry();
  await second;
  assert.equal(firstSignal.aborted, true, "new generation must abort the old telemetry/frame chain");
  assert.equal(harness.hooks.pendingSceneImage().src, "blob:new");

  oldBlob.resolve({ label: "old" });
  await first;
  await Promise.resolve();
  assert.deepEqual(harness.createdURLs, ["blob:new"], "stale blob must be rejected before URL creation");
  assert.equal(harness.hooks.pendingSceneImage().src, "blob:new", "stale blob must never be written to img.src");

  harness.hooks.pendingSceneImage().onload();
  await harness.hooks.pollTelemetry();
  assert.equal(frameCalls, 2, "older observedAt must not trigger another frame request");
  assert.deepEqual(harness.revokedURLs, [], "rejected older metadata must keep the current decoded image");
});

test("developer semantic trails are removed as soon as an entity disappears", () => {
  const harness = createHarness();
  const entity = { entityId: "red-cup", category: "cup", attributes: { color: "red" }, pose: [0.1, 0.2] };
  harness.hooks.drawScene2D([entity], {}, snapshot("2026-08-19T01:00:00Z"));
  assert.equal(harness.hooks.trails.has("red-cup"), true);

  harness.hooks.drawScene2D([], {}, snapshot("2026-08-19T01:00:01Z"));
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

test("demo entry is only requested when health explicitly advertises demo authentication", async () => {
  const harness = createHarness();
  harness.setFetch(async (url) => {
    if (url === "/healthz") return { ok: true, json: async () => ({ mode: "fleet", authMode: "demo" }) };
    return { ok: false, status: 503, json: async () => ({}) };
  });
  await harness.hooks.bootApplication();
  assert.ok(harness.fetches.includes("/v1/auth/demo-session"));
  assert.equal(harness.element("fleet-login").hidden, false, "failed demo entry must not fake an authenticated dashboard");
});

test("double-clicking create sends one task while the first request is pending", async () => {
  const harness = createHarness();
  const response = deferred();
  harness.element("fleet-request").value = "移动方块";
  harness.setFetch(() => response.promise);
  const first = harness.hooks.createFleetTask();
  await harness.hooks.createFleetTask();
  assert.equal(harness.fetches.filter(url => url === "/v1/tasks").length, 1);
  response.resolve({ ok: false, status: 400, json: async () => ({ message: "unsupported task" }) });
  await first;
  assert.equal(harness.element("fleet-create").disabled, false);
});


test("Fleet emergency stop and source anomalies propagate to the operator summary", () => {
  const updates = [];
  const harness = createHarness({ TangyingConsoleUI: { update: value => updates.push(value) } });
  harness.hooks.renderFleetWorld({ ...visualSnapshot(42),
    robots: { "robot-1": { robotId: "robot-1", emergencyStopped: true } },
    sources: { source: { freshness: "FRESH", anomalies: ["SENSOR_FAILURE"] } },
  });
  const update = updates.find(value => value.worldRevision === 42);
  assert.equal(update.emergencyStopped, true);
  assert.equal(update.anomalyCount, 1);
});


test("scene switching is presentation only and keeps world revision and renderer", async () => {
  const renderer = {
    bundle: { modelHash: "a".repeat(64), manifest: { sceneId: "robocasa-handoff-v1" } },
    status: { state: "READY" }, render() { return true; }, dispose() {}, select() {},
  };
  const harness = createHarness({ TangyingWebGL: {
    AssetRegistry: class { async load() { return renderer.bundle; } },
    WebGLSceneRenderer: { create() { return renderer; } },
  } });
  await harness.hooks.createFleetWorldRenderer(visualSnapshot(7));
  harness.hooks.renderFleetWorld(visualSnapshot(7));
  for (const view of ["simple", "cameras", "map", "three"]) {
    harness.hooks.setFleetSceneView(view);
    assert.equal(harness.element("fleet-scene-camera-panel").hidden, view !== "cameras");
    assert.equal(harness.element("fleet-scene-map-panel").hidden, view !== "map");
    assert.equal(harness.element("fleet-godview-webgl").hidden, view !== "three");
    assert.equal(harness.element("fleet-godview-canvas").hidden, view !== "simple");
    assert.equal(harness.hooks.activeFleetWebGLRenderer(), renderer);
    assert.equal(harness.hooks.latestFleetWorldSnapshot().revision, 7);
  }
  assert.equal(harness.fetches.length, 0, "view selection must not submit tasks or reset simulation");
  harness.hooks.setFleetSceneView("simple");
  const changed = visualSnapshot(8);
  changed.entities.kitchen.attributes.model_hash = "b".repeat(64);
  harness.hooks.renderFleetWorld(changed);
  harness.hooks.setFleetSceneView("three");
  assert.equal(harness.element("fleet-godview-webgl").hidden, true);
  assert.equal(harness.element("fleet-godview-canvas").hidden, false);
  assert.match(harness.element("fleet-visual-detail").textContent, /VISUAL_MODEL_MISMATCH/);
});

test("Fleet camera copies share bytes, mark failures, and clear URLs on logout", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("session");
  const copies = ["fleet-frame-robot-1", "fleet-workspace-frame-robot-1"].map(id => harness.element(id));
  for (const image of copies) { image.dataset.frameRobot = "robot-1"; image.hidden = true; }
  const state = harness.element("frame-state"); state.dataset.frameState = "robot-1";
  harness.setFetch(async url => url === "/v1/scene/frames"
    ? { ok: true, json: async () => ({ frames: [{ robotId: "robot-1" }] }) }
    : { ok: true, blob: async () => ({ label: "camera-1" }) });
  await harness.hooks.pollFleetFrames();
  for (const image of copies) { assert.equal(image.src, "blob:camera-1"); assert.equal(image.hidden, false); }
  assert.equal(harness.createdURLs.length, 1, "both views must share one frame request and URL");
  assert.match(state.textContent, /收到/);
  harness.setFetch(async () => ({ ok: false, status: 503 }));
  await harness.hooks.pollFleetFrames();
  assert.match(state.textContent, /最后画面/);
  assert.match(harness.element("fleet-workspace-camera-status").textContent, /未更新/);
  assert.equal(copies[0].src, "blob:camera-1");
  harness.hooks.fleetLogout();
  for (const image of copies) { assert.equal(image.src, ""); assert.equal(image.hidden, true); }
  assert.deepEqual(harness.revokedURLs, ["blob:camera-1"]);
});

test("Fleet camera response arriving after logout cannot restore an old session image", async () => {
  const harness = createHarness();
  harness.hooks.setFleetTokenForTest("session");
  const image = harness.element("fleet-frame-robot-1"); image.dataset.frameRobot = "robot-1"; image.hidden = true;
  const bytes = deferred(); const started = deferred();
  harness.setFetch(async url => url === "/v1/scene/frames"
    ? { ok: true, json: async () => ({ frames: [{ robotId: "robot-1" }] }) }
    : { ok: true, blob: () => { started.resolve(); return bytes.promise; } });
  const pending = harness.hooks.pollFleetFrames();
  await started.promise;
  await harness.hooks.pollFleetFrames();
  assert.equal(harness.fetches.length, 2, "overlapping frame polls must not start duplicate downloads");
  harness.hooks.fleetLogout();
  bytes.resolve({ label: "old-camera" });
  await pending;
  assert.equal(image.src, "");
  assert.equal(image.hidden, true);
  assert.deepEqual(harness.createdURLs, []);
});

function rgbdSnapshot() {
  const captured = Date.now() - 100;
  return { ...snapshot(new Date(captured).toISOString()), robotId: "robot-a",
    colorFrameAvailable: true, depthFrameAvailable: true,
    robotProfile: { robotId: "robot-a", adapterId: "mujoco", sensors: [{ sourceId: "head", sourceType: "rgbd_camera", frameId: "optical", transformRevision: "cal-1", maxAgeMs: 2000 }] },
    robotState: { perception: { mode: "rgbd", source_id: "head", observed_at_unix_ms: captured } },
    entities: [{ entityId: "hidden-full-world", category: "cup", pose: [9, 9, 9] }],
    reconstruction: { schemaVersion: "scene.reconstruction.v1", robotId: "robot-a", sourceType: "rgbd_camera", sourceId: "head", sourceFrameId: "optical", frameId: "world", units: "m", transformRevision: "cal-1", observationId: "obs-1", sequence: 1, observedAtUnixMs: captured, points: [[0, 0, .4], [.1, .1, .5]], entities: [{ entityId: "red-cup", category: "cup", pose: [.05, .05, .45, 1, 0, 0, 0] }] },
  };
}

test("observed point cloud requires current canonical RGBD with matching sensor identity", () => {
  const { hooks } = createHarness();
  const frame = rgbdSnapshot();
  assert.equal(hooks.pointCloudViewData(frame).available, true);
  for (const changes of [{ units: "mm" }, { frameId: "camera" }, { robotId: "another" }, { sourceId: "other-camera" }, { transformRevision: "old-cal" }, { observedAtUnixMs: Date.now() - 2500 }, { observedAtUnixMs: Date.now() + 1000 }, { points: [[NaN, 0, 0]] }, { points: [] }, { sourceType: "sim_ground_truth" }]) {
    assert.equal(hooks.pointCloudViewData({ ...frame, reconstruction: { ...frame.reconstruction, ...changes } }).available, false, JSON.stringify(changes));
  }
});

test("primary cloud renders only observed points and entity labels without a hidden scene model", () => {
  const harness = createHarness();
  harness.hooks.renderTelemetry(rgbdSnapshot());
  harness.hooks.setSceneViewMode("cloud");
  const calls = harness.element("scene-canvas").drawCalls;
  assert.equal(calls.filter(call => call[0] === "point").length, 2);
  assert.equal(calls.some(call => call[0] === "text" && String(call[1]).includes("红色杯子")), false, "labels default to hidden");
  harness.element("cloud-labels").checked = true;
  harness.hooks.renderTelemetry(rgbdSnapshot());
  assert.equal(calls.some(call => call[0] === "text" && String(call[1]).includes("红色杯子")), true);
  assert.equal(calls.some(call => String(call[1]).includes("hidden-full-world")), false);
  assert.equal(harness.hooks.sceneFrame.hidden, true);
  const empty = rgbdSnapshot();
  empty.reconstruction.points = [];
  harness.hooks.renderTelemetry(empty);
  assert.equal(harness.element("scene-live-state").textContent, "UNAVAILABLE");
  assert.match(harness.element("scene-frame-message").textContent, /未知/);
});

test("sensor controls reflect actual fresh media and do not expose semantic debug in operator mode", () => {
  const harness = createHarness();
  const data = rgbdSnapshot();
  data.depthFrameAvailable = false;
  harness.hooks.renderTelemetry(data);
  assert.equal(harness.element("view-depth").disabled, true);
  assert.equal(harness.element("view-cloud").disabled, false);
  harness.hooks.setSceneViewMode("depth");
  assert.equal(harness.hooks.currentSceneView(), "live");
  harness.hooks.setSceneViewMode("orbit");
  assert.equal(harness.hooks.currentSceneView(), "live");
  data.reconstruction.observedAtUnixMs = Date.now() - 10000;
  harness.hooks.renderTelemetry(data);
  assert.equal(harness.element("view-cloud").disabled, true);
});

test("late atomic color responses cannot replace selected depth and valid depth is shown only after decoding", async () => {
  const harness = createHarness();
  const color = deferred(); const started = deferred();
  const data = rgbdSnapshot(); let captures = 0;
  harness.setFetch(async url => {
    if (url.startsWith("/v1/telemetry")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: data }) };
    captures++;
    return { ok: true, json: async () => {
      if (captures === 1) { started.resolve(); return color.promise; }
      return { snapshot: data, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG };
    } };
  });
  const polling = harness.hooks.pollTelemetry(); await started.promise;
  await harness.hooks.setSceneViewMode("depth");
  assert.ok(harness.hooks.pendingSceneImage().src.startsWith("blob:"));
  assert.notEqual(harness.element("scene-live-state").textContent, "LIVE");
  harness.hooks.pendingSceneImage().onload();
  assert.equal(harness.element("scene-live-state").textContent, "LIVE");
  assert.match(harness.element("scene-frame-message").textContent, /深度/);
  color.resolve({ snapshot: data, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }); await polling;
  assert.equal(harness.createdURLs.length, 1);
});

test("stale atomic primary capture clears the scene with no semantic fallback", async () => {
  const harness = createHarness(); const data = rgbdSnapshot(); const stale = structuredClone(data);
  stale.reconstruction.observedAtUnixMs -= 10000;
  stale.observedAt = new Date(stale.reconstruction.observedAtUnixMs).toISOString();
  stale.robotState.perception.observed_at_unix_ms = stale.reconstruction.observedAtUnixMs;
  harness.setFetch(async url => url.startsWith("/v1/telemetry")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: data }) }
    : cameraResponse(stale));
  await harness.hooks.pollTelemetry();
  assert.equal(harness.createdURLs.length, 0);
  assert.equal(harness.hooks.sceneFrame.hidden, true);
  assert.match(harness.element("scene-frame-message").textContent, /过期|延迟/);
  assert.equal(harness.element("scene-canvas").drawCalls.some(call => call[0] === "point"), false);
});


test("opening a local history page selects the newest task from an ascending API response", async () => {
  const harness = createHarness();
  const tasks = [{ id: "old", state: "SUCCEEDED", request: "旧任务", updatedAt: "2026-08-01T00:00:00Z" }, { id: "new", state: "RECOVERABLE_FAILURE", request: "新任务", updatedAt: "2026-09-08T00:00:00Z" }];
  harness.setFetch(async url => {
    if (url === "/v1/tasks") return { ok: true, json: async () => tasks };
    if (url === "/v1/tasks/new") return { ok: true, json: async () => ({ ...tasks[1], events: [{ type: "STATE_CHANGED", sequence: 7, message: "工具未完成", occurredAt: tasks[1].updatedAt }] }) };
    return { ok: false, status: 404 };
  });
  await harness.hooks.loadLocalTasks({ openLatest: true });
  assert.equal(harness.element("task-id").textContent, "new");
  assert.ok(harness.fetches.includes("/v1/tasks/new"));
  assert.equal(harness.element("events").children.length, 1);
  assert.match(descendantText(harness.element("local-step-ribbon")), /历史记录未保存详细计划/);
});

test("selected point cloud becomes unknown after a frozen capture ages beyond sensor budget", () => {
  const harness = createHarness();
  const data = rgbdSnapshot();
  harness.hooks.renderTelemetry(data);
  harness.hooks.setSceneViewMode("cloud");
  data.reconstruction.observedAtUnixMs = Date.now() - 2100;
  harness.hooks.renderTelemetry(data);
  assert.equal(harness.element("scene-live-state").textContent, "UNAVAILABLE");
  assert.match(harness.element("scene-frame-message").textContent, /已过期.*未知/);
  assert.equal(harness.element("view-cloud").disabled, true);
});

test("an image that becomes stale during decoding never becomes live", async () => {
  const harness = createHarness();
  const data = rgbdSnapshot();
  harness.setFetch(async url => url.startsWith("/v1/telemetry")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: data }) }
    : cameraResponse(data));
  await harness.hooks.pollTelemetry();
  data.robotProfile.sensors[0].maxAgeMs = 1;
  harness.hooks.pendingSceneImage().onload();
  assert.equal(harness.hooks.sceneFrame.hidden, true);
  assert.equal(harness.element("scene-live-state").textContent, "UNAVAILABLE");
  assert.deepEqual(harness.revokedURLs, harness.createdURLs);
});

test("legacy simulation frames carry a visible non-perception warning and no depth or cloud", async () => {
  const harness = createHarness();
  const data = { ...snapshot(new Date().toISOString()), mode: "SIMULATION", robotState: { simulation: true } };
  harness.setFetch(async url => url.startsWith("/v1/telemetry")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: data }) }
    : { ok: true, blob: async () => ({ label: "legacy" }) });
  await harness.hooks.pollTelemetry();
  harness.hooks.pendingSceneImage().onload();
  assert.equal(harness.element("scene-frame-message").textContent, "仿真调试画面（非机器人感知）");
  assert.equal(harness.element("perception-label").textContent, "仿真调试画面（非机器人感知）");
  assert.equal(harness.element("view-depth").disabled, true);
  assert.equal(harness.element("view-cloud").disabled, true);
});


function evidenceRecord(overrides = {}) {
  return { schemaVersion: "evidence.capture.v1", id: "a".repeat(64), recordIndex: 4, taskId: "task-1", taskRevision: 1, stepId: "task01-pick", captureId: "head/old-capture", historical: true, expired: false, observedAtUnixMs: Date.parse("2026-08-01T01:02:03Z"), rgbBytes: 300, depthBytes: 150, sourceType: "rgbd_camera", sourceId: "head", robotId: "robot-a", transformRevision: "cal-1", ...overrides };
}

test("historical evidence displays the original capture despite age and never requests current imagery", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [{ sequence: 1, type: "TOOL_ACTIVITY", payload: { activityStatus: "CONFIRMED", stepId: "task01-pick", taskRevision: 1, evidenceIds: ["head/old-capture"] } }] });
  harness.setFetch(async url => url.endsWith("/observations?limit=100")
    ? { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord()] }) }
    : { ok: true, blob: async () => ({ label: url.endsWith("/rgb") ? "history-rgb" : "history-depth" }) });
  await harness.hooks.loadLocalEvidence("task-1");
  harness.element("local-evidence-rgb").onload();
  harness.element("local-evidence-depth").onload();
  assert.equal(harness.element("local-evidence-rgb").hidden, false);
  assert.equal(harness.element("local-evidence-depth").src, "blob:history-depth");
  assert.match(harness.element("local-evidence-description").textContent, /历史.*2026/);
  assert.equal(harness.fetches.some(url => url.startsWith("/v1/scene/")), false);
  harness.hooks.renderLocalMissionActivities([{ stepId: "task01-pick", displayName: "拿取物品", status: "CONFIRMED" }]);
  assert.match(descendantText(harness.element("local-tool-activities")), /回看当时观测/);
});

test("expired historical content does not show old or current images but retains capture identity", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord({ expired: true })] }) }));
  await harness.hooks.loadLocalEvidence("task-1");
  assert.equal(harness.fetches.length, 1);
  assert.equal(harness.element("local-evidence-rgb").hidden, true);
  assert.match(harness.element("local-evidence-description").textContent, /已清理/);
  assert.match(harness.element("local-evidence-details").textContent, /head\/old-capture/);
});

test("changing tasks fences a delayed historical image and revokes its companion", async () => {
  const harness = createHarness();
  const delayed = deferred();
  const started = deferred();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.setFetch(async url => {
    if (url.endsWith("/observations?limit=100")) return { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord()] }) };
    if (url.endsWith("/depth")) return { ok: true, blob: async () => ({ label: "old-depth" }) };
    return { ok: true, blob: async () => { started.resolve(); return delayed.promise; } };
  });
  const loading = harness.hooks.loadLocalEvidence("task-1");
  await started.promise;
  for (let turn = 0; turn < 10 && !harness.element("local-evidence-depth").src; turn += 1) await Promise.resolve();
  assert.equal(harness.element("local-evidence-depth").src, "blob:old-depth");
  harness.hooks.selectLocalTask({ id: "task-2", state: "READY" });
  delayed.resolve({ label: "old-rgb" });
  await loading;
  assert.equal(harness.element("local-evidence-rgb").src, "");
  assert.equal(harness.createdURLs.includes("blob:old-rgb"), false);
  assert.ok(harness.revokedURLs.includes("blob:old-depth"));
});


test("cloud default camera prioritizes observed entities over distant range returns", () => {
  const harness = createHarness();
  const data = rgbdSnapshot();
  data.reconstruction.points.push([20, 20, 20]);
  harness.hooks.renderTelemetry(data);
  harness.hooks.setSceneViewMode("cloud");
  const camera = harness.hooks.currentSceneCamera();
  assert.equal(camera.distance, .45);
  assert.deepEqual(Array.from(camera.target), [.05, .05, .45]);
});

test("historical 410 expiration clears both images without falling back to live", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.setFetch(async url => url.endsWith("/observations?limit=100")
    ? { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord()] }) }
    : { ok: false, status: 410 });
  await harness.hooks.loadLocalEvidence("task-1");
  assert.equal(harness.element("local-evidence-rgb").src, "");
  assert.equal(harness.element("local-evidence-depth").hidden, true);
  assert.match(harness.element("local-evidence-description").textContent, /已清理/);
  assert.equal(harness.fetches.some(url => url.startsWith("/v1/scene/")), false);
});

test("historical records from another task are rejected and old revisions do not attach to current action cards", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 2 });
  harness.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord({ expired: true }), evidenceRecord({ id: "b".repeat(64), taskId: "other-task", recordIndex: 5 })] }) }));
  await harness.hooks.loadLocalEvidence("task-1");
  assert.equal(harness.element("local-evidence-select").children.length, 1);
  harness.hooks.renderLocalMissionActivities([{ stepId: "task01-pick", displayName: "拿取物品" }]);
  assert.doesNotMatch(descendantText(harness.element("local-tool-activities")), /回看当时观测/);
});


test("historical plan remains loading until the experience endpoint confirms a missing record", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", plan: { source: "deterministic" } });
  assert.match(descendantText(harness.element("local-step-ribbon")), /正在读取/);
  assert.doesNotMatch(descendantText(harness.element("local-step-ribbon")), /未保存/);
  harness.setFetch(async () => ({ ok: false, status: 404 }));
  await harness.hooks.loadLocalTaskExperience("task-1");
  assert.match(descendantText(harness.element("local-step-ribbon")), /未保存详细计划/);
});

test("completed tasks hide stale recovery guidance even when the experience contains old failure advice", () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, recovery: { knownState: "出错", robotSafetyState: "机器人已停止推进出错步骤", userActions: ["重新批准"] } }));
  assert.equal(harness.element("local-recovery").hidden, true);
  assert.equal(descendantText(harness.element("local-recovery-content")), "");
});

test("a satisfied local goal links only its own confirmed placement capture without fabricating harness evidence", async () => {
  const harness = createHarness();
  const confirmation = { sequence: 18, type: "TOOL_ACTIVITY", stepId: "task02-verify_place", payload: { stepId: "task02-verify_place", toolName: "verify_placement", activityStatus: "CONFIRMED", taskRevision: 1, evidenceIds: ["head/placement-2"], commandId: "cmd-place-2" } };
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, intent: { sequence: [{}, {}] }, events: [confirmation] });
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, steps: [
    { stepId: "intent-000/a1", status: "SATISFIED", evidenceText: "等待环境证据" },
    { stepId: "intent-001/b2", status: "SATISFIED", evidenceText: "等待环境证据" },
  ], professional: { stepEvidence: [] } }));
  harness.setFetch(async url => url.endsWith("/observations?limit=100")
    ? { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord({ stepId: "task02-verify_place", captureId: "head/placement-2" })] }) }
    : { ok: true, blob: async () => ({ label: "placement-history" }) });
  await harness.hooks.loadLocalEvidence("task-1");
  const steps = harness.element("local-step-ribbon").children;
  assert.doesNotMatch(descendantText(steps[0]), /动作观测可回看/);
  assert.match(descendantText(steps[1]), /动作观测可回看.*回看当时观测/);
  assert.doesNotMatch(harness.element("local-professional-trace").textContent, /placement-2/);
  assert.match(harness.element("local-evidence-details").textContent, /cmd-place-2/);
});

test("Local macro progress follows the projected subtask and survives metadata refresh", () => {
  const harness = createHarness();
  const task = { id: "task-1", state: "RUNNING", currentRevision: 1, intent: { sequence: [{}, {}] } };
  harness.hooks.selectLocalTask(task);
  const step = (index, status, statusText, evidenceText) => ({
    stepId: `intent-00${index}/abc123`, status, statusText, evidenceText,
    explanation: index === 0 ? "把红色杯子放进右侧收纳盒" : "把蓝色瓶子拿过来",
  });
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, steps: [
    step(0, "RUNNING", "正在执行", "正在执行，完成后会观测确认结果"),
    step(1, "PENDING", "等待执行", "等待环境证据"),
  ] }));
  const initial = harness.element("local-step-ribbon").children;
  assert.equal(initial[0].className, "mission-step running");
  assert.match(descendantText(initial[0]), /正在执行/);
  assert.equal(initial[1].className, "mission-step pending");
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, steps: [
    step(0, "SATISFIED", "已完成", "已通过放置观测确认"),
    step(1, "RUNNING", "正在执行", "正在执行，完成后会观测确认结果"),
  ] }));
  harness.hooks.renderTask({ ...task, state: "PAUSED" });
  const resumed = harness.element("local-step-ribbon").children;
  assert.equal(resumed[0].className, "mission-step satisfied");
  assert.match(descendantText(resumed[0]), /已完成.*已通过放置观测确认/);
  assert.equal(resumed[1].className, "mission-step running");
  harness.hooks.renderLocalTaskExperience(taskExperience({ revision: 1, steps: [
    step(0, "SATISFIED", "已完成", "已通过放置观测确认"),
    step(1, "SATISFIED", "已完成", "已通过放置观测确认"),
  ] }));
  assert.equal(harness.element("local-step-ribbon").children.every(item => item.className === "mission-step satisfied"), true);
});

test("shared cached capture ids do not cross-link commands between steps or revisions", async () => {
  const harness = createHarness();
  const event = (sequence, revision, step, command) => ({ sequence, type: "TOOL_ACTIVITY", stepId: step, payload: { activityStatus: "CONFIRMED", taskRevision: revision, stepId: step, evidenceIds: ["head/old-capture"], commandId: command } });
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 2, events: [event(1, 1, "task01-pick", "wrong-revision"), event(2, 2, "task01-observe", "wrong-step"), event(3, 2, "task01-pick", "correct-command")] });
  harness.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord({ taskRevision: 2, stepId: "revision-2/task01-pick", expired: true })] }) }));
  await harness.hooks.loadLocalEvidence("task-1");
  const details = harness.element("local-evidence-details").textContent;
  assert.match(details, /correct-command/);
  assert.doesNotMatch(details, /wrong-step|wrong-revision/);
});


test("missing RGBD object or destination explains what the operator must correct before explicit resume", () => {
  for (const [message, expected] of [["ground subtask 1: grounding ambiguous: objects=0 destinations=1", /物品.*视野内后继续/], ["grounding ambiguous: objects=1 destinations=0", /放置区域.*视野内后继续/], ["grounding ambiguous: objects=0 destinations=0", /物品和放置区域/]]) {
    const harness = createHarness();
    harness.hooks.selectLocalTask({ id: "task-1", state: "RECOVERABLE_FAILURE", events: [{ type: "STATE_CHANGED", sequence: 3, message }] });
    harness.hooks.renderLocalRecovery({ taskId: "task-1", state: "RECOVERABLE_FAILURE", canResume: true, reason: "已保存执行进度" });
    const help = descendantText(harness.element("local-recovery-content"));
    assert.match(help, expected);
    assert.doesNotMatch(help, /grounding ambiguous|objects=|destinations=/);
    assert.equal(harness.fetches.length, 0, "guidance must not automatically resume");
  }
});

test("Local CONFIRMED describes execution success without claiming harness validation", async () => {
  const harness = createHarness();
  harness.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [{ sequence: 1, type: "TOOL_ACTIVITY", payload: { activityStatus: "CONFIRMED", stepId: "task01-pick", taskRevision: 1, evidenceIds: ["head/old-capture"] } }] });
  harness.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord({ expired: true })] }) }));
  await harness.hooks.loadLocalEvidence("task-1");
  harness.hooks.renderLocalMissionActivities([{ stepId: "task01-pick", displayName: "拿取物品", status: "CONFIRMED", statusText: "环境已经确认完成", evidenceText: "环境已经确认动作结果" }, { stepId: "plan", displayName: "规划动作", status: "CONFIRMED", statusText: "环境已经确认完成" }]);
  const cards = harness.element("local-tool-activities").children;
  assert.match(descendantText(cards[0]), /执行完成.*已保存执行后观测/);
  assert.match(descendantText(cards[1]), /执行完成/);
  assert.doesNotMatch(descendantText(harness.element("local-tool-activities")), /环境已经确认/);
});


test("point colors are optional but an explicitly malformed RGB array rejects the entire cloud", () => {
  const { hooks } = createHarness();
  const frame = rgbdSnapshot();
  assert.equal(hooks.pointCloudViewData(frame).available, true);
  frame.reconstruction.pointColors = [[255, 0, 12], [0, 80, 255]];
  assert.equal(hooks.pointCloudViewData(frame).available, true);
  for (const colors of [null, [[1, 2, 3]], [[1, 2], [1, 2, 3]], [[1, 2, 3, 4], [1, 2, 3]], [[1, 2, 256], [1, 2, 3]], [[1, -1, 0], [1, 2, 3]], [[1, .5, 0], [1, 2, 3]], [[1, NaN, 0], [1, 2, 3]], [[true, 0, 0], [1, 2, 3]], [["1", 0, 0], [1, 2, 3]]]) {
    const result = hooks.pointCloudViewData({ ...frame, reconstruction: { ...frame.reconstruction, pointColors: colors } });
    assert.equal(result.available, false, JSON.stringify(colors));
    assert.match(result.reason, /颜色/);
  }
});

test("cloud projection and depth sorting keep each visible point paired with its original RGB pixel", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  frame.reconstruction.points = [[0, .5, 0], [0, 2, 0], [0, -.5, 0]];
  frame.reconstruction.pointColors = [[255, 7, 0], [0, 255, 0], [0, 40, 255]];
  frame.reconstruction.entities = [];
  const saved = JSON.stringify(frame.reconstruction);
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  h.hooks.setSceneCamera({ target: [0, 0, 0], yaw: 0, pitch: 0, distance: 1 });
  h.element("scene-canvas").drawCalls.length = 0;
  h.hooks.drawObservedCloud(frame);
  const points = h.element("scene-canvas").drawCalls.filter(call => call[0] === "point");
  assert.deepEqual(points.map(call => call.at(-1)), ["rgb(0, 40, 255)", "rgb(255, 7, 0)"]);
  assert.equal(JSON.stringify(frame.reconstruction), saved, "rendering must not reorder input geometry or colors");
  assert.match(h.element("scene-frame-message").textContent, /真实 RGB/);
});

test("legacy uncolored clouds explicitly use one display color and malformed colors clear the previous view", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  const colors = h.element("scene-canvas").drawCalls.filter(call => call[0] === "point").map(call => call.at(-1));
  assert.equal(new Set(colors).size, 1);
  assert.match(h.element("scene-frame-message").textContent, /未含 RGB.*单色/);
  frame.reconstruction.pointColors = [[255, 0, 0]];
  h.element("scene-canvas").drawCalls.length = 0;
  h.hooks.renderTelemetry(frame);
  assert.equal(h.element("scene-live-state").textContent, "UNAVAILABLE");
  assert.equal(h.element("scene-canvas").drawCalls.some(call => call[0] === "point"), false);
});

test("point glyphs remain legible when a large canvas is displayed in a narrow operator panel", () => {
  const h = createHarness();
  h.element("scene-canvas").clientWidth = 400;
  const frame = rgbdSnapshot();
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  const radii = h.element("scene-canvas").drawCalls.filter(call => call[0] === "point").map(call => call[3] / 3);
  assert.ok(radii.every(radius => radius >= 1.2 && radius <= 2.4));
});

test("cloud fitting uses projected observed workspace extents and keeps distant background from shrinking objects", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  frame.reconstruction.entities = [
    { entityId: "red-cup", category: "cup", pose: [-.3, .3, .8, 1, 0, 0, 0] },
    { entityId: "blue-bottle", category: "bottle", pose: [.3, .3, .85, 1, 0, 0, 0] },
  ];
  const workPoints = [[-.38, .24, .72], [-.25, .37, .9], [.35, .25, .73], [.28, .35, .95]];
  frame.reconstruction.points = [...workPoints, [15, 15, 15]];
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  const projections = workPoints.map(point => h.hooks.projectScenePoint(point, 1200, 560));
  assert.ok(projections.every(point => point && point[0] >= 72 && point[0] <= 1128 && point[1] >= 64 && point[1] <= 496));
  const span = Math.max(...projections.map(point => point[0])) - Math.min(...projections.map(point => point[0]));
  assert.ok(span > 540, `observed work area should use the viewport, got ${span}px`);
  assert.ok(h.hooks.currentSceneCamera().distance < .9);
});

test("optional cloud labels prioritize observed task objects over three preceding containers", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  frame.reconstruction.entities = [
    { entityId: "right-bin", category: "storage_bin", pose: [.35, .3, .7] },
    { entityId: "left-bin", category: "storage_bin", pose: [-.35, .3, .7] },
    { entityId: "front-tray", category: "delivery_tray", pose: [0, .1, .7] },
    { entityId: "red-cup", category: "cup", pose: [-.15, .5, .8] },
    { entityId: "blue-bottle", category: "bottle", pose: [.15, .5, .8] },
  ];
  h.element("cloud-labels").checked = true;
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  const labels = h.element("scene-canvas").drawCalls.filter(call => call[0] === "text").map(call => call[1]);
  assert.ok(labels.includes("红色杯子"));
  assert.ok(labels.includes("蓝色瓶子"));
});


test("SDK empty pointColors is compatible with absent RGB and retains explicit monochrome labeling", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  frame.reconstruction.pointColors = [];
  const data = h.hooks.pointCloudViewData(frame);
  assert.equal(data.available, true);
  assert.equal(data.pointColors, null);
  h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
  assert.match(h.element("scene-frame-message").textContent, /未含 RGB.*单色/);
  const colors = h.element("scene-canvas").drawCalls.filter(call => call[0] === "point").map(call => call.at(-1));
  assert.equal(colors.length, frame.reconstruction.points.length);
  assert.equal(new Set(colors).size, 1);
});

test("diagnostic observation summary counts colored points without dumping coordinate or RGB arrays", () => {
  const h = createHarness();
  const frame = rgbdSnapshot();
  frame.reconstruction.pointColors = [[255, 0, 12], [0, 80, 255]];
  h.hooks.renderTelemetry(frame);
  const rendered = JSON.parse(h.element("sensor-json").textContent);
  assert.equal(rendered.reconstruction.pointCount, 2);
  assert.equal(rendered.reconstruction.pointColorCount, 2);
  assert.equal(Object.hasOwn(rendered.reconstruction, "points"), false);
  assert.equal(Object.hasOwn(rendered.reconstruction, "pointColors"), false);
});


test("observed cloud labels retain 12 CSS pixel type and padded collision boxes on a narrow panel", () => {
  for (const displayWidth of [478, 1200]) {
    const h = createHarness();
    const canvas = h.element("scene-canvas");
    canvas.clientWidth = displayWidth;
    const scale = canvas.width / displayWidth;
    const frame = rgbdSnapshot();
    h.element("cloud-labels").checked = true;
    h.hooks.renderTelemetry(frame); h.hooks.setSceneViewMode("cloud");
    const text = canvas.drawCalls.find(call => call[0] === "text" && call[1] === "红色杯子");
    assert.ok(text, "observed object label should be visible");
    const cssFontSize = parseFloat(text.at(-1)) / scale;
    assert.ok(cssFontSize >= 11.9 && cssFontSize <= 12.1, `got ${cssFontSize}px at ${displayWidth}px panel width`);
    const box = canvas.drawCalls.find(call => call[0] === "rect" && call.at(-1) === "rgba(10, 23, 39, .85)");
    assert.ok(box, "readable text needs its own contrast background");
    assert.ok(box[3] / scale >= 4 * 12 + 8 - .01, "horizontal background padding must scale with CSS pixels");
    assert.ok(box[4] / scale >= 18 - .01, "label background must retain room above and below 12px text");
  }
});


test("a tool review chooses its confirmed capture instead of a newer unlinked record with the same step", async () => {
  const h = createHarness();
  const event = { sequence: 3, type: "TOOL_ACTIVITY", stepId: "task01-verify_place", payload: { toolName: "verify_placement", stepId: "task01-verify_place", taskRevision: 1, activityStatus: "CONFIRMED", evidenceIds: ["linked-capture"], arguments: { objectId: "red-cup", destinationId: "right-bin" } } };
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  const linked = evidenceRecord({ stepId: "task01-verify_place", captureId: "linked-capture", expired: true });
  const unlinked = evidenceRecord({ id: "b".repeat(64), stepId: linked.stepId, recordIndex: 5, captureId: "later-unlinked", expired: true });
  h.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [unlinked, linked] }) }));
  await h.hooks.loadLocalEvidence("task-1");
  h.hooks.renderLocalMissionActivities([{ stepId: linked.stepId, status: "CONFIRMED", displayName: "确认已经放好", safeArguments: { objectId: "red-cup", destinationId: "right-bin" } }]);
  const card = h.element("local-tool-activities").children[0];
  assert.match(descendantText(card), /红色杯子.*右侧收纳盒/);
  card.children.find(child => child.textContent === "回看当时观测").emit("click");
  assert.equal(h.element("local-evidence-select").value, linked.id);
  assert.match(h.element("local-evidence-description").textContent, /红色杯子.*右侧收纳盒/);
});

test("late experience labels update the selected historical description without selecting or refetching a different image", async () => {
  const h = createHarness();
  const record = evidenceRecord({ stepId: "task01-verify_place", rgbBytes: 0, depthBytes: 0 });
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [{ sequence: 1, type: "TOOL_ACTIVITY", stepId: record.stepId, payload: { toolName: "verify_placement", stepId: record.stepId, activityStatus: "CONFIRMED", evidenceIds: [record.captureId], arguments: { objectId: "red-cup", destinationId: "right-bin" } } }] });
  h.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [record] }) }));
  await h.hooks.loadLocalEvidence("task-1");
  const requestCount = h.fetches.length;
  h.hooks.renderLocalMissionActivities([{ stepId: record.stepId, status: "CONFIRMED", displayName: "检查放置是否稳定", safeArguments: { objectId: "red-cup", destinationId: "right-bin" } }]);
  assert.match(h.element("local-evidence-description").textContent, /检查放置是否稳定.*红色杯子.*右侧收纳盒/);
  assert.equal(h.element("local-evidence-select").value, record.id);
  assert.equal(h.fetches.length, requestCount);
});

function verificationEvidenceFixture(overrides = {}) {
  const record = evidenceRecord({ stepId: "task01-verify_place", rgbBytes: 0, depthBytes: 0 });
  const verification = { kind: "verify_placement", object_id: "red-cup", destination_id: "right-bin", passed: true, sample_count: 3, stable_duration_s: .102, max_displacement_m: .003, source_id: record.sourceId, observation_id: record.captureId, first_observed_at_unix_ms: record.observedAtUnixMs - 102, last_observed_at_unix_ms: record.observedAtUnixMs, ...overrides };
  const event = { sequence: 1, type: "TOOL_ACTIVITY", payload: { toolName: "verify_placement", stepId: record.stepId, activityStatus: "CONFIRMED", taskRevision: 1, evidenceIds: [record.captureId], receiptObservationId: record.captureId, evidenceSource: "command_observation", arguments: { objectId: "red-cup", destinationId: "right-bin" } } };
  return { record, verification, event };
}

function navigationEvidenceFixture(source = "pose_confirmation") {
  const record = evidenceRecord({ stepId: "task02-navigate", rgbBytes: 0, depthBytes: 0 });
  const navigation = { kind: "navigation.navigate", passed: true, source_id: record.sourceId,
    observed_at_unix_ms: record.observedAtUnixMs, pose_source: "sim_proprioceptive_odom",
    position_error_m: .008, yaw_error_rad: .005,
    map_receipt: { completion_source: source, pose_source: "rtabmap_tf",
      checked_at_unix_ms: record.observedAtUnixMs, pose_observed_at_unix_ms: record.observedAtUnixMs - 30,
      completion_pose_observed_at_unix_ms: record.observedAtUnixMs - 50 } };
  const event = { sequence: 1, type: "TOOL_ACTIVITY", stepId: record.stepId, payload: {
    toolName: "navigation.navigate", stepId: record.stepId, taskRevision: 1, activityStatus: "CONFIRMED",
    evidenceIds: [record.captureId], receiptObservationId: record.captureId, evidenceSource: "command_observation" } };
  const activity = { stepId: record.stepId, displayName: "navigation.navigate", status: "CONFIRMED",
    purpose: "让机器人安全到达任务位置" };
  return { record, navigation, event, activity };
}

test("historical pose confirmation labels the exact action without claiming another movement", async () => {
  const h = createHarness();
  const { record, navigation, event, activity } = navigationEvidenceFixture();
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  h.hooks.renderLocalMissionActivities([activity]);
  h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
    ? { taskId: "task-1", historical: true, records: [record] }
    : { ...record, snapshot: { robotState: { navigation } } } }));
  await h.hooks.loadLocalEvidence("task-1");
  const card = descendantText(h.element("local-tool-activities"));
  assert.match(card, /确认当前操作位置.*当前位置已确认，未请求底盘移动/);
  assert.doesNotMatch(card, /移动到操作位置|让机器人安全到达/);
  assert.match(h.element("local-evidence-description").textContent, /确认当前操作位置/);
  assert.match(h.element("local-evidence-verification").textContent, /当前位置已确认，未请求底盘移动.*8.0 毫米/);
  assert.match(h.element("local-evidence-details").textContent, /pose_confirmation/);
  await h.hooks.loadLocalEvidence("task-1");
  assert.equal(h.fetches.filter(url => url.endsWith(record.id)).length, 1, "immutable navigation JSON is cached without refreshing images");
});

test("historical Nav2 action keeps the navigation label with an explicit completed receipt", async () => {
  const h = createHarness();
  const { record, navigation, event, activity } = navigationEvidenceFixture("nav2_action");
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  h.hooks.renderLocalMissionActivities([activity]);
  h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
    ? { taskId: "task-1", historical: true, records: [record] }
    : { ...record, snapshot: { robotState: { navigation } } } }));
  await h.hooks.loadLocalEvidence("task-1");
  assert.match(descendantText(h.element("local-tool-activities")), /移动到操作位置.*Nav2 导航已完成/);
  assert.doesNotMatch(h.element("local-evidence-verification").textContent, /未请求底盘移动/);
});

test("missing, mismatched, failed or stale navigation evidence never asserts no movement", async () => {
  for (const change of ["missing-source", "wrong-capture", "wrong-revision", "post-observation", "failed", "stale", "expired"]) {
    const h = createHarness();
    const { record, navigation, event, activity } = navigationEvidenceFixture();
    const detail = { ...record, snapshot: { robotState: { navigation } } };
    if (change === "missing-source") delete navigation.map_receipt.completion_source;
    if (change === "wrong-capture") detail.captureId = "other-capture";
    if (change === "wrong-revision") detail.taskRevision = 2;
    if (change === "post-observation") event.payload.evidenceSource = "post_tool_observation";
    if (change === "failed") { event.payload.activityStatus = "FAILED"; activity.status = "FAILED"; navigation.passed = false; }
    if (change === "stale") navigation.map_receipt.completion_pose_observed_at_unix_ms -= 1001;
    if (change === "expired") record.expired = true;
    h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
    h.hooks.renderLocalMissionActivities([activity]);
    h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
      ? { taskId: "task-1", historical: true, records: [record] } : detail }));
    await h.hooks.loadLocalEvidence("task-1");
    assert.doesNotMatch(descendantText(h.element("local-tool-activities")), /当前位置已确认|未请求底盘移动/, change);
    assert.doesNotMatch(h.element("local-evidence-verification").textContent, /当前位置已确认|未请求底盘移动/, change);
  }
});

test("a delayed navigation receipt updates captions without reloading decoded RGB or depth", async () => {
  const h = createHarness();
  const { record, navigation, event, activity } = navigationEvidenceFixture();
  record.rgbBytes = record.depthBytes = 100;
  const delayed = deferred();
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  h.hooks.renderLocalMissionActivities([activity]);
  h.setFetch(async url => {
    if (url.endsWith("?limit=100")) return { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [record] }) };
    if (url.endsWith(record.id)) return delayed.promise;
    return { ok: true, blob: async () => ({ label: url }) };
  });
  const loading = h.hooks.loadLocalEvidence("task-1");
  await new Promise(resolve => setImmediate(resolve));
  const rgb = h.element("local-evidence-rgb"), depth = h.element("local-evidence-depth");
  rgb.onload(); depth.onload();
  const before = [rgb.src, depth.src, h.createdURLs.length];
  delayed.resolve({ ok: true, json: async () => ({ ...record, snapshot: { robotState: { navigation } } }) });
  await loading;
  assert.deepEqual([rgb.src, depth.src, h.createdURLs.length], before);
  assert.equal(rgb.hidden, false); assert.equal(depth.hidden, false);
  assert.equal(h.element("local-evidence-select").value, record.id);
  assert.match(h.element("local-evidence-verification").textContent, /未请求底盘移动/);
});

test("a navigation receipt arriving after task selection cannot relabel the new task", async () => {
  const h = createHarness();
  const { record, navigation, event, activity } = navigationEvidenceFixture();
  const delayed = deferred();
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  h.hooks.renderLocalMissionActivities([activity]);
  h.setFetch(async url => url.endsWith("?limit=100")
    ? { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [record] }) }
    : delayed.promise);
  const loading = h.hooks.loadLocalEvidence("task-1");
  await new Promise(resolve => setImmediate(resolve));
  h.hooks.selectLocalTask({ id: "task-2", state: "RUNNING", currentRevision: 1 });
  h.hooks.renderLocalMissionActivities([{ ...activity, status: "RUNNING" }]);
  delayed.resolve({ ok: true, json: async () => ({ ...record, snapshot: { robotState: { navigation } } }) });
  await loading;
  assert.doesNotMatch(descendantText(h.element("local-tool-activities")), /当前位置已确认|未请求底盘移动/);
  assert.equal(h.element("local-evidence-verification").textContent, "");
});

test("failed verification links its original failed capture instead of an earlier success", async () => {
  const h = createHarness();
  const { record, verification, event } = verificationEvidenceFixture({ passed: false });
  event.sequence = 2; event.payload.activityStatus = "FAILED";
  const earlier = { ...event, sequence: 1, payload: { ...event.payload, activityStatus: "CONFIRMED", evidenceIds: ["old-success"] } };
  h.hooks.selectLocalTask({ id: "task-1", state: "FAILED", currentRevision: 1, events: [earlier, event] });
  h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
    ? { taskId: "task-1", historical: true, records: [record] }
    : { ...record, snapshot: { robotState: { verification } } } }));
  await h.hooks.loadLocalEvidence("task-1");
  h.hooks.renderLocalMissionActivities([{ stepId: record.stepId, status: "FAILED", displayName: "检查放置是否稳定" }]);
  const button = h.element("local-tool-activities").children[0].children.find(child => child.textContent === "回看当时观测");
  assert.ok(button, "failure needs its own camera review");
  button.emit("click");
  await new Promise(resolve => setImmediate(resolve));
  assert.match(h.element("local-evidence-description").textContent, /验证原始观测/);
  assert.match(h.element("local-evidence-verification").textContent, /结果为未通过/);
});

test("verification review explains the actual object, destination and sampling window from its original capture", async () => {
  const h = createHarness();
  const { record, verification, event } = verificationEvidenceFixture();
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
  h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
    ? { taskId: "task-1", historical: true, records: [record] }
    : { ...record, snapshot: { robotState: { verification } } } }));
  await h.hooks.loadLocalEvidence("task-1");
  assert.match(h.element("local-evidence-description").textContent, /验证原始观测/);
  assert.match(h.element("local-evidence-verification").textContent, /红色杯子.*右侧收纳盒.*结果为通过.*3 次.*102 毫秒.*3.0 毫米.*最后一帧/);
});

test("first-frame failed verification explains that no stable samples were established", async () => {
  const h = createHarness();
  const { record, verification, event } = verificationEvidenceFixture({ passed: false, sample_count: 0, stable_duration_s: 0, max_displacement_m: 0 });
  event.payload.activityStatus = "FAILED";
  h.hooks.selectLocalTask({ id: "task-1", state: "FAILED", currentRevision: 1, events: [event] });
  h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
    ? { taskId: "task-1", historical: true, records: [record] }
    : { ...record, snapshot: { robotState: { verification } } } }));
  await h.hooks.loadLocalEvidence("task-1");
  assert.match(h.element("local-evidence-verification").textContent, /结果为未通过.*未形成稳定样本.*实际失败判定帧/);
  assert.doesNotMatch(h.element("local-evidence-verification").textContent, /连续 0 次/);
});

test("legacy or receipt-mismatched evidence never claims to be original verification input", async () => {
  for (const adjustment of [{ evidenceSource: undefined }, { receiptObservationId: "different-input" }, { evidenceSource: "post_tool_observation" }]) {
    const h = createHarness();
    const { record, verification, event } = verificationEvidenceFixture();
    Object.assign(event.payload, adjustment);
    h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
    h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
      ? { taskId: "task-1", historical: true, records: [record] }
      : { ...record, snapshot: { robotState: { verification } } } }));
    await h.hooks.loadLocalEvidence("task-1");
    assert.match(h.element("local-evidence-description").textContent, /执行后现场画面/);
    assert.doesNotMatch(h.element("local-evidence-description").textContent, /验证原始观测/);
    assert.doesNotMatch(h.element("local-evidence-verification").textContent, /结果为通过/);
  }
});

test("verification metadata for a different object, capture, source or time cannot validate the selected picture", async () => {
  for (const change of [{ object_id: "blue-bottle" }, { destination_id: "front-tray" }, { observation_id: "other" }, { source_id: "other-camera" }, { last_observed_at_unix_ms: 1 }, { sample_count: NaN }]) {
    const h = createHarness();
    const { record, verification, event } = verificationEvidenceFixture(change);
    h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event] });
    h.setFetch(async url => ({ ok: true, json: async () => url.endsWith("?limit=100")
      ? { taskId: "task-1", historical: true, records: [record] }
      : { ...record, snapshot: { robotState: { verification } } } }));
    await h.hooks.loadLocalEvidence("task-1");
    assert.match(h.element("local-evidence-verification").textContent, /未提供可核对/);
    assert.doesNotMatch(h.element("local-evidence-verification").textContent, /结果为通过/);
  }
});

test("selecting another capture in the same task fences delayed images and list refresh preserves the selection", async () => {
  const h = createHarness();
  const first = evidenceRecord({ recordIndex: 5 });
  const second = evidenceRecord({ id: "b".repeat(64), recordIndex: 4, stepId: "task02-verify_place", captureId: "second-capture" });
  const delay = deferred(); const started = deferred();
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1 });
  h.setFetch(async url => {
    if (url.endsWith("?limit=100")) return { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [first, second] }) };
    if (url.endsWith(`${first.id}/rgb`)) return { ok: true, blob: async () => { started.resolve(); return delay.promise; } };
    return { ok: true, json: async () => ({}), blob: async () => ({ label: url.includes(second.id) ? "second" : "first-depth" }) };
  });
  const pending = h.hooks.loadLocalEvidence("task-1");
  await started.promise;
  await h.hooks.selectLocalEvidence(second.id);
  delay.resolve({ label: "delayed-first" }); await pending;
  assert.equal(h.element("local-evidence-select").value, second.id);
  assert.equal(h.element("local-evidence-rgb").src, "blob:second");
  assert.equal(h.createdURLs.includes("blob:delayed-first"), false);
  const before = h.fetches.length;
  await h.hooks.loadLocalEvidence("task-1");
  assert.equal(h.fetches.length, before + 1, "list refresh must not refetch or replace the selected images");
  assert.equal(h.element("local-evidence-select").value, second.id);
  assert.equal(h.element("local-evidence-rgb").src, "blob:second");
  assert.equal(h.fetches.some(url => url.startsWith("/v1/scene/")), false);
});

test("a repeated step without its latest confirmed capture does not borrow an earlier successful picture", async () => {
  const h = createHarness();
  const record = evidenceRecord({ expired: true });
  const event = (sequence, captureId) => ({ sequence, type: "TOOL_ACTIVITY", payload: { stepId: record.stepId, taskRevision: 1, activityStatus: "CONFIRMED", evidenceIds: [captureId] } });
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED", currentRevision: 1, events: [event(1, record.captureId), event(2, "not-yet-archived")] });
  h.setFetch(async () => ({ ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [record] }) }));
  await h.hooks.loadLocalEvidence("task-1");
  h.hooks.renderLocalMissionActivities([{ stepId: record.stepId, displayName: "拿取物品", status: "CONFIRMED" }]);
  assert.doesNotMatch(descendantText(h.element("local-tool-activities")), /回看当时观测/);
});

function cameraResponse(snapshot) {
  return { ok: true, json: async () => ({ snapshot, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }) };
}

const cameraTestPNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9Zl1sAAAAASUVORK5CYII=";

test("hidden pages do not fetch live cameras and resuming fences the old decode", async () => {
  const h = createHarness();
  let scene = rgbdSnapshot();
  h.setFetch(async url => url.startsWith("/v1/telemetry")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) }
    : cameraResponse(scene));
  await h.hooks.pollTelemetry();
  const late = h.hooks.pendingSceneImage()?.onload;
  h.hooks.setPage("tasks"); h.hooks.handlePageVisibility();
  const count = h.fetches.length;
  await h.hooks.pollTelemetry();
  assert.equal(h.fetches.length, count);
  late?.();
  assert.notEqual(h.element("scene-live-state").textContent, "LIVE");
  h.hooks.setDocumentHidden(true); h.hooks.setPage("workspace");
  await h.hooks.pollTelemetry();
  assert.equal(h.fetches.length, count);
  h.hooks.setDocumentHidden(false); h.hooks.handlePageVisibility();
  scene = { ...scene, observedAt: new Date(Date.now()).toISOString() };
  await h.hooks.pollTelemetry();
  assert.ok(h.fetches.length > count);
});

test("unchanged task history and tool evidence keep the same buttons and focus", async () => {
  const h = createHarness();
  const tasks = [{ id: "saved", request: "收好杯子", state: "SUCCEEDED", updatedAt: "2026-09-08T00:00:00Z" }];
  h.setFetch(async () => ({ ok: true, json: async () => tasks }));
  await h.hooks.loadLocalTasks();
  const row = h.element("local-task-list").children[0];
  row.children[0].focus();
  await h.hooks.loadLocalTasks();
  assert.equal(h.element("local-task-list").children[0], row);
  assert.equal(row.children[0].focused, true);
  const activities = [{ stepId: "pick", status: "RUNNING", displayName: "拿取杯子", safeArguments: { objectId: "red-cup" } }];
  h.hooks.renderLocalMissionActivities(activities);
  const card = h.element("local-tool-activities").children[0];
  h.hooks.renderLocalMissionActivities(JSON.parse(JSON.stringify(activities)));
  assert.equal(h.element("local-tool-activities").children[0], card);
});
function dualCameraFrames() {
  const head = rgbdSnapshot();
  head.robotProfile.sensors.push({ sourceId: "robot-a/base-rgbd", sourceType: "rgbd_camera", frameId: "base-optical", transformRevision: "base-cal-1", maxAgeMs: 2000 });
  const base = structuredClone(head);
  Object.assign(base.reconstruction, { sourceId: "robot-a/base-rgbd", sourceFrameId: "base-optical", transformRevision: "base-cal-1", observationId: "base-obs-2", points: [[.1, .5, .1]], entities: [] });
  base.robotState.perception.source_id = base.reconstruction.sourceId;
  return { head, base };
}

test("camera selection discovers RGBD sources and displays only the selected atomic capture", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames();
  h.hooks.renderTelemetry(head);
  assert.equal(h.element("scene-camera").children.length, 2);
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) }
    : { ok: true, json: async () => ({ snapshot: base, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }) });
  await h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  assert.equal(h.hooks.displayedTelemetry().reconstruction.sourceId, "head", "metadata stays with the visible capture until decoded");
  assert.ok(h.hooks.pendingSceneImage().src.startsWith("blob:"));
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.hooks.displayedTelemetry().reconstruction.sourceId, base.reconstruction.sourceId);
  assert.match(h.element("scene-frame-message").textContent, /底盘前方/);
  assert.equal(h.fetches.some(url => /\/scene\/(frame|depth)\?/.test(url)), false);
  assert.ok(h.fetches.some(url => url.includes("sourceId=robot-a%2Fbase-rgbd")));
  await h.hooks.setSceneViewMode("cloud");
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observationId, "base-obs-2");
  assert.equal(h.hooks.displayedTelemetry().reconstruction.points.length, 1);
});

test("camera snapshots with stale times, a mismatched source or malformed image fail closed", async () => {
  for (const invalid of ["stale", "source", "media"]) {
    const h = createHarness(); const { head, base } = dualCameraFrames(); h.hooks.renderTelemetry(head);
    if (invalid === "stale") base.reconstruction.observedAtUnixMs = Date.now() - 10000;
    if (invalid === "source") base.reconstruction.sourceId = "head";
    h.setFetch(async url => url.startsWith("/v1/telemetry?")
      ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) }
      : { ok: true, json: async () => ({ snapshot: base, rgbDataUrl: invalid === "media" ? "data:image/svg+xml;base64,PHN2Zy8+" : cameraTestPNG, depthDataUrl: cameraTestPNG }) });
    await h.hooks.selectSceneCamera("robot-a/base-rgbd");
    assert.equal(h.hooks.sceneFrame.src, "");
    assert.equal(h.hooks.displayedTelemetry(), null);
    assert.match(h.element("scene-frame-message").textContent, /当前现场未知/);
    assert.equal(h.createdURLs.length, 0);
  }
});

test("switching back to the primary camera rejects late secondary pixels and metadata", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames(); const delayed = deferred(); const started = deferred();
  h.hooks.renderTelemetry(head);
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    if (url.includes("sourceId=robot-a%2Fbase-rgbd")) { started.resolve(); return delayed.promise; }
    return cameraResponse(head);
  });
  const selectingBase = h.hooks.selectSceneCamera(base.reconstruction.sourceId); await started.promise;
  await h.hooks.selectSceneCamera("head");
  delayed.resolve({ ok: true, json: async () => ({ snapshot: base, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }) });
  await selectingBase;
  assert.equal(h.hooks.displayedTelemetry().reconstruction.sourceId, "head");
  h.hooks.pendingSceneImage().onload();
  assert.ok(h.hooks.sceneFrame.src.startsWith("blob:"));
  assert.equal(h.createdURLs.length, 1, "late base data cannot create an image URL");
});

test("a new secondary capture refreshes quietly until atomic decode and a 503 clears both", async () => {
  const h = createHarness(); let { head, base } = dualCameraFrames(); h.hooks.renderTelemetry(head);
  let failed = false;
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) }
    : failed ? { ok: false, status: 503 } : { ok: true, json: async () => ({ snapshot: base, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }) });
  await h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  h.hooks.pendingSceneImage().onload();
  const firstURL = h.hooks.sceneFrame.src;
  base = structuredClone(base);
  base.reconstruction.observationId = "next-base-capture";
  await h.hooks.pollTelemetry();
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observationId, "base-obs-2");
  assert.equal(h.hooks.sceneFrame.hidden, false, "previous pixels remain paired with their own metadata while decoding");
  assert.equal(h.hooks.sceneFrame.src, firstURL);
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
  assert.doesNotMatch(h.element("scene-frame-message").textContent, /正在读取|暂显上一帧/);
  assert.equal(h.revokedURLs.includes(firstURL), false);
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observationId, "next-base-capture");
  assert.ok(h.revokedURLs.includes(firstURL));
  assert.equal(h.hooks.sceneFrame.hidden, false);
  failed = true; await h.hooks.pollTelemetry();
  assert.equal(h.hooks.displayedTelemetry(), null);
  assert.equal(h.hooks.sceneFrame.src, "");
  assert.equal(h.element("view-cloud").disabled, true);
});

for (const mode of ["live", "depth"]) test(`${mode} background refresh keeps its caption and connection stable while downloading and decoding`, async () => {
  const patches = [];
  const h = createHarness({ TangyingConsoleUI: { update: patch => patches.push(patch) } });
  const scene = rgbdSnapshot();
  const downloading = deferred();
  let delay = false;
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) }
    : delay ? downloading.promise : cameraResponse(scene));
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  if (mode === "depth") { await h.hooks.setSceneViewMode(mode); h.hooks.pendingSceneImage().onload(); }
  const url = h.hooks.sceneFrame.src;
  const caption = h.element("scene-frame-message").textContent;
  patches.length = 0;
  delay = true;
  const refreshing = h.hooks.pollTelemetry();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(h.hooks.sceneFrame.src, url);
  assert.equal(h.element("scene-frame-message").textContent, caption);
  downloading.resolve(cameraResponse(scene)); await refreshing;
  assert.equal(h.hooks.sceneFrame.src, url, "keep decoded pixels until the next image is ready");
  assert.equal(h.element("scene-frame-message").textContent, caption, "normal refresh must not insert a loading box over the image");
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
  assert.equal(patches.some(patch => patch.connection && patch.connection !== "LIVE"), false);
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.element("scene-frame-message").textContent, caption);
  assert.equal(h.hooks.sceneFrame.hidden, false);
});

test("an initial camera failure publishes its connection state even when the HTML already says unavailable", async () => {
  const patches = [];
  const h = createHarness({ TangyingConsoleUI: { update: patch => patches.push(patch) } });
  h.element("scene-live-state").textContent = "UNAVAILABLE";
  await h.hooks.pollTelemetry();
  assert.equal(patches.some(patch => patch.connection === "UNAVAILABLE"), true);
});

test("camera data URLs accept bounded PNG only and never grant remote or active content an image URL", () => {
  const h = createHarness();
  assert.equal(h.hooks.pngDataURLBlob(cameraTestPNG).type, "image/png");
  for (const invalid of ["https://example.com/photo.png", "javascript:alert(1)", "data:image/svg+xml;base64,PHN2Zy8+", "data:image/png;base64,PHN2Zy8+", "data:image/png;base64,%%%", `data:image/png;base64,${"A".repeat(2800000)}`]) {
    assert.throws(() => h.hooks.pngDataURLBlob(invalid));
  }
  assert.equal(h.createdURLs.length, 0);
});

test("navigation and post-navigation observation names remain distinct from initial observation", () => {
  const h = createHarness(); h.hooks.selectLocalTask({ id: "task-1", state: "RUNNING" });
  h.hooks.renderLocalMissionActivities([
    { stepId: "task01-observe", displayName: "观察环境", status: "CONFIRMED" },
    { stepId: "task01-navigate", displayName: "navigation.navigate", status: "STARTED" },
    { stepId: "task01-observe_after_navigation", displayName: "观察环境", status: "WAITING" },
  ]);
  const cards = h.element("local-tool-activities").children;
  assert.match(descendantText(cards[0]), /观察环境/);
  assert.doesNotMatch(descendantText(cards[0]), /到位后/);
  assert.match(descendantText(cards[1]), /前往或确认操作位置/);
  assert.doesNotMatch(descendantText(cards[1]), /执行完成/);
  assert.match(descendantText(cards[2]), /到位后重新观察/);
  assert.doesNotMatch(descendantText(cards[2]), /执行完成/);
});

test("removing a camera from the profile clears its view even when primary telemetry has the same timestamp", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames(); h.hooks.renderTelemetry(head);
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    return cameraResponse(url.includes("sourceId=robot-a%2Fbase-rgbd") ? base : head);
  });
  await h.hooks.pollTelemetry();
  await h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  h.hooks.pendingSceneImage().onload();
  head.robotProfile.sensors = head.robotProfile.sensors.slice(0, 1);
  await h.hooks.pollTelemetry();
  assert.equal(h.element("scene-camera").value, "head");
  assert.equal(h.hooks.displayedTelemetry(), null, "removed source is unavailable until replacement decodes");
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.hooks.displayedTelemetry().reconstruction.sourceId, "head");
  assert.ok(h.hooks.sceneFrame.src.startsWith("blob:"));
});


test("FPS counts distinct decoded captures, never requests, duplicate captures, failed images or orbit redraws", async () => {
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const h = createHarness({ Date: Clock });
  let scene = rgbdSnapshot();
  function advance() {
    now += 1000;
    scene = structuredClone(scene);
    scene.observedAt = new Date(now).toISOString();
    scene.reconstruction.observedAtUnixMs = now;
    scene.reconstruction.observationId = `capture-${now}`;
    scene.robotState.perception.observed_at_unix_ms = now;
  }
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) }
    : cameraResponse(scene));
  await h.hooks.pollTelemetry();
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
  h.hooks.pendingSceneImage().onload();
  advance(); await h.hooks.pollTelemetry();
  assert.equal(h.element("scene-frame-stats").dataset.fps, "", "requests do not count before decode");
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.element("scene-frame-stats").dataset.fps, "1.00");
  await h.hooks.pollTelemetry();
  assert.equal(h.element("scene-frame-stats").dataset.fps, "1.00", "same capture is not an extra frame");
  now += 500; h.hooks.renderSceneFrameStats();
  assert.equal(h.element("scene-frame-stats").dataset.captureAgeMs, "500");
  assert.equal(h.element("scene-frame-stats").dataset.fps, "0.67", "a stalled stream decays instead of retaining a false current rate");
  advance(); await h.hooks.pollTelemetry();
  h.hooks.pendingSceneImage().onerror();
  assert.equal(h.element("scene-frame-stats").dataset.fps, "0.40", "decode failures add no sample");
  h.hooks.renderTelemetry(scene);
  await h.hooks.setSceneViewMode("cloud");
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
  h.hooks.drawObservedCloud(scene); h.hooks.drawObservedCloud(scene);
  assert.equal(h.element("scene-frame-stats").dataset.fps, "", "repainting one cloud is not a camera frame");
  advance(); h.hooks.drawObservedCloud(scene);
  assert.equal(h.element("scene-frame-stats").dataset.fps, "1.00");
});

test("mode changes keep one viewport and the prior decoded pixels until the selected mode is ready", async () => {
  const h = createHarness();
  const scene = rgbdSnapshot();
  h.element("scene-stage").clientWidth = 640;
  h.element("scene-stage").clientHeight = 360;
  h.hooks.sizeSceneCanvas();
  assert.equal(h.element("scene-canvas").width / h.element("scene-canvas").height, 640 / 360);
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) }
    : cameraResponse(scene));
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  const rgbURL = h.hooks.sceneFrame.src;
  await h.hooks.setSceneViewMode("depth");
  const lateDepth = h.hooks.pendingSceneImage().onload;
  assert.equal(h.hooks.sceneFrame.src, rgbURL);
  assert.equal(h.hooks.sceneFrame.hidden, false);
  assert.equal(h.element("scene-live-state").textContent, "LOADING");
  assert.match(h.element("scene-frame-message").textContent, /上一帧.*彩色/);
  assert.equal(h.element("scene-frame-stats").dataset.mode, "live");
  await h.hooks.setSceneViewMode("cloud");
  assert.equal(h.element("scene-frame-stats").dataset.mode, "cloud");
  assert.equal(h.element("scene-canvas").hidden, false);
  lateDepth();
  assert.equal(h.hooks.sceneFrame.hidden, true, "late depth decode cannot replace the selected cloud");
  assert.equal(h.element("scene-canvas").width, 1200);
  assert.equal(h.element("scene-canvas").height, 675);
  assert.equal(h.hooks.sizeSceneCanvas(), false, "same viewport does not resize and clear the canvas");
  const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
  assert.match(css, /\.scene-stage\s*\{[^}]*aspect-ratio:\s*16 \/ 9/);
  assert.match(css, /#scene-frame, #scene-canvas\s*\{[^}]*position:\s*absolute;[^}]*width:\s*100%;[^}]*height:\s*100%/);
});

test("camera changes reset frame rate and keep the previous source labeled until replacement decode", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames();
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    return cameraResponse(url.includes("sourceId=robot-a%2Fbase-rgbd") ? base : head);
  });
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  const previousURL = h.hooks.sceneFrame.src;
  await h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  assert.equal(h.hooks.sceneFrame.src, previousURL);
  assert.match(h.element("scene-frame-message").textContent, /底盘前方.*上一帧.*顶部桌面/);
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, "head");
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, base.reconstruction.sourceId);
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
});

for (const mode of ["cloud", "orbit"]) test(`${mode} camera switching never relabels the previous source as current`, async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames();
  h.hooks.setAudience("developer");
  h.hooks.renderTelemetry(head);
  await h.hooks.setSceneViewMode(mode);
  const waiting = deferred(); const started = deferred();
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    started.resolve(); return waiting.promise;
  });
  const switching = h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  await started.promise;
  assert.equal(h.element("scene-camera").value, base.reconstruction.sourceId);
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, "head");
  assert.equal(h.element("scene-live-state").textContent, "LOADING");
  assert.match(h.element("scene-frame-message").textContent, /底盘前方.*上一帧.*顶部桌面/);
  h.element("scene-canvas").emit("wheel", { deltaY: 5, preventDefault() {} });
  assert.equal(h.element("scene-live-state").textContent, "LOADING", "interaction must not revive the previous source");
  waiting.resolve(cameraResponse(base)); await switching;
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, base.reconstruction.sourceId);
  assert.equal(h.element("scene-live-state").textContent, mode === "orbit" ? "DEBUG" : "LIVE");
});

test("entering cloud during a camera switch retains the labeled decoded previous image until that source arrives", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames();
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) } : cameraResponse(head));
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  const previousURL = h.hooks.sceneFrame.src;
  const waiting = deferred(); const started = deferred();
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    started.resolve(); return waiting.promise;
  });
  const switching = h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  await started.promise;
  await h.hooks.setSceneViewMode("cloud");
  assert.equal(h.hooks.sceneFrame.src, previousURL);
  assert.equal(h.hooks.sceneFrame.hidden, false);
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, "head");
  assert.equal(h.element("scene-live-state").textContent, "LOADING");
  assert.match(h.element("scene-frame-message").textContent, /上一帧.*顶部桌面.*彩色/);
  waiting.resolve(cameraResponse(base)); await switching;
  assert.equal(h.element("scene-live-state").textContent, "LOADING", "the cancelled request cannot complete the new view");
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) } : cameraResponse(base));
  await h.hooks.pollTelemetry();
  assert.equal(h.hooks.sceneFrame.hidden, true);
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, base.reconstruction.sourceId);
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
});

test("primary RGBD pixels, point cloud and displayed metadata come from one atomic capture even when telemetry is older", async () => {
  const h = createHarness(); const telemetry = rgbdSnapshot(); const camera = structuredClone(telemetry);
  camera.reconstruction.observationId = "atomic-primary-newer";
  camera.reconstruction.observedAtUnixMs += 50;
  camera.observedAt = new Date(camera.reconstruction.observedAtUnixMs).toISOString();
  camera.robotState.perception.observed_at_unix_ms = camera.reconstruction.observedAtUnixMs;
  camera.reconstruction.points = [[.2, .3, .4]];
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: telemetry }) };
    if (url.startsWith("/v1/scene/camera?")) return { ok: true, json: async () => ({ snapshot: camera, rgbDataUrl: cameraTestPNG, depthDataUrl: cameraTestPNG }) };
    return { ok: true, headers: { get: name => name === "X-Observed-At" ? telemetry.observedAt : null }, blob: async () => ({ label: "unpaired" }) };
  });
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  assert.equal(h.fetches.some(url => /\/v1\/scene\/(frame|depth)\?/.test(url)), false);
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observationId, camera.reconstruction.observationId);
  assert.ok(Math.abs(Number(h.element("scene-frame-stats").dataset.captureAgeMs) - Math.max(0, Date.now() - camera.reconstruction.observedAtUnixMs)) < 20);
  await h.hooks.setSceneViewMode("cloud");
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observationId, camera.reconstruction.observationId);
  assert.deepEqual(Array.from(h.hooks.displayedTelemetry().reconstruction.points[0]), [.2, .3, .4]);
});


test("device diagnostics fetch metadata only, pause navigation view, and preserve the operator input and selected history", async () => {
  const updates = [];
  const h = createHarness({ TangyingNavigationView: { update: snapshot => updates.push(snapshot) } });
  h.hooks.selectLocalTask({ id: "task-1", state: "SUCCEEDED" });
  h.setFetch(async url => url.endsWith("/observations?limit=100")
    ? { ok: true, json: async () => ({ taskId: "task-1", historical: true, records: [evidenceRecord()] }) }
    : { ok: true, blob: async () => ({ label: url.endsWith("/rgb") ? "history-rgb" : "history-depth" }) });
  await h.hooks.loadLocalEvidence("task-1");
  const historyId = h.element("local-evidence-select").value;
  const historyURL = h.element("local-evidence-rgb").src;
  const input = h.element("request"); input.value = "还在编写的任务"; input.focus();
  const data = rgbdSnapshot();
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: data }) }
    : cameraResponse(data));
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  const start = h.fetches.length;
  h.hooks.setPage("devices"); h.hooks.handlePageVisibility();
  await h.hooks.pollTelemetry(); await h.hooks.pollMetrics(); await h.hooks.pollLocalWorld();
  assert.deepEqual(h.fetches.slice(start), ["/v1/telemetry?adapter=mujoco&limit=20"]);
  assert.equal(updates.at(-1), null, "map background timer has no enabled robot source away from workspace");
  assert.equal(h.element("local-evidence-rgb").src, historyURL);
  assert.equal(h.element("local-evidence-select").value, historyId);
  assert.equal(input.value, "还在编写的任务"); assert.equal(input.focused, true);
  h.hooks.setDocumentHidden(true); await h.hooks.pollTelemetry();
  assert.equal(h.fetches.length, start + 1);
});


test("browser image load waits for decode and an abandoned decode cannot publish into a newer view", async () => {
  const decoding = deferred();
  const h = createHarness({ decodeImage: () => decoding.promise });
  const scene = rgbdSnapshot();
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) }
    : cameraResponse(scene));
  await h.hooks.pollTelemetry();
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.hooks.sceneFrame.src, "");
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
  h.hooks.renderTelemetry(scene); await h.hooks.setSceneViewMode("cloud");
  decoding.resolve(); await Promise.resolve(); await Promise.resolve();
  assert.equal(h.hooks.sceneFrame.hidden, true);
  assert.equal(h.element("scene-canvas").hidden, false);
  assert.equal(h.element("scene-frame-stats").dataset.mode, "cloud");
  assert.deepEqual(h.revokedURLs, h.createdURLs);
});

test("periodic polls allow a valid 1200ms camera response and its pending decode to finish", async () => {
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const h = createHarness({ Date: Clock }); const scene = rgbdSnapshot();
  const waiting = deferred(); const started = deferred();
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) };
    started.resolve(); return waiting.promise;
  });
  const first = h.hooks.pollTelemetry(); await started.promise;
  const requests = h.fetches.length;
  now += 1000; const timerTick = h.hooks.pollTelemetry();
  await Promise.resolve();
  assert.equal(h.fetches.length, requests, "the timer must not abort and restart the valid request");
  now += 200; waiting.resolve(cameraResponse(scene)); await first; await timerTick;
  assert.ok(h.hooks.pendingSceneImage());
  now += 200; await h.hooks.pollTelemetry();
  assert.equal(h.fetches.length, requests, "predecoding also belongs to the in-flight capture");
  h.hooks.pendingSceneImage().onload();
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
  assert.ok(h.hooks.sceneFrame.src.startsWith("blob:"));
  await h.hooks.pollTelemetry();
  assert.ok(h.fetches.length > requests, "completion releases the next polling cycle");
});

test("switching cameras explicitly cancels a busy poll and never publishes the abandoned source", async () => {
  const h = createHarness(); const { head, base } = dualCameraFrames();
  const waiting = deferred(); const started = deferred(); let originalSignal;
  h.setFetch(async (url, options) => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: head }) };
    if (url.includes("sourceId=head")) { originalSignal = options.signal; started.resolve(); return waiting.promise; }
    return cameraResponse(base);
  });
  const first = h.hooks.pollTelemetry(); await started.promise;
  await h.hooks.selectSceneCamera(base.reconstruction.sourceId);
  assert.equal(originalSignal.aborted, true);
  h.hooks.pendingSceneImage().onload();
  const currentURL = h.hooks.sceneFrame.src;
  waiting.resolve(cameraResponse(head)); await first;
  assert.equal(h.hooks.sceneFrame.src, currentURL);
  assert.equal(h.element("scene-frame-stats").dataset.sourceId, base.reconstruction.sourceId);
  assert.equal(h.createdURLs.length, 1);
});

test("waiting for an in-flight camera never extends the capture freshness budget", async () => {
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const h = createHarness({ Date: Clock }); const scene = rgbdSnapshot();
  const waiting = deferred(); const started = deferred();
  h.setFetch(async url => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) };
    started.resolve(); return waiting.promise;
  });
  const first = h.hooks.pollTelemetry(); await started.promise;
  now += 2500; waiting.resolve(cameraResponse(scene)); await first;
  assert.equal(h.element("scene-live-state").textContent, "UNAVAILABLE");
  assert.equal(h.createdURLs.length, 0);
  assert.equal(h.element("scene-frame-stats").dataset.fps, "");
});

test("expiring previous pixels does not cancel a fresh replacement that is still decoding", async () => {
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const h = createHarness({ Date: Clock }); let scene = rgbdSnapshot();
  h.setFetch(async url => url.startsWith("/v1/telemetry?")
    ? { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) } : cameraResponse(scene));
  await h.hooks.pollTelemetry(); h.hooks.pendingSceneImage().onload();
  now += 1700;
  scene = structuredClone(scene); scene.observedAt = new Date(now).toISOString();
  scene.reconstruction.observedAtUnixMs = now; scene.robotState.perception.observed_at_unix_ms = now;
  await h.hooks.pollTelemetry();
  const pending = h.hooks.pendingSceneImage(); const requests = h.fetches.length;
  now += 400; await h.hooks.pollTelemetry();
  assert.equal(h.hooks.sceneFrame.hidden, true, "the older visible frame is expired");
  assert.equal(h.hooks.pendingSceneImage(), pending, "the fresh candidate keeps its own decode lifecycle");
  assert.equal(h.fetches.length, requests);
  pending.onload();
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
  assert.equal(h.hooks.displayedTelemetry().reconstruction.observedAtUnixMs, scene.reconstruction.observedAtUnixMs);
});

test("a camera request that never finishes is cancelled by the bounded polling watchdog", async () => {
  let now = Date.now();
  class Clock extends Date { static now() { return now; } }
  const h = createHarness({ Date: Clock }); let scene = rgbdSnapshot();
  const waiting = deferred(); const started = deferred(); let firstSignal; let blocked = true;
  h.setFetch(async (url, options) => {
    if (url.startsWith("/v1/telemetry?")) return { ok: true, json: async () => ({ adapters: ["mujoco"], latest: scene }) };
    if (blocked) { firstSignal = options.signal; started.resolve(); return waiting.promise; }
    return cameraResponse(scene);
  });
  const first = h.hooks.pollTelemetry(); await started.promise;
  now += 6000; blocked = false;
  scene = structuredClone(scene); scene.observedAt = new Date(now).toISOString();
  scene.reconstruction.observedAtUnixMs = now; scene.robotState.perception.observed_at_unix_ms = now;
  await h.hooks.pollTelemetry();
  assert.equal(firstSignal.aborted, true);
  h.hooks.pendingSceneImage().onload();
  const currentURL = h.hooks.sceneFrame.src;
  waiting.resolve(cameraResponse(rgbdSnapshot())); await first;
  assert.equal(h.hooks.sceneFrame.src, currentURL);
  assert.equal(h.element("scene-live-state").textContent, "LIVE");
});
