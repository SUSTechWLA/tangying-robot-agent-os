/* Read-only browser acceptance for a running Fleet/RoboCasa demo.
 * CONSOLE_URL=... PLAYWRIGHT_MODULE=... PLAYWRIGHT_EXECUTABLE=... node scripts/check-scene-views.cjs
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.CONSOLE_URL || 'http://127.0.0.1:18130';
const output = path.resolve(process.env.CONSOLE_UI_OUTPUT || 'artifacts/ui-v1/scenes');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE } : {}),
    args: ['--enable-unsafe-swiftshader'],
  });
  const checks = []; const errors = []; const mutations = [];
  const record = name => { checks.push(name); console.log(`PASS ${name}`); };
  try {
    const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', error => errors.push(error.message));
    await page.route('**/v1/**', async route => {
      const request = route.request();
      if (!['GET', 'HEAD'].includes(request.method()) && !new URL(request.url()).pathname.startsWith('/v1/auth/')) {
        mutations.push(`${request.method()} ${new URL(request.url()).pathname}`);
        return route.abort();
      }
      return route.continue();
    });
    const load = async () => {
      await page.goto(base + '/#workspace', { waitUntil: 'domcontentloaded' });
      await page.waitForFunction(() => document.querySelector('#fleet-visual-state').textContent === 'VISUAL LIVE');
    };
    const select = async view => {
      await page.locator(`[data-scene-view="${view}"]`).click();
      assert.equal(await page.locator(`[data-scene-view="${view}"]`).getAttribute('aria-pressed'), 'true');
    };
    const screenshot = async name => {
      await page.mouse.move(5, 5);
      return page.screenshot({ path: path.join(output, name + '.png'), fullPage: true, animations: 'disabled' });
    };
    await load();
    assert.equal(await page.locator('#fleet-godview-webgl').isVisible(), true);
    const identity = await page.evaluate(() => {
      window.sceneTestRenderer = fleetWorldWebGLRenderer;
      return { modelHash: fleetWorldWebGLRenderer.bundle.modelHash, revision: fleetWorldLatestSnapshot.revision };
    });
    await screenshot('three-desktop');
    record('matching scene identity loads full WebGL by default');
    for (const preset of ['top', 'robot-1', 'robot-2', 'overview']) {
      await page.locator(`[data-world-preset="${preset}"]`).click();
      assert.match(await page.locator(`[data-world-preset="${preset}"]`).getAttribute('class'), /active/);
    }
    await page.locator('.scene-display-options summary').click();
    for (const layer of ['models', 'fixtures', 'labels', 'path']) {
      const button = page.locator(`#fleet-world-${layer}-toggle`);
      const initial = await button.getAttribute('aria-pressed');
      await button.click(); assert.notEqual(await button.getAttribute('aria-pressed'), initial);
      await button.click();
    }
    await page.locator('.scene-display-options summary').click();
    record('all four camera presets and display options remain available to operators');
    await select('simple');
    assert.equal(await page.locator('#fleet-godview-canvas').isVisible(), true);
    assert.equal(await page.locator('#fleet-godview-webgl').isVisible(), false);
    await screenshot('simple-desktop');
    await select('cameras');
    await page.waitForFunction(() => [...document.querySelectorAll('#fleet-scene-camera-panel img')].every(img => !img.hidden && img.naturalWidth > 0));
    await screenshot('cameras-desktop');
    await select('map');
    await page.waitForFunction(() => document.querySelector('#fleet-workspace-map-meta').textContent.includes('收到地图'));
    assert.equal(await page.locator('#fleet-workspace-map-canvas').isVisible(), true);
    assert.equal(await page.evaluate(() => {
      const canvas = document.querySelector('#fleet-workspace-map-canvas');
      const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
      return pixels.some((value, i) => i % 4 !== 3 && value > 40);
    }), true, 'map must contain drawn content');
    await screenshot('map-desktop');
    await select('three');
    assert.equal(await page.evaluate(() => sceneTestRenderer === fleetWorldWebGLRenderer), true);
    assert.ok(await page.evaluate(() => fleetWorldLatestSnapshot.revision) >= identity.revision);
    record('four scene modes share the active world and renderer');
    await page.locator('#fleet-scene-expand').click();
    await page.waitForFunction(() => Boolean(document.fullscreenElement));
    assert.ok((await page.locator('#fleet-godview-webgl').boundingBox()).height > 600);
    await page.locator('#fleet-scene-expand').click();
    await page.waitForFunction(() => !document.fullscreenElement);
    record('fullscreen expands the actual scene and exits normally');
    await select('cameras');
    await load();
    assert.equal(await page.locator('[data-scene-view="cameras"]').getAttribute('aria-pressed'), 'true');
    record('scene preference survives reload');
    for (const width of [390, 768, 1024]) {
      await page.setViewportSize({ width, height: 844 });
      for (const view of ['three', 'simple', 'cameras', 'map']) {
        await select(view);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `${view} overflow at ${width}`);
      }
      if (width === 390) { await select('three'); await screenshot('three-mobile'); await select('cameras'); await screenshot('cameras-mobile'); }
    }
    record('all views fit mobile, tablet, and compact desktop');
    await page.setViewportSize({ width: 1440, height: 1000 });
    await select('cameras');
    await page.waitForFunction(() => [...document.querySelectorAll('#fleet-scene-camera-panel img')].every(img => !img.hidden && img.naturalWidth > 0));
    await page.route('**/v1/scene/frames', route => route.fulfill({ status: 503, json: {} }));
    await page.waitForFunction(() => document.querySelector('#fleet-workspace-camera-status').textContent.includes('未更新'));
    assert.equal(await page.locator('#fleet-workspace-frame-robot-1').evaluate(img => img.classList.contains('stale') && img.naturalWidth > 0), true);
    await screenshot('cameras-disconnected');
    record('failed camera polling preserves and clearly marks the last received frame');
    await select('three');
    const mismatch = await page.evaluate(() => {
      const snapshot = structuredClone(fleetWorldLatestSnapshot);
      for (const entity of Object.values(snapshot.entities)) {
        if (entity.attributes?.model_hash) entity.attributes.model_hash = 'f'.repeat(64);
      }
      renderFleetWorld(snapshot);
      return { fallback: !document.querySelector('#fleet-godview-canvas').hidden,
        detail: document.querySelector('#fleet-visual-detail').textContent,
        guidance: document.querySelector('#visual-guidance').textContent };
    });
    assert.equal(mismatch.fallback, true);
    assert.match(mismatch.detail, /VISUAL_MODEL_MISMATCH/);
    assert.match(mismatch.guidance, /版本不一致/);
    await select('map');
    assert.equal(await page.locator('#fleet-scene-map-panel').isVisible(), true);
    record('model mismatch still rejects 3D assets without blocking alternate views');
    assert.deepEqual(errors, []); assert.deepEqual(mutations, []);
    fs.writeFileSync(path.join(output, 'browser-checks.json'), JSON.stringify({ checks, pageErrors: errors, taskMutations: mutations, liveSource: base, identity }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
