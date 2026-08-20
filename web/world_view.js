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
      this.palette = {
        background: "#0b1720", grid: "rgba(91, 136, 153, 0.22)", text: "#dce7e9",
        telemetry: "#41b8c4", custody: "#e7a34a", fault: "#de6a5f", surface: "#29404c",
      };
    }

    render(snapshot) {
      const context = this.context;
      const width = this.canvas.width;
      const height = this.canvas.height;
      context.clearRect(0, 0, width, height);
      context.fillStyle = this.palette.background;
      context.fillRect(0, 0, width, height);
      this.drawGrid(width, height);
      for (const entity of Object.values(snapshot?.entities || {})) this.drawEntity(entity, width, height);
      for (const robot of Object.values(snapshot?.robots || {})) this.drawRobot(robot, width, height);
      this.drawCustody(snapshot, width, height);
      context.fillStyle = this.palette.text;
      context.font = "600 12px ui-monospace, monospace";
      context.textAlign = "left";
      context.fillText(`WORLD ${snapshot?.worldId || "—"} / REV ${snapshot?.revision ?? 0}`, 18, 24);
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

    drawEntity(entity, width, height) {
      const pose = Array.isArray(entity.pose) ? entity.pose : [0, 0, 0];
      const point = this.camera.project(pose, width, height);
      if (!point) return;
      const zone = ["handoff_zone", "target_zone", "storage_bin", "delivery_tray"].includes(entity.category);
      const color = entity.category === "handoff_zone" ? this.palette.custody : entityColor(entity, this.palette.telemetry);
      this.context.strokeStyle = color;
      this.context.fillStyle = zone ? `${color}33` : color;
      this.context.lineWidth = zone ? 2 : 1;
      if (zone) {
        this.context.fillRect(point[0] - 30, point[1] - 18, 60, 36);
        this.context.strokeRect(point[0] - 30, point[1] - 18, 60, 36);
      } else {
        this.context.beginPath();
        this.context.arc(point[0], point[1], entity.category === "block" ? 8 : 6, 0, Math.PI * 2);
        this.context.fill();
      }
      this.label(entity.entityId, point[0], point[1] - (zone ? 25 : 12), color);
      if (entity.freshness && entity.freshness !== "FRESH") this.label(entity.freshness, point[0], point[1] + 28, this.palette.fault);
    }

    drawRobot(robot, width, height) {
      const point = this.camera.project(robot.pose || [0, 0, 0], width, height);
      if (!point) return;
      const color = robot.emergencyStopped ? this.palette.fault : this.palette.telemetry;
      this.context.fillStyle = `${color}3d`;
      this.context.strokeStyle = color;
      this.context.lineWidth = 2;
      this.context.fillRect(point[0] - 24, point[1] - 15, 48, 30);
      this.context.strokeRect(point[0] - 24, point[1] - 15, 48, 30);
      this.label(`${robot.robotId} · ${robot.activity || "IDLE"}${robot.held ? ` · ${robot.held}` : ""}`, point[0], point[1] - 24, color);
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

  root.TangyingWorld = { WorldCamera, WorldRenderer, WorldRealtimeClient };
})(globalThis);
