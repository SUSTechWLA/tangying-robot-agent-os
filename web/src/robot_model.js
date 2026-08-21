import * as THREE from "three";

function robotError(code, detail) {
  const error = new Error(detail ? `${code}: ${detail}` : code);
  error.code = code;
  return error;
}

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

function finitePose(raw) {
  if (!Array.isArray(raw) || (raw.length !== 3 && raw.length !== 7)) {
    throw robotError("INVALID_ROBOT_STATE", "pose must contain xyz or xyz plus qw,qx,qy,qz");
  }
  const values = raw.slice(0, raw.length >= 7 ? 7 : 3).map(Number);
  if (!values.every(Number.isFinite)) throw robotError("INVALID_ROBOT_STATE", "pose must be finite");
  const position = values.slice(0, 3);
  const quaternion = values.length === 7
    ? new THREE.Quaternion(values[4], values[5], values[6], values[3])
    : new THREE.Quaternion();
  if (quaternion.lengthSq() <= 1e-18) {
    throw robotError("INVALID_ROBOT_STATE", "pose quaternion must be non-zero");
  }
  quaternion.normalize();
  return { position, quaternion };
}

function copyPose(pose) {
  return { position: [...pose.position], quaternion: pose.quaternion.clone() };
}

function samplePose(from, to, alpha) {
  return {
    position: from.position.map((value, index) => value + (to.position[index] - value) * alpha),
    quaternion: from.quaternion.clone().slerp(to.quaternion, alpha),
  };
}

function validateBindingEntry(name, entry) {
  const mappedEndpoints = [
    entry?.minimum * entry?.direction + entry?.offset,
    entry?.maximum * entry?.direction + entry?.offset,
  ];
  if (!entry || typeof entry.node !== "string" || !entry.node
    || !Array.isArray(entry.axis) || entry.axis.length !== 3 || !entry.axis.every(Number.isFinite)
    || Math.hypot(...entry.axis) <= 1e-12
    || ![entry.direction, entry.offset, entry.minimum, entry.maximum].every(Number.isFinite)
    || Math.abs(entry.direction) !== 1 || entry.minimum > entry.maximum
    || !mappedEndpoints.every(Number.isFinite)) {
    throw robotError("VISUAL_BINDING_INVALID", name);
  }
}

export class RobotModelInstance {
  static fromTemplate(template, binding, robotId, options = {}) {
    return new RobotModelInstance(template, binding, robotId, options);
  }

  constructor(template, binding, robotId, options = {}) {
    if (!template?.clone || typeof robotId !== "string" || !robotId) {
      throw robotError("VISUAL_ROBOT_TEMPLATE_INVALID", "template and robot ID are required");
    }
    this.robotId = robotId;
    this.interpolationMs = Number.isFinite(options.interpolationMs)
      ? clamp(options.interpolationMs, 0, 1000)
      : 100;
    this.root = new THREE.Group();
    this.root.name = robotId;
    this.root.userData.robotId = robotId;
    this.model = template.clone(true);
    this.model.updateMatrixWorld(true);
    this.modelAnchor = new THREE.Group();
    this.modelAnchor.name = `${robotId}-model-anchor`;
    // The exported template keeps the source MJCF chassis transform. Remove
    // that transform once so this.root can represent the authoritative chassis
    // world pose directly for every reusable instance.
    const chassis = this.model.getObjectByName("chassis");
    if (chassis) {
      chassis.updateWorldMatrix(true, false);
      this.modelAnchor.matrix.copy(chassis.matrixWorld).invert();
      this.modelAnchor.matrixAutoUpdate = false;
    }
    this.modelAnchor.add(this.model);
    this.root.add(this.modelAnchor);
    this.binding = binding;
    this.boundNodes = new Map();
    for (const [name, entry] of Object.entries(binding || {})) {
      validateBindingEntry(name, entry);
      const object = this.model.getObjectByName(entry.node);
      if (!object) throw robotError("VISUAL_BINDING_NODE_MISSING", `${name}: ${entry.node}`);
      this.boundNodes.set(name, {
        object,
        axis: new THREE.Vector3(...entry.axis).normalize(),
        direction: entry.direction,
        offset: entry.offset,
        minimum: entry.minimum,
        maximum: entry.maximum,
        baseQuaternion: object.quaternion.clone(),
      });
    }
    const bounds = new THREE.Box3().setFromObject(this.modelAnchor);
    if (bounds.isEmpty()) bounds.set(new THREE.Vector3(-0.25, -0.25, 0), new THREE.Vector3(0.25, 0.25, 0.8));
    this.statusHelper = new THREE.Box3Helper(bounds, 0xf4b942);
    this.statusHelper.name = `${robotId}-status`;
    this.statusHelper.visible = false;
    this.statusHelper.raycast = () => {};
    this.root.add(this.statusHelper);
    this.hasState = false;
    this.from = null;
    this.target = null;
    this.transitionStartedAt = 0;
    this.transitionDuration = 0;
    this.freshness = "UNKNOWN";
    this.emergencyStopped = false;
    this.activity = "";
    this.held = "";
    this.disposed = false;
  }

  node(name) {
    return this.model.getObjectByName(name) || null;
  }

  applyState(robot, receivedAtMs) {
    if (this.disposed) throw robotError("VISUAL_ROBOT_DISPOSED");
    if (!Number.isFinite(receivedAtMs) || !robot || typeof robot !== "object") {
      throw robotError("INVALID_ROBOT_STATE", "state and finite receive time are required");
    }
    if (robot.robotId && robot.robotId !== this.robotId) {
      throw robotError("INVALID_ROBOT_STATE", `state belongs to ${robot.robotId}`);
    }
    const pose = finitePose(robot.pose);
    const incomingJoints = robot.state || {};
    if (!incomingJoints || typeof incomingJoints !== "object" || Array.isArray(incomingJoints)) {
      throw robotError("INVALID_ROBOT_STATE", "joint state must be an object");
    }
    for (const [name, value] of Object.entries(incomingJoints)) {
      if (this.boundNodes.has(name) && !Number.isFinite(value)) {
        throw robotError("INVALID_ROBOT_STATE", `${name} must be finite`);
      }
    }

    const freshness = typeof robot.freshness === "string" ? robot.freshness : "UNKNOWN";
    const previous = this.hasState ? this.sample(receivedAtMs) : null;
    const joints = {};
    for (const [name, joint] of this.boundNodes) {
      const fallback = previous?.joints[name] ?? 0;
      const raw = Object.hasOwn(incomingJoints, name) ? incomingJoints[name] : fallback;
      joints[name] = clamp(Number(raw), joint.minimum, joint.maximum);
    }
    const requested = { pose, joints };

    this.freshness = freshness;
    this.emergencyStopped = Boolean(robot.emergencyStopped);
    this.activity = typeof robot.activity === "string" ? robot.activity : "";
    this.held = typeof robot.held === "string" ? robot.held : "";
    if (!this.hasState) {
      this.from = requested;
      this.target = requested;
      this.transitionDuration = 0;
      this.hasState = true;
    } else if (freshness === "FRESH") {
      this.from = { pose: copyPose(previous.pose), joints: { ...previous.joints } };
      this.target = requested;
      this.transitionDuration = this.interpolationMs;
    } else {
      this.from = { pose: copyPose(previous.pose), joints: { ...previous.joints } };
      this.target = this.from;
      this.transitionDuration = 0;
    }
    this.transitionStartedAt = receivedAtMs;
    this.#updateStatusVisual();
    return this.sample(receivedAtMs);
  }

  applyVolatileState(robot, receivedAtMs) {
    if (this.disposed) throw robotError("VISUAL_ROBOT_DISPOSED");
    if (!this.hasState || !Number.isFinite(receivedAtMs) || !robot || typeof robot !== "object") {
      throw robotError("INVALID_ROBOT_STATE", "existing state and finite receive time are required");
    }
    if (robot.robotId && robot.robotId !== this.robotId) {
      throw robotError("INVALID_ROBOT_STATE", `state belongs to ${robot.robotId}`);
    }
    const current = this.sample(receivedAtMs);
    this.from = { pose: copyPose(current.pose), joints: { ...current.joints } };
    this.target = this.from;
    this.transitionStartedAt = receivedAtMs;
    this.transitionDuration = 0;
    if (typeof robot.freshness === "string") this.freshness = robot.freshness;
    if (Object.hasOwn(robot, "emergencyStopped")) this.emergencyStopped = Boolean(robot.emergencyStopped);
    if (typeof robot.activity === "string") this.activity = robot.activity;
    if (typeof robot.held === "string") this.held = robot.held;
    this.#updateStatusVisual();
    return this.sample(receivedAtMs);
  }

  sample(nowMs) {
    if (!this.hasState) return null;
    if (!Number.isFinite(nowMs)) throw robotError("INVALID_ROBOT_STATE", "sample time must be finite");
    const alpha = this.transitionDuration === 0
      ? 1
      : clamp((nowMs - this.transitionStartedAt) / this.transitionDuration, 0, 1);
    const pose = samplePose(this.from.pose, this.target.pose, alpha);
    const joints = {};
    for (const name of this.boundNodes.keys()) {
      joints[name] = this.from.joints[name] + (this.target.joints[name] - this.from.joints[name]) * alpha;
    }
    this.#applyTransforms(pose, joints);
    return {
      robotId: this.robotId,
      pose,
      joints,
      freshness: this.freshness,
      emergencyStopped: this.emergencyStopped,
      activity: this.activity,
      held: this.held,
    };
  }

  #applyTransforms(pose, joints) {
    this.root.position.fromArray(pose.position);
    this.root.quaternion.copy(pose.quaternion);
    for (const [name, joint] of this.boundNodes) {
      const angle = joints[name] * joint.direction + joint.offset;
      const rotation = new THREE.Quaternion().setFromAxisAngle(joint.axis, angle);
      joint.object.quaternion.copy(joint.baseQuaternion).multiply(rotation);
    }
    this.root.updateMatrixWorld(true);
  }

  #updateStatusVisual() {
    const degraded = this.freshness !== "FRESH";
    this.statusHelper.visible = degraded || this.emergencyStopped;
    this.statusHelper.material.color.setHex(this.emergencyStopped ? 0xff4136 : 0xf4b942);
    this.root.userData.visualStatus = this.emergencyStopped
      ? "EMERGENCY_STOPPED"
      : degraded ? this.freshness : "LIVE";
    this.root.userData.freshness = this.freshness;
    this.root.userData.emergencyStopped = this.emergencyStopped;
    this.root.userData.activity = this.activity;
    this.root.userData.held = this.held;
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.root.remove(this.statusHelper);
    this.statusHelper.geometry.dispose();
    this.statusHelper.material.dispose();
    this.root.remove(this.modelAnchor);
  }
}
