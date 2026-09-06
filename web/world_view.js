(function installTangyingWorld(root) {
  "use strict";

  const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
  const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const scale3 = (v, amount) => [v[0] * amount, v[1] * amount, v[2] * amount];
  const dot3 = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross3 = (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
  const normalize3 = (v) => {
    const length = Math.hypot(v[0], v[1], v[2]) || 1;
    return scale3(v, 1 / length);
  };

  function fixtureBounds(entity) {
    const values = String(entity?.attributes?.bounds || "").split(",").map(Number);
    if (values.length !== 6 || !values.every(Number.isFinite)) return null;
    const minimum = values.slice(0, 3);
    const maximum = values.slice(3, 6);
    if (minimum.some((value, index) => value >= maximum[index])) return null;
    return { minimum, maximum };
  }

  function handoffPath(snapshot) {
    const entities = snapshot?.entities || {};
    const ids = ["left-start-zone", "handoff-zone", "right-target-zone"];
    const points = ids.map((id) => entities[id]).filter(Boolean);
    const relations = entities["red-block"]?.relations || {};
    const placement = relations.inside;
    const owner = snapshot?.resources?.["block:red-block"]?.owner;
    let stage = Math.max(0, ids.indexOf(placement));
    if (relations.held_by === "robot-2" || owner === "robot-2") stage = Math.max(stage, 1);
    if (placement === "right-target-zone") stage = 2;
    return { points, stage };
  }

  function sceneBounds(snapshot) {
    const fixtures = Object.values(snapshot?.entities || {}).map(fixtureBounds).filter(Boolean);
    if (!fixtures.length) return { minimum: [-1.5, -2, 0], maximum: [3.5, 2, 2] };
    return {
      minimum: [0, 1, 2].map((axis) => Math.min(...fixtures.map((bounds) => bounds.minimum[axis]))),
      maximum: [0, 1, 2].map((axis) => Math.max(...fixtures.map((bounds) => bounds.maximum[axis]))),
    };
  }

  function fixtureFaces(bounds, cameraPosition) {
    const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
    return [
      cameraPosition[0] < center[0] ? [0, 3, 7, 4] : [1, 2, 6, 5],
      cameraPosition[1] < center[1] ? [0, 1, 5, 4] : [3, 2, 6, 7],
      cameraPosition[2] < center[2] ? [0, 1, 2, 3] : [4, 5, 6, 7],
    ];
  }

  class WorldCamera {
    constructor(options = {}) {
      this.yaw = Number.isFinite(options.yaw) ? options.yaw : 0.7;
      this.pitch = clamp(Number.isFinite(options.pitch) ? options.pitch : 0.72, 0.12, 1.42);
      this.distance = clamp(Number.isFinite(options.distance) ? options.distance : 3.2, 0.45, 20);
      const target = Array.isArray(options.target) ? options.target.slice(0, 3).map(Number) : [0.3, 0.4, 0];
      this.target = target.length === 3 && target.every(Number.isFinite) ? target : [0.3, 0.4, 0];
    }

    basis() {
      const cp = Math.cos(this.pitch);
      const position = [
        this.target[0] + this.distance * cp * Math.sin(this.yaw),
        this.target[1] + this.distance * cp * Math.cos(this.yaw),
        this.target[2] + this.distance * Math.sin(this.pitch),
      ];
      const forward = normalize3([
        this.target[0] - position[0],
        this.target[1] - position[1],
        this.target[2] - position[2],
      ]);
      const right = normalize3(cross3(forward, [0, 0, 1]));
      const up = normalize3(cross3(right, forward));
      return { position, forward, right, up };
    }

    drag({ button, dx, dy, viewport }) {
      if (button === 2) {
        this.yaw += dx * 0.008;
        this.pitch = clamp(this.pitch + dy * 0.008, 0.12, 1.42);
        return;
      }
      if (button !== 0) return;
      const height = Math.max(1, viewport?.[1] || 1);
      const { right, forward } = this.basis();
      const groundForward = normalize3([forward[0], forward[1], 0]);
      const unitsPerPixel = (2 * this.distance * Math.tan(Math.PI / 8)) / height;
      const horizontal = scale3(right, -dx * unitsPerPixel);
      const vertical = scale3(groundForward, dy * unitsPerPixel);
      this.target = add3(this.target, add3(horizontal, vertical));
    }

    unprojectToGround(pointerX, pointerY, width, height, groundZ = 0) {
      const { position, forward, right, up } = this.basis();
      const focal = Math.max(1, height) * 0.9;
      const x = (pointerX - width / 2) / focal;
      const y = -(pointerY - height / 2) / focal;
      const ray = normalize3(add3(forward, add3(scale3(right, x), scale3(up, y))));
      if (Math.abs(ray[2]) < 1e-9) return [this.target[0], this.target[1], groundZ];
      const distance = (groundZ - position[2]) / ray[2];
      if (!Number.isFinite(distance) || distance < 0) return [this.target[0], this.target[1], groundZ];
      return add3(position, scale3(ray, distance));
    }

    zoomAt(deltaY, pointerX, pointerY, width, height) {
      const before = this.unprojectToGround(pointerX, pointerY, width, height);
      this.distance = clamp(this.distance * Math.exp(deltaY * 0.0012), 0.45, 20);
      const after = this.unprojectToGround(pointerX, pointerY, width, height);
      this.target[0] += before[0] - after[0];
      this.target[1] += before[1] - after[1];
    }

    applyPreset(name, snapshot = {}) {
      if (name === "robot-1" || name === "robot-2") {
        const robot = snapshot?.robots?.[name];
        if (!robot?.pose) return false;
        this.target = [Number(robot.pose[0]), Number(robot.pose[1]), Number(robot.pose[2] || 0) + 0.315];
        this.yaw = name === "robot-1" ? 2.78 : -2.78;
        this.pitch = 0.52;
        this.distance = 2.1;
        return true;
      }
      const bounds = sceneBounds(snapshot);
      const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
      const span = Math.max(bounds.maximum[0] - bounds.minimum[0], bounds.maximum[1] - bounds.minimum[1]);
      this.target = [center[0], center[1], Math.max(0, center[2] * 0.22)];
      if (name === "top") {
        this.yaw = 0;
        this.pitch = 1.38;
        this.distance = clamp(span * 1.15, 5, 12);
        return true;
      }
      this.yaw = -2.36;
      this.pitch = 1.03;
      this.distance = clamp(span * 1.22, 5.4, 12);
      return true;
    }

    project(point, width, height) {
      const { position, forward, right, up } = this.basis();
      const relative = [point[0] - position[0], point[1] - position[1], point[2] - position[2]];
      const depth = dot3(relative, forward);
      if (depth <= 0.02) return null;
      const focal = height * 0.9;
      return [
        width / 2 + (dot3(relative, right) * focal) / depth,
        height / 2 - (dot3(relative, up) * focal) / depth,
        depth,
      ];
    }

    toJSON() {
      return { yaw: this.yaw, pitch: this.pitch, distance: this.distance, target: [...this.target] };
    }
  }

  class WorldRealtimeClient {
    constructor(options = {}) {
      this.requestSnapshot = options.requestSnapshot || null;
      this.render = options.render || null;
      this.onState = options.onState || null;
      this.revision = 0;
      this.snapshot = null;
      this.state = "IDLE";
      this.resyncPromise = null;
    }

    setState(state) {
      this.state = state;
      if (this.onState) this.onState(state, this.revision);
    }

    acceptSnapshot(snapshot) {
      const revision = Number(snapshot?.revision);
      if (!Number.isSafeInteger(revision) || revision < this.revision) return false;
      this.snapshot = snapshot;
      this.revision = revision;
      this.setState("LIVE");
      if (this.render) this.render(snapshot);
      return true;
    }

    async resync() {
      if (this.resyncPromise) return this.resyncPromise;
      this.setState("RESYNCING");
      this.resyncPromise = (async () => {
        if (!this.requestSnapshot) throw new Error("world snapshot loader is not configured");
        const snapshot = await this.requestSnapshot();
        this.acceptSnapshot(snapshot);
      })();
      try {
        await this.resyncPromise;
      } finally {
        this.resyncPromise = null;
      }
    }

    async receive(message) {
      if (message?.type === "RESYNC_REQUIRED") {
        await this.resync();
        return;
      }
      const revision = Number(message?.revision);
      if (!Number.isSafeInteger(revision) || revision <= this.revision) return;
      if (revision !== this.revision + 1) {
        await this.resync();
        return;
      }
      this.acceptSnapshot(message.snapshot || message);
    }
  }

  class WorldRenderer {
    constructor(canvas, camera = new WorldCamera()) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d");
      this.camera = camera;
      this.hitRegions = [];
      this.selectedEntityId = "";
      this.showFixtures = true;
      this.visibility = { models: true, bounds: true, labels: true, path: true };
      this.palette = {
        background: "#07131b", horizon: "#102631", grid: "rgba(95, 148, 160, 0.19)",
        text: "#e5f0f1", telemetry: "#4fd1dd", custody: "#f0ad4e", fault: "#ef6f61",
        surface: "#314d59", selected: "#f7d58b",
      };
      this.fixturePalette = {
        cabinet: "#6d7b7d", counter: "#788a86", dishwasher: "#60747c", floor: "#233841",
        fridge: "#9aa8a6", microwave: "#596b72", sink: "#87a5a8", stove: "#3f5057", wall: "#344b55",
      };
    }

    render(snapshot) {
      const context = this.context;
      const width = this.canvas.width;
      const height = this.canvas.height;
      context.clearRect(0, 0, width, height);
      const gradient = context.createLinearGradient?.(0, 0, 0, height);
      if (gradient) {
        gradient.addColorStop(0, this.palette.horizon);
        gradient.addColorStop(0.48, this.palette.background);
        gradient.addColorStop(1, "#050d12");
      }
      context.fillStyle = gradient || this.palette.background;
      context.fillRect(0, 0, width, height);
      this.hitRegions = [];
      this.drawGrid(width, height);
      const entities = Object.values(snapshot?.entities || {});
      if (this.showFixtures && (this.visibility.models || this.visibility.bounds || this.visibility.labels)) {
        const fixtures = entities
          .filter((entity) => fixtureBounds(entity))
          .map((entity) => ({ entity, depth: this.camera.project(entity.pose || [0, 0, 0], width, height)?.[2] || 0 }))
          .sort((a, b) => b.depth - a.depth);
        for (const { entity } of fixtures) this.drawFixture(entity, width, height, {
          models: this.visibility.models,
          bounds: this.visibility.bounds,
        });
      }
      if (this.visibility.path) this.drawHandoffPath(snapshot, width, height);
      if (this.visibility.models || this.visibility.labels) {
        for (const entity of entities) {
          if (fixtureBounds(entity) || entity.category === "robot") continue;
          this.drawEntity(entity, width, height, this.visibility.models);
        }
        for (const robot of Object.values(snapshot?.robots || {})) {
          this.drawRobot(robot, width, height, this.visibility.models);
        }
      }
      this.drawCustody(snapshot, width, height);
      context.fillStyle = this.palette.text;
      context.font = "600 12px ui-monospace, monospace";
      context.textAlign = "left";
      context.fillText(`WORLD ${snapshot?.worldId || "—"} / REV ${snapshot?.revision ?? 0}`, 18, 24);
    }

    setVisibility(visibility = {}) {
      for (const key of Object.keys(this.visibility)) {
        if (typeof visibility[key] === "boolean") this.visibility[key] = visibility[key];
      }
      this.showFixtures = this.visibility.models || this.visibility.bounds || this.visibility.labels;
      return this;
    }

    drawFixture(entity, width, height, visibility = { models: true, bounds: true }) {
      const bounds = fixtureBounds(entity);
      if (!bounds) return;
      const [x0, y0, z0] = bounds.minimum;
      const [x1, y1, z1] = bounds.maximum;
      const worldCorners = [
        [x0, y0, z0], [x1, y0, z0], [x1, y1, z0], [x0, y1, z0],
        [x0, y0, z1], [x1, y0, z1], [x1, y1, z1], [x0, y1, z1],
      ];
      const corners = worldCorners.map((point) => this.camera.project(point, width, height));
      const visible = corners.filter(Boolean);
      if (visible.length < 4) return;
      const color = this.fixturePalette[entity.category] || this.palette.surface;
      const faceAlpha = entity.category === "wall"
        ? ["12", "18", "24"]
        : entity.category === "floor"
          ? ["26", "30", "42"]
          : ["a8", "8f", "dc"];
      if (visibility.models || visibility.bounds || entity.entityId === this.selectedEntityId) {
        const faces = fixtureFaces(bounds, this.camera.basis().position).map((indices, index) => ({
          indices,
          alpha: faceAlpha[index],
        }));
        for (const face of faces) {
          const points = face.indices.map((index) => corners[index]);
          if (points.some((point) => !point)) continue;
          this.context.beginPath();
          this.context.moveTo(points[0][0], points[0][1]);
          for (const point of points.slice(1)) this.context.lineTo(point[0], point[1]);
          this.context.closePath();
          if (visibility.models) {
            this.context.fillStyle = `${color}${face.alpha}`;
            this.context.fill();
          }
          if (visibility.bounds || entity.entityId === this.selectedEntityId) {
            this.context.strokeStyle = entity.entityId === this.selectedEntityId
              ? this.palette.selected
              : `${color}${entity.category === "wall" ? "78" : "f2"}`;
            this.context.lineWidth = entity.entityId === this.selectedEntityId ? 3 : 1;
            this.context.stroke();
          }
        }
      }
      this.registerHit(entity, visible, 3);
      if (!["wall", "floor", "cabinet"].includes(entity.category) || entity.entityId === this.selectedEntityId) {
        const top = this.camera.project([(x0 + x1) / 2, (y0 + y1) / 2, z1], width, height);
        if (top) this.label(entity.attributes?.label || entity.entityId, top[0], top[1] - 8, "#bfd0d2");
      }
    }

    drawHandoffPath(snapshot, width, height) {
      const path = handoffPath(snapshot);
      if (path.points.length !== 3) return;
      const projected = path.points.map((entity) => {
        const pose = entity.pose || [0, 0, 0];
        return this.camera.project([pose[0], pose[1], Number(pose[2] || 0) + 0.08], width, height);
      });
      if (projected.some((point) => !point)) return;
      this.context.save?.();
      this.context.setLineDash?.([12, 8]);
      this.context.lineWidth = 4;
      for (let index = 0; index < projected.length - 1; index += 1) {
        this.context.strokeStyle = index < path.stage ? this.palette.telemetry : this.palette.custody;
        this.context.beginPath();
        this.context.moveTo(projected[index][0], projected[index][1]);
        this.context.lineTo(projected[index + 1][0], projected[index + 1][1]);
        this.context.stroke();
      }
      this.context.setLineDash?.([]);
      this.context.restore?.();
    }

    drawGrid(width, height) {
      const context = this.context;
      context.strokeStyle = this.palette.grid;
      context.lineWidth = 1;
      for (let value = -2; value <= 3; value += 0.2) {
        const first = this.camera.project([value, -1.2, 0], width, height);
        const second = this.camera.project([value, 2, 0], width, height);
        if (first && second) this.line(first, second);
        const third = this.camera.project([-2, value, 0], width, height);
        const fourth = this.camera.project([3, value, 0], width, height);
        if (third && fourth) this.line(third, fourth);
      }
    }

    line(a, b) {
      this.context.beginPath();
      this.context.moveTo(a[0], a[1]);
      this.context.lineTo(b[0], b[1]);
      this.context.stroke();
    }

    drawEntity(entity, width, height, drawModel = true) {
      const pose = Array.isArray(entity.pose) ? entity.pose : [0, 0, 0];
      const point = this.camera.project(pose, width, height);
      if (!point) return;
      const zone = ["handoff_zone", "target_zone", "storage_bin", "delivery_tray"].includes(entity.category);
      const color = entity.category === "handoff_zone" ? this.palette.custody : entityColor(entity, this.palette.telemetry);
      const selected = entity.entityId === this.selectedEntityId;
      if (drawModel) {
        this.context.strokeStyle = selected ? this.palette.selected : color;
        this.context.fillStyle = zone ? `${color}33` : color;
        this.context.lineWidth = selected ? 3 : (zone ? 2 : 1);
        if (zone) {
          this.context.fillRect(point[0] - 30, point[1] - 18, 60, 36);
          this.context.strokeRect(point[0] - 30, point[1] - 18, 60, 36);
        } else {
          this.context.beginPath();
          this.context.arc(point[0], point[1], entity.category === "block" ? 8 : 6, 0, Math.PI * 2);
          this.context.fill();
          if (selected) this.context.stroke();
        }
      }
      this.registerHit(entity, [point], zone ? 34 : 14);
      this.label(entity.entityId, point[0], point[1] - (zone ? 25 : 12), color);
      if (entity.freshness && entity.freshness !== "FRESH") this.label(entity.freshness, point[0], point[1] + 28, this.palette.fault);
    }

    drawRobot(robot, width, height, drawModel = true) {
      const point = this.camera.project(robot.pose || [0, 0, 0], width, height);
      if (!point) return;
      const color = robot.emergencyStopped ? this.palette.fault : this.palette.telemetry;
      const selected = robot.robotId === this.selectedEntityId;
      if (drawModel) {
        this.context.fillStyle = `${color}3d`;
        this.context.strokeStyle = selected ? this.palette.selected : color;
        this.context.lineWidth = selected ? 3 : 2;
        this.context.fillRect(point[0] - 24, point[1] - 15, 48, 30);
        this.context.strokeRect(point[0] - 24, point[1] - 15, 48, 30);
      }
      this.registerHit(
        { entityId: robot.robotId, category: "robot", pose: robot.pose, robot },
        [point],
        28,
      );
      this.label(`${robot.robotId} · ${robot.activity || "IDLE"}${robot.held ? ` · ${robot.held}` : ""}`, point[0], point[1] - 24, color);
    }

    registerHit(entity, points, padding = 0) {
      const xs = points.map((point) => point[0]);
      const ys = points.map((point) => point[1]);
      this.hitRegions.push({
        entity,
        minimumX: Math.min(...xs) - padding,
        maximumX: Math.max(...xs) + padding,
        minimumY: Math.min(...ys) - padding,
        maximumY: Math.max(...ys) + padding,
      });
    }

    pick(x, y) {
      for (const region of [...this.hitRegions].reverse()) {
        if (x >= region.minimumX && x <= region.maximumX && y >= region.minimumY && y <= region.maximumY) {
          return region.entity;
        }
      }
      return null;
    }

    focus(entity) {
      if (!entity) return false;
      const bounds = fixtureBounds(entity);
      if (bounds) {
        const center = bounds.minimum.map((value, axis) => (value + bounds.maximum[axis]) / 2);
        const extent = Math.max(...bounds.maximum.map((value, axis) => value - bounds.minimum[axis]));
        this.camera.target = center;
        this.camera.distance = clamp(extent * 2.6, 1.4, 7);
      } else if (Array.isArray(entity.pose)) {
        this.camera.target = [Number(entity.pose[0]), Number(entity.pose[1]), Number(entity.pose[2] || 0) + 0.2];
        this.camera.distance = entity.category === "robot" ? 2.0 : 1.35;
      } else {
        return false;
      }
      return true;
    }

    drawCustody(snapshot, width, height) {
      const resource = snapshot?.resources?.["block:red-block"];
      if (!resource) return;
      const context = this.context;
      context.fillStyle = "rgba(11, 23, 32, 0.88)";
      context.strokeStyle = this.palette.custody;
      context.fillRect(18, height - 58, Math.min(520, width - 36), 38);
      context.strokeRect(18, height - 58, Math.min(520, width - 36), 38);
      context.fillStyle = this.palette.custody;
      context.font = "700 11px ui-monospace, monospace";
      context.textAlign = "left";
      context.fillText(`CUSTODY  robot-1 → handoff-zone → robot-2  /  owner ${resource.owner}  /  token ${resource.fencingToken}`, 30, height - 34);
    }

    label(text, x, y, color) {
      if (!this.visibility.labels) return;
      this.context.fillStyle = color;
      this.context.font = "700 10px ui-monospace, monospace";
      this.context.textAlign = "center";
      this.context.fillText(text || "—", x, y);
    }
  }

  function entityColor(entity, fallback) {
    const colors = { red: "#de6a5f", blue: "#5798c8", green: "#69aa83", orange: "#e7a34a", gray: "#718995" };
    return colors[entity?.attributes?.color] || fallback;
  }

  root.TangyingWorld = {
    WorldCamera,
    WorldRenderer,
    WorldRealtimeClient,
    fixtureBounds,
    fixtureFaces,
    handoffPath,
  };
})(globalThis);
