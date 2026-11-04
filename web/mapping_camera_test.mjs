import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

const source = await readFile(new URL("./robot_services.js", import.meta.url), "utf8");
const sandbox = { console, Date, JSON, Map, Error, Blob, Uint8Array, atob, AbortController, setTimeout, clearTimeout };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const { readMappingCamera, createMappingCameraPreview } = sandbox.TangyingRobotServices;
const png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZQmcAAAAASUVORK5CYII=";
const now = 1793205000000;
const binding = { robotId: "robot-1", adapter: "gazebo", sourceId: "robot-1/head-rgbd" };
const copy = value => structuredClone(value);
const response = value => ({ ok: true, json: async () => value });

function capture(sourceId = binding.sourceId) {
  return {
    snapshot: {
      schemaVersion: "telemetry.v1", robotId: binding.robotId, adapter: binding.adapter,
      observedAt: new Date(now).toISOString(),
      reconstruction: {
        schemaVersion: "scene.reconstruction.v1", robotId: binding.robotId,
        sourceId, sourceType: "rgbd_camera", sourceFrameId: sourceId + "/optical",
        transformRevision: "transform-1", frameId: "world", units: "m",
        observationId: sourceId + "/capture-1", sequence: 1, observedAtUnixMs: now,
      },
      robotState: { perception: { source_id: sourceId, observed_at_unix_ms: now } },
      robotProfile: {
        schemaVersion: "robot.profile.v1", robotId: binding.robotId, adapterId: binding.adapter,
        sensors: [binding.sourceId, "robot-1/base-rgbd"].map(id => ({
          sourceId: id, sourceType: "rgbd_camera", frameId: id + "/optical", transformRevision: "transform-1", maxAgeMs: 1000,
        })),
      },
    },
    rgbDataUrl: png, depthDataUrl: png,
  };
}

function captureFetch(body = capture(), metadata = capture().snapshot, calls = []) {
  return async (path, options) => {
    calls.push({ path, options });
    if (path === "/v1/runtime") return response({ RobotID: binding.robotId, Adapter: binding.adapter });
    if (path.startsWith("/v1/telemetry?adapter=&")) return response({ adapters: [binding.adapter], hasLatest: false });
    if (path.startsWith("/v1/telemetry?")) return response({ latest: metadata });
    assert.match(path, /^\/v1\/scene\/camera\?/);
    return response(body);
  };
}

test("mapping reads a registered atomic RGB-D capture rather than the absent legacy frame cache", async () => {
  const calls = [];
  const metadata = capture().snapshot;
  metadata.observedAt = new Date(now - 10000).toISOString();
  const result = await readMappingCamera(captureFetch(capture(), metadata, calls), binding, undefined, () => now + 100);
  assert.equal(result.robotId, binding.robotId);
  assert.equal(result.sourceId, binding.sourceId);
  assert.equal(result.observedAt, now, "camera freshness belongs to the atomic capture, not the cached metadata");
  assert.equal(result.rgb.type, "image/png");
  assert.ok(result.rgb.size > 8);
  assert.equal(calls.length, 2);
  assert.equal(calls[1].path, "/v1/scene/camera?adapter=gazebo&sourceId=robot-1%2Fhead-rgbd");
  assert.ok(calls.every(call => call.options.cache === "no-store"));
  assert.ok(calls.every(call => !call.path.includes("/scene/frame")));
});

test("mapping opened directly derives adapter and source only from identity-bound metadata", async () => {
  const directCalls = [];
  const result = await readMappingCamera(captureFetch(capture(), capture().snapshot, directCalls), { robotId: binding.robotId }, undefined, () => now);
  assert.equal(result.adapter, binding.adapter);
  assert.equal(result.sourceId, binding.sourceId);
  assert.deepEqual(directCalls.map(call => call.path), [
    "/v1/runtime", "/v1/telemetry?adapter=gazebo&history=false", "/v1/scene/camera?adapter=gazebo&sourceId=robot-1%2Fhead-rgbd",
  ]);
  const calls = [];
  await assert.rejects(readMappingCamera(captureFetch(capture(), capture().snapshot, calls), { ...binding, sourceId: "unregistered" }, undefined, () => now));
  assert.equal(calls.length, 1, "an unregistered camera is never queried as a fallback");
});

test("direct mapping entry refuses a missing or foreign runtime identity instead of choosing history", async () => {
  for (const runtime of [{}, { RobotID: "other", Adapter: "gazebo" }, { RobotID: binding.robotId, Adapter: "" }]) {
    const paths = [];
    await assert.rejects(readMappingCamera(async path => { paths.push(path); return response(runtime); }, { robotId: binding.robotId }, undefined, () => now));
    assert.deepEqual(paths, ["/v1/runtime"]);
  }
});

test("mapping rejects foreign identities, invalid captures, stale time and missing RGB-D members", async t => {
  const mutations = {
    "telemetry robot": (_, metadata) => { metadata.robotId = "other"; },
    "telemetry adapter": (_, metadata) => { metadata.adapter = "other"; },
    "profile robot": (_, metadata) => { metadata.robotProfile.robotId = "other"; },
    "capture robot": body => { body.snapshot.robotId = "other"; },
    "capture adapter": body => { body.snapshot.adapter = "other"; },
    "source": body => { body.snapshot.reconstruction.sourceId = "robot-1/base-rgbd"; },
    "frame": body => { body.snapshot.reconstruction.sourceFrameId = "wrong-frame"; },
    "transform revision": body => { body.snapshot.reconstruction.transformRevision = "old-transform"; },
    "profile revision": body => { body.snapshot.robotProfile.sensors[0].transformRevision = "old-transform"; },
    "profile freshness bound": body => { body.snapshot.robotProfile.sensors[0].maxAgeMs = 5000; },
    "capture time mismatch": body => { body.snapshot.observedAt = new Date(now - 1).toISOString(); },
    "perception source mismatch": body => { body.snapshot.robotState.perception.source_id = "other"; },
    "perception time mismatch": body => { body.snapshot.robotState.perception.observed_at_unix_ms -= 1; },
    "unsafe sequence": body => { body.snapshot.reconstruction.sequence = Number.MAX_SAFE_INTEGER + 1; },
    "missing depth": body => { delete body.depthDataUrl; },
    "non-PNG": body => { body.rgbDataUrl = "data:image/svg+xml;base64,PHN2Zz4="; },
    "false PNG signature": body => { body.rgbDataUrl = "data:image/png;base64,YmFkYml0cw=="; },
  };
  for (const [name, mutate] of Object.entries(mutations)) await t.test(name, async () => {
    const body = capture(), metadata = capture().snapshot;
    mutate(body, metadata);
    await assert.rejects(readMappingCamera(captureFetch(body, metadata), binding, undefined, () => now));
  });
  for (const offset of [1001, -251]) await t.test(`time offset ${offset}`, async () => {
    await assert.rejects(readMappingCamera(captureFetch(), binding, undefined, () => now + offset));
  });
});

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function previewHarness(fetchImpl = captureFetch()) {
  let clock = now, currentBinding = copy(binding), sequence = 0, timerSequence = 0;
  const created = [], revoked = [], timers = new Map();
  const image = {
    hidden: true, dataset: {}, src: "", title: "",
    removeAttribute(key) { this[key] = ""; },
  };
  const empty = { hidden: false, textContent: "" };
  const preview = createMappingCameraPreview({
    fetchImpl, getBinding: () => currentBinding, image, empty, now: () => clock,
    urls: {
      createObjectURL(blob) { const url = `blob:fixture-${++sequence}`; created.push({ url, blob }); return url; },
      revokeObjectURL(url) { revoked.push(url); },
    },
    schedule(fn, delay) { const id = ++timerSequence; timers.set(id, { fn, delay }); return id; },
    unschedule(id) { timers.delete(id); },
  });
  return { preview, image, empty, created, revoked, timers, setBinding(value) { currentBinding = value; }, setClock(value) { clock = value; } };
}

test("mapping publishes blob pixels with capture provenance and expires them even if polling stops", async () => {
  const h = previewHarness();
  await h.preview.refresh();
  assert.match(h.image.src, /^blob:/);
  assert.equal(h.image.hidden, true, "wait until decode before showing the image");
  h.image.onload();
  assert.equal(h.image.hidden, false);
  assert.equal(h.empty.hidden, true);
  assert.equal(h.image.dataset.robotId, binding.robotId);
  assert.equal(h.image.dataset.sourceId, binding.sourceId);
  assert.equal(h.image.dataset.observedAt, String(now));
  assert.equal(h.timers.size, 1, "only the capture expiry remains after a completed request");
  h.setClock(now + 1001);
  [...h.timers.values()][0].fn();
  assert.equal(h.image.hidden, true);
  assert.equal(h.image.src, "");
  assert.equal(h.empty.hidden, false);
  assert.match(h.empty.textContent, /过期/);
  assert.deepEqual(h.revoked, ["blob:fixture-1"]);
  assert.equal(h.image.dataset.sourceId, undefined);
});

test("leaving mapping aborts pending capture and rejects a late image without leaking an object URL", async () => {
  const pending = deferred(), started = deferred();
  let signal;
  const h = previewHarness(async (path, options) => {
    if (path.startsWith("/v1/telemetry?")) return response({ latest: capture().snapshot });
    signal = options.signal; started.resolve(); return pending.promise;
  });
  const refreshing = h.preview.refresh();
  await started.promise;
  h.setBinding(null); h.preview.reset();
  assert.equal(signal.aborted, true);
  pending.resolve(response(capture())); await refreshing;
  assert.equal(h.created.length, 0);
  assert.equal(h.image.hidden, true);
  assert.equal(h.image.src, "");
});

test("a newer selected source wins over a delayed previous capture", async () => {
  const pending = deferred(), started = deferred();
  const base = "robot-1/base-rgbd";
  const h = previewHarness(async path => {
    if (path.startsWith("/v1/telemetry?")) return response({ latest: capture().snapshot });
    if (path.includes("head-rgbd")) { started.resolve(); return pending.promise; }
    return response(capture(base));
  });
  const oldRequest = h.preview.refresh();
  await started.promise;
  h.setBinding({ ...binding, sourceId: base });
  await h.preview.refresh(); h.image.onload();
  assert.equal(h.image.dataset.sourceId, base);
  const url = h.image.src;
  pending.resolve(response(capture())); await oldRequest;
  assert.equal(h.image.src, url);
  assert.equal(h.image.dataset.sourceId, base);
  assert.equal(h.created.length, 1);
});

test("request failure clears the previous image and never falls back to cached frame", async () => {
  let fail = false;
  const calls = [];
  const good = captureFetch(capture(), capture().snapshot, calls);
  const h = previewHarness((path, options) => fail ? Promise.resolve({ ok: false }) : good(path, options));
  await h.preview.refresh(); h.image.onload();
  fail = true;
  await h.preview.refresh();
  assert.equal(h.image.src, "");
  assert.equal(h.image.hidden, true);
  assert.deepEqual(h.revoked, ["blob:fixture-1"]);
  assert.ok(calls.every(call => !call.path.includes("/scene/frame")));
});

test("request timeout releases the in-flight slot even if the old transport ignores abort", async () => {
  const pending = deferred(), started = deferred();
  let delayed = true;
  const h = previewHarness(async path => {
    if (path.startsWith("/v1/telemetry?")) return response({ latest: capture().snapshot });
    if (delayed) { started.resolve(); return pending.promise; }
    return response(capture());
  });
  const oldRequest = h.preview.refresh();
  await started.promise;
  [...h.timers.values()].find(timer => timer.delay === 4000).fn();
  assert.match(h.empty.textContent, /超时/);
  delayed = false;
  await h.preview.refresh(); h.image.onload();
  assert.equal(h.image.hidden, false);
  const url = h.image.src;
  pending.resolve(response(capture())); await oldRequest;
  assert.equal(h.image.src, url);
  assert.equal(h.created.length, 1);
});

test("the mapping route wires its preview through the selected robot workspace fetch", async () => {
  const calls = [];
  const intervals = new Map();
  let intervalID = 0;
  const image = { hidden: true, dataset: {}, src: "", removeAttribute(key) { this[key] = ""; } };
  const empty = { hidden: false, textContent: "" };
  const nodes = new Map([
    ["#mapping-camera", image], ["#mapping-camera-empty", empty],
    ["#adapter", { value: "" }], ["#scene-camera", { value: "" }],
  ]);
  const routed = {
    ...sandbox, TangyingRobotServices: undefined,
    Date: class extends Date { static now() { return now; } },
    location: { protocol: "http:", hash: "" },
    document: { body: { dataset: { page: "" } }, querySelector: selector => nodes.get(selector) || null, querySelectorAll: () => [] },
    addEventListener() {},
    setInterval(fn, delay) { const id = ++intervalID; intervals.set(id, { fn, delay }); return id; },
    clearInterval(id) { intervals.delete(id); },
    setTimeout() { return 1; }, clearTimeout() {},
    URL: { createObjectURL() { return "blob:mapping-route"; }, revokeObjectURL() {} },
    fetch() { throw new Error("unscoped native fetch must not be used"); },
    TangyingEdgeWorkspace: {
      async fetch(path, options) {
        calls.push(path);
        if (path === "/v1/robot/services") return options.method === "POST"
          ? response({ ok: true, result: { state: "idle" } })
          : response({ robotId: binding.robotId, services: [{ name: "mapping.status", available: true }] });
        return captureFetch()(path, options);
      },
    },
  };
  vm.createContext(routed); vm.runInContext(source, routed);
  await routed.TangyingRobotServices.enter("mapping");
  for (let index = 0; index < 20; index++) await Promise.resolve();
  assert.equal(image.src, "blob:mapping-route");
  image.onload();
  assert.equal(image.hidden, false);
  assert.equal(image.dataset.robotId, binding.robotId);
  assert.ok(calls.includes("/v1/runtime"));
  assert.ok(calls.includes("/v1/telemetry?adapter=gazebo&history=false"));
  assert.ok(calls.every(path => !path.startsWith("/v1/telemetry?adapter=&")));
  assert.ok(calls.some(path => path.startsWith("/v1/scene/camera?")));
  assert.ok(calls.every(path => !path.includes("/scene/frame")));
  assert.deepEqual([...intervals.values()].map(timer => timer.delay).sort(), [1500, 500]);
  await routed.TangyingRobotServices.enter("workspace");
  assert.equal(image.hidden, true);
  assert.equal(image.src, "");
  assert.equal(intervals.size, 0, "both camera and mapping polls stop when leaving the route");
});
