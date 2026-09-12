import * as THREE from "three";

// Turning a decoded level of detail into something three.js can draw.
//
// This is the one place that needs both halves: the decoder in web/map_cloud.js
// produces typed arrays and knows nothing about three.js, and the scene knows
// nothing about the wire format. Keeping the conversion here means neither side
// has to learn about the other.
//
// Colours arrive as uint8. Declaring the attribute normalised is what makes three
// divide by 255; without it a scan renders at 255x brightness and looks like a
// white blob, which is easy to misread as a broken decoder.

const POINT_SIZE_M = 0.012;

export function createMapPoints(geometry, options = {}) {
  if (!geometry?.positions) throw new Error("map points need decoded positions");
  const buffer = new THREE.BufferGeometry();
  buffer.setAttribute("position", new THREE.BufferAttribute(geometry.positions, 3));
  if (geometry.colors) {
    buffer.setAttribute("color", new THREE.BufferAttribute(geometry.colors, 3, true));
  }
  buffer.computeBoundingSphere();
  const material = new THREE.PointsMaterial({
    size: options.size ?? POINT_SIZE_M,
    sizeAttenuation: true,
    vertexColors: Boolean(geometry.colors),
    color: geometry.colors ? 0xffffff : 0x8fa2c4,
  });
  const points = new THREE.Points(buffer, material);
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
