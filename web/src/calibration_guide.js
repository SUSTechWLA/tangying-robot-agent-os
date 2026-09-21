import * as THREE from "three";

// The picture for one calibration step.
//
// The wizard owns the plan, the wording and the progress; the console owns the
// drawing, and the two meet at a step's `guide` object. This module draws that
// object and nothing else, so a scene can never describe a different procedure
// than the sentence beside it.
//
// Every scene is built from three.js primitives at construction time: no model
// files, no textures, no fetches. The console is served by a robot on a bench,
// often with no route to the internet at all, and a guide that has to download
// something is a guide that shows nothing exactly when the operator needs it.
//
// Nothing here throws. A canvas that cannot give us WebGL, a step this build has
// no animation for, or a browser that never fires an animation frame all end in
// the same place: a status the caller can read, and a page that still shows the
// operator what to do in words.

const BACKGROUND = 0xeef1ef;

// The console's own palette, so a step animation looks like the page it sits in
// rather than like a separate application.
const INK = 0x273433;
const METAL = 0x9aa5a3;
const DARK = 0x55605e;
const ACCENT = 0x167d72;
const ACCENT_SOFT = 0x8fc4bd;
const AMBER = 0xd99a2b;
const BLUE = 0x3f7fb5;
const BOARD_DARK = 0x2b3440;
const BOARD_LIGHT = 0xf4f6f4;
const TABLE = 0xd8d3c6;

const UP = new THREE.Vector3(0, 0, 1);

//: Where a reduced-motion frame is taken when a scene does not say. Each scene
//: picks its own so its one frame is a settled pose — the joint at mid-range, the
//: board a little tilted — rather than whatever the animation happened to be
//: passing through. That is the difference between a still picture and a frozen
//: accident.
const STATIC_FRAME_MS = 1200;

const PULSE_MS = 1400;

//: Where the shoulder sits, and how far the arm reaches from it. The travel arc
//: and the hand-eye poses are both measured against these, so they stay true to
//: the same arm instead of to three different guesses.
const SHOULDER_HEIGHT = 0.26;
const ARM_REACH = 0.66;

//: What each joint in the plan is on screen: which node of the arm turns, about
//: which axis, how far a demonstration moves it, and where its middle is.
//:
//: The names are the wizard's (`shoulder_pan` … `gripper`), and the mid positions
//: follow the poses the wizard asks for in words: the gripper's middle is "the
//: two fingers just touching", not "half open". A joint name this build does not
//: know is drawn as a still arm rather than as a wrong one.
const JOINT_SPECS = {
  shoulder_pan: { node: "shoulderPan", axis: "z", sign: 1, sweep: -0.80, mid: 0 },
  shoulder_lift: { node: "shoulderLift", axis: "y", sign: -1, sweep: -0.75, mid: 0.12 },
  elbow_flex: { node: "elbowFlex", axis: "y", sign: -1, sweep: -0.55, mid: 0.18 },
  wrist_flex: { node: "wristFlex", axis: "y", sign: -1, sweep: -0.60, mid: 0 },
  wrist_roll: { node: "wristRoll", axis: "x", sign: 1, sweep: -1.20, mid: 0 },
  gripper: { node: "gripper", axis: "y", sign: 1, sweep: 0.50, mid: 0.02, opening: true },
  head_motor_1: { node: "headPan", axis: "z", sign: 1, sweep: -0.75, mid: 0 },
  head_motor_2: { node: "headTilt", axis: "y", sign: -1, sweep: -0.45, mid: 0 },
  base_left_wheel: { node: "wheelLeft", axis: "x", sign: 1, sweep: -3.10, mid: 0, spin: true },
  base_right_wheel: { node: "wheelRight", axis: "x", sign: 1, sweep: -3.10, mid: 0, spin: true },
};

//: Joints that arrive as numbers, in the order the arm is built. A plan may name
//: the chain either by joint or by index, and a plan written with numbers must not
//: end up animating nothing.
const ARM_JOINT_ORDER = ["shoulder_pan", "shoulder_lift", "elbow_flex", "wrist_flex", "wrist_roll", "gripper"];

const GRIPPER_CLOSED = 0.055;

//: The travel step's two end stops, as shoulder angles. They are not symmetric:
//: the lower one is where the forearm would otherwise enter the table, and the
//: picture has to stop where the machine stops.
const TRAVEL_FROM = -0.35;
const TRAVEL_TO = 0.75;

//: Where a connection point sits on the figure: mains at the side of the base,
//: the control board's cable at the back of the torso, a camera on the head, and
//: a second camera at the front of the base — which is where a second camera
//: physically goes. A port kind this build has never seen takes the last free
//: place rather than a plausible-looking wrong one.
const CONNECT_SLOTS = [
  { port: "power", position: [0.20, 0.07, 0.08], lead: [0.40, 0.16, 0.02] },
  { port: "usb", position: [0.15, 0.10, 0.37], lead: [0.34, 0.22, 0.30] },
  { port: "camera", position: [0.05, -0.13, 0.50], lead: [0.16, -0.42, 0.62] },
  { port: "camera", position: [-0.12, -0.17, 0.10], lead: [-0.34, -0.38, 0.06] },
];

const INTRINSICS_VIEW_HOLD_MS = 1250;
//: Distance from the lens, tilt towards and away from the board, and height: the
//: three things a person varies when a solver asks for views from several angles.
const INTRINSICS_POSES = [
  { distance: 0.46, yaw: 0.00, pitch: 0.00, lift: 0.00 },
  { distance: 0.62, yaw: 0.38, pitch: 0.06, lift: 0.05 },
  { distance: 0.38, yaw: -0.32, pitch: -0.12, lift: -0.04 },
  { distance: 0.70, yaw: 0.12, pitch: 0.34, lift: 0.02 },
  { distance: 0.44, yaw: 0.58, pitch: -0.20, lift: 0.06 },
  { distance: 0.58, yaw: -0.48, pitch: 0.24, lift: -0.05 },
];

const HANDEYE_POSE_HOLD_MS = 1700;
//: Arm poses, spread out on purpose: poses that are nearly the same solve for
//: nothing, which is what the step's own detail text warns about.
const HANDEYE_POSES = [
  { pan: -0.25, lift: 0.55, elbow: -0.30, flex: 0.10, roll: 0.0 },
  { pan: -0.08, lift: 0.78, elbow: -0.58, flex: 0.26, roll: 0.6 },
  { pan: 0.12, lift: 0.44, elbow: -0.20, flex: -0.16, roll: 1.2 },
  { pan: 0.30, lift: 0.66, elbow: -0.46, flex: 0.30, roll: -0.6 },
  { pan: 0.04, lift: 0.88, elbow: -0.72, flex: -0.06, roll: 1.8 },
  { pan: -0.34, lift: 0.70, elbow: -0.60, flex: 0.20, roll: -1.1 },
  { pan: 0.22, lift: 0.34, elbow: -0.10, flex: 0.34, roll: 0.4 },
  { pan: -0.04, lift: 0.60, elbow: -0.40, flex: -0.26, roll: 2.4 },
];

const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));

function easeInOut(alpha) {
  if (alpha <= 0) return 0;
  if (alpha >= 1) return 1;
  return alpha * alpha * (3 - 2 * alpha);
}

function progressOf(elapsedMs, fromMs, toMs) {
  return clamp((elapsedMs - fromMs) / Math.max(1, toMs - fromMs), 0, 1);
}

function finite(value, fallback = 0) {
  const number = Number(value);
  return Number.isFinite(number) ? number : fallback;
}

/** "3" and "elbow_flex" are the same joint, written by two different plans. */
function jointKey(raw) {
  const name = String(raw || "");
  if (Object.hasOwn(JOINT_SPECS, name)) return name;
  const index = Number(name);
  return Number.isInteger(index) && index >= 1 && index <= ARM_JOINT_ORDER.length
    ? ARM_JOINT_ORDER[index - 1]
    : "";
}

function normalizedList(value) {
  return Array.isArray(value) ? value : [];
}

/** Copy a guide into an owned descriptor; the animation reads only these. */
function normalizeGuide(raw) {
  const guide = raw && typeof raw === "object" ? raw : {};
  return {
    kind: String(guide.kind || ""),
    motor: String(guide.motor || ""),
    side: String(guide.side || ""),
    joint: String(guide.joint || ""),
    camera: String(guide.camera || ""),
    views: Math.max(0, Math.round(finite(guide.views))),
    poses: Math.max(0, Math.round(finite(guide.poses))),
    ports: normalizedList(guide.ports).map(port => ({
      id: String(port?.id || ""),
      port: String(port?.port || ""),
      label: String(port?.label || ""),
    })),
    checks: normalizedList(guide.checks).map(check => ({
      id: String(check?.id || ""),
      label: String(check?.label || ""),
    })),
    cameras: normalizedList(guide.cameras).map(name => String(name)),
    joints: normalizedList(guide.joints).map(name => String(name)),
  };
}

function prefersReducedMotion() {
  try {
    return globalThis.matchMedia?.("(prefers-reduced-motion: reduce)")?.matches === true;
  } catch (_) {
    return false;
  }
}

function defaultRendererFactory(options) {
  return new THREE.WebGLRenderer(options);
}

export class CalibrationGuide {
  static create(element, guide = null, options = {}) {
    return new CalibrationGuide(element, guide, options);
  }

  constructor(element, guide = null, options = {}) {
    this.options = options;
    this.now = options.now || (() => globalThis.performance?.now?.() ?? Date.now());
    this.requestFrame = options.requestFrame || (typeof globalThis.requestAnimationFrame === "function"
      ? (callback) => globalThis.requestAnimationFrame(callback)
      : null);
    this.cancelFrame = options.cancelFrame || (typeof globalThis.cancelAnimationFrame === "function"
      ? (handle) => globalThis.cancelAnimationFrame(handle)
      : null);
    this.reducedMotion = typeof options.reducedMotion === "boolean"
      ? options.reducedMotion
      : prefersReducedMotion();
    this.onProgress = typeof options.onProgress === "function" ? options.onProgress : null;
    this.pixelRatioCap = Number.isFinite(options.pixelRatioCap) ? options.pixelRatioCap : 2;

    this.ownedCanvas = false;
    this.canvas = this.#resolveCanvas(element);
    this.guide = normalizeGuide(null);
    this.kind = "";
    this.progress = { index: 0, count: 0 };
    this.updater = null;
    this.staticFrameMs = STATIC_FRAME_MS;
    this.owned = [];
    this.listeners = [];
    this.frameRequest = null;
    this.running = false;
    this.disposed = false;
    this.elapsedMs = 0;
    this.startedAt = this.now();
    this.logicalWidth = 0;
    this.logicalHeight = 0;
    this.renderPixelRatio = 0;
    this.status = Object.freeze({ state: "INITIALIZING", code: "CALIBRATION_GUIDE_INITIALIZING", kind: "" });

    if (!this.canvas) {
      this.#setStatus("UNAVAILABLE", "CALIBRATION_GUIDE_CANVAS_MISSING");
      return;
    }

    this.onContextLost = (event) => {
      event.preventDefault?.();
      this.#setStatus("DEGRADED", "WEBGL_CONTEXT_LOST");
    };
    this.onContextRestored = () => {
      this.#setStatus(this.gpuRenderer ? "READY" : "UNAVAILABLE",
        this.gpuRenderer ? "CALIBRATION_GUIDE_READY" : "WEBGL_UNAVAILABLE");
    };
    this.#listen("webglcontextlost", this.onContextLost);
    this.#listen("webglcontextrestored", this.onContextRestored);

    try {
      const factory = options.rendererFactory || defaultRendererFactory;
      this.gpuRenderer = factory({ canvas: this.canvas, antialias: true, alpha: false, powerPreference: "low-power" });
    } catch (_) {
      // Headless, an old browser, or a page that already spent its contexts: the
      // step is still readable, it just has no picture.
      this.gpuRenderer = null;
      this.#setStatus("UNAVAILABLE", "WEBGL_UNAVAILABLE");
      return;
    }

    this.scene = new THREE.Scene();
    this.scene.name = "CalibrationGuide";
    this.scene.up.set(0, 0, 1);
    this.scene.background = new THREE.Color(BACKGROUND);
    this.camera = new THREE.PerspectiveCamera(38, 1, 0.04, 40);
    this.camera.name = "CalibrationGuideCamera";
    this.camera.up.set(0, 0, 1);
    this.stage = new THREE.Group();
    this.stage.name = "GuideStage";
    this.viewPosition = new THREE.Vector3(1.1, -1.5, 1.0);
    this.viewTarget = new THREE.Vector3(0, 0, 0.35);
    const ambient = new THREE.HemisphereLight(0xffffff, 0xb6bfbd, 1.55);
    const key = new THREE.DirectionalLight(0xfff6e8, 1.7);
    key.position.set(-2.6, -3.4, 4.4);
    const fill = new THREE.DirectionalLight(0xffffff, 0.6);
    fill.position.set(3.2, -1.4, 2.0);
    this.scene.add(ambient, key, fill, this.stage);

    this.show(guide);
  }

  /**
   * Draw another step.
   *
   * A kind this build has no animation for leaves an empty stage: a wrong
   * animation beside the right words is worse than no animation, because the
   * operator has no way to tell which of the two to believe.
   */
  show(guide) {
    if (this.disposed || !this.gpuRenderer) return this;
    this.guide = normalizeGuide(guide);
    this.kind = this.guide.kind;
    this.progress = { index: 0, count: 0 };
    this.staticFrameMs = STATIC_FRAME_MS;
    this.updater = null;
    this.startedAt = this.now();
    this.elapsedMs = 0;
    this.#clearStage();
    const built = this.#build(this.guide);
    if (built) this.#setStatus("READY", "CALIBRATION_GUIDE_READY");
    else this.#setStatus("READY", this.kind ? "CALIBRATION_GUIDE_KIND_UNKNOWN" : "CALIBRATION_GUIDE_EMPTY");
    this.#scheduleFrame();
    return this;
  }

  /** One frame. Returns false when nothing could be drawn, and never throws. */
  render(nowMs = this.now()) {
    if (this.disposed || !this.gpuRenderer || !this.scene) return false;
    // A hidden panel has no layout, so there is nothing to size the drawing
    // buffer from and nothing the operator can see. Skipping the GPU work keeps
    // an open calibration tab cheap while the operator is on another page.
    if (this.canvas.hidden || this.canvas.clientWidth === 0 || this.canvas.clientHeight === 0) return false;
    // Reduced motion keeps the loop — a panel that becomes visible later still
    // gets its frame — but freezes the clock, so every frame is one picture.
    this.elapsedMs = this.reducedMotion
      ? this.staticFrameMs
      : Math.max(0, finite(nowMs, this.now()) - this.startedAt);
    this.#resize();
    this.#updateCamera(this.elapsedMs);
    if (typeof this.updater === "function") this.updater(this.elapsedMs);
    this.scene.updateMatrixWorld(true);
    this.gpuRenderer.render(this.scene, this.camera);
    return true;
  }

  /** Idempotent: a panel that is closed twice must not dispose anything twice. */
  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    this.running = false;
    if (this.frameRequest !== null) {
      this.cancelFrame?.(this.frameRequest);
      this.frameRequest = null;
    }
    for (const { target, name, handler } of this.listeners) {
      target.removeEventListener?.(name, handler, false);
    }
    this.listeners = [];
    this.onProgress = null;
    if (this.stage) this.#clearStage();
    if (this.scene) this.scene.clear();
    this.gpuRenderer?.dispose?.();
    // A page gets a handful of live WebGL contexts; a guide that is finished with
    // one has to hand it back, or the next panel that asks for one gets nothing.
    this.gpuRenderer?.forceContextLoss?.();
    this.gpuRenderer = null;
    if (this.ownedCanvas) this.canvas?.remove?.();
    this.#setStatus("DISPOSED", "CALIBRATION_GUIDE_DISPOSED");
  }

  // -- lifecycle ----------------------------------------------------------

  #build(guide) {
    switch (guide.kind) {
      case "connect": this.#buildConnect(guide); return true;
      case "preflight": this.#buildPreflight(guide); return true;
      case "zero": this.#buildZero(guide); return true;
      case "travel": this.#buildTravel(guide); return true;
      case "intrinsics": this.#buildIntrinsics(guide); return true;
      case "handeye": this.#buildHandeye(guide); return true;
      case "review": this.#buildReview(); return true;
      default: return false;
    }
  }

  #resolveCanvas(element) {
    if (!element) return null;
    if (typeof element.getContext === "function") return element;
    if (typeof element.append !== "function" && typeof element.appendChild !== "function") return null;
    const owner = element.ownerDocument || globalThis.document;
    const canvas = owner?.createElement?.("canvas");
    if (!canvas) return null;
    canvas.className = "calibration-guide-canvas";
    canvas.setAttribute?.("role", "img");
    if (typeof element.append === "function") element.append(canvas);
    else element.appendChild(canvas);
    this.ownedCanvas = true;
    return canvas;
  }

  #scheduleFrame() {
    if (this.disposed || this.running || typeof this.requestFrame !== "function") return;
    this.running = true;
    this.frameRequest = this.requestFrame((timestamp) => {
      this.frameRequest = null;
      this.running = false;
      if (this.disposed) return;
      this.render(Number.isFinite(timestamp) ? timestamp : this.now());
      if (this.disposed) return;
      this.#scheduleFrame();
    });
  }

  #setStatus(state, code) {
    this.status = Object.freeze({ state, code, kind: this.kind });
  }

  #listen(name, handler) {
    if (typeof this.canvas?.addEventListener !== "function") return;
    this.canvas.addEventListener(name, handler, false);
    this.listeners.push({ target: this.canvas, name, handler });
  }

  /** Tell the panel which item of the step is on screen now. */
  #report(index, count) {
    if (this.progress.index === index && this.progress.count === count) return;
    this.progress = { index, count };
    try {
      this.onProgress?.({ index, count, kind: this.kind });
    } catch (_) {
      // A listener that throws must not take the animation down with it.
    }
  }

  #pulse(elapsedMs, period = PULSE_MS) {
    if (this.reducedMotion) return 0.5;
    return 0.5 + 0.5 * Math.sin((elapsedMs / period) * Math.PI * 2);
  }

  #resize() {
    const width = Math.max(1, Number(this.canvas.clientWidth || this.canvas.width || 1));
    const height = Math.max(1, Number(this.canvas.clientHeight || this.canvas.height || 1));
    const pixelRatio = Math.max(1, Math.min(finite(this.options.devicePixelRatio ?? globalThis.devicePixelRatio, 1),
      this.pixelRatioCap));
    const sizeChanged = width !== this.logicalWidth || height !== this.logicalHeight;
    const ratioChanged = pixelRatio !== this.renderPixelRatio;
    if (ratioChanged) {
      this.gpuRenderer.setPixelRatio?.(pixelRatio);
      this.renderPixelRatio = pixelRatio;
    }
    if (sizeChanged) {
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
      this.logicalWidth = width;
      this.logicalHeight = height;
    }
    if (sizeChanged || ratioChanged) this.gpuRenderer.setSize?.(width, height, false);
  }

  /**
   * What a step draws, in world space, without the floor.
   *
   * The floor runs off the edge of every picture by design, so measuring it would
   * make every scene "too big" and say nothing about whether the machine is in
   * frame.
   */
  #subjectBounds() {
    const box = new THREE.Box3();
    const corner = new THREE.Vector3();
    this.stage.updateMatrixWorld(true);
    this.stage.traverse((object) => {
      if (!object.isMesh && !object.isLine) return;
      for (let parent = object; parent; parent = parent.parent) {
        if (parent.name === "guide-ground") return;
      }
      if (!object.geometry.boundingBox) object.geometry.computeBoundingBox();
      const geometryBox = object.geometry.boundingBox;
      if (!geometryBox) return;
      for (const x of [geometryBox.min.x, geometryBox.max.x]) {
        for (const y of [geometryBox.min.y, geometryBox.max.y]) {
          for (const z of [geometryBox.min.z, geometryBox.max.z]) {
            box.expandByPoint(corner.set(x, y, z).applyMatrix4(object.matrixWorld));
          }
        }
      }
    });
    return box;
  }

  /**
   * Aim the camera at what the step draws.
   *
   * The distance is computed from the scene rather than written down per step:
   * these scenes are the same arm and the same board at different sizes, and a
   * hand-tuned distance survives exactly until somebody moves a segment and
   * leaves half the machine out of frame.
   *
   * `poses` frames the whole motion instead of the pose the scene happens to be
   * built in. A yawing arm points at the camera at one end of its travel and
   * across it at the other, and framing the middle leaves both ends too small to
   * read. `reach` covers what cannot be posed — the views still to be visited.
   */
  #view(direction, options = {}) {
    const poses = options.poses || [];
    const saved = poses.map(pose => ({ node: pose.node, axis: pose.axis, angle: pose.node.rotation[pose.axis] }));
    const box = new THREE.Box3();
    for (const pose of poses.length > 0 ? [null, ...poses] : [null]) {
      if (pose) pose.node.rotation[pose.axis] = pose.angle;
      box.union(this.#subjectBounds());
    }
    for (const entry of saved) entry.node.rotation[entry.axis] = entry.angle;
    if (box.isEmpty()) box.setFromCenterAndSize(new THREE.Vector3(0, 0, 0.3), new THREE.Vector3(0.6, 0.6, 0.6));
    const centre = box.getCenter(new THREE.Vector3());
    const radius = Math.max(box.getSize(new THREE.Vector3()).length() / 2, 0.15) * (options.reach ?? 1);
    const fov = options.fov ?? 38;
    this.camera.fov = fov;
    this.camera.updateProjectionMatrix();
    this.viewTarget.copy(centre);
    this.viewPosition.copy(centre)
      .add(new THREE.Vector3(...direction).normalize()
        .multiplyScalar(radius / Math.sin((fov * Math.PI / 180) / 2)));
    this.#updateCamera(this.elapsedMs);
  }

  /**
   * The camera drifts a few degrees every few seconds.
   *
   * A completely still camera on a still scene reads as a broken panel; a slow
   * drift says the picture is alive without asking the operator to watch it.
   * Reduced motion gets the composed frame and no drift.
   */
  #updateCamera(elapsedMs) {
    const sway = this.reducedMotion ? 0 : Math.sin(elapsedMs / 5200) * 0.055;
    this.camera.position.copy(this.viewPosition).sub(this.viewTarget).applyAxisAngle(UP, sway).add(this.viewTarget);
    this.camera.lookAt(this.viewTarget);
    this.camera.updateMatrixWorld(true);
  }

  // -- primitives ---------------------------------------------------------

  /**
   * Every mesh in this module is built at runtime, so every geometry and material
   * has to be given back when the step changes; this is the only place that
   * happens, which is what makes the leak checkable rather than hoped for.
   */
  #part(geometry, color, options = {}) {
    const material = new THREE.MeshStandardMaterial({
      color,
      roughness: options.roughness ?? 0.7,
      metalness: options.metalness ?? 0.05,
      emissive: options.emissive ?? color,
      emissiveIntensity: options.emissiveIntensity ?? 0,
      transparent: (options.opacity ?? 1) < 1,
      opacity: options.opacity ?? 1,
    });
    const mesh = new THREE.Mesh(geometry, material);
    if (options.name) mesh.name = options.name;
    mesh.position.set(...(options.position || [0, 0, 0]));
    mesh.rotation.set(...(options.rotation || [0, 0, 0]));
    this.owned.push(geometry, material);
    return mesh;
  }

  #line(points, color, options = {}) {
    const geometry = new THREE.BufferGeometry().setFromPoints(points.map(point => new THREE.Vector3(...point)));
    const material = new THREE.LineBasicMaterial({
      color,
      transparent: (options.opacity ?? 1) < 1,
      opacity: options.opacity ?? 1,
    });
    const line = new THREE.Line(geometry, material);
    if (options.name) line.name = options.name;
    this.owned.push(geometry, material);
    return line;
  }

  #group(name, parent) {
    const group = new THREE.Group();
    group.name = name;
    if (parent) parent.add(group);
    return group;
  }

  #clearStage() {
    for (const resource of this.owned) resource.dispose?.();
    this.owned = [];
    this.stage.clear();
  }

  #ground(radius = 1.1, color = 0xe1e4e0) {
    const ground = this.#part(new THREE.CylinderGeometry(radius, radius, 0.012, 56), color, {
      name: "guide-ground", position: [0, 0, -0.006], roughness: 0.92, metalness: 0,
    });
    this.stage.add(ground);
    return ground;
  }

  // -- figures ------------------------------------------------------------

  /**
   * The robot as an owner sees it: a base on wheels, a torso, a head with two
   * camera lenses and two arms. Crude on purpose — the step is about where a
   * cable goes, and a detailed model would only make that harder to find.
   */
  #robotFigure() {
    const root = this.#group("robot");
    root.add(this.#part(new THREE.BoxGeometry(0.36, 0.30, 0.12), METAL, { position: [0, 0, 0.09] }));
    const wheelGeometry = new THREE.CylinderGeometry(0.065, 0.065, 0.04, 20);
    for (const x of [-0.19, 0.19]) {
      root.add(this.#part(wheelGeometry, DARK, { position: [x, -0.02, 0.065], rotation: [0, 0, Math.PI / 2] }));
    }
    root.add(this.#part(new THREE.BoxGeometry(0.26, 0.20, 0.30), INK, { position: [0, 0, 0.30], roughness: 0.6 }));
    const headPan = this.#group("headPan", root);
    headPan.position.set(0, 0, 0.49);
    const headTilt = this.#group("headTilt", headPan);
    headTilt.add(this.#part(new THREE.BoxGeometry(0.20, 0.17, 0.14), INK, { roughness: 0.55 }));
    const lensGeometry = new THREE.CylinderGeometry(0.026, 0.026, 0.03, 16);
    for (const x of [-0.055, 0.055]) {
      headTilt.add(this.#part(lensGeometry, BLUE, {
        position: [x, -0.095, 0.005], rotation: [Math.PI / 2, 0, 0], emissiveIntensity: 0.25,
      }));
    }
    // Two arms at rest. They are not jointed: arms that moved while nothing asked
    // them to would suggest the robot is doing something.
    for (const side of [-1, 1]) {
      const shoulder = this.#group(`arm${side < 0 ? "Left" : "Right"}`, root);
      shoulder.position.set(side * 0.17, 0.02, 0.44);
      shoulder.add(this.#part(new THREE.BoxGeometry(0.055, 0.055, 0.16), METAL, { position: [0, 0, -0.08] }));
      const elbow = this.#group("elbow", shoulder);
      elbow.position.set(0, 0, -0.16);
      elbow.add(this.#part(new THREE.BoxGeometry(0.05, 0.05, 0.14), METAL, { position: [0, 0, -0.07] }));
      elbow.add(this.#part(new THREE.BoxGeometry(0.07, 0.05, 0.05), ACCENT, { position: [0, -0.02, -0.16] }));
    }
    return { root, headPan, headTilt };
  }

  /**
   * The arm the calibration flow moves by hand: a wheeled base, a mast, shoulder,
   * upper arm, elbow, forearm, wrist and a two-finger gripper. Built once per
   * scene because a plan addresses joints by node, and a reused hierarchy is how
   * a highlight ends up on the wrong segment.
   */
  #armRig(side) {
    const root = this.#group("armRig");
    if (side === "right") root.scale.x = -1;
    root.add(this.#part(new THREE.CylinderGeometry(0.12, 0.145, 0.06, 24), DARK, { position: [0, 0, 0.03] }));
    const wheelGeometry = new THREE.CylinderGeometry(0.055, 0.055, 0.035, 18);
    const wheelLeft = this.#group("wheelLeft", root);
    wheelLeft.position.set(-0.15, 0, 0.055);
    wheelLeft.add(this.#part(wheelGeometry, DARK, { rotation: [0, 0, Math.PI / 2] }));
    const wheelRight = this.#group("wheelRight", root);
    wheelRight.position.set(0.15, 0, 0.055);
    wheelRight.add(this.#part(wheelGeometry, DARK, { rotation: [0, 0, Math.PI / 2] }));

    const shoulderPan = this.#group("shoulderPan", root);
    shoulderPan.add(this.#part(new THREE.BoxGeometry(0.12, 0.12, 0.24), METAL, { position: [0, 0, 0.17] }));
    const headPan = this.#group("headPan", shoulderPan);
    headPan.position.set(0, 0, 0.31);
    const headTilt = this.#group("headTilt", headPan);
    headTilt.position.set(0, 0, 0.07);
    headTilt.add(this.#part(new THREE.BoxGeometry(0.17, 0.15, 0.12), INK, { roughness: 0.55 }));
    headTilt.add(this.#part(new THREE.CylinderGeometry(0.022, 0.022, 0.03, 14), BLUE, {
      position: [0, -0.085, 0], rotation: [Math.PI / 2, 0, 0], emissiveIntensity: 0.25,
    }));

    const shoulderLift = this.#group("shoulderLift", shoulderPan);
    shoulderLift.position.set(0, 0, SHOULDER_HEIGHT);
    shoulderLift.add(this.#part(new THREE.BoxGeometry(0.10, 0.12, 0.12), ACCENT, { roughness: 0.6 }));
    shoulderLift.add(this.#part(new THREE.BoxGeometry(0.30, 0.075, 0.075), METAL, { position: [0.16, 0, 0] }));
    const elbowFlex = this.#group("elbowFlex", shoulderLift);
    elbowFlex.position.set(0.32, 0, 0);
    elbowFlex.add(this.#part(new THREE.BoxGeometry(0.085, 0.09, 0.09), ACCENT, { roughness: 0.6 }));
    elbowFlex.add(this.#part(new THREE.BoxGeometry(0.26, 0.062, 0.062), METAL, { position: [0.14, 0, 0] }));
    const wristFlex = this.#group("wristFlex", elbowFlex);
    wristFlex.position.set(0.28, 0, 0);
    wristFlex.add(this.#part(new THREE.BoxGeometry(0.07, 0.075, 0.075), ACCENT, { roughness: 0.6 }));
    const wristRoll = this.#group("wristRoll", wristFlex);
    wristRoll.position.set(0.05, 0, 0);
    wristRoll.add(this.#part(new THREE.BoxGeometry(0.05, 0.05, 0.05), METAL, { position: [0.03, 0, 0] }));
    const gripper = this.#group("gripper", wristRoll);
    gripper.position.set(0.06, 0, 0);
    gripper.add(this.#part(new THREE.BoxGeometry(0.04, 0.04, 0.04), DARK, { position: [0.02, 0, 0] }));
    const fingerGeometry = new THREE.BoxGeometry(0.075, 0.016, 0.035);
    const fingerTop = this.#part(fingerGeometry, ACCENT, { name: "fingerTop", position: [0.075, 0.022, 0] });
    const fingerBottom = this.#part(fingerGeometry, ACCENT, { name: "fingerBottom", position: [0.075, -0.022, 0] });
    gripper.add(fingerTop, fingerBottom);

    return {
      root,
      nodes: {
        shoulderPan, shoulderLift, elbowFlex, wristFlex, wristRoll, gripper,
        headPan, headTilt, wheelLeft, wheelRight,
      },
      fingers: { top: fingerTop, bottom: fingerBottom },
    };
  }

  /** A checkerboard, as tiles: a texture would be the one thing this bundle cannot ship. */
  #checkerboard(columns = 6, rows = 4, tile = 0.042) {
    const board = this.#group("checkerboard");
    const offsetX = -(columns - 1) * tile / 2;
    const offsetY = -(rows - 1) * tile / 2;
    board.add(this.#part(new THREE.BoxGeometry(columns * tile + 0.012, rows * tile + 0.012, 0.012), BOARD_LIGHT, {
      position: [0, 0, -0.01], roughness: 0.85, metalness: 0,
    }));
    const tileGeometry = new THREE.BoxGeometry(tile, tile, 0.004);
    for (let column = 0; column < columns; column += 1) {
      for (let row = 0; row < rows; row += 1) {
        // Only the dark squares are drawn: the light ones are the backing, which
        // halves the meshes and makes the pattern impossible to misalign.
        if ((column + row) % 2 === 0) continue;
        board.add(this.#part(tileGeometry, BOARD_DARK, {
          position: [offsetX + column * tile, offsetY + row * tile, 0.003], roughness: 0.9, metalness: 0,
        }));
      }
    }
    return board;
  }

  /** A camera body whose lens looks along its own +Z, which is what lookAt turns. */
  #cameraBody(name = "camera") {
    const camera = this.#group(name);
    camera.add(this.#part(new THREE.BoxGeometry(0.10, 0.075, 0.075), INK, { roughness: 0.55 }));
    camera.add(this.#part(new THREE.CylinderGeometry(0.028, 0.032, 0.045, 18), DARK, {
      position: [0, 0, 0.06], rotation: [Math.PI / 2, 0, 0],
    }));
    camera.add(this.#part(new THREE.CylinderGeometry(0.020, 0.020, 0.01, 18), BLUE, {
      position: [0, 0, 0.083], rotation: [Math.PI / 2, 0, 0], emissiveIntensity: 0.3,
    }));
    return camera;
  }

  /** The count the plan asked for, as a row of ticks that fills as work completes. */
  #ticks(count, options = {}) {
    const group = this.#group(options.name || "ticks");
    const total = Math.min(Math.max(0, Math.round(count)), 24);
    const entries = [];
    for (let index = 0; index < total; index += 1) {
      const tick = this.#part(new THREE.BoxGeometry(0.03, 0.03, 0.03), ACCENT_SOFT, {
        position: [(index - (total - 1) / 2) * 0.048, 0, 0], emissiveIntensity: 0.05,
      });
      group.add(tick);
      entries.push(tick);
    }
    group.position.set(...(options.position || [0, 0, 0]));
    group.rotation.set(...(options.rotation || [0, 0, 0]));
    return { group, entries, total };
  }

  #fillTicks(ticks, done) {
    ticks.entries.forEach((tick, index) => {
      tick.material.emissiveIntensity = index < done ? 0.55 : 0.05;
    });
  }

  // -- connect ------------------------------------------------------------

  #buildConnect(guide) {
    this.#ground(0.95);
    const robot = this.#robotFigure();
    this.stage.add(robot.root);

    // Only the ports the plan lists are drawn. A step that names none gets a
    // robot with nothing highlighted rather than three invented sockets.
    this.connections = [];
    const taken = new Set();
    for (const port of guide.ports) {
      const entry = this.#connectionMarker(port.port, this.#connectSlot(port.port, taken));
      robot.root.add(entry.group);
      this.connections.push(entry);
    }
    this.#report(this.connections.length > 0 ? 0 : -1, this.connections.length);
    this.#view([1.0, -1.35, 0.85], { fov: 36 });

    const hold = 2000;
    this.updater = (elapsedMs) => {
      const index = this.connections.length > 0 ? Math.floor(elapsedMs / hold) % this.connections.length : -1;
      this.connections.forEach((entry, position) => {
        if (position === index) this.#highlight(entry, elapsedMs);
        else this.#quiet(entry);
      });
      this.#report(index, this.connections.length);
      // The leads sway; the point being highlighted is the only thing that moves,
      // so the eye goes there without a caption telling it where to look.
      const drift = this.reducedMotion ? 0 : Math.sin(elapsedMs / 900) * 0.012;
      for (const entry of this.connections) entry.lead.position.z = drift;
    };
  }

  #connectSlot(port, taken) {
    const preferred = { power: 0, usb: 1, camera: 2 };
    let index = preferred[port] ?? CONNECT_SLOTS.length - 1;
    while (taken.has(index) && index + 1 < CONNECT_SLOTS.length) index += 1;
    if (taken.has(index)) index = Math.max(0, CONNECT_SLOTS.findIndex((_, position) => !taken.has(position)));
    taken.add(index);
    return CONNECT_SLOTS[index];
  }

  #connectionMarker(port, slot) {
    const group = this.#group(`port-${port || "unknown"}`);
    group.position.set(...slot.position);
    const color = port === "power" ? AMBER : port === "usb" ? BLUE : ACCENT;
    // The shape says what kind of thing plugs in: a round barrel for mains, a
    // flat block for a board cable, a lens for a camera.
    const core = port === "usb"
      ? this.#part(new THREE.BoxGeometry(0.055, 0.05, 0.045), color, { emissiveIntensity: 0.2 })
      : this.#part(new THREE.CylinderGeometry(0.026, 0.030, 0.05, 18), color, { emissiveIntensity: 0.2 });
    const halo = this.#part(new THREE.TorusGeometry(0.055, 0.007, 8, 28), color, {
      opacity: 0.2, emissiveIntensity: 0.12,
    });
    const lead = this.#line([[0, 0, 0], [
      slot.lead[0] - slot.position[0], slot.lead[1] - slot.position[1], slot.lead[2] - slot.position[2],
    ]], color, { opacity: 0.55, name: "lead" });
    group.add(core, halo, lead);
    return { group, core, halo, lead };
  }

  /** The highlighted one glows and swells; the rest stay quiet. */
  #highlight(entry, elapsedMs) {
    const pulse = this.#pulse(elapsedMs);
    entry.core.material.emissiveIntensity = 0.18 + 0.72 * pulse;
    entry.halo.material.opacity = 0.28 + 0.5 * pulse;
    entry.halo.scale.setScalar(1 + 0.28 * pulse);
  }

  #quiet(entry) {
    entry.core.material.emissiveIntensity = 0.04;
    entry.halo.material.opacity = 0.14;
    entry.halo.scale.setScalar(1);
  }

  // -- preflight ----------------------------------------------------------

  #buildPreflight(guide) {
    this.#ground(1.05);
    const robot = this.#robotFigure();
    this.stage.add(robot.root);

    // A slow sweep around the machine stands in for "look at the whole area
    // first". It is the calmest animation here on purpose: nothing is being
    // moved, and the operator is being asked to look rather than to act. The arc
    // lies on the floor, which is where an area is.
    const sweep = this.#part(new THREE.TorusGeometry(0.62, 0.009, 8, 64, Math.PI * 1.15), ACCENT_SOFT, {
      name: "preflight-sweep", position: [0, 0, 0.02], opacity: 0.55, emissiveIntensity: 0.25,
    });
    this.stage.add(sweep);

    // One marker per check the plan lists, anywhere on the ring: the check ids are
    // ids, and inventing a position per id would be inventing the plan.
    this.checks = [];
    const total = Math.min(guide.checks.length, 8);
    for (let index = 0; index < total; index += 1) {
      const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
      const post = this.#part(new THREE.BoxGeometry(0.05, 0.05, 0.14), ACCENT_SOFT, {
        position: [Math.cos(angle) * 0.62, Math.sin(angle) * 0.62, 0.07], emissiveIntensity: 0.06,
      });
      this.stage.add(post);
      this.checks.push(post);
    }
    this.#report(0, total);
    // The sweep turns through a whole circle, so it is framed through a whole
    // circle: a box measured at the pose it starts in would let the far end of
    // the arc leave the picture before the operator ever saw it arrive.
    this.#view([1.0, -1.40, 0.95], {
      fov: 36,
      poses: [0, Math.PI / 2, Math.PI, Math.PI * 1.5].map(angle => ({ node: sweep, axis: "z", angle })),
    });

    const hold = 2400;
    this.updater = (elapsedMs) => {
      if (!this.reducedMotion) sweep.rotation.z = -Math.PI * 0.55 + (elapsedMs / 6000) * Math.PI * 2;
      const index = total > 0 ? Math.floor(elapsedMs / hold) % total : -1;
      this.checks.forEach((post, position) => {
        post.material.emissiveIntensity = position === index ? 0.3 + 0.5 * this.#pulse(elapsedMs) : 0.06;
      });
      this.#report(index, total);
    };
    this.staticFrameMs = 1400;
  }

  // -- zero ---------------------------------------------------------------

  #buildZero(guide) {
    this.#ground(0.85);
    const rig = this.#armRig(guide.side);
    this.stage.add(rig.root);

    const joint = jointKey(guide.joint) || jointKey(String(guide.motor || "").replace(/^(left|right)_arm_/, ""));
    const spec = joint ? JOINT_SPECS[joint] : null;
    const node = spec ? rig.nodes[spec.node] : null;
    const indicator = node ? this.#jointIndicator(rig, node, spec, joint) : null;
    this.#view([0.05, -1.0, 0.60], {
      fov: 36,
      poses: spec && node && !spec.spin ? [
        { node, axis: spec.axis, angle: spec.sign * spec.sweep },
        { node, axis: spec.axis, angle: spec.sign * spec.mid },
      ] : [],
    });

    if (spec?.spin && node) {
      // A wheel has no middle to find: the step asks for a wheel that turns
      // freely, so it turns, slowly, and the point is only that it moves.
      this.staticFrameMs = 1200;
      this.updater = (elapsedMs) => {
        node.rotation[spec.axis] = (this.reducedMotion ? this.staticFrameMs : elapsedMs) / 1000 * 0.9;
        indicator.update(this.#pulse(elapsedMs, 900));
      };
      return;
    }

    const from = spec ? spec.sweep : -0.5;
    const mid = spec ? spec.mid : 0;
    const ease = 1400;
    const hold = 2000;
    const back = 1200;
    const cycle = ease + hold + back;
    //: A settled frame: the joint has arrived at its middle and is holding there.
    this.staticFrameMs = ease + 400;
    this.updater = (elapsedMs) => {
      const phase = elapsedMs % cycle;
      let angle = mid;
      if (phase < ease) angle = from + (mid - from) * easeInOut(progressOf(phase, 0, ease));
      else if (phase >= ease + hold) angle = mid + (from - mid) * easeInOut(progressOf(phase, ease + hold, cycle));
      if (node && spec) {
        if (spec.opening) this.#setGripper(rig, angle);
        else node.rotation[spec.axis] = spec.sign * angle;
      }
      if (indicator) indicator.update(phase < ease ? 0.85 : this.#pulse(elapsedMs, 900));
    };
  }

  #setGripper(rig, opening) {
    const gap = Math.max(GRIPPER_CLOSED / 2, opening / 2);
    rig.fingers.top.position.y = gap;
    rig.fingers.bottom.position.y = -gap;
  }

  /**
   * The ring the joint's middle is measured against, plus a needle that follows
   * where the joint actually is. Without both, "转到中间位置" is only a sentence;
   * with them it is something the operator can match on the robot in front of
   * them.
   */
  #jointIndicator(rig, node, spec, joint) {
    const holder = node.parent || rig.root;
    const radius = joint === "gripper" ? 0.075 : spec.node === "shoulderPan" ? 0.105 : 0.085;
    const group = this.#group(`${joint}-range`, holder);
    group.position.copy(node.position);
    // Put the ring's own +Z along the joint axis, so everything inside it is a
    // plain 2D angle in the plane the joint turns in.
    if (spec.axis === "y") group.rotation.x = Math.PI / 2;
    if (spec.axis === "x") group.rotation.y = Math.PI / 2;

    // A joint angle reaches the ring differently per axis: this is the one place
    // the three conventions meet, and it is written out rather than folded into a
    // sign that would be wrong for two of them.
    const angleInRing = (alpha) => (spec.axis === "y" ? -alpha : spec.axis === "x" ? alpha + Math.PI / 2 : alpha);
    const ring = this.#part(new THREE.TorusGeometry(radius, 0.006, 8, 40), ACCENT, {
      name: `${joint}-ring`, opacity: 0.5, emissiveIntensity: 0.3,
    });
    const midAngle = angleInRing(spec.sign * spec.mid);
    const tick = this.#part(new THREE.BoxGeometry(radius * 0.26, 0.014, 0.014), AMBER, {
      name: `${joint}-mid`, position: [Math.cos(midAngle) * radius * 0.82, Math.sin(midAngle) * radius * 0.82, 0],
      rotation: [0, 0, midAngle], emissiveIntensity: 0.5,
    });
    const needle = this.#part(new THREE.BoxGeometry(radius * 0.8, 0.012, 0.012), INK, {
      name: `${joint}-needle`, emissiveIntensity: 0.1,
    });
    group.add(ring, tick, needle);
    return {
      group,
      update(pulse) {
        ring.material.emissiveIntensity = 0.15 + 0.5 * pulse;
        needle.material.emissiveIntensity = 0.1 + 0.5 * pulse;
        const angle = angleInRing(node.rotation[spec.axis]);
        needle.position.set(Math.cos(angle) * radius * 0.4, Math.sin(angle) * radius * 0.4, 0);
        needle.rotation.z = angle;
      },
    };
  }

  // -- travel -------------------------------------------------------------

  #buildTravel(guide) {
    this.#ground(0.95);
    const rig = this.#armRig(guide.side);
    this.stage.add(rig.root);

    // The arc is the range the operator is asked to cover, drawn where the arm
    // actually sweeps: dots rather than a line, because a hairline arc is
    // invisible on a projector and unreadable on a laptop in daylight.
    const samples = 24;
    this.arcDots = [];
    for (let index = 0; index <= samples; index += 1) {
      const angle = TRAVEL_FROM + (index / samples) * (TRAVEL_TO - TRAVEL_FROM);
      const dot = this.#part(new THREE.SphereGeometry(0.011, 10, 8), ACCENT_SOFT, {
        position: [Math.cos(angle) * ARM_REACH, 0, SHOULDER_HEIGHT + Math.sin(angle) * ARM_REACH],
        emissiveIntensity: 0.05,
      });
      this.stage.add(dot);
      this.arcDots.push({ dot, angle });
    }
    // The two end stops, drawn where the arm can reach them rather than written
    // as text at the edge of the picture.
    for (const angle of [TRAVEL_FROM, TRAVEL_TO]) {
      this.stage.add(this.#part(new THREE.BoxGeometry(0.032, 0.032, 0.07), AMBER, {
        position: [Math.cos(angle) * ARM_REACH, 0, SHOULDER_HEIGHT + Math.sin(angle) * ARM_REACH],
        emissiveIntensity: 0.45,
      }));
    }

    // The joints the plan lists follow the sweep so the whole arm takes part; the
    // shoulder is the one being swept, so it is not also a follower.
    const follows = guide.joints
      .map(jointKey)
      .filter(name => name && name !== "shoulder_lift");
    this.#view([0.10, -1.0, 0.55], { fov: 38 });
    const centre = (TRAVEL_FROM + TRAVEL_TO) / 2;
    const half = (TRAVEL_TO - TRAVEL_FROM) / 2;
    const cycle = 7000;
    //: A quarter of the cycle is the arm at mid-sweep with half the arc covered.
    this.staticFrameMs = cycle / 4;
    this.updater = (elapsedMs) => {
      const angle = centre - half * Math.cos((elapsedMs / cycle) * Math.PI * 2);
      rig.nodes.shoulderLift.rotation.y = -angle;
      this.arcDots.forEach(({ dot, angle: at }) => {
        dot.material.emissiveIntensity = at <= angle + 1e-6 ? 0.55 : 0.05;
      });
      follows.forEach((name, index) => {
        const spec = JOINT_SPECS[name];
        const node = rig.nodes[spec.node];
        const offset = Math.sin((elapsedMs / cycle) * Math.PI * 2 + index * 0.5) * 0.16;
        if (spec.opening) this.#setGripper(rig, 0.24 + offset);
        else node.rotation[spec.axis] = spec.sign * (spec.mid + offset);
      });
      this.#report(0, 0);
    };
  }

  // -- intrinsics ---------------------------------------------------------

  #buildIntrinsics(guide) {
    // The board faces the lens along +Y and the panel camera looks in from the
    // front-left, so both the sheet and the camera holding still for it are in
    // the same picture: "hold the board in front of the camera" is a sentence
    // about two objects, and seeing only one of them explains nothing.
    const cameraPosition = [0, 0.45, 0.33];
    this.#ground(0.85);
    const body = this.#cameraBody("intrinsics-camera");
    body.position.set(...cameraPosition);
    body.rotation.x = Math.PI / 2;
    this.stage.add(body);
    this.stage.add(this.#part(new THREE.BoxGeometry(0.05, 0.05, 0.30), DARK, { position: [0, 0.45, 0.15] }));
    const cone = this.#line([
      [0, 0, 0], [0.11, -0.40, 0.11], [0, 0, 0], [-0.11, -0.40, 0.11],
      [0, 0, 0], [0.11, -0.40, -0.11], [0, 0, 0], [-0.11, -0.40, -0.11],
    ], ACCENT_SOFT, { opacity: 0.35, name: "view-cone", position: [0, 0.39, 0.33] });
    this.stage.add(cone);

    const board = this.#checkerboard();
    board.name = "intrinsics-board";
    this.stage.add(board);
    this.board = board;

    const views = guide.views > 0 ? guide.views : 12;
    this.ticks = this.#ticks(views, { position: [0, 0.52, 0.62], name: "intrinsics-views" });
    this.stage.add(this.ticks.group);
    this.#view([0.85, -1.05, 0.60], { fov: 38 });

    this.updater = (elapsedMs) => {
      const index = Math.floor(elapsedMs / INTRINSICS_VIEW_HOLD_MS);
      const view = index % views;
      const pose = INTRINSICS_POSES[index % INTRINSICS_POSES.length];
      const previous = INTRINSICS_POSES[(index - 1 + INTRINSICS_POSES.length) % INTRINSICS_POSES.length];
      const alpha = easeInOut(progressOf(elapsedMs, index * INTRINSICS_VIEW_HOLD_MS,
        index * INTRINSICS_VIEW_HOLD_MS + 500));
      const blend = (key) => previous[key] + (pose[key] - previous[key]) * alpha;
      this.board.position.set(0, 0.45 - blend("distance"), 0.33 + blend("lift"));
      // -90° is the board facing the lens; the rest is the tilt and turn the step
      // asks the operator for.
      this.board.rotation.set(-Math.PI / 2 + blend("pitch"), blend("yaw"), 0);
      this.#fillTicks(this.ticks, view);
      this.#report(view, views);
    };
    this.staticFrameMs = INTRINSICS_VIEW_HOLD_MS;
  }

  // -- handeye ------------------------------------------------------------

  #buildHandeye(guide) {
    const tableTop = 0.36;
    this.#ground(1.25);
    const table = this.#group("table");
    table.add(this.#part(new THREE.BoxGeometry(0.96, 0.66, 0.035), TABLE, {
      position: [0.12, 0, tableTop], roughness: 0.92, metalness: 0,
    }));
    for (const [x, y] of [[-0.32, -0.26], [-0.32, 0.26], [0.56, -0.26], [0.56, 0.26]]) {
      table.add(this.#part(new THREE.BoxGeometry(0.035, 0.035, tableTop), DARK, {
        position: [0.12 + x, y, tableTop / 2],
      }));
    }
    this.stage.add(table);

    // The board is the fixed thing in this step, so it is the one object given an
    // anchor: two pins through its corners and a bracket behind it. It does not
    // move in any frame, because a board that drifted would make the whole step
    // meaningless and the picture has to say so before the words do.
    const anchored = this.#group("handeye-board");
    anchored.position.set(0.30, 0.02, tableTop + 0.022);
    const board = this.#checkerboard();
    board.rotation.set(-Math.PI / 2 + 0.22, 0, 0.18);
    anchored.add(board);
    const pinGeometry = new THREE.CylinderGeometry(0.006, 0.006, 0.03, 10);
    for (const [x, y] of [[-0.11, -0.07], [0.11, 0.07]]) {
      anchored.add(this.#part(pinGeometry, AMBER, { position: [x, y, 0.026], emissiveIntensity: 0.4 }));
    }
    anchored.add(this.#part(new THREE.BoxGeometry(0.07, 0.05, 0.05), DARK, { position: [0, 0.13, -0.005] }));
    this.stage.add(anchored);
    this.anchoredBoard = anchored;

    const rig = this.#armRig(guide.side);
    rig.root.position.set(-0.16, -0.20, tableTop + 0.02);
    this.stage.add(rig.root);
    const camera = this.#cameraBody("handeye-camera");
    camera.position.set(0.10, 0, 0);
    rig.nodes.wristRoll.add(camera);

    const poses = guide.poses > 0 ? guide.poses : 8;
    this.ticks = this.#ticks(poses, {
      position: [0.30, -0.22, tableTop + 0.06], rotation: [-Math.PI / 2, 0, 0], name: "handeye-poses",
    });
    this.stage.add(this.ticks.group);
    this.#view([0.90, -1.25, 0.85], { fov: 38, reach: 1.3 });

    this.#applyHandeyePose(rig, HANDEYE_POSES[0]);
    this.updater = (elapsedMs) => {
      const index = Math.floor(elapsedMs / HANDEYE_POSE_HOLD_MS);
      const pose = HANDEYE_POSES[index % HANDEYE_POSES.length];
      const previous = HANDEYE_POSES[(index - 1 + HANDEYE_POSES.length) % HANDEYE_POSES.length];
      const alpha = easeInOut(progressOf(elapsedMs, index * HANDEYE_POSE_HOLD_MS,
        index * HANDEYE_POSE_HOLD_MS + 650));
      const blended = {};
      for (const key of ["pan", "lift", "elbow", "flex", "roll"]) {
        blended[key] = previous[key] + (pose[key] - previous[key]) * alpha;
      }
      this.#applyHandeyePose(rig, blended);
      // The arm is what moves; the camera it carries keeps looking at the board,
      // because pairing a pose with what the camera saw is the entire step.
      camera.lookAt(this.anchoredBoard.position);
      this.#fillTicks(this.ticks, index % poses);
      this.#report(index % poses, poses);
    };
    this.staticFrameMs = HANDEYE_POSE_HOLD_MS + 400;
  }

  #applyHandeyePose(rig, pose) {
    rig.nodes.shoulderPan.rotation.z = pose.pan;
    rig.nodes.shoulderLift.rotation.y = -pose.lift;
    rig.nodes.elbowFlex.rotation.y = -pose.elbow;
    rig.nodes.wristFlex.rotation.y = -pose.flex;
    rig.nodes.wristRoll.rotation.x = pose.roll;
    this.#setGripper(rig, 0.09);
  }

  // -- review -------------------------------------------------------------

  #buildReview() {
    this.#ground(0.95);
    const robot = this.#robotFigure();
    this.stage.add(robot.root);
    const halo = this.#part(new THREE.TorusGeometry(0.52, 0.008, 8, 64), ACCENT_SOFT, {
      name: "review-halo", position: [0, 0, 0.34], rotation: [0.32, 0, 0],
      opacity: 0.6, emissiveIntensity: 0.3,
    });
    this.stage.add(halo);

    // Three bars that settle once and then hold: the measurements are taken, and
    // a summary that kept moving would suggest there is still work left to do.
    const bars = [];
    for (let index = 0; index < 3; index += 1) {
      const bar = this.#part(new THREE.BoxGeometry(0.05, 0.26, 0.05), ACCENT, {
        position: [-0.11 + index * 0.11, 0.34, 0.13], emissiveIntensity: 0.25,
      });
      this.stage.add(bar);
      bars.push(bar);
    }
    this.bars = bars;
    this.#view([1.0, -1.35, 0.90], { fov: 36 });
    this.staticFrameMs = 2400;
    this.updater = (elapsedMs) => {
      halo.rotation.z = (this.reducedMotion ? this.staticFrameMs : elapsedMs) / 3200;
      bars.forEach((bar, index) => {
        const grown = easeInOut(progressOf(elapsedMs, 300 + index * 350, 1500 + index * 350));
        bar.scale.z = 0.2 + 0.8 * grown;
        bar.position.z = 0.13 + 0.13 * grown;
      });
      this.#report(0, 0);
    };
  }
}
