import { NodeIO } from "@gltf-transform/core";
import { normals, simplify, weld } from "@gltf-transform/functions";
import { MeshoptSimplifier } from "meshoptimizer";

const [input, output] = process.argv.slice(2);
if (!input || !output) {
  throw new Error("usage: node optimize_robot_asset.mjs <input.glb> <output.glb>");
}

await MeshoptSimplifier.ready;
const io = new NodeIO();
const document = await io.read(input);

// MuJoCo's STL-derived XLeRobot visuals contain independent vertex normals,
// which prevent topology welding and leave each browser robot above 400k
// triangles. Rebuild normals after simplification so node names, articulated
// joint hierarchy, materials, and the complete silhouette remain intact.
for (const mesh of document.getRoot().listMeshes()) {
  for (const primitive of mesh.listPrimitives()) primitive.setAttribute("NORMAL", null);
}
await document.transform(
  weld(),
  simplify({simplifier: MeshoptSimplifier, ratio: 0.08, error: 1}),
  normals({overwrite: true}),
);
await io.write(output, document);
