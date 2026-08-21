const ZONE_IDS = Object.freeze(["left-start-zone", "handoff-zone", "right-target-zone"]);
const DEFAULT_VISIBILITY = Object.freeze({ models: true, bounds: false, labels: true, path: true });

const PALETTE = Object.freeze({
  live: 0x4fd1dd,
  custody: 0xe7a34a,
  fault: 0xef6f61,
  selected: 0xf7d58b,
  stale: 0x718995,
  source: 0x5798c8,
  target: 0x69aa83,
});

function finitePose(raw) {
  if (!Array.isArray(raw) || (raw.length !== 3 && raw.length !== 7)) return null;
  const pose = raw.map(Number);
  return pose.every(Number.isFinite) ? pose : null;
}

function fixtureBounds(entity) {
  const values = String(entity?.attributes?.bounds || "").split(",").map(Number);
  if (values.length !== 6 || !values.every(Number.isFinite)) return null;
  const minimum = values.slice(0, 3);
  const maximum = values.slice(3, 6);
  if (minimum.some((value, axis) => value >= maximum[axis])) return null;
  return { minimum, maximum };
}

function objectBounds(entity, robot = false) {
  const explicit = fixtureBounds(entity);
  if (explicit) return explicit;
  const pose = finitePose(entity?.pose);
  if (!pose) return null;
  const half = robot ? [0.25, 0.25, 0.42] : [0.055, 0.055, 0.055];
  const center = robot ? [pose[0], pose[1], pose[2] + half[2]] : pose.slice(0, 3);
  return {
    minimum: center.map((value, axis) => value - half[axis]),
    maximum: center.map((value, axis) => value + half[axis]),
  };
}

function handoffStage(snapshot) {
  const relations = snapshot?.entities?.["red-block"]?.relations || {};
  const placement = relations.inside;
  const owner = snapshot?.resources?.["block:red-block"]?.owner;
  let stage = Math.max(0, ZONE_IDS.indexOf(placement));
  if (relations.held_by === "robot-2" || owner === "robot-2") stage = Math.max(stage, 1);
  if (placement === "right-target-zone") stage = 2;
  return stage;
}

function entityPoint(entity, robot = false) {
  const bounds = fixtureBounds(entity);
  if (bounds) {
    return [
      (bounds.minimum[0] + bounds.maximum[0]) / 2,
      (bounds.minimum[1] + bounds.maximum[1]) / 2,
      bounds.maximum[2] + 0.06,
    ];
  }
  const pose = finitePose(entity?.pose) || [0, 0, 0];
  return [pose[0], pose[1], pose[2] + (robot ? 0.9 : 0.12)];
}

function custodyState(snapshot) {
  const resource = snapshot?.resources?.["block:red-block"] || null;
  const entityHolder = snapshot?.entities?.["red-block"]?.relations?.held_by || "";
  const robotHolders = Object.entries(snapshot?.robots || {})
    .filter(([, robot]) => robot?.held === "red-block")
    .map(([id]) => id)
    .sort();
  const robotEntityConflict = entityHolder
    ? robotHolders.length !== 1 || robotHolders[0] !== entityHolder
    : robotHolders.length > 0;
  const resourceConflict = Boolean(
    entityHolder && resource?.owner && resource.owner !== "environment" && resource.owner !== entityHolder,
  );
  const healthConflict = (snapshot?.health?.conflicts || []).some((value) => String(value).includes("red-block"));
  return Object.freeze({
    resourceId: resource?.resourceId || "block:red-block",
    owner: resource?.owner || "",
    fencingToken: resource?.fencingToken ?? 0,
    freshness: resource?.freshness || "UNKNOWN",
    entityHolder,
    robotHolders: Object.freeze(robotHolders),
    conflict: robotEntityConflict || resourceConflict || healthConflict,
  });
}

function setObjectColor(object, color) {
  object?.material?.color?.setHex?.(color);
}

function sameRevisionFreshness(current, incoming) {
  if (typeof incoming !== "string") return current;
  if (current !== "FRESH" && incoming === "FRESH") return current;
  return incoming;
}

export class SemanticOverlay {
  constructor(three, options = {}) {
    if (!three?.Group || !three?.Vector3) throw new TypeError("SemanticOverlay requires a Three.js namespace");
    this.THREE = three;
    this.document = options.document || globalThis.document || null;
    this.labelContainer = options.labelContainer || null;
    this.labelCullDistance = Number.isFinite(options.labelCullDistance) ? options.labelCullDistance : 8;
    this.root = new three.Group();
    this.root.name = "SemanticOverlayLayer";
    this.zoneRoot = new three.Group();
    this.zoneRoot.name = "SemanticZoneLayer";
    this.pathRoot = new three.Group();
    this.pathRoot.name = "SemanticPathLayer";
    this.boundsRoot = new three.Group();
    this.boundsRoot.name = "SemanticBoundsLayer";
    this.alertRoot = new three.Group();
    this.alertRoot.name = "SemanticAlertLayer";
    this.root.add(this.zoneRoot, this.pathRoot, this.boundsRoot, this.alertRoot);

    this.visibility = { ...DEFAULT_VISIBILITY };
    this.selectedEntityId = "";
    this.zones = new Map();
    this.bounds = new Map();
    this.labels = new Map();
    this.models = new Map();
    this.outlines = new Map();
    this.pathState = { visible: true, stage: 0, segments: [] };
    this.custodyState = custodyState({});
    const custodyElement = this.document?.createElement?.("div") || { style: {}, dataset: {}, remove() {} };
    custodyElement.className = "world-semantic-custody";
    custodyElement.dataset.resourceId = "block:red-block";
    Object.assign(custodyElement.style, {
      position: "absolute", left: "18px", bottom: "18px", pointerEvents: "none",
      display: "none",
      padding: "8px 10px", border: "1px solid #e7a34a", color: "#e7a34a",
      background: "rgba(11, 23, 32, 0.88)", font: "700 10px/1.2 ui-monospace, monospace",
    });
    this.labelContainer?.appendChild?.(custodyElement);
    this.custodyBadgeState = { element: custodyElement, text: "", visible: false };
    this.snapshotRevision = -1;
  }

  apply(snapshot, visibility = {}) {
    const revision = Number(snapshot?.revision);
    if (!snapshot || typeof snapshot !== "object") return false;
    this.selectedEntityId = typeof visibility.selectedEntityId === "string"
      ? visibility.selectedEntityId
      : this.selectedEntityId;
    this.visibility = { ...this.visibility, ...Object.fromEntries(
      Object.entries(visibility).filter(([key, value]) => key in DEFAULT_VISIBILITY && typeof value === "boolean"),
    ) };
    this.custodyState = custodyState(snapshot);
    this.#applyZones(snapshot);
    this.#applyPath(snapshot);
    this.#applyEntities(snapshot);
    this.#applyRobots(snapshot);
    this.#reconcileLabels(snapshot);
    this.snapshotRevision = Number.isSafeInteger(revision) ? revision : this.snapshotRevision;
    this.#applyVisibility();
    return true;
  }

  applyVolatileState(snapshot) {
    if (!snapshot || typeof snapshot !== "object") return false;
    const resourceFreshness = snapshot?.resources?.["block:red-block"]?.freshness;
    if (typeof resourceFreshness === "string") {
      this.custodyState = Object.freeze({
        ...this.custodyState,
        freshness: sameRevisionFreshness(this.custodyState.freshness, resourceFreshness),
      });
    }
    for (const entity of Object.values(snapshot.entities || {})) {
      const label = this.labels.get(entity?.entityId);
      if (!label || typeof entity.freshness !== "string") continue;
      label.freshness = sameRevisionFreshness(label.freshness, entity.freshness);
      label.text = `${label.baseText}${label.freshness !== "FRESH" ? ` · ${label.freshness}` : ""}${label.conflict ? " · CONFLICT" : ""}`;
      label.element.textContent = label.text;
      label.color = label.conflict ? PALETTE.fault : label.freshness !== "FRESH" ? PALETTE.stale : PALETTE.live;
    }
    for (const [id, robot] of Object.entries(snapshot.robots || {})) {
      const model = this.models.get(id);
      const outline = this.outlines.get(id);
      const label = this.labels.get(id);
      if (!model || !outline || !label) continue;
      model.freshness = sameRevisionFreshness(model.freshness, robot.freshness);
      model.desaturated = model.freshness !== "FRESH";
      model.staleObject.visible = model.desaturated;
      label.text = this.#robotLabel(id, {
        freshness: model.freshness,
        activity: model.activity,
        held: model.held,
        emergencyStopped: model.emergency,
      });
      label.element.textContent = label.text;
      label.color = outline.emergency ? PALETTE.fault : model.desaturated ? PALETTE.stale : PALETTE.live;
    }
    this.#applyVisibility();
    return true;
  }

  setVisibility(visibility = {}) {
    if (typeof visibility.selectedEntityId === "string") this.selectedEntityId = visibility.selectedEntityId;
    for (const key of Object.keys(DEFAULT_VISIBILITY)) {
      if (typeof visibility[key] === "boolean") this.visibility[key] = visibility[key];
    }
    this.#applySelection();
    this.#applyVisibility();
    return this;
  }

  setSelection(entityId = "") {
    this.selectedEntityId = typeof entityId === "string" ? entityId : "";
    this.#applySelection();
    this.#applyVisibility();
    return this;
  }

  zoneIds() { return ZONE_IDS.filter((id) => this.zones.has(id)); }
  zone(id) { return this.zones.get(id) || null; }
  bound(id) { return this.bounds.get(id) || null; }
  label(id) { return this.labels.get(id) || null; }
  model(id) { return this.models.get(id) || null; }
  outline(id) { return this.outlines.get(id) || null; }
  path() { return this.pathState; }
  custody() { return this.custodyState; }
  custodyBadge() { return this.custodyBadgeState; }

  update(camera, canvas) {
    if (!camera || !canvas) return;
    const width = Math.max(1, Number(canvas.clientWidth || canvas.width || 1));
    const height = Math.max(1, Number(canvas.clientHeight || canvas.height || 1));
    const cameraPosition = camera.getWorldPosition
      ? camera.getWorldPosition(new this.THREE.Vector3())
      : camera.position;
    for (const label of this.labels.values()) {
      const projected = label.position.clone().project(camera);
      const distance = cameraPosition?.distanceTo?.(label.position) ?? 0;
      const inFrustum = projected.z >= -1 && projected.z <= 1
        && projected.x >= -1.1 && projected.x <= 1.1
        && projected.y >= -1.1 && projected.y <= 1.1;
      const distanceVisible = label.alwaysVisible || distance <= this.labelCullDistance;
      label.visible = this.visibility.labels && inFrustum && distanceVisible;
      label.element.style.display = label.visible ? "block" : "none";
      if (!label.visible) continue;
      label.element.style.transform = `translate(${((projected.x + 1) * width) / 2}px, ${((-projected.y + 1) * height) / 2}px)`;
      label.element.style.color = `#${label.color.toString(16).padStart(6, "0")}`;
    }
  }

  #applyZones(snapshot) {
    const current = new Set();
    for (const [index, id] of ZONE_IDS.entries()) {
      const entity = snapshot?.entities?.[id];
      const pose = finitePose(entity?.pose);
      if (!entity || !pose) continue;
      current.add(id);
      let state = this.zones.get(id);
      if (!state) {
        const colors = [PALETTE.source, PALETTE.custody, PALETTE.target];
        const material = new this.THREE.MeshBasicMaterial({
          color: colors[index], transparent: true, opacity: 0.24, depthWrite: false, side: this.THREE.DoubleSide,
        });
        const object = new this.THREE.Mesh(new this.THREE.PlaneGeometry(0.24, 0.24), material);
        object.name = `${id}-zone`;
        object.raycast = () => {};
        this.zoneRoot.add(object);
        state = { id, entity, object, visible: true };
        this.zones.set(id, state);
      }
      state.entity = entity;
      state.object.position.set(pose[0], pose[1], pose[2] + 0.003);
    }
    for (const [id, state] of this.zones) {
      if (current.has(id)) continue;
      this.#removeObject(state.object);
      this.zones.delete(id);
    }
  }

  #applyPath(snapshot) {
    for (const segment of this.pathState.segments) this.#removeObject(segment.object);
    const points = ZONE_IDS.map((id) => finitePose(snapshot?.entities?.[id]?.pose));
    const stage = handoffStage(snapshot);
    const segments = [];
    if (points.every(Boolean)) {
      for (let index = 0; index < 2; index += 1) {
        const state = index < stage ? "complete" : "pending";
        const geometry = new this.THREE.BufferGeometry().setFromPoints([
          new this.THREE.Vector3(points[index][0], points[index][1], points[index][2] + 0.08),
          new this.THREE.Vector3(points[index + 1][0], points[index + 1][1], points[index + 1][2] + 0.08),
        ]);
        const object = new this.THREE.Line(geometry, new this.THREE.LineBasicMaterial({
          color: state === "complete" ? PALETTE.live : PALETTE.custody,
        }));
        object.name = `handoff-path-${index}`;
        object.raycast = () => {};
        this.pathRoot.add(object);
        segments.push({ index, state, object });
      }
    }
    this.pathState = { visible: this.visibility.path, stage, segments };
  }

  #applyEntities(snapshot) {
    const currentBounds = new Set();
    const currentOutlines = new Set();
    for (const entity of Object.values(snapshot.entities || {})) {
      if (ZONE_IDS.includes(entity.entityId)) continue;
      const bounds = objectBounds(entity, false);
      if (bounds) {
        currentBounds.add(entity.entityId);
        this.#upsertBound(entity.entityId, bounds, entity);
      }
      if (entity.entityId === "red-block") {
        currentOutlines.add(entity.entityId);
        this.#upsertOutline(entity.entityId, bounds, {
          heldBy: entity.relations?.held_by || "",
          conflict: this.custodyState.conflict,
          selected: entity.entityId === this.selectedEntityId,
        });
      }
    }
    this.#removeMissing(this.bounds, currentBounds);
    for (const [id, outline] of this.outlines) {
      if (this.models.has(id) || currentOutlines.has(id)) continue;
      this.#removeObject(outline.object);
      this.outlines.delete(id);
    }
  }

  #applyRobots(snapshot) {
    const current = new Set();
    for (const [id, robot] of Object.entries(snapshot.robots || {})) {
      const pose = finitePose(robot?.pose);
      if (!pose) continue;
      current.add(id);
      const bounds = objectBounds({ pose }, true);
      this.#upsertBound(id, bounds, { entityId: id, category: "robot", pose });
      const selected = id === this.selectedEntityId;
      this.#upsertOutline(id, bounds, {
        emergency: Boolean(robot.emergencyStopped), selected, heldBy: robot.held || "",
      });
      let model = this.models.get(id);
      if (!model) {
        const staleObject = new this.THREE.Mesh(
          new this.THREE.BoxGeometry(1, 1, 1),
          new this.THREE.MeshBasicMaterial({
            color: PALETTE.stale, transparent: true, opacity: 0.2, depthWrite: false,
            side: this.THREE.DoubleSide,
          }),
        );
        staleObject.name = `${id}-stale-veil`;
        staleObject.raycast = () => {};
        staleObject.renderOrder = 2;
        this.alertRoot.add(staleObject);
        model = { id, staleObject };
        this.models.set(id, model);
      }
      const size = bounds.maximum.map((value, axis) => value - bounds.minimum[axis] + 0.02);
      const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
      Object.assign(model, {
        pose: [...pose], freshness: robot.freshness || "UNKNOWN",
        activity: robot.activity || "", held: robot.held || "",
        desaturated: robot.freshness !== "FRESH", emergency: Boolean(robot.emergencyStopped),
      });
      model.staleObject.position.fromArray(center);
      model.staleObject.scale.fromArray(size);
      model.staleObject.visible = model.desaturated;
    }
    for (const id of [...this.models.keys()]) {
      if (current.has(id)) continue;
      this.#removeObject(this.models.get(id).staleObject);
      this.models.delete(id);
      const bound = this.bounds.get(id);
      if (bound) this.#removeObject(bound.object);
      this.bounds.delete(id);
      const outline = this.outlines.get(id);
      if (outline) this.#removeObject(outline.object);
      this.outlines.delete(id);
    }
  }

  #upsertBound(id, bounds, entity) {
    let state = this.bounds.get(id);
    const size = bounds.maximum.map((value, axis) => value - bounds.minimum[axis]);
    const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
    if (!state) {
      const box = new this.THREE.BoxGeometry(1, 1, 1);
      const object = new this.THREE.LineSegments(
        new this.THREE.EdgesGeometry(box),
        new this.THREE.LineBasicMaterial({ color: PALETTE.live, transparent: true, opacity: 0.82 }),
      );
      box.dispose();
      object.name = `${id}-bound`;
      object.raycast = () => {};
      this.boundsRoot.add(object);
      state = { id, entity, object, visible: true, selected: false };
      this.bounds.set(id, state);
    }
    state.entity = entity;
    state.object.position.fromArray(center);
    state.object.scale.fromArray(size);
    state.object.updateMatrix();
  }

  #upsertOutline(id, bounds, values) {
    if (!bounds) return;
    let state = this.outlines.get(id);
    const size = bounds.maximum.map((value, axis) => value - bounds.minimum[axis] + 0.025);
    const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
    if (!state) {
      const box = new this.THREE.BoxGeometry(1, 1, 1);
      const object = new this.THREE.LineSegments(
        new this.THREE.EdgesGeometry(box),
        new this.THREE.LineBasicMaterial({ color: PALETTE.selected, transparent: true, opacity: 0.95 }),
      );
      box.dispose();
      object.name = `${id}-outline`;
      object.raycast = () => {};
      this.alertRoot.add(object);
      state = { id, object, emergency: false, selected: false, heldBy: "", conflict: false };
      this.outlines.set(id, state);
    }
    Object.assign(state, values);
    state.object.position.fromArray(center);
    state.object.scale.fromArray(size);
    state.object.updateMatrix();
    const visible = state.emergency || state.selected || Boolean(state.heldBy) || state.conflict;
    state.object.visible = visible;
    setObjectColor(state.object, state.emergency || state.conflict ? PALETTE.fault : PALETTE.selected);
  }

  #reconcileLabels(snapshot) {
    const current = new Set();
    for (const entity of Object.values(snapshot.entities || {})) {
      const pose = finitePose(entity.pose);
      if (!pose && !fixtureBounds(entity)) continue;
      current.add(entity.entityId);
      const conflict = entity.entityId === "red-block" && this.custodyState.conflict;
      const text = `${entity.attributes?.label || entity.entityId}${entity.freshness && entity.freshness !== "FRESH" ? ` · ${entity.freshness}` : ""}${conflict ? " · CONFLICT" : ""}`;
      this.#upsertLabel(entity.entityId, text, entityPoint(entity), {
        category: entity.category,
        baseText: entity.attributes?.label || entity.entityId,
        freshness: entity.freshness || "UNKNOWN",
        conflict,
        alwaysVisible: entity.entityId === this.selectedEntityId || entity.entityId === "red-block",
        color: conflict ? PALETTE.fault : entity.freshness !== "FRESH" ? PALETTE.stale : PALETTE.live,
      });
    }
    for (const [id, robot] of Object.entries(snapshot.robots || {})) {
      if (!finitePose(robot.pose)) continue;
      current.add(id);
      this.#upsertLabel(id, this.#robotLabel(id, robot), entityPoint(robot, true), {
        category: "robot", alwaysVisible: true,
        color: robot.emergencyStopped ? PALETTE.fault : robot.freshness !== "FRESH" ? PALETTE.stale : PALETTE.live,
      });
    }
    for (const [id, label] of this.labels) {
      if (current.has(id)) continue;
      label.element.remove?.();
      this.labels.delete(id);
    }
  }

  #robotLabel(id, robot) {
    const held = robot.held ? ` · HELD ${robot.held}` : "";
    const emergency = robot.emergencyStopped ? " · EMERGENCY" : "";
    return `${id} · ${robot.freshness || "UNKNOWN"} · ${robot.activity || "IDLE"}${held}${emergency}`;
  }

  #upsertLabel(id, text, point, values) {
    let label = this.labels.get(id);
    if (!label) {
      const element = this.document?.createElement?.("span") || { style: {}, dataset: {}, remove() {} };
      element.className = "world-semantic-label";
      element.dataset.entityId = id;
      Object.assign(element.style, {
        position: "absolute", left: "0", top: "0", pointerEvents: "none",
        font: "700 10px/1.2 ui-monospace, monospace", whiteSpace: "nowrap",
      });
      this.labelContainer?.appendChild?.(element);
      label = { id, element, position: new this.THREE.Vector3(), text: "", visible: true };
      this.labels.set(id, label);
    }
    Object.assign(label, values);
    label.text = text;
    label.position.fromArray(point);
    label.element.textContent = text;
  }

  #applySelection() {
    for (const [id, bound] of this.bounds) {
      bound.selected = id === this.selectedEntityId;
      setObjectColor(bound.object, bound.selected ? PALETTE.selected : PALETTE.live);
    }
    for (const [id, outline] of this.outlines) {
      outline.selected = id === this.selectedEntityId;
      outline.object.visible = outline.selected || outline.emergency || Boolean(outline.heldBy) || outline.conflict;
      setObjectColor(outline.object, outline.emergency || outline.conflict ? PALETTE.fault : PALETTE.selected);
    }
    for (const [id, label] of this.labels) label.alwaysVisible = label.category === "robot" || id === "red-block" || id === this.selectedEntityId;
  }

  #applyVisibility() {
    this.#applySelection();
    this.pathRoot.visible = this.visibility.path;
    this.zoneRoot.visible = this.visibility.path;
    this.boundsRoot.visible = true;
    this.pathState.visible = this.visibility.path;
    for (const zone of this.zones.values()) {
      zone.visible = this.visibility.path;
      zone.object.visible = zone.visible;
    }
    for (const bound of this.bounds.values()) {
      bound.visible = this.visibility.bounds || bound.selected;
      bound.object.visible = bound.visible;
    }
    for (const model of this.models.values()) model.staleObject.visible = this.visibility.models && model.desaturated;
    for (const label of this.labels.values()) {
      label.visible = this.visibility.labels;
      label.element.style.display = label.visible ? "block" : "none";
    }
    const owner = this.custodyState.owner || "—";
    const conflict = this.custodyState.conflict ? " · CONFLICT" : "";
    this.custodyBadgeState.text = `CUSTODY · owner ${owner} · token ${this.custodyState.fencingToken} · ${this.custodyState.freshness}${conflict}`;
    this.custodyBadgeState.element.textContent = this.custodyBadgeState.text;
    this.custodyBadgeState.visible = this.visibility.labels && Boolean(this.custodyState.owner);
    this.custodyBadgeState.element.style.display = this.custodyBadgeState.visible ? "block" : "none";
  }

  #removeMissing(map, current) {
    for (const [id, state] of map) {
      if (current.has(id) || this.models.has(id)) continue;
      this.#removeObject(state.object);
      map.delete(id);
    }
  }

  #removeObject(object) {
    object?.parent?.remove(object);
    object?.geometry?.dispose?.();
    if (Array.isArray(object?.material)) object.material.forEach((material) => material.dispose?.());
    else object?.material?.dispose?.();
  }

  dispose() {
    for (const state of [...this.zones.values(), ...this.bounds.values(), ...this.outlines.values()]) this.#removeObject(state.object);
    for (const model of this.models.values()) this.#removeObject(model.staleObject);
    for (const segment of this.pathState.segments) this.#removeObject(segment.object);
    for (const label of this.labels.values()) label.element.remove?.();
    this.custodyBadgeState.element.remove?.();
    this.zones.clear();
    this.bounds.clear();
    this.labels.clear();
    this.models.clear();
    this.outlines.clear();
    this.root.removeFromParent();
  }
}
