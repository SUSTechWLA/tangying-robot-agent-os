import * as THREE from "three";

// Turning a decoded level of detail into something three.js can draw.
//
// This is the one place that needs both halves: the decoder in web/map_cloud.js
// produces typed arrays and knows nothing about three.js, and the scene knows
// nothing about the wire format. Keeping the conversion here means neither side
// has to learn about the other.
//
// Camera RGB bytes are sRGB. Three's renderer expects vertex colours in its
// linear working space, so decode explicitly instead of relying on a normalized
// uint8 attribute (which would make mid-tones much too bright).

const POINT_SIZE_M = 0.012;

function srgbToLinear(value) {
  const channel = value / 255;
  return channel <= .04045 ? channel / 12.92 : ((channel + .055) / 1.055) ** 2.4;
}

function colourAttribute(geometry, options) {
  const count = geometry.positions.length / 3;
  const output = new Float32Array(count * 3);
  const mode = options.colorMode || (geometry.colors ? "rgb" : "neutral");
  if (mode === "rgb" && geometry.colors) {
    for (let index = 0; index < output.length; index += 1) output[index] = srgbToLinear(geometry.colors[index]);
    return output;
  }
  if (mode !== "height") {
    output.fill(.18);
    return output;
  }
  const low = Number(options.bounds?.min?.[2]);
  const high = Number(options.bounds?.max?.[2]);
  const span = Number.isFinite(low) && Number.isFinite(high) && high > low ? high - low : 1;
  for (let point = 0; point < count; point += 1) {
    const t = Math.max(0, Math.min(1, (geometry.positions[point * 3 + 2] - (Number.isFinite(low) ? low : 0)) / span));
    // Deep teal through sea-glass to a small amber cap: readable on the neutral
    // canvas without implying these are camera colours.
    output[point * 3] = .035 + .62 * t;
    output[point * 3 + 1] = .25 + .34 * (1 - Math.abs(t - .5) * 1.1);
    output[point * 3 + 2] = .27 - .16 * t;
  }
  return output;
}

export function createMapPoints(geometry, options = {}) {
  if (!geometry?.positions) throw new Error("map points need decoded positions");
  const buffer = new THREE.BufferGeometry();
  buffer.setAttribute("position", new THREE.BufferAttribute(geometry.positions, 3));
  buffer.setAttribute("color", new THREE.BufferAttribute(colourAttribute(geometry, options), 3));
  buffer.computeBoundingSphere();
  const material = new THREE.PointsMaterial({
    size: options.size ?? POINT_SIZE_M,
    sizeAttenuation: true,
    vertexColors: true,
    color: 0xffffff,
  });
  const points = new THREE.Points(buffer, material);
  points.userData.measuredColorAvailable = Boolean(geometry.colors);
  points.name = `MapCloudLod${geometry.level}`;
  // The cloud is scenery: it must not swallow clicks meant for the robot or the
  // semantic overlay.
  points.raycast = () => {};
  return points;
}

export function disposeMapPoints(points) {
  if (!points) return;
  points.parent?.remove(points);
  points.geometry?.dispose?.();
  points.material?.dispose?.();
}

export const MapCloudPoints = Object.freeze({ create: createMapPoints, dispose: disposeMapPoints });
