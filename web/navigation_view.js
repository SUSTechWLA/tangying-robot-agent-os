(function (root) {
  "use strict";
  const finite = value => typeof value === "number" && Number.isFinite(value);
  function pose(value) {
    return Array.isArray(value) && value.length === 7 && value.every(finite)
      && Math.abs(value.slice(3).reduce((sum, v) => sum + v * v, 0) - 1) < .001;
  }
  function validateGrid(map) {
    return map?.frameId === "map" && typeof map.mapRevision === "string" && map.mapRevision.length > 0
      && Number.isSafeInteger(map.width) && Number.isSafeInteger(map.height) && map.width > 0 && map.height > 0
      && map.width * map.height <= 262144 && Array.isArray(map.cells) && map.cells.length === map.width * map.height
      && map.cells.every(cell => Number.isInteger(cell) && cell >= -1 && cell <= 100)
      && finite(map.resolution) && map.resolution > 0 && pose(map.origin)
      && Math.abs(map.origin[4]) < 1e-6 && Math.abs(map.origin[5]) < 1e-6;
  }
  function yaw(p) {
    const [w, x, y, z] = p.slice(3);
    return Math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z));
  }
  function robotPixel(map, now = Date.now()) {
    if (!validateGrid(map) || !pose(map.mapPose) || map.poseSource !== "rtabmap_tf"
      || !Number.isSafeInteger(map.poseObservedAtUnixMs) || now < map.poseObservedAtUnixMs || now - map.poseObservedAtUnixMs > 1000) return null;
    const angle = yaw(map.origin), dx = map.mapPose[0] - map.origin[0], dy = map.mapPose[1] - map.origin[1];
    const x = (Math.cos(angle) * dx + Math.sin(angle) * dy) / map.resolution;
    const y = map.height - (-Math.sin(angle) * dx + Math.cos(angle) * dy) / map.resolution;
    if (x < 0 || y < 0 || x > map.width || y > map.height) return null;
    return { x, y, yaw: yaw(map.mapPose) - angle };
  }
  function cellColor(cell) {
    if (cell === -1) return [225, 233, 239];
    const shade = Math.round(255 - cell * 2.05);
    return [shade, shade, shade];
  }

  let robotId = "", requestId = 0, fetching = false, timer = null, poseExpiryTimer = null, lastMap = null;
  const get = id => root.document?.getElementById(id);
  function clear(message) {
    root.clearTimeout(poseExpiryTimer); poseExpiryTimer = null; lastMap = null;
    const canvas = get("navigation-map-canvas");
    if (!canvas) return;
    canvas.hidden = true;
    get("navigation-map-help").textContent = message;
    const badge = get("navigation-map-state");
    badge.textContent = "导航未就绪"; badge.dataset.tone = "pending";
  }
  function render(map) {
    if (map.robotId !== robotId) { clear("地图与当前机器人不一致，请检查连接配置。"); return; }
    root.clearTimeout(poseExpiryTimer); poseExpiryTimer = null;
    lastMap = map;
    const badge = get("navigation-map-state");
    const robot = robotPixel(map), ready = map.ready === true && robot !== null;
    badge.textContent = ready ? map.mode === "mapping" ? "正在建图" : "已定位" : "定位不可用";
    badge.dataset.tone = ready ? "success" : "pending";
    get("navigation-map-details").textContent = JSON.stringify({ robotId: map.robotId, mode: map.mode, mapRevision: map.mapRevision,
      localizationState: map.localizationState, frameId: map.frameId, poseSource: map.poseSource,
      poseObservedAtUnixMs: map.poseObservedAtUnixMs, mapPose: map.mapPose, resolution: map.resolution }, null, 2);
    if (!validateGrid(map)) { clear("还没有可显示的相机地图。请等待相机和定位准备完成。"); return; }
    const canvas = get("navigation-map-canvas"), ctx = canvas.getContext("2d");
    const raster = root.document.createElement("canvas");
    raster.width = map.width; raster.height = map.height;
    const rc = raster.getContext("2d"), pixels = rc.createImageData(map.width, map.height);
    for (let row = 0; row < map.height; row++) {
      for (let col = 0; col < map.width; col++) {
        const index = ((map.height - row - 1) * map.width + col) * 4;
        pixels.data.set([...cellColor(map.cells[row * map.width + col]), 255], index);
      }
    }
    rc.putImageData(pixels, 0, 0);
    canvas.hidden = false;
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const scale = Math.min((canvas.width - 32) / map.width, (canvas.height - 32) / map.height);
    const left = (canvas.width - map.width * scale) / 2, top = (canvas.height - map.height * scale) / 2;
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(raster, left, top, map.width * scale, map.height * scale);
    if (robot) {
      ctx.save(); ctx.translate(left + robot.x * scale, top + robot.y * scale); ctx.rotate(-robot.yaw);
      ctx.beginPath(); ctx.moveTo(11, 0); ctx.lineTo(-8, -7); ctx.lineTo(-4, 0); ctx.lineTo(-8, 7); ctx.closePath();
      ctx.fillStyle = "#078584"; ctx.fill(); ctx.strokeStyle = "#ffffff"; ctx.lineWidth = 2; ctx.stroke(); ctx.restore();
      // Local freshness must expire even while the next HTTP request hangs.
      // Preserve the saved grid and redraw without a stale robot marker.
      poseExpiryTimer = root.setTimeout(() => {
        if (lastMap === map && map.robotId === robotId) render(map);
      }, Math.max(1, map.poseObservedAtUnixMs + 1001 - Date.now()));
    }
    get("navigation-map-help").textContent = `${(map.width * map.resolution).toFixed(1)} × ${(map.height * map.resolution).toFixed(1)} 米的二维通行图，由 RGB-D 观测累积形成。${robot ? "绿色箭头是当前定位。" : "当前定位不可用，已隐藏机器人位置。"}地图仅供查看；机器人移动仍由任务控制。`;
  }
  function unavailable(message) {
    if (lastMap?.robotId === robotId && validateGrid(lastMap)) {
      render({ ...lastMap, ready: false, mapPose: null, poseObservedAtUnixMs: 0 });
      get("navigation-map-help").textContent = `${message}仅保留已保存地图，当前定位不可用。`;
    } else clear(message);
  }
  async function refresh() {
    if (!robotId || fetching || !get("local-navigation-panel")?.open) return;
    fetching = true;
    const generation = requestId, controller = new AbortController();
    const timeout = root.setTimeout(() => controller.abort(), 2500);
    try {
      const response = await root.fetch("/v1/navigation/map", { cache: "no-store", signal: controller.signal });
      if (generation !== requestId) return;
      if (!response.ok) { unavailable("导航地图暂时不可用，请检查导航服务、相机和定位状态。"); return; }
      const map = await response.json();
      if (generation === requestId) render(map);
    } catch (_) { if (generation === requestId) unavailable("导航连接已中断。恢复连接后会重新读取地图。"); }
    finally { root.clearTimeout(timeout); fetching = false; }
  }
  function update(snapshot) {
    const panel = get("local-navigation-panel");
    if (!panel) return;
    const enabled = snapshot?.robotState?.navigation?.backend === "rtabmap_nav2";
    panel.hidden = !enabled;
    const next = enabled ? snapshot.robotId : "";
    if (next !== robotId) { robotId = next; requestId++; clear("正在读取相机建出的地图…"); }
    if (enabled && !timer) {
      panel.addEventListener("toggle", refresh);
      timer = root.setInterval(refresh, 1000);
    }
    if (enabled) void refresh();
  }
  root.TangyingNavigationView = { update, validateGrid, robotPixel, cellColor };
})(globalThis);
