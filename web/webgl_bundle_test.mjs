import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

test("classic bundle is self-contained and exposes only the WebGL public API", async () => {
  const source = await readFile(new URL("./webgl_scene.js", import.meta.url), "utf8");
  assert.doesNotMatch(source, /\bimport\s*(?:\(|["'{*])/);
  assert.doesNotMatch(source, /https?:\/\//i);
  assert.doesNotMatch(source, /\beval\s*\(/);
  assert.doesNotMatch(source, /sourceMappingURL/i);

  const context = vm.createContext({
    globalThis: {}, console, URL, TextDecoder, TextEncoder,
    ArrayBuffer, DataView, Uint8Array, Math, Number, Object, Promise,
    AbortController,
  });
  vm.runInContext(source, context, { filename: "webgl_scene.js" });
  assert.deepEqual(
    Object.keys(context.globalThis.TangyingWebGL).sort(),
    ["AssetRegistry", "RobotModelInstance", "WebGLSceneRenderer"],
  );
});
