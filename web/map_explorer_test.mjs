import assert from "node:assert/strict";
import test from "node:test";
import vm from "node:vm";
import { readFile } from "node:fs/promises";

const context = vm.createContext({});
vm.runInContext(await readFile(new URL("./map_explorer.js", import.meta.url), "utf8"), context);
const explorer = context.TangyingMapExplorer;

const map = {
  mapId: "scan-home", robotId: "robot-1", frameId: "map", calibrationRevision: "cal-7",
  hash: "map-revision", pointCount: 52193, lodLevels: 5,
  bounds: { min: [-4.18, -.76, 0], max: [4.43, 8.45, 1.56] },
};

test("manifest validation requires a finite map frame and stable identity", () => {
  assert.equal(explorer.validateManifest(map).mapId, "scan-home");
  assert.throws(() => explorer.validateManifest({...map, frameId: "world"}), /frame/i);
  assert.throws(() => explorer.validateManifest({...map, bounds: {min:[0, 0, NaN], max:[1, 1, 1]}}), /finite/i);
  assert.throws(() => explorer.validateManifest({...map, calibrationRevision: ""}), /calibration/i);
});

test("semantic annotations are accepted only for the selected saved map identity", () => {
  const payload = {mapId:"scan-home", frameId:"map", calibrationRevision:"cal-7", mapRevision:"map-revision",
    annotations:[{id:"living_room", label:"living_room", position:[1,2,.4], collectedPointCount:144}]};
  const rows = explorer.normalizeSemantics(payload, map);
  assert.equal(rows[0].label, "客厅");
  assert.equal(rows[0].collectedPointCount, 144);
  assert.throws(() => explorer.normalizeSemantics({...payload, mapId:"other"}, map), /identity/i);
  assert.throws(() => explorer.normalizeSemantics({...payload, annotations:[{label:"bad", position:[Infinity,0,0]}]}, map), /finite/i);
});

test("commissioned workspace semantics use their saved target and Chinese alias", () => {
  const payload = {mapId:"scan-home", frameId:"map", calibrationRevision:"cal-7",
    workspaces:[{name:"bathroom", aliases:["浴室", "卫生间"], target:[-2,6.5,.035]}]};
  const rows = explorer.normalizeSemantics(payload,map);
  assert.equal(rows[0].label,"卫生间");
  assert.deepEqual([...rows[0].position],[-2,6.5,.035]);
});

test("trajectory validation rejects stale calibration and non-finite samples", () => {
  const payload = {mapId:"scan-home", frameId:"map", calibrationRevision:"cal-7", mapRevision:"map-revision",
    trajectory:[[0,0,0],[1,2,.1]]};
  assert.deepEqual(explorer.normalizeTrajectory(payload, map).map(point => [...point]), [[0,0,0],[1,2,.1]]);
  assert.throws(() => explorer.normalizeTrajectory({...payload, calibrationRevision:"cal-old"}, map), /calibration/i);
  assert.throws(() => explorer.normalizeTrajectory({...payload, trajectory:[[0,NaN,0]]}, map), /finite/i);
});

test("declared GeoJSON trajectories are normalized into map XYZ samples", () => {
  const payload = {type:"FeatureCollection",features:[{type:"Feature",geometry:{type:"LineString",coordinates:[[0,1],[2,3,.2]]}}]};
  assert.deepEqual(explorer.normalizeTrajectory(payload,map).map(point=>[...point]),[[0,1,0],[2,3,.2]]);
  assert.throws(() => explorer.normalizeTrajectory({...payload,features:[{geometry:{type:"LineString",coordinates:[[0,Infinity]]}}]},map),/finite/i);
});

test("local browsing marks are revision scoped and labels are bounded", () => {
  const storage = new Map();
  const adapter = {getItem:key=>storage.get(key) || null, setItem:(key,value)=>storage.set(key,value)};
  const first = explorer.saveLocalMark(adapter, map, {label:"  沙发旁边  ", position:[1,2,.3]});
  assert.equal(first.label, "沙发旁边");
  assert.equal(explorer.loadLocalMarks(adapter, map).length, 1);
  assert.equal(explorer.loadLocalMarks(adapter, {...map, hash:"next-revision"}).length, 0);
  assert.throws(() => explorer.saveLocalMark(adapter, map, {label:"x".repeat(81), position:[0,0,0]}), /80/);
  assert.throws(() => explorer.saveLocalMark(adapter, map, {label:"bad", position:[0,Infinity,0]}), /finite/i);
  explorer.deleteLocalMark(adapter, map, first.id);
  assert.equal(explorer.loadLocalMarks(adapter, map).length, 0);
});

test("local browsing marks retain sample counts and cap stored rows", () => {
  const storage = new Map();
  const adapter = {getItem:key=>storage.get(key) || null, setItem:(key,value)=>storage.set(key,value)};
  for (let index=0; index<105; index+=1) explorer.saveLocalMark(adapter,map,{label:`mark-${index}`,position:[index,0,0],collectedPointCount:index+1});
  const rows=explorer.loadLocalMarks(adapter,map);
  assert.equal(rows.length,100);
  assert.equal(rows.at(-1).collectedPointCount,105);
});

test("focus pose stays stable across repeated and alternating targets", () => {
  const bounds={min:[-4,-1,0],max:[4,8,2]};
  const first=explorer.focusPose({position:[8,-8,8],target:[0,3,1]},[1,2,.5],bounds);
  const repeat=explorer.focusPose(first,[1,2,.5],bounds);
  assert.ok(Math.abs(Math.hypot(...first.position.map((v,i)=>v-first.target[i]))-Math.hypot(...repeat.position.map((v,i)=>v-repeat.target[i])))<1e-9);
  const alternate=explorer.focusPose(repeat,[-2,6,.5],bounds);
  assert.ok(Math.hypot(...alternate.position.map((v,i)=>v-alternate.target[i]))>=1.5);
});

test("local mark counts stay unknown before cloud arrival and refresh without semantics", () => {
  const marks=[{id:"one",label:"入口",position:[1,2,0],collectedPointCount:9}];
  const before=explorer.refreshLocalMarkCounts(marks,null,false);
  assert.equal(before[0].collectedPointCount,null);
  let radius;
  const after=explorer.refreshLocalMarkCounts(marks,(_position,value)=>{radius=value;return 37;},true);
  assert.equal(after[0].collectedPointCount,37);
  assert.equal(radius,.45);
  assert.equal(explorer.LOCAL_MARK_RADIUS,.45);
});

test("pending picks are valid only for the same map revision and generation", () => {
  const pending=explorer.bindPendingPick({position:[1,2,0],collectedPointCount:4},map,12);
  assert.equal(explorer.pendingPickMatches(pending,map,12),true);
  assert.equal(explorer.pendingPickMatches(pending,{...map,mapId:"other"},12),false);
  assert.equal(explorer.pendingPickMatches(pending,{...map,hash:"next"},12),false);
  assert.equal(explorer.pendingPickMatches(pending,map,13),false);
});

// ── object layer ────────────────────────────────────────────────────────────
// The map's object layer is evidence with timestamps. These cases hold the two
// things an operator would be misled by: a position presented without its age,
// and an entry the map never claimed (unknown category relabelled as a known one).

function objectMap() {
  return { mapId: "home", hash: "a".repeat(64), calibrationRevision: "c".repeat(64),
           robotId: "unit-1", frameId: "map", pointCount: 10, lodLevels: 1,
           bounds: { min: [0, 0, 0], max: [1, 1, 1] } };
}
function objectLayer(objects) {
  return { schemaVersion: "map.objects.v1", mapId: "home", frameId: "map",
           calibrationRevision: "c".repeat(64), objects };
}
function mugEntry(overrides = {}) {
  return { id: "cup-000", category: "cup", attributes: { color: "white" },
           pose: [2.0, 3.0, .85], confidence: .9, sightings: 3,
           lastSeenUnixMs: 1_700_000_000_000, ageMs: 12_000, ...overrides };
}

test("object layer keeps every position together with its age", () => {
  const rows = explorer.normalizeObjects(objectLayer([mugEntry()]), objectMap());
  assert.equal(rows.length, 1);
  assert.equal(rows[0].label, "杯子");
  assert.deepEqual([...rows[0].position], [2.0, 3.0, .85]);
  assert.equal(rows[0].ageMs, 12_000);
  assert.equal(rows[0].sightings, 3);
  assert.equal(Object.isFrozen(rows[0]), true);
});

test("an unknown category keeps its own name instead of a known one", () => {
  const rows = explorer.normalizeObjects(objectLayer([mugEntry({ category: "kettle" })]), objectMap());
  assert.equal(rows[0].label, "kettle", "the map must not relabel what it never claimed");
});

test("object layer refuses foreign maps, missing poses and missing ages", () => {
  const bad = (payload) => assert.throws(() => explorer.normalizeObjects(payload, objectMap()));
  bad(objectLayer([mugEntry()]).valueOf() && { ...objectLayer([mugEntry()]), schemaVersion: "map.objects.v2" });
  bad({ ...objectLayer([mugEntry()]), frameId: "world" });
  bad({ ...objectLayer([mugEntry()]), mapId: "other" });
  bad(objectLayer([mugEntry({ pose: [1, 2] })]));
  bad(objectLayer([mugEntry({ ageMs: -1 })]));
  bad(objectLayer([mugEntry({ category: "" })]));
  bad(objectLayer([mugEntry(), mugEntry()]));
  bad(objectLayer(new Array(513).fill(mugEntry({ id: "x" }))));
  bad({ ...objectLayer([mugEntry()]), objects: "not-an-array" });
});
