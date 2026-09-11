/* Optional browser acceptance for the task replay panel.
 *
 * Read-only: it opens the console, selects an existing task from the history
 * list and asserts that the replay is complete, that every evidence thumbnail
 * actually decodes from the API, and that no page error occurred. It never
 * creates, approves or cancels a task.
 *
 *   CONSOLE_URL=http://127.0.0.1:8787 \
 *   PLAYWRIGHT_MODULE=/path/to/playwright \
 *   PLAYWRIGHT_EXECUTABLE=/path/to/chrome-headless-shell \
 *   node scripts/check-task-replay.cjs
 *
 * Optional:
 *   REPLAY_TASK=<task id>       select a specific task instead of the newest
 *   REPLAY_SCREENSHOT=/tmp/x.png  save a cropped screenshot of the summary
 */
const assert = require('node:assert/strict');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');

const base = process.env.CONSOLE_URL || 'http://127.0.0.1:8787';
const taskId = process.env.REPLAY_TASK || '';

(async () => {
  const browser = await chromium.launch({
    headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE } : {}),
    args: ['--enable-unsafe-swiftshader'],
  });
  const pageErrors = [];
  const context = await browser.newContext({ viewport: { width: 1180, height: 1200 } });
  const page = await context.newPage();
  page.on('pageerror', error => pageErrors.push(error.message));
  try {
    await page.goto(base, { waitUntil: 'domcontentloaded' });

    const panel = page.locator('#local-replay-panel');
    await panel.waitFor({ state: 'visible', timeout: 15000 });
    assert.match(await panel.locator('h2').innerText(), /任务全过程回放/);

    // The module must be present as a classic script; a stray `export` would
    // parse as an error and leave this undefined.
    const moduleKeys = await page.evaluate(() => Object.keys(globalThis.TangyingTaskTrace || {}));
    assert.ok(moduleKeys.includes('buildTaskTrace'), 'TangyingTaskTrace must be published');
    assert.ok(moduleKeys.includes('renderTaskTraceNodes'), 'DOM builder must be published');

    await page.locator('[data-nav="tasks"]').click();
    await page.waitForSelector('#local-task-list li', { timeout: 20000 });
    const entry = taskId
      ? page.locator(`#local-task-list li:has-text("${taskId}")`).first()
      : page.locator('#local-task-list li').first();
    await entry.click();

    await page.waitForSelector('.task-trace', { timeout: 25000 });
    // The experience record loads separately; give the panel its update.
    await page.waitForFunction(() => {
      const values = [...document.querySelectorAll('.trace-summary dd')].map(node => node.textContent.trim());
      return values.length > 1 && values[1] && values[1] !== '—';
    }, { timeout: 20000 });

    const summary = await page.evaluate(() =>
      [...document.querySelectorAll('.trace-summary dd')].map(node => node.textContent.trim()));
    const steps = await page.locator('.trace-step').count();
    const evidenceImages = await page.locator('.trace-evidence img').count();
    const eventRows = await page.locator('.trace-table tbody tr').count();
    const verdict = (await page.locator('.trace-verdict').innerText()).trim();
    const declared = await page.locator('.trace-declared li').count();

    console.log('verdict:', verdict);
    console.log('steps:', steps, '| evidence images:', evidenceImages,
      '| event rows:', eventRows, '| declared subtasks:', declared);
    console.log('understanding:', summary[1]);

    assert.ok(steps > 0, 'the replay must list tool steps');
    assert.ok(eventRows > 0, 'the replay must list raw events');
    assert.ok(declared > 0, 'the declared decomposition must be shown');
    assert.notEqual(summary[1], '—', 'the plain-language understanding must be filled in');

    // Thumbnails must resolve through the API, not render a broken image: the
    // route takes the record hash, so a wrong id shows up here. They are lazy
    // loaded on purpose, so first wait for the browser to finish decoding.
    await page.evaluate(() => {
      window.scrollTo(0, document.body.scrollHeight);
    });
    await page.waitForFunction(() => {
      const images = [...document.querySelectorAll('.trace-evidence img')];
      return images.length > 0 && images.every(image => image.complete);
    }, { timeout: 30000 });
    const loaded = await page.evaluate(() =>
      [...document.querySelectorAll('.trace-evidence img')]
        .filter(image => image.complete)
        .map(image => ({ src: image.src, ok: image.naturalWidth > 0 })));
    const broken = loaded.filter(item => !item.ok);
    assert.equal(broken.length, 0, `evidence thumbnails failed to load: ${JSON.stringify(broken.slice(0, 2))}`);
    console.log('evidence thumbnails decoded:', loaded.length);

    // Per-step detail exposes the arguments and command identity.
    await page.locator('.trace-step .trace-detail summary').first().click();
    const detail = await page.locator('.trace-step .trace-detail').first().innerText();
    assert.match(detail, /调用参数/);
    assert.match(detail, /命令编号/);

    if (process.env.REPLAY_SCREENSHOT) {
      await page.locator('.trace-block').first().screenshot({ path: process.env.REPLAY_SCREENSHOT });
    }

    assert.deepEqual(pageErrors, [], `page errors: ${pageErrors.join(' | ')}`);
    console.log('TASK REPLAY OK');
  } finally {
    await browser.close();
  }
})().catch(error => {
  console.error('task replay check failed:', error.message);
  process.exit(1);
});
