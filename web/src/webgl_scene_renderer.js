import * as THREE from "three";

import { InteractionController } from "./interaction_controller.js";
import { RobotModelInstance } from "./robot_model.js";
import { SemanticOverlay } from "./semantic_overlay.js";

function rendererError(code, detail) {
  const error = new Error(detail ? `${code}: ${detail}` : code);
  error.code = code;
  return error;
}

function finiteEntityPose(raw) {
  if (!Array.isArray(raw) || (raw.length !== 3 && raw.length !== 7)) return null;
  const pose = raw.map(Number);
  if (!pose.every(Number.isFinite)) return null;
  if (pose.length === 7 && Math.hypot(pose[3], pose[4], pose[5], pose[6]) <= 1e-12) return null;
  return pose;
}

function applyPose(object, pose) {
  object.position.fromArray(pose.slice(0, 3));
  if (pose.length >= 7) {
    object.quaternion.set(pose[4], pose[5], pose[6], pose[3]).normalize();
  } else {
    object.quaternion.identity();
  }
}

function isStaticEntity(entity) {
  return entity?.attributes?.static === "true" || Boolean(entity?.attributes?.bounds);
}

function finiteBounds(entity) {
  const values = String(entity?.attributes?.bounds || "").split(",").map(Number);
  if (values.length !== 6 || !values.every(Number.isFinite)) return null;
  const minimum = values.slice(0, 3);
  const maximum = values.slice(3, 6);
  return minimum.some((value, axis) => value >= maximum[axis]) ? null : { minimum, maximum };
}

function colorForEntity(entity) {
  if (entity?.attributes?.color === "red" || entity?.category === "block") return 0xd94b45;
  if (entity?.attributes?.color === "blue") return 0x4c86bd;
  if (entity?.attributes?.color === "green") return 0x5a9c74;
  return 0x4fd1dd;
}

function defaultRendererFactory(options) {
  return new THREE.WebGLRenderer(options);
}

const WORLD_CAMERA_VERTICAL_FOV = 2 * Math.atan(1 / 1.8) * 180 / Math.PI;

function sameRevisionFreshness(current, incoming) {
  if (typeof incoming !== "string") return current;
  if (current !== "FRESH" && incoming === "FRESH") return current;
  return incoming;
}

export class WebGLSceneRenderer {
  static create(canvas, options = {}) {
    return new WebGLSceneRenderer(canvas, options);
  }

  static bindInteraction(canvas, renderer, options = {}) {
    return InteractionController.bind(canvas, renderer, options);
  }

  constructor(canvas, options = {}) {
    if (!canvas?.addEventListener) throw rendererError("WEBGL_CANVAS_INVALID");
    this.canvas = canvas;
    this.options = options;
    this.now = options.now || (() => performance.now());
    this.pixelRatioCap = Number.isFinite(options.pixelRatioCap) ? options.pixelRatioCap : 2;
    this.scene = new THREE.Scene();
    this.scene.name = "TangyingWorld";
    this.scene.up.set(0, 0, 1);
    this.scene.background = new THREE.Color(options.background ?? 0x07131b);
    this.camera = new THREE.PerspectiveCamera(WORLD_CAMERA_VERTICAL_FOV, 1, 0.01, 100);
    this.camera.name = "WorldCamera";
    this.camera.up.set(0, 0, 1);
    this.camera.position.set(4.4, -5.2, 3.8);
    this.focusTarget = new THREE.Vector3(0.5, 0, 0.5);
    this.camera.lookAt(this.focusTarget);

    this.staticSceneRoot = new THREE.Group();
    this.staticSceneRoot.name = "StaticSceneLayer";
    this.robotRoot = new THREE.Group();
    this.robotRoot.name = "RobotModelLayer";
    this.dynamicObjectRoot = new THREE.Group();
    this.dynamicObjectRoot.name = "DynamicObjectLayer";
    this.scene.add(this.staticSceneRoot, this.robotRoot, this.dynamicObjectRoot);
    this.semanticOverlay = options.semanticOverlay || new SemanticOverlay(THREE, {
      document: options.document,
      labelContainer: options.labelContainer,
      labelCullDistance: options.labelCullDistance,
    });
    this.scene.add(this.semanticOverlay.root);
    this.scene.add(new THREE.HemisphereLight(0xd5e8ed, 0x27323a, 1.6));
    const keyLight = new THREE.DirectionalLight(0xfff4dc, 2.1);
    keyLight.name = "KitchenKeyLight";
    keyLight.position.set(-3, -4, 7);
    keyLight.castShadow = true;
    this.scene.add(keyLight);

    const factory = options.rendererFactory || defaultRendererFactory;
    this.gpuRenderer = factory({
      canvas,
      antialias: true,
      alpha: false,
      powerPreference: "high-performance",
    });
    this.gpuRenderer.shadowMap.enabled = true;
    this.gpuRenderer.shadowMap.type = THREE.PCFSoftShadowMap;
    if ("outputColorSpace" in this.gpuRenderer) this.gpuRenderer.outputColorSpace = THREE.SRGBColorSpace;
    this.configuredDevicePixelRatio = Number.isFinite(options.devicePixelRatio)
      ? options.devicePixelRatio
      : null;
    this.logicalWidth = 0;
    this.logicalHeight = 0;
    this.renderPixelRatio = 0;

    this.raycaster = new THREE.Raycaster();
    this.pointer = new THREE.Vector2();
    this.bundle = null;
    this.staticScene = null;
    this.robotInstances = new Map();
    this.dynamicObjects = new Map();
    this.dynamicGeometry = new THREE.BoxGeometry(0.09, 0.09, 0.09);
    this.dynamicMaterials = new Map();
    this.visibility = { models: true, bounds: false, labels: true, path: true };
    this.worldCamera = options.worldCamera || null;
    this.interactionController = null;
    this.latestSnapshot = null;
    this.selectedEntityId = "";
    this.revision = -1;
    this.disposed = false;
    this.frameRequest = null;
    this.status = Object.freeze({ state: "INITIALIZING", code: "WEBGL_INITIALIZING", revision: -1 });

    this.onContextLost = (event) => {
      event.preventDefault?.();
      this.#setStatus("DEGRADED", "WEBGL_CONTEXT_LOST");
    };
    this.onContextRestored = () => {
      this.#setStatus(this.bundle ? "READY" : "INITIALIZING", this.bundle ? "WEBGL_READY" : "WEBGL_INITIALIZING");
    };
    canvas.addEventListener("webglcontextlost", this.onContextLost, false);
    canvas.addEventListener("webglcontextrestored", this.onContextRestored, false);
    this.#resize();
    if (options.bundle) this.setBundle(options.bundle);
    if (options.autoStart !== false && typeof globalThis.requestAnimationFrame === "function") this.#scheduleFrame();
  }

  setBundle(bundle) {
    if (this.disposed) throw rendererError("WEBGL_RENDERER_DISPOSED");
    if (!bundle?.scene?.clone || !bundle?.robotTemplate?.clone || !bundle?.binding) {
      throw rendererError("VISUAL_BUNDLE_INVALID");
    }
    for (const instance of this.robotInstances.values()) instance.dispose();
    this.robotInstances.clear();
    this.robotRoot.clear();
    this.staticSceneRoot.clear();
    this.bundle = bundle;
    this.staticScene = bundle.scene.clone(true);
    this.staticScene.name ||= "RoboCasaScene";
    this.staticScene.traverse((object) => {
      if (object.isMesh) {
        object.receiveShadow = true;
        object.castShadow = false;
      }
    });
    this.staticSceneRoot.add(this.staticScene);
    this.#setStatus("READY", "WEBGL_READY");
    return this;
  }

  setVisibility(visibility = {}) {
    for (const key of Object.keys(this.visibility)) {
      if (typeof visibility[key] === "boolean") this.visibility[key] = visibility[key];
    }
    const modelsVisible = this.visibility.models;
    this.staticSceneRoot.visible = modelsVisible;
    this.robotRoot.visible = modelsVisible;
    this.dynamicObjectRoot.visible = modelsVisible;
    this.semanticOverlay.setVisibility({ ...this.visibility, selectedEntityId: this.selectedEntityId });
    return this;
  }

  setWorldCamera(camera) {
    if (!camera?.basis || !camera?.toJSON) throw rendererError("WORLD_CAMERA_INVALID");
    this.worldCamera = camera;
    this.syncWorldCamera();
    return this;
  }

  syncWorldCamera() {
    if (!this.worldCamera) return false;
    const basis = this.worldCamera.basis();
    if (!Array.isArray(basis?.position) || basis.position.length !== 3 || !basis.position.every(Number.isFinite)
      || !Array.isArray(this.worldCamera.target) || this.worldCamera.target.length !== 3
      || !this.worldCamera.target.every(Number.isFinite)) return false;
    this.camera.position.fromArray(basis.position);
    this.focusTarget.fromArray(this.worldCamera.target);
    this.camera.lookAt(this.focusTarget);
    this.camera.updateMatrixWorld(true);
    return true;
  }

  select(entity) {
    this.selectedEntityId = entity?.entityId || entity?.robotId || "";
    this.semanticOverlay.setSelection(this.selectedEntityId);
    return this.selectedEntityId;
  }

  render(snapshot, nowMs = this.now()) {
    if (this.disposed || !this.bundle) return false;
    const revision = Number(snapshot?.revision);
    if (!Number.isSafeInteger(revision) || revision < 0 || revision < this.revision) return false;
    if (revision === this.revision) {
      // Projector freshness is time-derived and may change without a new fact
      // revision. Accept only volatile fields; a transition away from FRESH
      // freezes interpolation, while repeated FRESH projections leave timing.
      this.#applyVolatileState(snapshot, nowMs);
      this.semanticOverlay.applyVolatileState(snapshot);
      this.#applyFollowSnapshot(snapshot, true);
      this.#setStatus("READY", "WEBGL_READY");
      this.#draw(nowMs);
      return true;
    }
    if (!this.#validateSnapshot(snapshot)) {
      this.#setStatus("DEGRADED", "WEBGL_SNAPSHOT_INVALID");
      return false;
    }
    this.#applyRobots(snapshot.robots || {}, nowMs);
    this.#applyEntities(snapshot.entities || {}, new Set(Object.keys(snapshot.robots || {})));
    this.latestSnapshot = snapshot;
    this.semanticOverlay.apply(snapshot, { ...this.visibility, selectedEntityId: this.selectedEntityId });
    this.#applyFollowSnapshot(snapshot, false);
    this.revision = revision;
    this.#setStatus("READY", "WEBGL_READY");
    this.#draw(nowMs);
    return true;
  }

  #validateSnapshot(snapshot) {
    const robotIds = new Set(Object.keys(snapshot.robots || {}));
    for (const [id, robot] of Object.entries(snapshot.robots || {})) {
      if (!robot || (robot.robotId && robot.robotId !== id) || !finiteEntityPose(robot.pose)) return false;
      for (const [name, value] of Object.entries(robot.state || {})) {
        if (name.startsWith("joint.") && !Number.isFinite(value)) return false;
      }
    }
    for (const entity of Object.values(snapshot.entities || {})) {
      if (typeof entity?.entityId !== "string" || !entity.entityId
        || (!isStaticEntity(entity) && entity.category !== "robot"
          && !robotIds.has(entity.entityId) && !finiteEntityPose(entity.pose))) return false;
    }
    return true;
  }

  #applyRobots(robots, nowMs) {
    const current = new Set(Object.keys(robots));
    for (const [id, instance] of this.robotInstances) {
      if (!current.has(id)) {
        this.robotRoot.remove(instance.root);
        instance.dispose();
        this.robotInstances.delete(id);
      }
    }
    for (const [id, robot] of Object.entries(robots)) {
      let instance = this.robotInstances.get(id);
      if (!instance) {
        instance = RobotModelInstance.fromTemplate(this.bundle.robotTemplate, this.bundle.binding, id, {
          interpolationMs: this.options.interpolationMs,
        });
        this.robotInstances.set(id, instance);
        this.robotRoot.add(instance.root);
      }
      instance.applyState({ ...robot, robotId: id }, nowMs);
      instance.root.userData.pickEntity = {
        entityId: id,
        robotId: id,
        category: "robot",
        pose: [...robot.pose],
        freshness: robot.freshness,
      };
    }
  }

  #applyVolatileState(snapshot, nowMs) {
    for (const [id, instance] of this.robotInstances) {
      const robot = snapshot?.robots?.[id] || {};
      instance.applyVolatileState({
        robotId: id,
        freshness: robot.freshness,
      }, nowMs);
      const picked = instance.root.userData.pickEntity;
      if (picked) picked.freshness = instance.freshness;
    }
    for (const entity of Object.values(snapshot?.entities || {})) {
      if (typeof entity?.freshness !== "string") continue;
      const object = this.dynamicObjects.get(entity.entityId)
        || this.staticScene?.getObjectByName(entity.entityId);
      if (object?.userData?.pickEntity) {
        object.userData.pickEntity.freshness = sameRevisionFreshness(
          object.userData.pickEntity.freshness,
          entity.freshness,
        );
      }
    }
  }

  #applyEntities(entities, robotIds) {
    const dynamic = Object.values(entities).filter((entity) => !isStaticEntity(entity)
      && entity.category !== "robot" && !robotIds.has(entity.entityId));
    const current = new Set(dynamic.map((entity) => entity.entityId));
    for (const [id, object] of this.dynamicObjects) {
      if (!current.has(id)) {
        this.dynamicObjectRoot.remove(object);
        this.dynamicObjects.delete(id);
      }
    }
    for (const entity of dynamic) {
      let object = this.dynamicObjects.get(entity.entityId);
      if (!object) {
        const color = colorForEntity(entity);
        let material = this.dynamicMaterials.get(color);
        if (!material) {
          material = new THREE.MeshStandardMaterial({ color, roughness: 0.58, metalness: 0.05 });
          this.dynamicMaterials.set(color, material);
        }
        object = new THREE.Mesh(this.dynamicGeometry, material);
        object.name = entity.entityId;
        object.castShadow = true;
        object.receiveShadow = true;
        this.dynamicObjects.set(entity.entityId, object);
        this.dynamicObjectRoot.add(object);
      }
      applyPose(object, finiteEntityPose(entity.pose));
      object.userData.pickEntity = {
        entityId: entity.entityId,
        category: entity.category,
        attributes: { ...(entity.attributes || {}) },
        pose: [...entity.pose],
        relations: { ...(entity.relations || {}) },
        freshness: entity.freshness,
      };
    }
    for (const entity of Object.values(entities).filter(isStaticEntity)) {
      const node = this.staticScene?.getObjectByName(entity.entityId);
      if (node) node.userData.pickEntity = {
        entityId: entity.entityId,
        category: entity.category,
        attributes: { ...(entity.attributes || {}) },
        pose: Array.isArray(entity.pose) ? [...entity.pose] : undefined,
        freshness: entity.freshness,
      };
    }
  }

  #resize() {
    const width = Math.max(1, Number(this.canvas.clientWidth || this.canvas.width || 1));
    const height = Math.max(1, Number(this.canvas.clientHeight || this.canvas.height || 1));
    const devicePixelRatio = this.configuredDevicePixelRatio ?? (globalThis.devicePixelRatio || 1);
    const pixelRatio = Math.max(1, Math.min(devicePixelRatio, this.pixelRatioCap));
    const sizeChanged = width !== this.logicalWidth || height !== this.logicalHeight;
    const ratioChanged = pixelRatio !== this.renderPixelRatio;
    if (ratioChanged) {
      this.gpuRenderer.setPixelRatio(pixelRatio);
      this.renderPixelRatio = pixelRatio;
    }
    if (sizeChanged) {
      this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix();
      this.logicalWidth = width;
      this.logicalHeight = height;
    }
    if (sizeChanged || ratioChanged) this.gpuRenderer.setSize(width, height, false);
  }

  #draw(nowMs) {
    if (this.disposed || this.status.code === "WEBGL_CONTEXT_LOST") return;
    this.#resize();
    for (const instance of this.robotInstances.values()) instance.sample(nowMs);
    this.syncWorldCamera();
    this.camera.lookAt(this.focusTarget);
    this.scene.updateMatrixWorld(true);
    this.semanticOverlay.update(this.camera, this.canvas);
    this.gpuRenderer.render(this.scene, this.camera);
  }

  #applyFollowSnapshot(snapshot, volatileOnly) {
    if (!this.interactionController) return;
    if (!volatileOnly) {
      this.interactionController.applySnapshot(snapshot);
      return;
    }
    const robots = {};
    for (const [id, instance] of this.robotInstances) {
      const pose = instance.root.userData.pickEntity?.pose;
      if (!Array.isArray(pose)) continue;
      robots[id] = { pose, freshness: instance.freshness };
    }
    this.interactionController.applySnapshot({ revision: this.revision, robots });
  }

  #scheduleFrame() {
    this.frameRequest = globalThis.requestAnimationFrame((timestamp) => {
      this.frameRequest = null;
      this.#draw(timestamp);
      if (!this.disposed) this.#scheduleFrame();
    });
  }

  pick(x, y) {
    if (this.disposed || !Number.isFinite(x) || !Number.isFinite(y)) return null;
    const rect = this.canvas.getBoundingClientRect?.() || {
      left: 0,
      top: 0,
      width: this.canvas.clientWidth || 1,
      height: this.canvas.clientHeight || 1,
    };
    const width = Math.max(1, Number(rect.width || this.canvas.clientWidth || 1));
    const height = Math.max(1, Number(rect.height || this.canvas.clientHeight || 1));
    const localX = (x - Number(rect.left || 0)) / width;
    const localY = (y - Number(rect.top || 0)) / height;
    if (localX < 0 || localX > 1 || localY < 0 || localY > 1) return null;
    this.pointer.set(localX * 2 - 1, 1 - localY * 2);
    this.camera.updateMatrixWorld(true);
    this.raycaster.setFromCamera(this.pointer, this.camera);
    this.scene.updateMatrixWorld(true);
    const roots = [this.dynamicObjectRoot, this.robotRoot, this.staticSceneRoot];
    for (const hit of this.raycaster.intersectObjects(roots, true)) {
      let object = hit.object;
      while (object) {
        if (object.userData?.pickEntity) return { ...object.userData.pickEntity };
        object = object.parent;
      }
    }
    return null;
  }

  focus(entity) {
    if (this.disposed || !entity) return false;
    if (this.worldCamera) {
      const bounds = finiteBounds(entity);
      const pose = finiteEntityPose(entity.pose);
      if (bounds) {
        this.worldCamera.target = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
        const extent = Math.max(...bounds.maximum.map((value, axis) => value - bounds.minimum[axis]));
        this.worldCamera.distance = Math.min(7, Math.max(1.4, extent * 2.6));
      } else if (pose) {
        this.worldCamera.target = [pose[0], pose[1], pose[2] + 0.2];
        this.worldCamera.distance = entity.category === "robot" ? 2 : 1.35;
      } else {
        return false;
      }
      this.syncWorldCamera();
      return true;
    }
    const id = entity.entityId || entity.robotId;
    const object = this.dynamicObjects.get(id) || this.robotInstances.get(id)?.root
      || this.staticScene?.getObjectByName(id);
    let center;
    let distance;
    if (object) {
      const bounds = new THREE.Box3().setFromObject(object);
      if (bounds.isEmpty()) return false;
      center = bounds.getCenter(new THREE.Vector3());
      distance = Math.max(0.8, bounds.getSize(new THREE.Vector3()).length() * 2.2);
    } else {
      const pose = finiteEntityPose(entity.pose);
      if (!pose) return false;
      center = new THREE.Vector3().fromArray(pose);
      distance = entity.category === "robot" ? 2 : 1.35;
    }
    const direction = this.camera.position.clone().sub(this.focusTarget);
    if (direction.lengthSq() <= 1e-12) direction.set(1, -1, 0.7);
    direction.normalize();
    this.focusTarget.copy(center);
    this.camera.position.copy(center).addScaledVector(direction, distance);
    this.camera.lookAt(center);
    this.camera.updateMatrixWorld(true);
    return true;
  }

  #setStatus(state, code) {
    this.status = Object.freeze({ state, code, revision: this.revision });
  }

  dispose() {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frameRequest !== null && typeof globalThis.cancelAnimationFrame === "function") {
      globalThis.cancelAnimationFrame(this.frameRequest);
      this.frameRequest = null;
    }
    this.canvas.removeEventListener("webglcontextlost", this.onContextLost, false);
    this.canvas.removeEventListener("webglcontextrestored", this.onContextRestored, false);
    for (const instance of this.robotInstances.values()) instance.dispose();
    this.robotInstances.clear();
    this.dynamicObjects.clear();
    this.dynamicGeometry.dispose();
    for (const material of this.dynamicMaterials.values()) material.dispose();
    this.dynamicMaterials.clear();
    this.interactionController?.dispose?.();
    this.semanticOverlay.dispose();
    this.staticSceneRoot.clear();
    this.robotRoot.clear();
    this.dynamicObjectRoot.clear();
    this.gpuRenderer.dispose();
    this.#setStatus("DISPOSED", "WEBGL_DISPOSED");
  }
}
