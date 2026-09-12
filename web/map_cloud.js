// Streaming a dense map into the browser a level at a time.
//
// A million-point cloud must not be the first thing the page downloads. The map
// is stored as levels of detail, coarsest first, so the viewer can draw a few
// thousand points immediately, then refine as the camera settles. This module owns
// the two decisions that make that work - which level to show, and how to turn a
// chunk of bytes into geometry - and it keeps them apart from three.js so both are
// testable without a GPU.
//
// The wire format is written by the Python pipeline: a 20-byte header followed by
// float32 positions and uint8 colours. Twenty bytes is deliberate; the header is
// checked on every chunk, because a chunk that arrives truncated must fail loudly
// rather than be drawn as a cloud of garbage.
//
// Loaded as a classic script under the console's CSP, so no `export`.

const MAGIC = 0x43505954; // "TYPC" little-endian
const FORMAT_VERSION = 1;
const HEADER_BYTES = 20;
const POSITION_BYTES = 12;
const COLOUR_BYTES = 3;

/**
 * Decode one level of detail.
 *
 * Returns typed arrays that can go straight into a BufferAttribute; nothing is
 * copied twice and nothing is guessed. A chunk whose declared point count does not
 * match the bytes it actually carries is rejected.
 */
function decodeChunk(buffer) {
  if (!buffer || buffer.byteLength === undefined) {
    throw new Error("a chunk must be an ArrayBuffer");
  }
  if (buffer.byteLength < HEADER_BYTES) {
    throw new Error(`chunk is ${buffer.byteLength} bytes, shorter than its own header`);
  }
  const view = new DataView(buffer);
  if (view.getUint32(0, true) !== MAGIC) {
    throw new Error("not a TYPC chunk");
  }
  const version = view.getUint32(4, true);
  if (version !== FORMAT_VERSION) {
    throw new Error(`unsupported chunk version ${version}`);
  }
  const count = view.getUint32(8, true);
  const level = view.getUint32(12, true);
  const hasColour = view.getUint32(16, true) === 1;
  const expected = HEADER_BYTES + count * POSITION_BYTES + (hasColour ? count * COLOUR_BYTES : 0);
  if (buffer.byteLength !== expected) {
    throw new Error(`chunk declares ${count} points (${expected} bytes) but carries ${buffer.byteLength}`);
  }
  const positions = new Float32Array(buffer, HEADER_BYTES, count * 3);
  const colors = hasColour
    ? new Uint8Array(buffer, HEADER_BYTES + count * POSITION_BYTES, count * 3)
    : null;
  return { positions, colors, count, level, hasColour };
}

/**
 * Choose a level from how far the camera is from the map.
 *
 * Coarsest (level 0) when far away, finest when close. The bands double with each
 * level, matching how the pipeline doubled the voxel size, so the on-screen point
 * density stays roughly constant instead of collapsing to a smear or exploding to
 * millions of points.
 */
function selectLodLevel(distanceMetres, lodLevels, { nearMetres = 3, farMetres = 40 } = {}) {
  if (!Number.isFinite(lodLevels) || lodLevels < 1) return 0;
  if (!Number.isFinite(distanceMetres) || distanceMetres <= nearMetres) return lodLevels - 1;
  if (distanceMetres >= farMetres) return 0;
  const span = farMetres - nearMetres;
  const ratio = (distanceMetres - nearMetres) / span;         // 0 near, 1 far
  const level = Math.round((1 - ratio) * (lodLevels - 1));
  return Math.max(0, Math.min(lodLevels - 1, level));
}

/** Camera-to-map distance, using the map's bounding sphere centre. */
function cameraDistance(cameraPosition, bounds) {
  if (!cameraPosition || !bounds || !bounds.min || !bounds.max) return 0;
  const centre = [0, 1, 2].map(axis => (bounds.min[axis] + bounds.max[axis]) / 2);
  const position = Array.isArray(cameraPosition) ? cameraPosition : [cameraPosition.x, cameraPosition.y, cameraPosition.z];
  return Math.hypot(position[0] - centre[0], position[1] - centre[1], position[2] - centre[2]);
}

/**
 * The level to draw and the levels to keep, given what is already loaded.
 *
 * Keeping every level visited would defeat the purpose: the point of levels is to
 * bound what is resident, so coarser levels are dropped once a finer one is in and
 * the camera has moved on. Returns the plan rather than performing it, so the
 * policy is testable on its own.
 */
function planLevels({ distanceMetres, lodLevels, loaded = [], maxResident = 3 }) {
  const target = selectLodLevel(distanceMetres, lodLevels);
  const wanted = [];
  // Always keep the coarsest level: it is tiny and it is what shows if a finer
  // fetch is still in flight or fails.
  wanted.push(0);
  for (let level = Math.max(0, target - 1); level <= target; level += 1) {
    if (!wanted.includes(level)) wanted.push(level);
  }
  wanted.sort((left, right) => left - right);
  const keep = new Set(wanted.slice(-maxResident));
  keep.add(0);
  return {
    target,
    fetch: wanted.filter(level => !loaded.includes(level)),
    evict: loaded.filter(level => !keep.has(level)),
  };
}

/** Format a byte count the way a person reads it. */
function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Streams levels from the console and hands geometry to a renderer.
 *
 * The renderer is injected rather than imported: this file has no access to the
 * bundled three.js, and keeping it that way means the loading policy can be tested
 * without a GL context. `renderer` must provide `show(level, geometry)` and
 * `hide(level)`.
 */
class MapCloudLayer {
  constructor({ baseUrl, mapId, lodLevels, bounds, renderer, fetchImpl, maxResident = 3 }) {
    if (!baseUrl || !mapId) throw new Error("a map cloud layer needs a base URL and a map id");
    if (!renderer) throw new Error("a map cloud layer needs a renderer");
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.mapId = mapId;
    this.lodLevels = lodLevels;
    this.bounds = bounds;
    this.renderer = renderer;
    this.fetchImpl = fetchImpl || (typeof fetch === "function" ? fetch : null);
    this.maxResident = maxResident;
    this.loaded = [];
    this.pending = new Set();
    this.bytes = 0;
    this.lastError = null;
  }

  url(level) {
    return `${this.baseUrl}/v1/maps/${encodeURIComponent(this.mapId)}/cloud?lod=${level}`;
  }

  async loadLevel(level) {
    if (this.loaded.includes(level) || this.pending.has(level)) return false;
    if (!this.fetchImpl) throw new Error("no fetch available");
    this.pending.add(level);
    try {
      const response = await this.fetchImpl(this.url(level));
      if (!response.ok) throw new Error(`level ${level} returned ${response.status}`);
      const buffer = await response.arrayBuffer();
      const decoded = decodeChunk(buffer);
      this.bytes += buffer.byteLength;
      this.renderer.show(level, decoded);
      this.loaded.push(level);
      this.lastError = null;
      return true;
    } catch (error) {
      // Keeping the previously loaded level on screen is the whole reason level 0
      // is never evicted: a failed refinement must not empty the view.
      this.lastError = error;
      return false;
    } finally {
      this.pending.delete(level);
    }
  }

  /** Pick levels for this camera position and start whatever is missing. */
  async update(cameraPosition) {
    const distance = cameraDistance(cameraPosition, this.bounds);
    const plan = planLevels({
      distanceMetres: distance, lodLevels: this.lodLevels,
      loaded: this.loaded, maxResident: this.maxResident,
    });
    for (const level of plan.evict) {
      this.renderer.hide(level);
      this.loaded = this.loaded.filter(entry => entry !== level);
    }
    for (const level of plan.fetch) {
      void this.loadLevel(level);
    }
    return plan;
  }

  dispose() {
    for (const level of this.loaded) this.renderer.hide(level);
    this.loaded = [];
    this.pending.clear();
    this.bytes = 0;
  }

  status() {
    return {
      loaded: [...this.loaded].sort((left, right) => left - right),
      pending: [...this.pending].sort((left, right) => left - right),
      bytes: this.bytes,
      bytesText: formatBytes(this.bytes),
      error: this.lastError ? String(this.lastError.message || this.lastError) : "",
    };
  }
}

// Published last, once every declaration exists.
globalThis.TangyingMapCloud = {
  decodeChunk,
  selectLodLevel,
  cameraDistance,
  planLevels,
  formatBytes,
  MapCloudLayer,
  HEADER_BYTES,
};
