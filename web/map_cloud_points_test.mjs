import assert from "node:assert/strict";
import test from "node:test";
import { createMapPoints } from "./src/map_cloud_points.js";
import { candidatePointIndices } from "./src/map_viewer.js";

const geometry = {
  level: 2,
  positions: new Float32Array([0, 0, 0, 0, 0, 1]),
  colors: new Uint8Array([128, 64, 32, 255, 255, 255]),
};

test("measured RGB bytes are converted from sRGB into linear display values", () => {
  const points = createMapPoints(geometry, {colorMode:"rgb"});
  const colors = points.geometry.getAttribute("color").array;
  assert.ok(Math.abs(colors[0] - 0.21586) < .001);
  assert.equal(colors[3], 1);
  points.geometry.dispose(); points.material.dispose();
});

test("point picking candidates stay bounded for million-point levels", () => {
  const indices=candidatePointIndices(1_000_000,20_000);
  assert.ok(indices.length<=20_000);
  assert.equal(indices[0],0);
  assert.ok(indices.at(-1)<1_000_000);
});

test("height auxiliary colour spans a legible teal-to-amber scale", () => {
  const points = createMapPoints(geometry, {colorMode:"height", bounds:{min:[0,0,0],max:[1,1,1]}});
  const colors = points.geometry.getAttribute("color").array;
  assert.notDeepEqual([...colors.slice(0, 3)], [...colors.slice(3, 6)]);
  assert.equal(points.material.vertexColors, true);
  points.geometry.dispose(); points.material.dispose();
});

test("default consumers retain measured RGB while explicit RGB without samples is neutral", () => {
  const normal=createMapPoints(geometry);
  assert.ok(Math.abs(normal.geometry.getAttribute("color").array[0]-.21586)<.001);
  const colourless=createMapPoints({...geometry,colors:null},{colorMode:"rgb"});
  const values=[...colourless.geometry.getAttribute("color").array];
  assert.deepEqual(values.slice(0,3),values.slice(3,6));
  assert.equal(colourless.userData.measuredColorAvailable,false);
  normal.geometry.dispose();normal.material.dispose();colourless.geometry.dispose();colourless.material.dispose();
});
