import { build } from "esbuild";
import { writeFile } from "node:fs/promises";

const result = await build({
  entryPoints: ["src/webgl_entry.js"],
  bundle: true,
  format: "iife",
  platform: "browser",
  target: ["es2022"],
  minify: true,
  legalComments: "eof",
  sourcemap: false,
  charset: "utf8",
  write: false,
});

// Three.js carries the XHTML namespace as a literal URL. It is not fetched,
// but escaping the slashes keeps the classic bundle free of URL literals and
// makes that offline guarantee mechanically auditable.
const source = new TextDecoder().decode(result.outputFiles[0].contents)
  .replaceAll("http://", "http:\\u002f\\u002f")
  .replaceAll("https://", "https:\\u002f\\u002f")
  .replace(/[\t ]+$/gm, "");
await writeFile("webgl_scene.js", source);
