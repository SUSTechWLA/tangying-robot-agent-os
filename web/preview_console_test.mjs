import assert from "node:assert/strict";
import http from "node:http";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createPreviewServer } from "../scripts/preview-console.cjs";

test("preview serves current UI but keeps visual assets and world data on the same backend", async (t) => {
  const root = await mkdtemp(path.join(os.tmpdir(), "tangying-preview-"));
  const backend = http.createServer((req, res) => {
    res.setHeader("content-type", "application/json");
    res.end(JSON.stringify({ modelHash: "running-scene", path: req.url }));
  });
  await new Promise(resolve => backend.listen(0, "127.0.0.1", resolve));
  await writeFile(path.join(root, "index.html"), "current workspace UI");
  const preview = createPreviewServer({ root, backend: `http://127.0.0.1:${backend.address().port}` });
  await new Promise(resolve => preview.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    preview.closeAllConnections(); backend.closeAllConnections();
    await Promise.all([new Promise(resolve => preview.close(resolve)), new Promise(resolve => backend.close(resolve))]);
    await rm(root, { recursive: true });
  });
  const url = `http://127.0.0.1:${preview.address().port}`;
  assert.equal(await (await fetch(url)).text(), "current workspace UI");
  for (const resource of ["/assets/scenes/scene/manifest.json", "/assets/robots/robot.glb?v=hash", "/v1/world"]) {
    const response = await fetch(url + resource);
    assert.equal(response.headers.get('cache-control'), 'no-store');
    const value = await response.json();
    assert.equal(value.modelHash, "running-scene");
    assert.equal(value.path, resource);
  }
});

test("preview preserves the browser host for same-origin websocket checks without admitting foreign origins", async (t) => {
  const backend = http.createServer();
  backend.on("upgrade", (req, socket) => {
    if (new URL(req.headers.origin).host !== req.headers.host) {
      socket.end("HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n");
      return;
    }
    socket.end("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n");
  });
  await new Promise(resolve => backend.listen(0, "127.0.0.1", resolve));
  const preview = createPreviewServer({ backend: `http://127.0.0.1:${backend.address().port}` });
  await new Promise(resolve => preview.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    await Promise.all([new Promise(resolve => preview.close(resolve)), new Promise(resolve => backend.close(resolve))]);
  });
  const url = `http://127.0.0.1:${preview.address().port}`;
  const upgradeStatus = origin => new Promise((resolve, reject) => {
    const req = http.request(url + "/v1/world/events/ws", { headers: { Upgrade: "websocket", Connection: "Upgrade", Origin: origin } });
    req.on("upgrade", (res, socket) => { socket.destroy(); resolve(res.statusCode); });
    req.on("response", res => { res.resume(); resolve(res.statusCode); });
    req.on("error", reject);
    req.setTimeout(2000, () => req.destroy(new Error("upgrade timed out")));
    req.end();
  });
  assert.equal(await upgradeStatus(url), 101);
  assert.equal(await upgradeStatus("http://foreign.example"), 403);
});
