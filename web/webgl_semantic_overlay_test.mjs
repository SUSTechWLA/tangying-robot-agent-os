import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { SemanticOverlay } from "./src/semantic_overlay.js";

function handoffSnapshot() {
  return {
    revision: 7,
    entities: {
      "counter-main": {
        entityId: "counter-main", category: "counter", pose: [1, 0, 0.45], freshness: "FRESH",
        attributes: { bounds: "0,-0.5,0,2,0.5,0.9", label: "主工作台", static: "true" },
      },
      "left-start-zone": {
        entityId: "left-start-zone", category: "source_zone", pose: [1.15, -0.62, 0.955], freshness: "FRESH",
      },
      "handoff-zone": {
        entityId: "handoff-zone", category: "handoff_zone", pose: [2, -0.62, 0.955], freshness: "FRESH",
      },
      "right-target-zone": {
        entityId: "right-target-zone", category: "target_zone", pose: [2.65, -0.62, 0.955], freshness: "FRESH",
      },
      "red-block": {
        entityId: "red-block", category: "block", pose: [2, -0.62, 1.01], freshness: "FRESH",
        attributes: { color: "red" }, relations: { inside: "handoff-zone" },
      },
    },
    robots: {
      "robot-1": {
        robotId: "robot-1", pose: [1.15, -1.15, 0.035, Math.PI / 2], activity: "PLACE", held: "", freshness: "FRESH",
        emergencyStopped: false,
      },
      "robot-2": {
        robotId: "robot-2", pose: [2.65, -1.15, 0.035, Math.PI / 2], activity: "IDLE", held: "", freshness: "FRESH",
        emergencyStopped: false,
      },
    },
    resources: {
      "block:red-block": {
        resourceId: "block:red-block", owner: "environment", fencingToken: 3, freshness: "FRESH",
      },
    },
    health: { degradedSources: [], conflicts: [] },
  };
}

test("custody badge is hidden before the first authoritative snapshot", () => {
  const appended = [];
  const document = {
    createElement() {
      return { className: "", dataset: {}, style: {}, textContent: "", remove() {} };
    },
  };
  const overlay = new SemanticOverlay(THREE, {
    document,
    labelContainer: { appendChild(element) { appended.push(element); } },
  });

  assert.equal(appended.length, 1);
  assert.equal(appended[0].style.display, "none");
  assert.equal(overlay.custodyBadge().visible, false);
});

test("semantic overlay keeps full models and authoritative markers visible together", () => {
  const overlay = new SemanticOverlay(THREE);
  overlay.apply(handoffSnapshot(), { models: true, bounds: true, labels: true, path: true });

  assert.deepEqual(overlay.zoneIds(), ["left-start-zone", "handoff-zone", "right-target-zone"]);
  assert.equal(overlay.bound("robot-1").visible, true);
  assert.match(overlay.label("robot-2").text, /FRESH/);
  assert.equal(overlay.custody().owner, "environment");
  assert.equal(overlay.visibility.models, true);
  assert.equal(overlay.root.parent, null, "the renderer owns attachment of the independent overlay root");
});

test("production xyz-yaw robot poses preserve status evidence and use positive Z yaw", () => {
  const snapshot = handoffSnapshot();
  snapshot.robots["robot-1"].freshness = "STALE";
  snapshot.robots["robot-1"].activity = "HANDOFF";
  snapshot.robots["robot-1"].held = "red-block";
  snapshot.robots["robot-1"].emergencyStopped = true;
  snapshot.entities["red-block"].relations = { held_by: "robot-1" };
  snapshot.resources["block:red-block"].owner = "robot-1";
  snapshot.resources["block:red-block"].fencingToken = 1;

  const overlay = new SemanticOverlay(THREE);
  overlay.apply(snapshot, { models: true, bounds: true, labels: true, path: true });

  const model = overlay.model("robot-1");
  const bound = overlay.bound("robot-1");
  const outline = overlay.outline("robot-1");
  assert.ok(model, "the production four-component robot pose must create robot semantics");
  assert.ok(bound);
  assert.ok(outline);
  assert.equal(model.desaturated, true);
  assert.equal(model.staleObject.visible, true);
  assert.equal(outline.emergency, true);
  assert.equal(outline.heldBy, "red-block");
  assert.match(overlay.label("robot-1").text, /STALE · HANDOFF · HELD red-block · EMERGENCY/);
  assert.equal(overlay.custody().owner, "robot-1");
  assert.equal(overlay.custody().fencingToken, 1);
  assert.equal(overlay.custody().conflict, false);

  for (const object of [bound.object, outline.object, model.staleObject]) {
    const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(object.quaternion);
    assert.ok(Math.abs(forward.x) < 1e-12, `x=${forward.x}`);
    assert.ok(Math.abs(forward.y - 1) < 1e-12, `y=${forward.y}`);
    assert.ok(Math.abs(forward.z) < 1e-12, `z=${forward.z}`);
  }
});

test("semantic robot poses retain xyz and quaternion compatibility", () => {
  const cases = [
    { pose: [1, 2, 0.035], forward: [1, 0, 0] },
    { pose: [1, 2, 0.035, Math.SQRT1_2, 0, 0, Math.SQRT1_2], forward: [0, 1, 0] },
  ];
  for (const entry of cases) {
    const snapshot = handoffSnapshot();
    snapshot.robots = { "robot-1": { ...snapshot.robots["robot-1"], pose: entry.pose } };
    const overlay = new SemanticOverlay(THREE);
    overlay.apply(snapshot, { models: true, bounds: true, labels: true, path: true });
    const forward = new THREE.Vector3(1, 0, 0).applyQuaternion(overlay.bound("robot-1").object.quaternion);
    assert.ok(forward.distanceTo(new THREE.Vector3(...entry.forward)) < 1e-12);
    assert.ok(overlay.label("robot-1"));
  }
});

test("malformed robot poses fail closed without leaving robot semantic ghosts", () => {
  const malformed = [
    [1, 2, 0.035, Number.POSITIVE_INFINITY],
    [1, 2, 0.035, 0, 0, 0, 0],
    [1, 2, 0.035, 0, 0],
  ];
  for (const pose of malformed) {
    const overlay = new SemanticOverlay(THREE);
    const valid = handoffSnapshot();
    valid.robots = { "robot-1": valid.robots["robot-1"] };
    overlay.apply(valid, { models: true, bounds: true, labels: true, path: true });
    const invalid = handoffSnapshot();
    invalid.revision += 1;
    invalid.robots = { "robot-1": { ...invalid.robots["robot-1"], pose } };

    overlay.apply(invalid, { models: true, bounds: true, labels: true, path: true });

    assert.equal(overlay.model("robot-1"), null);
    assert.equal(overlay.bound("robot-1"), null);
    assert.equal(overlay.outline("robot-1"), null);
    assert.equal(overlay.label("robot-1"), null);
  }
});

test("selection stays explicit while ordinary fixture evidence obeys independent visibility", () => {
  const snapshot = handoffSnapshot();
  const before = structuredClone(snapshot);
  const overlay = new SemanticOverlay(THREE);

  overlay.apply(snapshot, {
    models: true, bounds: false, labels: true, path: false, selectedEntityId: "counter-main",
  });

  assert.equal(overlay.bound("counter-main").visible, true, "selection remains locatable with ordinary bounds hidden");
  assert.equal(overlay.bound("counter-main").selected, true);
  assert.equal(overlay.label("counter-main").alwaysVisible, true);
  assert.equal(overlay.path().visible, false);
  assert.ok(overlay.zoneIds().every((id) => overlay.zone(id).visible === false));
  assert.deepEqual(snapshot, before, "view projection must not mutate authoritative world data");

  overlay.setVisibility({ bounds: true, labels: false, path: true, models: false });
  assert.equal(overlay.bound("robot-2").visible, true);
  assert.equal(overlay.label("robot-2").visible, false);
  assert.equal(overlay.path().visible, true);
  assert.equal(overlay.visibility.models, false);
});

test("held object, task stage, stale, emergency, and custody conflict remain separate evidence", () => {
  const snapshot = handoffSnapshot();
  snapshot.entities["red-block"].relations = { held_by: "robot-2" };
  snapshot.robots["robot-1"].held = "red-block";
  snapshot.robots["robot-1"].emergencyStopped = true;
  snapshot.robots["robot-2"].freshness = "STALE";
  snapshot.resources["block:red-block"].owner = "robot-2";

  const overlay = new SemanticOverlay(THREE);
  overlay.apply(snapshot, { models: true, bounds: true, labels: true, path: true });

  assert.deepEqual(overlay.path().segments.map((segment) => segment.state), ["complete", "pending"]);
  assert.equal(overlay.model("robot-2").desaturated, true);
  assert.equal(overlay.model("robot-2").staleObject.visible, true);
  assert.equal(overlay.outline("robot-1").emergency, true);
  assert.equal(overlay.outline("red-block").heldBy, "robot-2");
  assert.equal(overlay.custody().owner, "robot-2");
  assert.equal(overlay.custody().entityHolder, "robot-2");
  assert.deepEqual(overlay.custody().robotHolders, ["robot-1"]);
  assert.equal(overlay.custody().conflict, true);
  assert.match(overlay.label("red-block").text, /CONFLICT/);

  overlay.setVisibility({ models: false });
  assert.equal(overlay.model("robot-2").staleObject.visible, false);
  assert.equal(overlay.label("robot-2").visible, true);
  overlay.setVisibility({ models: true });
  assert.equal(overlay.model("robot-2").staleObject.visible, true);
});

test("removed world facts remove their semantic projection without ghost evidence", () => {
  const overlay = new SemanticOverlay(THREE);
  const first = handoffSnapshot();
  overlay.apply(first, { models: true, bounds: true, labels: true, path: true });
  const next = handoffSnapshot();
  next.revision = 8;
  delete next.entities["counter-main"];
  delete next.robots["robot-2"];

  overlay.apply(next, { models: true, bounds: true, labels: true, path: true });

  assert.equal(overlay.bound("counter-main"), null);
  assert.equal(overlay.label("counter-main"), null);
  assert.equal(overlay.bound("robot-2"), null);
  assert.equal(overlay.label("robot-2"), null);
  assert.equal(overlay.outline("robot-2"), null);
  assert.equal(overlay.model("robot-2"), null);
});

test("DOM labels project each frame and distance-cull only ordinary fixtures", () => {
  const appended = [];
  const labelContainer = { appendChild(element) { appended.push(element); }, removeChild() {} };
  const document = {
    createElement() {
      return { className: "", dataset: {}, style: {}, textContent: "", remove() {} };
    },
  };
  const overlay = new SemanticOverlay(THREE, { document, labelContainer, labelCullDistance: 4 });
  overlay.apply(handoffSnapshot(), { models: true, bounds: true, labels: true, path: true });
  const camera = new THREE.PerspectiveCamera(45, 2, 0.01, 100);
  camera.up.set(0, 0, 1);
  camera.position.set(1, -12, 4);
  camera.lookAt(1, 0, 0.5);
  camera.updateMatrixWorld(true);

  overlay.update(camera, { clientWidth: 800, clientHeight: 400 });

  assert.ok(appended.length >= 7);
  assert.equal(overlay.label("counter-main").visible, false, "distant ordinary fixture is culled");
  assert.equal(overlay.label("robot-1").visible, true, "robot status remains visible at distance");
  assert.match(overlay.label("robot-1").element.style.transform, /^translate\(/);
  overlay.label("robot-1").position.set(1000, 0, 0);
  overlay.update(camera, { clientWidth: 800, clientHeight: 400 });
  assert.equal(overlay.label("robot-1").visible, false, "offscreen labels are clipped on x/y as well as depth");
});

test("custody badge preserves conflict facts while same-revision freshness display degrades", () => {
  const appended = [];
  const document = {
    createElement() {
      return { className: "", dataset: {}, style: {}, textContent: "", removed: false, remove() { this.removed = true; } };
    },
  };
  const overlay = new SemanticOverlay(THREE, {
    document,
    labelContainer: { appendChild(element) { appended.push(element); } },
  });
  const snapshot = handoffSnapshot();
  snapshot.health.conflicts = ["red-block custody mismatch"];
  overlay.apply(snapshot, { models: true, bounds: true, labels: true, path: true });
  const badge = overlay.custodyBadge();
  assert.match(badge.element.textContent, /owner environment/);
  assert.match(badge.element.textContent, /token 3/);
  assert.match(badge.element.textContent, /FRESH/);
  assert.match(badge.element.textContent, /CONFLICT/);

  const sameRevision = handoffSnapshot();
  sameRevision.resources["block:red-block"] = {
    owner: "robot-2", fencingToken: 99, freshness: "STALE",
  };
  sameRevision.robots["robot-1"].held = "red-block";
  sameRevision.robots["robot-1"].activity = "MUTATED";
  sameRevision.robots["robot-1"].emergencyStopped = true;
  sameRevision.health.conflicts = [];
  overlay.applyVolatileState(sameRevision);

  assert.equal(overlay.custody().owner, "environment");
  assert.equal(overlay.custody().fencingToken, 3);
  assert.equal(overlay.custody().conflict, true);
  assert.deepEqual(overlay.custody().robotHolders, []);
  assert.match(badge.element.textContent, /owner environment/);
  assert.match(badge.element.textContent, /token 3/);
  assert.match(badge.element.textContent, /STALE/);
  assert.match(badge.element.textContent, /CONFLICT/);
  assert.doesNotMatch(overlay.label("robot-1").text, /MUTATED|EMERGENCY|HELD/);

  overlay.setVisibility({ labels: false });
  assert.equal(badge.visible, false);
  overlay.dispose();
  assert.equal(badge.element.removed, true);
});

test("same-revision non-fresh overlay state never returns to FRESH", () => {
  const snapshot = handoffSnapshot();
  snapshot.robots["robot-2"].freshness = "STALE";
  snapshot.entities["red-block"].freshness = "STALE";
  snapshot.resources["block:red-block"].freshness = "STALE";
  const overlay = new SemanticOverlay(THREE);
  overlay.apply(snapshot, { labels: true });

  overlay.applyVolatileState(handoffSnapshot());

  assert.equal(overlay.model("robot-2").freshness, "STALE");
  assert.equal(overlay.label("red-block").freshness, "STALE");
  assert.equal(overlay.custody().freshness, "STALE");
});
