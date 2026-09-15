/**
 * 把某一期（或全部）信息图的图源 HTML 渲染成 PNG。
 *
 *   PLAYWRIGHT_MODULE=<playwright-core 路径> node tools/render.mjs [期目录] [图名关键字 ...]
 *
 * 例：
 *   node tools/render.mjs                      # 渲染所有期
 *   node tools/render.mjs 03-工具调用            # 只渲染第 03 期
 *   node tools/render.mjs 03-工具调用 04         # 只渲染第 03 期里文件名含 "04" 的图
 *   node tools/render.mjs 02 04 05             # 全部期里文件名含 04 或 05 的图
 *
 * 约定每一期的目录形状：
 *   <期目录>/figures/src/*.html   ← 图源（改文案改这里）
 *   <期目录>/figures/*.png        ← 产物（渲染生成，不要手改）
 *
 * 所有 SVG 都是内联手写的，因此输出是确定性的：不依赖网络字体、不引用外部图片。
 * 截图对象是 `.fig` 元素本身而不是整个视口，所以页面留白永远不会混进裁切结果。
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
const seriesRoot = path.join(here, '..'); // artifacts/marketing

/** 每一期是一个「NN-名称」目录，且带 figures/src/ 图源目录。 */
function listSeries() {
  return readdirSync(seriesRoot)
    .filter((d) => /^\d\d-/.test(d))
    .filter((d) => existsSync(path.join(seriesRoot, d, 'figures', 'src')))
    .sort();
}

/**
 * 第一个参数如果是已存在的期目录，就只渲染那一期；否则把所有参数都当作图名关键字。
 * 这样 `render.mjs 03-工具调用 04` 和 `render.mjs 04` 都能按直觉工作。
 */
const argv = process.argv.slice(2);
const all = listSeries();
const seriesArg = argv.find((a) => all.includes(a));
const series = seriesArg ? [seriesArg] : all;
const filters = argv.filter((a) => a !== seriesArg);

if (series.length === 0) {
  console.error('没有找到任何期目录（期望形如 01-总体架构/figures/src/ 的结构）');
  process.exit(2);
}

// 收集待渲染任务：{ 期目录, 图源文件, 产物路径 }
const jobs = [];
for (const s of series) {
  const srcDir = path.join(seriesRoot, s, 'figures', 'src');
  const outDir = path.join(seriesRoot, s, 'figures');
  mkdirSync(outDir, { recursive: true });
  for (const f of readdirSync(srcDir).filter((f) => f.endsWith('.html')).sort()) {
    if (filters.length > 0 && !filters.some((k) => f.includes(k))) continue;
    jobs.push({ series: s, srcDir, outDir, file: f });
  }
}

if (jobs.length === 0) {
  console.error('没有匹配的图源 HTML；期目录 =', series.join(', '), '关键字 =', filters.join(', ') || '(无)');
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

for (const job of jobs) {
  const { series: seriesName, srcDir, outDir, file } = job;
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
    console.error(`✗ ${seriesName}/${name}: expected exactly one .fig element, found ${count}`);
    failed++;
    await page.close();
    continue;
  }

  const box = await fig.boundingBox();
  const out = path.join(outDir, `${name}.png`);
  await fig.screenshot({ path: out });
  const report = `${seriesName}/${name}.png  ${Math.round(box.width)}×${Math.round(box.height)} css → ${Math.round(box.width * 2)}×${Math.round(box.height * 2)} px`;
  if (errors.length) {
    console.error(`✗ ${seriesName}/${name}: page errors\n  ${errors.join('\n  ')}`);
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
