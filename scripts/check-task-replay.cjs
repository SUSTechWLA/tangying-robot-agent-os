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
    if (taskId) {
      // Older tasks sit outside the visible history window, so reach them the
      // way a developer would: paste the id into the lookup box.
      await page.fill('#local-task-id', taskId);
      await page.click('#local-task-lookup button[type="submit"]');
      await page.waitForFunction(id => {
        const state = document.querySelector('#local-task-lookup-state');
        return state && state.textContent.includes(id) && !state.textContent.startsWith('正在读取');
      }, taskId, { timeout: 20000 });
      const message = await page.locator('#local-task-lookup-state').innerText();
      assert.ok(!message.includes('找不到任务编号'), `lookup failed for ${taskId}: ${message}`);
    } else {
      await page.locator('#local-task-list li').first().click();
    }

    await page.waitForSelector('.task-trace', { timeout: 25000 });
    // The experience record loads separately; give the panel its update.
    const summaryValue = label => page.evaluate(name => {
      const terms = [...document.querySelectorAll('.trace-summary dt')];
      const match = terms.find(node => node.textContent.trim() === name);
      return match?.nextElementSibling?.textContent.trim() || '';
    }, label);
    // Playwright's second argument is `arg`, not options: passing the options
    // object there silently leaves the wait on its 30 s default.
    await page.waitForFunction(() => {
      const terms = [...document.querySelectorAll('.trace-summary dt')];
      const match = terms.find(node => node.textContent.trim() === '系统理解为');
      const value = match?.nextElementSibling?.textContent.trim();
      return Boolean(value);
    }, null, { timeout: 20000 });

    const understanding = await summaryValue('系统理解为');
    const replayedId = await summaryValue('任务编号');
    const steps = await page.locator('.trace-step').count();
    const evidenceImages = await page.locator('.trace-evidence img').count();
    const eventRows = await page.locator('.trace-table tbody tr').count();
    const verdict = (await page.locator('.trace-verdict').innerText()).trim();
    const declared = await page.locator('.trace-declared li').count();

    console.log('verdict:', verdict);
    console.log('steps:', steps, '| evidence images:', evidenceImages,
      '| event rows:', eventRows, '| declared subtasks:', declared);
    console.log('understanding:', understanding);
    console.log('replayed task id:', replayedId);

    assert.ok(eventRows > 0, 'the replay must list raw events');
    assert.ok(understanding, 'the replay must state what the system understood');
    if (taskId) assert.equal(replayedId, taskId, 'the replay must show the task it is replaying');

    if (steps === 0) {
      // A task can end without a single tool call: the failure happened during
      // decomposition or target binding, or the task is still waiting to start.
      // The replay then has to explain that gap, because "no steps" is the
      // finding rather than an empty panel.
      assert.match(await page.locator('.task-trace').innerText(), /还没有工具步骤：/);
      assert.match(verdict, /0 个工具步骤/, `a stepless task needs a verdict that says so: ${verdict}`);
      console.log('no tool steps: the replay explains the gap instead of showing an empty panel');
    } else {
      assert.ok(declared > 0, 'the declared decomposition must be shown');
    }
    if (process.env.REPLAY_EXPECT_STEPS) {
      assert.ok(steps > 0, `REPLAY_EXPECT_STEPS was set but the replay listed no tool steps`);
    }

    // Thumbnails must resolve through the API, not render a broken image: the
    // route takes the record hash, so a wrong id shows up here. They are lazy
    // loaded on purpose, so walk the page instead of jumping to the end: a
    // single jump past the viewport never triggers the images it skipped, and
    // the check would then time out on thumbnails that are perfectly healthy.
    const deadline = Date.now() + 60000;
    await page.evaluate(async () => {
      const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
      for (let y = 0; y < document.body.scrollHeight; y += Math.round(window.innerHeight / 2)) {
        window.scrollTo(0, y);
        await pause(120);
      }
      window.scrollTo(0, document.body.scrollHeight);
    });
    while (Date.now() < deadline) {
      const pending = await page.evaluate(() => {
        const images = [...document.querySelectorAll('.trace-evidence img')];
        return images.filter(image => !image.complete).length;
      });
      if (pending === 0) break;
      await page.waitForTimeout(500);
    }
    if (evidenceImages > 0) {
      await page.waitForFunction(() => {
        const images = [...document.querySelectorAll('.trace-evidence img')];
        return images.length > 0 && images.every(image => image.complete);
      }, null, { timeout: 15000 });
      const loaded = await page.evaluate(() =>
        [...document.querySelectorAll('.trace-evidence img')]
          .filter(image => image.complete)
          .map(image => ({ src: image.src, ok: image.naturalWidth > 0 })));
      const broken = loaded.filter(item => !item.ok);
      assert.equal(broken.length, 0, `evidence thumbnails failed to load: ${JSON.stringify(broken.slice(0, 2))}`);
      console.log('evidence thumbnails decoded:', loaded.length);
    } else {
      console.log('no evidence images: the task recorded no capture to verify against');
    }

    // Per-step detail exposes the arguments and command identity.
    if (steps > 0) {
      await page.locator('.trace-step .trace-detail summary').first().click();
      const detail = await page.locator('.trace-step .trace-detail').first().innerText();
      assert.match(detail, /调用参数/);
      assert.match(detail, /命令编号/);
    }
    // The lookup that reaches a task outside the visible history window is part
    // of the feature, so a named task must also prove it is reachable by id.
    if (taskId) {
      assert.match(await page.locator('#local-task-lookup-state').innerText(), new RegExp(`已打开 ${taskId}`));
    }

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
