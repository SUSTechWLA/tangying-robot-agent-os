import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";
import * as THREE from "three";
import { MapViewer } from "./src/map_viewer.js";

const context = vm.createContext({ console, Date });
vm.runInContext(await readFile(new URL("./semantic_map.js", import.meta.url), "utf8"), context);
const { bounds, boundsFromPoints, matchesMap, saveParameters, Session, Panel } = context.TangyingSemanticMap;
const plain = value => JSON.parse(JSON.stringify(value));
const revision = character => character.repeat(64);
const map = { mapId: "map-one", hash: revision("a"), calibrationRevision: revision("b"), createdAtUnixMs: 1700000000000 };
function annotation(id = "cup") {
  return { id, name: "厨房杯子", category: "cup", kind: "object", aliases: ["水杯"],
    bounds: { min: [1, 2, .5], max: [1.2, 2.2, .8] }, source: { kind: "operator", reference: "frame-032" },
    confidence: 1, observedAtUnixMs: 1700000000000, pointCount: 23, centroid: [1.1, 2.1, .6],
    measuredBounds: { min: [1.01, 2.02, .51], max: [1.19, 2.18, .79] }, stale: true, ambiguous: false };
}
function snapshot(annotations = [annotation()], semanticRevision = revision("c")) {
  return { activeMap: { mapId: map.mapId, mapRevision: map.hash, calibrationRevision: map.calibrationRevision },
    mapCreatedAtUnixMs: map.createdAtUnixMs, semanticRevision, annotations, annotationCount: annotations.length,
    frameId: "map", cloudSha256: revision("d"), coverage: { pointCount: 2500 } };
}
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }

test("point-cloud bounds reject reversed, zero-volume and nonfinite selections", () => {
  assert.deepEqual(plain(boundsFromPoints([3, -2, 4], [1, 5, .4])), { min: [1, -2, .4], max: [3, 5, 4] });
  for (const value of [{ min: [0, 0, 0], max: [0, 1, 1] }, { min: [2, 0, 0], max: [1, 1, 1] },
    { min: [NaN, 0, 0], max: [1, 1, 1] }, { min: [0, 0], max: [1, 1, 1] }]) assert.throws(() => bounds(value));
});

test("identity checks bind selected geometry, map revision, frame and calibration", () => {
  assert.equal(matchesMap(snapshot(), map), true);
  for (const changed of [{ ...map, mapId: "map-two" }, { ...map, hash: revision("e") }, { ...map, calibrationRevision: revision("f") }]) {
    assert.equal(matchesMap(snapshot(), changed), false);
    assert.throws(() => saveParameters(snapshot(), changed, annotation()));
  }
  assert.equal(matchesMap({ ...snapshot(), frameId: "world" }, map), false);
});

test("upsert preserves the full batch and evidence timestamps but strips server-derived fields", () => {
  const previous = snapshot([annotation(), annotation("bottle")]);
  const changed = { ...annotation(), name: "杯子（已复核标签）" };
  const parameters = saveParameters(previous, map, changed);
  assert.equal(parameters.annotations.length, 2);
  assert.equal(parameters.mapRevision, map.hash);
  assert.equal(parameters.expectedSemanticRevision, revision("c"));
  assert.equal(parameters.annotations[0].observedAtUnixMs, 1700000000000, "editing a label is not a fresh observation");
  assert.equal(parameters.annotations[1].source.reference, "frame-032");
  assert.equal(Object.hasOwn(parameters.annotations[0], "pointCount"), false);
  assert.equal(Object.hasOwn(parameters.annotations[0], "stale"), false);
  assert.equal(previous.annotations[0].name, "厨房杯子", "snapshot is immutable to the request builder");
  assert.equal(saveParameters(previous, map, annotation("new-cup")).annotations.length, 3);
});

test("truncated or duplicate snapshots cannot cause destructive whole-batch replacement", () => {
  assert.throws(() => saveParameters({ ...snapshot(), annotationCount: 2 }, map, annotation()));
  assert.throws(() => saveParameters({ ...snapshot(), truncated: true }, map, annotation()));
  assert.throws(() => saveParameters(snapshot([annotation(), annotation()]), map, annotation()));
  assert.throws(() => saveParameters({ ...snapshot(), semanticRevision: "" }, map, annotation()));
});

test("a map switch discards an old semantic read and cannot enable writes on different geometry", async () => {
  const pending = deferred();
  const session = new Session({ call: () => pending.promise });
  session.select(map);
  const reading = session.read();
  session.select({ ...map, mapId: "other-map" });
  pending.resolve(snapshot());
  assert.equal(await reading, null);
  assert.equal(session.snapshot, null);
  assert.equal(session.busy, false);
  await assert.rejects(session.save(annotation()), /重新读取/);
});

test("CAS failure never retries or refreshes automatically, and blocks a second submit until explicit read", async () => {
  const calls = []; let fail = true;
  const session = new Session({ async call(name, parameters) {
    calls.push({ name, parameters: plain(parameters) });
    if (name === "semantic.annotate" && fail) throw Object.assign(new Error("SEMANTIC_REVISION_CONFLICT"), { code: "SEMANTIC_REVISION_CONFLICT", status: 409 });
    return snapshot([annotation()], revision(fail ? "c" : "e"));
  } });
  session.select(map); await session.read();
  await assert.rejects(session.save(annotation()), /CONFLICT/);
  await assert.rejects(session.save(annotation()), /重新读取/);
  assert.deepEqual(calls.map(call => call.name), ["semantic.map", "semantic.annotate"]);
  assert.equal(session.conflicted, true);
  fail = false; await session.read(); await session.save(annotation());
  assert.equal(session.conflicted, false);
  assert.equal(calls.at(-1).parameters.expectedSemanticRevision, revision("e"));
  assert.ok(calls.every(call => ["semantic.map", "semantic.annotate"].includes(call.name)), "editing semantics never issues a motion service");
});

test("an uncertain network write locks the old batch rather than issuing a duplicate command", async () => {
  let writes = 0;
  const session = new Session({ async call(name) {
    if (name === "semantic.annotate") { writes += 1; throw new Error("network timeout after dispatch"); }
    return snapshot();
  } });
  session.select(map); await session.read();
  await assert.rejects(session.save(annotation()), /timeout/);
  await assert.rejects(session.save(annotation()), /重新读取/);
  assert.equal(writes, 1);
});

test("a historical commit receipt is acknowledged and locks editing after active-map switch", async () => {
  const calls = [];
  const session = new Session({ async call(name) {
    calls.push(name);
    return name === "semantic.map" ? snapshot() : { committed: true, activeMapChanged: true, semanticRevision: revision("e") };
  } });
  session.select(map); await session.read();
  const result = await session.save(annotation());
  assert.equal(result.committed, true);
  assert.equal(session.snapshot, null);
  assert.equal(session.conflicted, true);
  await assert.rejects(session.save(annotation()), /重新读取/);
  assert.deepEqual(calls, ["semantic.map", "semantic.annotate"], "a committed historical batch is neither retried nor silently migrated");
});

test("bounded semantic filtering keeps the complete write snapshot and includes stale evidence explicitly", async () => {
  let request;
  const session = new Session({ async call(name, parameters) {
    if (name === "semantic.map") return snapshot([annotation(), annotation("other")]);
    request = plain(parameters);
    return { ...snapshot(), results: [annotation()], total: 1, nextOffset: null };
  } });
  session.select(map); await session.read();
  const result = await session.query({ name: "水杯" });
  assert.equal(result.results.length, 1);
  assert.equal(session.snapshot.annotations.length, 2);
  assert.deepEqual(request, { limit: 50, offset: 0, includeStale: true, mapRevision: map.hash, semanticRevision: revision("c"), name: "水杯" });
});

test("a late query after a successful write cannot replace the current revision's view", async () => {
  const pending = deferred();
  const session = new Session({ async call(name) {
    if (name === "semantic.query") return pending.promise;
    return snapshot([annotation()], revision(name === "semantic.map" ? "c" : "e"));
  } });
  session.select(map); await session.read();
  const querying = session.query({}); await session.save(annotation());
  pending.resolve({ ...snapshot(), results: [annotation()] });
  assert.equal(await querying, null);
  assert.equal(session.snapshot.semanticRevision, revision("e"));
});

test("editing a query summary saves complete aliases, provenance and original observation time", async () => {
  const fields = new Map();
  const field = name => { if (!fields.has(name)) fields.set(name, { value: "" }); return fields.get(name); };
  const panel = Object.create(Panel.prototype);
  let saved;
  Object.assign(panel, { session: { snapshot: snapshot(), map, generation: 0,
    async save(candidate) { saved = plain(candidate); return snapshot(); } }, viewer: null, field,
    setPicking() {}, preview() {}, render() {}, message() {}, $(selector) { return selector === "[data-semantic-form]" ? { reset() {} } : { textContent: "" }; } });
  panel.clearForm();
  assert.equal(new Date(field("observedAt").value).getTime(), map.createdAtUnixMs);
  panel.session.snapshot.annotations[0].observedAtUnixMs = 1600000000000;
  panel.edit({ id: "cup", name: "这是查询摘要，不含别名" });
  assert.equal(new Date(field("observedAt").value).getTime(), 1600000000000);
  assert.equal(field("aliases").value, "水杯");
  assert.equal(field("reference").value, "frame-032");
  assert.equal(field("name").value, "厨房杯子");
  field("name").value = "复核后的杯子";
  await panel.save();
  assert.deepEqual(saved.aliases, ["水杯"]);
  assert.deepEqual(saved.source, { kind: "operator", reference: "frame-032" });
  assert.equal(saved.observedAtUnixMs, 1600000000000);
  assert.equal(saved.name, "复核后的杯子");
});

test("renaming a perception annotation preserves its source and low confidence", async () => {
  const fields = new Map();
  const field = name => { if (!fields.has(name)) fields.set(name, { value: "" }); return fields.get(name); };
  const original = { ...annotation(), source: { kind: "perception", reference: "detector-frame-032" }, confidence: .2 };
  const panel = Object.create(Panel.prototype);
  let saved;
  Object.assign(panel, { session: { snapshot: snapshot([original]), map, generation: 0,
    async save(candidate) { saved = plain(candidate); return snapshot([candidate]); } }, viewer: null, field,
    setPicking() {}, preview() {}, render() {}, message() {}, $(selector) { return selector === "[data-semantic-form]" ? { reset() {} } : { textContent: "" }; } });
  panel.edit({ id: "cup" });
  field("name").value = "重命名后的杯子";
  await panel.save();
  assert.equal(saved.name, "重命名后的杯子");
  assert.deepEqual(saved.source, original.source);
  assert.equal(saved.confidence, .2);
  assert.equal(saved.observedAtUnixMs, original.observedAtUnixMs);

  panel.edit({ id: "cup" });
  field("reference").value = "corrected-detector-frame-033";
  await panel.save();
  assert.deepEqual(saved.source, { kind: "perception", reference: "corrected-detector-frame-033" });
  assert.equal(saved.confidence, .2);
});

test("new annotations retain operator provenance and default confidence", async () => {
  const values = { name: "新杯子", category: "cup", kind: "object", aliases: "", reference: "operator-note-1",
    observedAt: "2023-11-14T12:00:00.000", min0: "1", min1: "2", min2: ".5", max0: "1.2", max1: "2.2", max2: ".8" };
  const panel = Object.create(Panel.prototype);
  let saved;
  const services = context.TangyingRobotServices;
  context.TangyingRobotServices = { requestId: () => "new-annotation" };
  Object.assign(panel, { session: { snapshot: snapshot(), generation: 0,
    async save(candidate) { saved = plain(candidate); return null; } }, editingId: "", field: name => ({ value: values[name] }),
    setPicking() {}, render() {}, message() {} });
  try { await panel.save(); } finally { context.TangyingRobotServices = services; }
  assert.equal(saved.id, "new-annotation");
  assert.deepEqual(saved.source, { kind: "operator", reference: "operator-note-1" });
  assert.equal(saved.confidence, 1);
});

test("viewer draws map-frame AABBs and disposes every replaced overlay", () => {
  const viewer = Object.create(MapViewer.prototype);
  Object.assign(viewer, { scene: new THREE.Scene(), semanticBoundsGroup: null, draw() {} });
  viewer.setSemanticBounds([annotation(), { ...annotation("ambiguous"), ambiguous: true }], { min: [-1, -2, .1], max: [1, 2, .9] });
  assert.equal(viewer.scene.children.length, 1);
  const group = viewer.semanticBoundsGroup;
  assert.equal(group.children.length, 3);
  assert.deepEqual(group.children[0].box.min.toArray(), [1, 2, .5]);
  assert.equal(group.children[1].material.color.getHex(), 0xe47b70);
  assert.equal(group.children[2].material.color.getHex(), 0xffb450);
  let disposed = 0;
  for (const helper of group.children) { helper.geometry.addEventListener("dispose", () => { disposed += 1; }); helper.material.addEventListener("dispose", () => { disposed += 1; }); }
  viewer.setSemanticBounds([{ bounds: { min: [NaN, 0, 0], max: [1, 1, 1] } }]);
  assert.equal(disposed, 6);
  assert.equal(viewer.scene.children.length, 0);
  assert.equal(viewer.semanticBoundsGroup, null);
});
