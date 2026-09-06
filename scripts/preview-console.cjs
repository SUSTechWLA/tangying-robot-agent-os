/* Current workspace UI + one coherent backend for observations and scene assets.
 * CONSOLE_BACKEND_URL=http://127.0.0.1:18080 CONSOLE_PREVIEW_PORT=18130 node scripts/preview-console.cjs
 */
const http = require('node:http');
const https = require('node:https');
const net = require('node:net');
const tls = require('node:tls');
const fs = require('node:fs');
const path = require('node:path');

function createPreviewServer(options = {}) {
  const root = path.resolve(options.root || path.join(__dirname, '../web'));
  const backend = new URL(options.backend || process.env.CONSOLE_BACKEND_URL || 'http://127.0.0.1:18080');
  if (!['http:', 'https:'].includes(backend.protocol) || backend.username || backend.password) throw new Error('Backend must be an HTTP(S) origin without credentials');
  const secure = backend.protocol === 'https:';
  const transport = secure ? https : http;
  const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json' };
  const server = http.createServer((req, res) => {
    const pathname = new URL(req.url, 'http://localhost').pathname;
    // Never mix a local export with the runtime's different model identity.
    if (pathname === '/healthz' || pathname.startsWith('/v1/') || pathname.startsWith('/assets/')) {
      const target = new URL(backend.origin);
      target.pathname = pathname;
      target.search = new URL(req.url, 'http://localhost').search;
      const proxy = transport.request(target, { method: req.method, headers: { ...req.headers, host: backend.host } }, upstream => {
        res.writeHead(upstream.statusCode, { ...upstream.headers, 'cache-control': 'no-store' }); upstream.pipe(res);
      });
      proxy.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end('Backend unavailable'); });
      res.on('close', () => proxy.destroy());
      req.pipe(proxy);
      return;
    }
    const file = path.resolve(root, '.' + (pathname === '/' ? '/index.html' : pathname));
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); res.end(); return; }
    res.writeHead(200, { 'content-type': types[path.extname(file)] || 'application/octet-stream', 'cache-control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  });
  server.on('upgrade', (req, socket, head) => {
    const port = Number(backend.port || (secure ? 443 : 80));
    const connect = () => {
      // Preserve the browser-facing Host so the backend can validate Origin.
      // Rewriting Host to the internal port rejects legitimate same-origin WS.
      upstream.write(`${req.method} ${req.url} HTTP/1.1\r\n` + Object.entries(req.headers).map(([key, value]) => `${key}: ${value}`).join('\r\n') + '\r\n\r\n');
      if (head.length) upstream.write(head);
      socket.pipe(upstream); upstream.pipe(socket);
    };
    const upstream = secure ? tls.connect({ host: backend.hostname, port, servername: backend.hostname }, connect) : net.connect(port, backend.hostname, connect);
    upstream.on('error', () => socket.destroy()); socket.on('error', () => upstream.destroy());
    socket.on('close', () => upstream.destroy()); upstream.on('close', () => socket.destroy());
  });
  return server;
}

function start() {
  const port = Number(process.env.CONSOLE_PREVIEW_PORT || 18130);
  const server = createPreviewServer();
  server.listen(port, '127.0.0.1', () => console.log(`Workspace UI: http://127.0.0.1:${port} (scene assets follow backend)`));
  return server;
}
module.exports = { createPreviewServer, start };
if (require.main === module) start();
