import assert from "node:assert/strict";
import test from "node:test";
import * as THREE from "three";

import { RobotModelInstance } from "./src/robot_model.js";

function robotTemplate() {
  const root = new THREE.Group();
  root.name = "XLeRobot";
  const geometry = new THREE.BoxGeometry(0.2, 0.2, 0.4);
  const material = new THREE.MeshStandardMaterial({ color: 0x7799aa });
  for (const name of ["Upper_Arm", "Upper_Arm_2", "head_pan_link"]) {
    const pivot = new THREE.Group();
    pivot.name = name;
    pivot.add(new THREE.Mesh(geometry, material));
    root.add(pivot);
  }
  return root;
}

const binding = {
  "joint.left.pitch": {
    node: "Upper_Arm", axis: [1, 0, 0], direction: -1,
    offset: 0.1, minimum: -1, maximum: 1,
  },
  "joint.right.pitch": {
    node: "Upper_Arm_2", axis: [1, 0, 0], direction: 1,
    offset: 0, minimum: -1, maximum: 1,
  },
  "joint.head.pan": {
    node: "head_pan_link", axis: [0, 0, 1], direction: 1,
    offset: 0, minimum: -1.57, maximum: 1.57,
  },
};

const state = (joints, overrides = {}) => ({
  robotId: "robot-1",
  pose: [0, 0, 0, 1, 0, 0, 0],
  state: joints,
  freshness: "FRESH",
  emergencyStopped: false,
  ...overrides,
});

test("two robot instances share geometry and materials while joints move independently", () => {
  const template = robotTemplate();
  const first = RobotModelInstance.fromTemplate(template, binding, "robot-1", { interpolationMs: 100 });
  const second = RobotModelInstance.fromTemplate(template, binding, "robot-2", { interpolationMs: 100 });

  assert.equal(first.node("Upper_Arm").children[0].geometry, second.node("Upper_Arm").children[0].geometry);
  assert.equal(first.node("Upper_Arm").children[0].material, second.node("Upper_Arm").children[0].material);
  first.applyState(state({ "joint.left.pitch": 0.8 }), 1000);
  second.applyState(state({ "joint.left.pitch": -0.2 }, { robotId: "robot-2" }), 1000);

  assert.notEqual(first.sample(1100).joints["joint.left.pitch"], second.sample(1100).joints["joint.left.pitch"]);
  assert.notEqual(first.node("Upper_Arm").quaternion.x, second.node("Upper_Arm").quaternion.x);
});

test("joint mapping applies direction, offset, and range without extrapolation", () => {
  const robot = RobotModelInstance.fromTemplate(robotTemplate(), binding, "robot-1", { interpolationMs: 100 });
  robot.applyState(state({ "joint.left.pitch": 0 }), 1000);
  assert.equal(robot.sample(1000).joints["joint.left.pitch"], 0);

  robot.applyState(state({ "joint.left.pitch": 5 }), 1100);
  assert.equal(robot.sample(1150).joints["joint.left.pitch"], 0.5);
  assert.equal(robot.sample(5000).joints["joint.left.pitch"], 1);
  const expected = new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(1, 0, 0), -0.9);
  assert.ok(robot.node("Upper_Arm").quaternion.angleTo(expected) < 1e-9);
});

test("non-finite authoritative transforms fail closed", () => {
  const robot = RobotModelInstance.fromTemplate(robotTemplate(), binding, "robot-1");
  assert.throws(
    () => robot.applyState(state({ "joint.left.pitch": Number.NaN }), 1000),
    /INVALID_ROBOT_STATE/,
  );
  assert.throws(
    () => robot.applyState(state({}, { pose: [0, 0, Infinity, 1, 0, 0, 0] }), 1000),
    /INVALID_ROBOT_STATE/,
  );
  assert.throws(
    () => robot.applyState(state({}, { pose: [0, 0, 0, 1, 0, 0, 0, 99] }), 1000),
    /INVALID_ROBOT_STATE/,
  );
});

test("non-fresh updates freeze transforms immediately while preserving status flags", () => {
  const robot = RobotModelInstance.fromTemplate(robotTemplate(), binding, "robot-1", { interpolationMs: 100 });
  robot.applyState(state({ "joint.left.pitch": 0.8 }), 1000);
  robot.sample(1100);
  robot.applyState(state({ "joint.left.pitch": -0.2 }), 1200);
  assert.ok(Math.abs(robot.sample(1250).joints["joint.left.pitch"] - 0.3) < 1e-12);

  robot.applyState(state(
    { "joint.left.pitch": 1 },
    { freshness: "STALE", emergencyStopped: true },
  ), 1250);
  const frozen = robot.sample(5000);
  assert.ok(Math.abs(frozen.joints["joint.left.pitch"] - 0.3) < 1e-12);
  assert.equal(frozen.freshness, "STALE");
  assert.equal(frozen.emergencyStopped, true);
  assert.equal(robot.statusHelper.visible, true);
  assert.equal(robot.statusHelper.material.color.getHex(), 0xff4136);
});

test("missing binding nodes are rejected instead of partially animating a robot", () => {
  assert.throws(
    () => RobotModelInstance.fromTemplate(robotTemplate(), {
      "joint.left.pitch": { ...binding["joint.left.pitch"], node: "missing" },
    }, "robot-1"),
    /VISUAL_BINDING_NODE_MISSING/,
  );
});
