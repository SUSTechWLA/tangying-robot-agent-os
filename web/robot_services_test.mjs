import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const source = await readFile(new URL("./robot_services.js", import.meta.url), "utf8");
const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const styles = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const sandbox = { console, Date, JSON, Map, Error, setTimeout, clearTimeout };
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const { createServiceClient, calibrationSaveParameters, acceptCalibrationSnapshot, mapSelectionAfterStatus, mappingCanFinish, serviceMap } = sandbox.TangyingRobotServices;

function response(status, payload) {
  return { ok: status >= 200 && status < 300, status, async json() { return payload; } };
}

test("service calls use the frozen envelope and a unique id for every deliberate command", async () => {
  const requests = [];
  const client = createServiceClient(async (url, options = {}) => {
    requests.push({ url, options });
    return response(200, { ok: true, code: "OK", message: "", result: { state: "recording" } });
  });
  await client.call("mapping.start", { mode: "survey", name: "一楼" });
  await client.call("mapping.start", { mode: "survey", name: "一楼" });
  const first = JSON.parse(requests[0].options.body);
  const second = JSON.parse(requests[1].options.body);
  assert.equal(requests[0].url, "/v1/robot/services");
  assert.equal(requests[0].options.method, "POST");
  assert.deepEqual(first.parameters, { mode: "survey", name: "一楼" });
  assert.equal(first.name, "mapping.start");
  assert.notEqual(first.requestId, second.requestId);
  assert.equal(requests.length, 2, "mutations are never retried by the client");
});

test("catalogue is a no-store GET and exposes advertised availability", async () => {
  let request;
  const client = createServiceClient(async (url, options) => {
    request = { url, options };
    return response(200, { robotId: "robot-1", services: [{ name: "calibration.run", available: true }] });
  });
  const catalogue = await client.list();
  assert.equal(request.url, "/v1/robot/services");
  assert.equal(request.options.cache, "no-store");
  assert.equal(serviceMap(catalogue).get("calibration.run").available, true);
});

test("HTTP and service failures preserve the server code and message", async () => {
  const httpClient = createServiceClient(async () => response(409, { code: "REVISION_CONFLICT", message: "版本已变化" }));
  await assert.rejects(httpClient.call("calibration.save", {}), error => error.code === "REVISION_CONFLICT" && /版本已变化/.test(error.message));
  const serviceClient = createServiceClient(async () => response(200, { ok: false, code: "SCAN_NOT_READY", message: "请先开始" }));
  await assert.rejects(serviceClient.call("mapping.move", {}), error => error.code === "SCAN_NOT_READY" && error.status === 200);
});

test("manual calibration keeps algorithm provenance outside the strict document", () => {
  const original = { schemaVersion: "robot.calibration.v1", source: "measured", motors: {} };
  const parameters = calibrationSaveParameters(original, "rev-1", "  bundle adjustment  ");
  assert.equal(parameters.document.source, "manual");
  assert.equal(parameters.expectedRevision, "rev-1");
  assert.equal(parameters.algorithm, "bundle adjustment");
  assert.equal(Object.hasOwn(parameters.document, "algorithm"), false);
  assert.equal(original.source, "measured", "building a request does not mutate the editor document");
});

test("polling updates session state without overwriting a dirty calibration editor", () => {
  const edited = { source: "manual", motors: { wrist: { homing_offset: 9 } } };
  const state = { document: edited, revision: "old", dirty: true, session: { status: "idle" } };
  const next = acceptCalibrationSnapshot(state, {
    document: { source: "manual", motors: { wrist: { homing_offset: 44 } } },
    revision: "new", session: { status: "running" },
  });
  assert.equal(next.document, edited);
  assert.equal(next.revision, "old");
  assert.equal(next.remoteRevision, "new");
  assert.equal(next.session.status, "running");
});

test("a clean calibration accepts the saved document and revision", () => {
  const document = { schemaVersion: "robot.calibration.v1", source: "manual" };
  const next = acceptCalibrationSnapshot({ dirty: false, document: null, revision: "" }, { document, revision: "rev-2" });
  assert.deepEqual(next.document, document);
  assert.notEqual(next.document, document, "the editor owns a copy rather than the response object");
  assert.equal(next.revision, "rev-2");
});

test("mapping completion explicitly chooses the newly published map", () => {
  assert.equal(mapSelectionAfterStatus("old", { state: "finalizing", mapId: "new" }), "old");
  assert.equal(mapSelectionAfterStatus("old", { state: "completed", mapId: "new" }), "new");
  assert.equal(mapSelectionAfterStatus("", {state:"idle",activeMap:{mapId:"restored"}}), "restored");
  assert.equal(mapSelectionAfterStatus("selected", {state:"idle",activeMap:{mapId:"restored"}}), "selected");
});

test("a failed scan with the backend minimum result can still be saved", () => {
  assert.equal(mappingCanFinish({ state: "recording", frameCount: 1, travelledM: 0 }, "manual"), true);
  assert.equal(mappingCanFinish({ state: "failed", frameCount: 3, travelledM: 0.15 }, ""), true);
  assert.equal(mappingCanFinish({ state: "failed", frameCount: 2, travelledM: 4 }, ""), false);
  assert.equal(mappingCanFinish({ state: "failed", frameCount: 10, travelledM: 0.14 }, ""), false);
});

test("the task composer identifies the robot while keeping adapter routing internal", () => {
  assert.match(html, /id="task-robot-label"/);
  assert.match(html, /id="adapter" hidden aria-hidden="true"/);
  assert.doesNotMatch(html, /aria-label="选择机器人连接"/);
});

test("manual calibration offers compact table, collapsed cameras, and a top JSON mode", () => {
  assert.match(html, /id="calibration-view-json"/);
  assert.match(source, /function renderMotorTable/);
  assert.match(source, /document\.createElement\("details"\)/);
  assert.match(styles, /calibration-motor-table-wrap \{ max-height: 430px; overflow: auto/);
  assert.match(styles, /calibration-camera-editor-grid \{ display: grid; grid-template-columns: repeat\(2/);
});


test("workspace reads registered calibration without running or saving calibration", async () => {
  const calls = [];
  const client = {
    async list() { return { robotId: "gazebo-home", services: [{ name: "calibration.get", available: true }] }; },
    async call(name) { calls.push(name); return { revision: "actual-revision", cameraCount: 2 }; },
  };
  const result = await sandbox.TangyingRobotServices.readCalibrationReadiness(client);
  assert.equal(result.robotId, "gazebo-home");
  assert.equal(result.revision, "actual-revision");
  assert.deepEqual(calls, ["calibration.get"]);
  client.list = async () => ({ services: [{ name: "calibration.get", available: false }] });
  assert.equal(await sandbox.TangyingRobotServices.readCalibrationReadiness(client), null);
  assert.equal(calls.length, 1);
});
