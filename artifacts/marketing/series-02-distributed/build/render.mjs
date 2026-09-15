/**
 * Render the series-02 (distributed systems) infographics from HTML to PNG.
 *
 *   PLAYWRIGHT_MODULE=/path/to/playwright-core node build/render.mjs [name ...]
 *
 * All SVGs are hand-authored inline in the HTML so the output is deterministic:
 * no external fonts, no images, no network. We screenshot each `.fig` element
 * (not the viewport) so page padding can never leak into the crop.
 */
import { createRequire } from 'node:module';
import { readdirSync, mkdirSync, existsSync, statSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import os from 'node:os';
import path from 'node:path';

const require = createRequire(import.meta.url);
const MODULE = process.env.PLAYWRIGHT_MODULE;
if (!MODULE) {
  console.error('set PLAYWRIGHT_MODULE to a playwright or playwright-core package path');
  process.exit(2);
}
const { chromium } = require(MODULE);

/**
 * An npx-cached playwright-core rarely matches the browser revision in the local
 * cache, so resolve an already-downloaded binary ourselves instead of failing on
 * a missing revision. CHROME_PATH overrides everything.
 */
function findChromium() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH ||
    path.join(os.homedir(), 'Library', 'Caches', 'ms-playwright');
  if (!existsSync(root)) return undefined;
  const candidates = [];
  for (const dir of readdirSync(root)) {
    if (!/^chromium/.test(dir)) continue;
    for (const rel of [
      'chrome-headless-shell-mac-arm64/chrome-headless-shell',
      'chrome-headless-shell-mac-x64/chrome-headless-shell',
      'chrome-mac/Chromium.app/Contents/MacOS/Chromium',
      'chrome-mac-arm64/Chromium.app/Contents/MacOS/Chromium',
      'chrome-linux/chrome',
    ]) {
      const p = path.join(root, dir, rel);
      if (existsSync(p)) candidates.push({ p, rev: Number(dir.split('-').pop()) || 0 });
    }
  }
  if (candidates.length === 0) return undefined;
  // Highest revision first: closest to whatever the package expects.
  candidates.sort((a, b) => b.rev - a.rev);
  return candidates[0].p;
}

const here = path.dirname(fileURLToPath(import.meta.url));
const srcDir = path.join(here, 'html');
const outDir = path.join(here, '..', 'figures');
mkdirSync(outDir, { recursive: true });

const filter = process.argv.slice(2);
const files = readdirSync(srcDir)
  .filter((f) => f.endsWith('.html'))
  .filter((f) => filter.length === 0 || filter.some((k) => f.includes(k)))
  .sort();

if (files.length === 0) {
  console.error('no matching html files in', srcDir);
  process.exit(2);
}

const executablePath = findChromium();
if (!executablePath) {
  console.error('no chromium found; set CHROME_PATH');
  process.exit(2);
}
console.log('chromium:', executablePath);

const browser = await chromium.launch({ headless: true, executablePath });
let failed = 0;

for (const file of files) {
  const name = file.replace(/\.html$/, '');
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1200 },
    deviceScaleFactor: 2,
  });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('console', (m) => {
    if (m.type() === 'error') errors.push(m.text());
  });

  await page.goto('file://' + path.join(srcDir, file), { waitUntil: 'load' });
  // Fonts must be settled before measuring, or the crop is sized for fallback text.
  await page.evaluate(() => document.fonts.ready);

  const fig = page.locator('.fig');
  const count = await fig.count();
  if (count !== 1) {
    console.error(`✗ ${name}: expected exactly one .fig element, found ${count}`);
    failed++;
    await page.close();
    continue;
  }

  const box = await fig.boundingBox();
  const out = path.join(outDir, `${name}.png`);
  await fig.screenshot({ path: out });
  const report = `${name}.png  ${Math.round(box.width)}×${Math.round(box.height)} css → ${Math.round(box.width * 2)}×${Math.round(box.height * 2)} px`;
  if (errors.length) {
    console.error(`✗ ${name}: page errors\n  ${errors.join('\n  ')}`);
    failed++;
  } else {
    console.log(`✓ ${report}`);
  }
  await page.close();
}

await browser.close();
if (failed) {
  console.error(`\n${failed} figure(s) failed`);
  process.exit(1);
}
