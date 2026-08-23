const CAMERA_STORAGE_KEY = "tangyingFleetWorldCameraV2";

function localPoint(canvas, event) {
  const rect = canvas.getBoundingClientRect?.() || {
    left: 0, top: 0, width: canvas.clientWidth || canvas.width || 1, height: canvas.clientHeight || canvas.height || 1,
  };
  return {
    x: Number(event.clientX || 0) - Number(rect.left || 0),
    y: Number(event.clientY || 0) - Number(rect.top || 0),
    width: Math.max(1, Number(rect.width || canvas.clientWidth || 1)),
    height: Math.max(1, Number(rect.height || canvas.clientHeight || 1)),
  };
}

function loadCamera(options) {
  const Camera = options.WorldCamera || globalThis.TangyingWorld?.WorldCamera;
  if (!Camera) return null;
  let stored = null;
  try {
    stored = JSON.parse(options.storage?.getItem?.(options.storageKey || CAMERA_STORAGE_KEY) || "null");
  } catch {
    stored = null;
  }
  return new Camera(stored || options.cameraState || {});
}

function sameRevisionFreshness(current, incoming) {
  if (typeof incoming !== "string") return current;
  if (current !== "FRESH" && incoming === "FRESH") return current;
  return incoming;
}

export class InteractionController {
  static bind(canvas, renderer, options = {}) {
    return new InteractionController(canvas, renderer, options);
  }

  constructor(canvas, renderer, options = {}) {
    if (!canvas?.addEventListener || !renderer) throw new TypeError("canvas and renderer are required");
    this.canvas = canvas;
    this.renderer = renderer;
    this.options = options;
    this.keyTarget = options.keyTarget || globalThis;
    this.storage = options.storage ?? (typeof window !== "undefined" ? window.localStorage : null);
    this.storageKey = options.storageKey || CAMERA_STORAGE_KEY;
    this.camera = options.camera || renderer.worldCamera || loadCamera({ ...options, storage: this.storage });
    if (!this.camera?.drag || !this.camera?.zoomAt || !this.camera?.applyPreset || !this.camera?.toJSON) {
      throw new TypeError("InteractionController requires the existing WorldCamera contract");
    }
    renderer.setWorldCamera?.(this.camera);
    renderer.worldCamera ||= this.camera;
    renderer.interactionController = this;
    this.followId = "";
    this.latestRevision = -1;
    this.acceptedFollowTargets = new Map();
    this.drag = null;
    this.disposed = false;
    this.listeners = [];
    this.#bind();
  }

  setFollow(robotId = "") {
    this.followId = typeof robotId === "string" ? robotId : "";
    this.#applyFollowTarget();
    return Boolean(this.followId);
  }

  applySnapshot(snapshot) {
    const revision = Number(snapshot?.revision);
    if (!Number.isSafeInteger(revision) || revision < 0) return false;
    if (revision < this.latestRevision) return false;
    if (revision > this.latestRevision) {
      this.latestRevision = revision;
      this.acceptedFollowTargets.clear();
      for (const [id, robot] of Object.entries(snapshot?.robots || {})) {
        if (!Array.isArray(robot?.pose) || robot.pose.length < 3
          || !robot.pose.slice(0, 3).every(Number.isFinite)) continue;
        this.acceptedFollowTargets.set(id, {
          pose: Object.freeze(robot.pose.slice(0, 3).map(Number)),
          freshness: robot.freshness,
        });
      }
    } else {
      for (const [id, robot] of Object.entries(snapshot?.robots || {})) {
        const accepted = this.acceptedFollowTargets.get(id);
        if (!accepted) continue;
        accepted.freshness = sameRevisionFreshness(accepted.freshness, robot?.freshness);
      }
    }
    return this.#applyFollowTarget();
  }

  #applyFollowTarget() {
    const accepted = this.acceptedFollowTargets.get(this.followId);
    if (!this.followId || accepted?.freshness !== "FRESH") return false;
    this.camera.target = [accepted.pose[0], accepted.pose[1], accepted.pose[2] + 0.315];
    this.renderer.syncWorldCamera?.();
    return true;
  }

  cancelFollow() { this.followId = ""; }

  #listen(target, name, handler, options) {
    target?.addEventListener?.(name, handler, options);
    this.listeners.push([target, name, handler, options]);
  }

  #bind() {
    this.onPointerDown = (event) => {
      if (event.button !== 0 && event.button !== 2) return;
      if (this.drag) return;
      this.cancelFollow();
      this.drag = {
        button: event.button, pointerId: event.pointerId,
        x: Number(event.clientX || 0), y: Number(event.clientY || 0), moved: 0,
      };
      this.canvas.classList?.add?.("dragging");
      this.canvas.setPointerCapture?.(event.pointerId);
    };
    this.onPointerMove = (event) => {
      if (!this.drag || (event.pointerId !== undefined && event.pointerId !== this.drag.pointerId)) return;
      const x = Number(event.clientX || 0);
      const y = Number(event.clientY || 0);
      const dx = x - this.drag.x;
      const dy = y - this.drag.y;
      this.drag.x = x;
      this.drag.y = y;
      this.drag.moved += Math.hypot(dx, dy);
      const point = localPoint(this.canvas, event);
      this.camera.drag({ button: this.drag.button, dx, dy, viewport: [point.width, point.height] });
      this.renderer.syncWorldCamera?.();
    };
    this.endPointer = (event, cancelled = false) => {
      if (!this.drag || (event.pointerId !== undefined && event.pointerId !== this.drag.pointerId)) return;
      const completed = this.drag;
      this.drag = null;
      this.canvas.classList?.remove?.("dragging");
      if (this.canvas.hasPointerCapture?.(completed.pointerId)) this.canvas.releasePointerCapture?.(completed.pointerId);
      if (!cancelled && completed.button === 0 && completed.moved < 5) {
        const selected = this.renderer.pick?.(Number(event.clientX || 0), Number(event.clientY || 0)) || null;
        this.renderer.select?.(selected);
      }
      this.#persist();
    };
    this.onWheel = (event) => {
      event.preventDefault?.();
      this.cancelFollow();
      const point = localPoint(this.canvas, event);
      this.camera.zoomAt(Number(event.deltaY || 0), point.x, point.y, point.width, point.height);
      this.renderer.syncWorldCamera?.();
      this.#persist();
    };
    this.onDoubleClick = (event) => {
      this.cancelFollow();
      const selected = this.renderer.pick?.(Number(event.clientX || 0), Number(event.clientY || 0));
      if (selected && this.renderer.focus?.(selected)) {
        this.renderer.select?.(selected);
        this.#persist();
      }
    };
    this.onKeyDown = (event) => {
      if (String(event.key || "").toLowerCase() !== "f") return;
      const active = this.canvas.ownerDocument?.activeElement;
      if (active && active !== this.canvas) return;
      this.cancelFollow();
      if (!this.camera.applyPreset("overview", this.renderer.latestSnapshot || {})) return;
      this.renderer.syncWorldCamera?.();
      this.#persist();
    };
    this.onContextMenu = (event) => event.preventDefault?.();

    this.#listen(this.canvas, "pointerdown", this.onPointerDown);
    this.#listen(this.canvas, "pointermove", this.onPointerMove);
    this.#listen(this.canvas, "pointerup", (event) => this.endPointer(event, false));
    this.#listen(this.canvas, "pointercancel", (event) => this.endPointer(event, true));
    this.#listen(this.canvas, "lostpointercapture", (event) => this.endPointer(event, true));
    this.#listen(this.canvas, "contextmenu", this.onContextMenu);
    this.#listen(this.canvas, "wheel", this.onWheel, { passive: false });
    this.#listen(this.canvas, "dblclick", this.onDoubleClick);
    this.#listen(this.keyTarget, "keydown", this.onKeyDown);
  }

  #persist() {
    const json = this.camera.toJSON();
    try { this.storage?.setItem?.(this.storageKey, JSON.stringify(json)); } catch { /* persistence is optional */ }
    this.options.onCameraChange?.(json);
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    const activePointerId = this.drag?.pointerId;
    this.drag = null;
    this.canvas.classList?.remove?.("dragging");
    if (activePointerId !== undefined && this.canvas.hasPointerCapture?.(activePointerId)) {
      this.canvas.releasePointerCapture?.(activePointerId);
    }
    for (const [target, name, handler, options] of this.listeners) target?.removeEventListener?.(name, handler, options);
    this.listeners = [];
    if (this.renderer.interactionController === this) this.renderer.interactionController = null;
  }
}
