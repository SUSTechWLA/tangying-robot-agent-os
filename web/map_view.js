// The scene map: what the robot has actually seen, and how much is left.
//
// One data source answers both questions a first-time operator has. The occupancy
// grid RTAB-Map publishes says what was observed (so it drives the coverage
// numbers) and is also the thing worth drawing (so it drives the picture). The
// grid is drawn on a canvas rather than in WebGL because an occupancy map is a few
// hundred thousand flat cells: a canvas keeps it readable at any zoom, costs
// nothing to redraw, and does not compete with the 3D scene for the same GPU budget.
//
// Loaded as a classic script under the console's CSP, so no `export`.

const UNKNOWN_CELL = -1;
const FREE_CELL = 0;

// Nav2 and RTAB-Map treat anything at or above this as lethal for planning, which
// is what makes it worth distinguishing from "probably occupied".
const LETHAL_THRESHOLD = 50;

const COLOR_UNKNOWN = [232, 235, 241, 255];
const COLOR_FREE = [252, 252, 254, 255];
const COLOR_OCCUPIED = [92, 101, 122, 255];
const COLOR_LETHAL = [40, 46, 61, 255];
const COLOR_ROBOT = [85, 91, 214, 255];

/** Count what the grid is made of. Pure, so the numbers are testable. */
function summariseGrid(cells) {
  const summary = { total: 0, unknown: 0, free: 0, occupied: 0, lethal: 0 };
  if (!cells || typeof cells.length !== "number") return summary;
  for (let index = 0; index < cells.length; index += 1) {
    const value = cells[index];
    summary.total += 1;
    if (value === UNKNOWN_CELL || value === null || value === undefined) summary.unknown += 1;
    else if (value <= FREE_CELL) summary.free += 1;
    else if (value >= LETHAL_THRESHOLD) { summary.occupied += 1; summary.lethal += 1; }
    else summary.occupied += 1;
  }
  return summary;
}

/**
 * Paint the grid into an RGBA buffer.
 *
 * Unknown cells are drawn lighter than free space on purpose: an operator has to
 * be able to see, at a glance, where the robot has not been. Rendering them the
 * same as free space is how a half-built map looks finished.
 */
function gridImage(cells, width, height) {
  const pixels = new Uint8ClampedArray(width * height * 4);
  for (let index = 0; index < width * height; index += 1) {
    const value = cells && index < cells.length ? cells[index] : UNKNOWN_CELL;
    let colour = COLOR_UNKNOWN;
    if (value !== UNKNOWN_CELL && value !== null && value !== undefined) {
      if (value >= LETHAL_THRESHOLD) colour = COLOR_LETHAL;
      else if (value > FREE_CELL) colour = COLOR_OCCUPIED;
      else colour = COLOR_FREE;
    }
    const offset = index * 4;
    pixels[offset] = colour[0];
    pixels[offset + 1] = colour[1];
    pixels[offset + 2] = colour[2];
    pixels[offset + 3] = colour[3];
  }
  return pixels;
}

/** Where the robot is, in grid cells, from the map payload's pose. */
function robotCell(payload) {
  const pose = payload && Array.isArray(payload.mapPose) ? payload.mapPose : null;
  const origin = payload && Array.isArray(payload.origin) ? payload.origin : null;
  if (!pose || !origin || payload.resolution <= 0) return null;
  const column = Math.round((pose[0] - origin[0]) / payload.resolution);
  const row = Math.round((pose[1] - origin[1]) / payload.resolution);
  if (!Number.isFinite(column) || !Number.isFinite(row)) return null;
  if (column < 0 || row < 0 || column >= payload.width || row >= payload.height) return null;
  return { column, row };
}

/**
 * Turn a navigation map payload into what the screen shows.
 *
 * Anything the payload does not carry is reported as unavailable rather than
 * assumed: a page that draws an empty grid and calls it "the map" is worse than
 * one that says it has nothing yet.
 */
function buildMapView(payload) {
  if (!payload || payload.ready == null) {
    return {
      available: false,
      headline: "还没有读到地图",
      hint: "先连上机器人并启动建图；地图就绪后这里会显示机器人已经看过哪些地方。",
      stats: null,
    };
  }
  if (payload.ready !== true) {
    return {
      available: false,
      headline: "地图还不可用",
      hint: "导航或 RTAB-Map 尚未就绪；开发诊断里有更详细的阻断原因。",
      stats: null,
      mode: payload.mode || "",
    };
  }

  const width = Number(payload.width || 0);
  const height = Number(payload.height || 0);
  const cells = payload.cells || [];
  const summary = summariseGrid(cells);
  const known = summary.total - summary.unknown;
  const ratio = summary.total > 0 ? known / summary.total : 0;
  const mapping = String(payload.mode || "") === "mapping";

  return {
    available: true,
    width,
    height,
    cells,
    resolution: Number(payload.resolution || 0),
    mapRevision: String(payload.mapRevision || ""),
    poseSource: String(payload.poseSource || ""),
    robot: robotCell(payload),
    mode: payload.mode || "",
    stats: {
      knownCells: known,
      unknownCells: summary.unknown,
      occupiedCells: summary.occupied,
      totalCells: summary.total,
      coverage: Math.round(ratio * 100),
    },
    headline: mapping
      ? `地图覆盖 ${Math.round(ratio * 100)}%（正在建图）`
      : `地图覆盖 ${Math.round(ratio * 100)}%`,
    hint: mapping
      ? "浅色区域是还没走过的地方。让机器人慢慢开过去，覆盖率会继续上升。"
      : "浅色区域是还没走过的地方；跨房间任务需要先把它补齐。",
    legend: [
      { key: "free", text: "已确认可通行" },
      { key: "unknown", text: "还没走过" },
      { key: "occupied", text: "障碍" },
      { key: "robot", text: "机器人当前位置" },
    ],
  };
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

/** Draw the grid and the robot onto a canvas, scaled to fit its box. */
function paintMap(canvas, view) {
  if (!canvas || !view.available || view.width <= 0 || view.height <= 0) return false;
  const context = canvas.getContext && canvas.getContext("2d");
  if (!context) return false;
  canvas.width = view.width;
  canvas.height = view.height;
  const image = context.createImageData(view.width, view.height);
  image.data.set(gridImage(view.cells, view.width, view.height));
  context.putImageData(image, 0, 0);
  if (view.robot) {
    context.fillStyle = "rgb(85, 91, 214)";
    context.beginPath();
    context.arc(view.robot.column, view.robot.row, 2, 0, Math.PI * 2);
    context.fill();
  }
  return true;
}

/** Build the map panel as a DOM subtree; server strings are never injected. */
function renderMapNodes(view) {
  if (!view) return null;
  const root = element("div", `map-view ${view.available ? "available" : "unavailable"}`);
  root.append(element("p", "map-headline", view.headline));
  root.append(element("p", "map-hint", view.hint));
  if (!view.available) return root;

  const frame = element("div", "map-frame");
  const canvas = element("canvas", "map-canvas");
  canvas.setAttribute("role", "img");
  canvas.setAttribute("aria-label",
    `占据栅格地图，覆盖 ${view.stats.coverage}%，机器人位置已标出`);
  frame.append(canvas);
  root.append(frame);

  const stats = element("dl", "map-stats");
  for (const [label, value] of [
    ["已确认可通行", `${view.stats.knownCells} 格`],
    ["还没走过", `${view.stats.unknownCells} 格`],
    ["障碍", `${view.stats.occupiedCells} 格`],
    ["分辨率", `${view.resolution} 米/格`],
  ]) {
    stats.append(element("dt", "", label), element("dd", "", value));
  }
  root.append(stats);

  const legend = element("ul", "map-legend");
  for (const entry of view.legend || []) {
    const row = element("li", `map-legend-${entry.key}`);
    row.append(element("i", "", ""), element("span", "", entry.text));
    legend.append(row);
  }
  root.append(legend);
  root.mapCanvas = canvas;
  return root;
}

// Published last, once every declaration exists.
globalThis.TangyingMapView = {
  buildMapView,
  renderMapNodes,
  paintMap,
  summariseGrid,
  gridImage,
  robotCell,
};
