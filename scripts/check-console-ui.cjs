/* Optional browser acceptance. Uses read-only live data, then intercepts all task
 * mutations with fixtures. Start a console first; install playwright separately.
 * CONSOLE_URL=http://127.0.0.1:18130 PLAYWRIGHT_MODULE=... node scripts/check-console-ui.cjs
 */
const assert = require('node:assert/strict');
const path = require('node:path');
const fs = require('node:fs');
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright');
const base = process.env.CONSOLE_URL || 'http://127.0.0.1:18130';
const output = path.resolve(process.env.CONSOLE_UI_OUTPUT || 'artifacts/ui-v1');
fs.mkdirSync(output, { recursive: true });
(async () => {
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PLAYWRIGHT_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_EXECUTABLE } : {}),
    args: ['--enable-unsafe-swiftshader'],
  });
  const checks = [];
  const errors = [];
  const record = (name) => { checks.push(name); console.log(`PASS ${name}`); };
  try {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, permissions: ['clipboard-read', 'clipboard-write'] });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(base, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(() => document.body.classList.contains('fleet-mode') && !document.querySelector('#fleet-dashboard').hidden);
    await page.waitForFunction(() => document.querySelector('#workspace-connection').textContent === '场景已同步');
    assert.equal(await page.locator('[data-nav="diagnostics"]').isVisible(), false);
    assert.equal(await page.locator('.fleet-diagnostics').isVisible(), false);
    assert.equal(await page.locator('#fleet-request').inputValue(), '');
    await page.locator('#fleet-mission-rail .example-chip').first().click();
    assert.match(await page.locator('#fleet-request').inputValue(), /方块/);
    assert.equal(await page.locator('#workspace-task-status').innerText(), '准备新任务');
    await page.locator('#fleet-request').fill('');
    await page.screenshot({ path: path.join(output, 'workspace-desktop.png'), fullPage: true });
    record('live desktop defaults to operator, template only fills, no overflow');
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);

    await page.locator('[data-nav="devices"]').click();
    assert.equal(await page.locator('#fleet-devices').isVisible(), true);
    assert.equal(await page.locator('.fleet-mission-layout').isVisible(), false);
    await page.screenshot({ path: path.join(output, 'devices-desktop.png'), fullPage: true });
    await page.locator('#audience-toggle').click();
    assert.equal(await page.locator('.fleet-diagnostics').isVisible(), true);
    assert.equal(await page.locator('#page-title').evaluate(el => el === document.activeElement), true);
    await page.locator('#copy-diagnostics').click();
    const copied = JSON.parse(await page.evaluate(() => navigator.clipboard.readText()));
    assert.ok(copied.world.revision > 0);
    assert.doesNotMatch(JSON.stringify(copied), /fleetToken|apiKey|password/);
    await page.locator('#fleet-mission-professional').evaluate(el => { el.open = true; });
    await page.locator('#diagnostic-search').fill('pick');
    await page.evaluate(() => {
      const make = (value) => { const el = document.createElement('code'); el.textContent = value; return el; };
      document.querySelector('#fleet-professional-activities').replaceChildren(make('pick command'), make('place command'));
    });
    await page.waitForFunction(() => document.querySelector('#fleet-professional-activities').lastElementChild.hidden);
    await page.locator('#diagnostic-search').fill('');
    await page.screenshot({ path: path.join(output, 'diagnostics-desktop.png'), fullPage: true });
    await page.locator('#audience-toggle').click();
    assert.equal(await page.locator('.fleet-diagnostics').isVisible(), false);
    record('device route, developer route, focus, copy summary, live filter, return to operator');

    for (const width of [390, 768]) {
      await page.setViewportSize({ width, height: 844 });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true, `overflow at ${width}`);
      assert.equal(await page.locator('#fleet-create').isVisible(), true);
      await page.locator('[data-nav="devices"]').click();
      assert.equal(await page.locator('#fleet-devices').isVisible(), true);
      await page.locator('[data-nav="workspace"]').last().click();
      if (width === 390) await page.screenshot({ path: path.join(output, 'workspace-mobile.png'), fullPage: true });
    }
    record('390px mobile and 768px tablet navigation and layout');
    await page.setViewportSize({ width: 1440, height: 1000 });

    // From here, every task API call is intercepted; no live task is created.
    let task = { id: 'ui-test-task', state: 'READY', request: '浏览器交互测试任务', approved: false };
    let creates = 0; let approves = 0; let cancels = 0; let failCreate = false; let failCancel = false;
    const experience = () => ({ schemaVersion: 'task.experience.v1', taskId: task.id, revision: 1,
      aggregateVersion: task.state === 'READY' ? 1 : task.state === 'EXECUTING' ? 2 : 3, cursor: 1,
      headline: '把方块放到交接区', understanding: '1 号机器人把红色方块放到交接区。', updateStatus: 'ACTIVE',
      steps: [{ stepId: 'pick-1', status: 'RUNNING', statusText: '正在执行', explanation: '移动红色方块', assignedRobot: 'robot-1' }],
      activities: [], professional: { activities: [{ commandId: 'cmd-test', toolName: 'pick' }], stepEvidence: [] },
      allowedActions: ['update'], recovery: null,
    });
    await page.route('**/v1/tasks**', async route => {
      const req = route.request(); const pathname = new URL(req.url()).pathname;
      let body;
      if (pathname === '/v1/tasks' && req.method() === 'POST') {
        creates++; await new Promise(resolve => setTimeout(resolve, 250));
        if (failCreate) return route.fulfill({ status: 400, json: { message: '请描述一个受支持的任务' } });
        body = task;
      } else if (pathname.endsWith('/approve')) { approves++; task = { ...task, approved: true, state: 'EXECUTING' }; body = task;
      } else if (pathname.endsWith('/cancel')) { cancels++; if (failCancel) return route.abort(); task = { ...task, state: 'CANCELLED' }; body = task;
      } else if (pathname.endsWith('/experience')) body = experience();
      else if (pathname.endsWith('/intents')) body = { state: task.state, intents: [], robots: [] };
      else if (pathname === '/v1/tasks') body = [task];
      else body = task;
      await route.fulfill({ status: 200, json: body });
    });
    await page.locator('#fleet-request').fill('让1号机器人把红色方块放到交接区');
    await page.locator('#fleet-create').click();
    await page.locator('#fleet-create').dispatchEvent('click');
    await page.waitForFunction(() => document.querySelector('#workspace-task-status').textContent === '正在执行');
    assert.equal(creates, 1); assert.equal(approves, 1);
    assert.equal(await page.locator('#fleet-mission-understanding').isVisible(), true);
    await page.locator('[data-nav="tasks"]').click();
    await page.locator('#fleet-tasks button').first().focus();
    await page.keyboard.press('Enter');
    assert.equal(await page.locator('.fleet-mission-layout').isVisible(), true);
    failCancel = true;
    await page.locator('#fleet-cancel').click();
    await page.waitForFunction(() => document.querySelector('#console-feedback').textContent.includes('取消结果尚未确认'));
    failCancel = false;
    await page.locator('#fleet-cancel').click();
    await page.waitForFunction(() => document.querySelector('#workspace-task-status').textContent === '任务已取消');
    assert.equal(cancels, 2);
    failCreate = true;
    await page.locator('#fleet-create').click();
    await page.waitForFunction(() => document.querySelector('#console-feedback').textContent.includes('受支持'));
    assert.equal(await page.locator('#console-feedback').isVisible(), true);
    record('fixture task create, duplicate suppression, keyboard history, cancel success/unknown, visible errors');

    // Exercise Fleet safety propagation through the actual world renderer.
    const safetyVisible = await page.evaluate(() => {
      renderFleetWorld({ ...fleetWorldLatestSnapshot, robots: { 'robot-test': { robotId: 'robot-test', emergencyStopped: true, pose: [0,0,0] } } });
      return !document.querySelector('#workspace-safety-alert').hidden;
    });
    assert.equal(safetyVisible, true);
    record('Fleet emergency stop reaches the operator alert');

    const operator = await context.newPage();
    await operator.route('**/', async route => {
      const response = await route.fetch();
      const html = (await response.text()).replace('name="tangying-console-edition" content="full"', 'name="tangying-console-edition" content="operator"');
      await route.fulfill({ response, body: html });
    });
    await operator.goto(base + '/#diagnostics', { waitUntil: 'domcontentloaded' });
    assert.equal(await operator.locator('#audience-toggle').isVisible(), false);
    assert.equal(await operator.locator('#page-title').innerText(), '工作台');
    await operator.locator('#audience-toggle').dispatchEvent('click');
    assert.equal(await operator.locator('.diagnostics-overview').isVisible(), false);
    record('operator edition cannot expose diagnostic routes through navigation');

    const local = await context.newPage(); local.on('pageerror', error => errors.push(error.message));
    let localCreates = 0;
    await local.route('**/healthz', route => route.fulfill({ json: { status: 'ok' } }));
    await local.route('**/v1/**', async route => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/v1/telemetry') return route.fulfill({ json: { adapters: ['mujoco'], latest: {
        adapter: 'mujoco', robotId: 'test-robot', observedAt: new Date().toISOString(), activity: 'IDLE', emergencyStopped: false,
        entities: [{ entityId: 'robot-test', category: 'robot', pose: [0,0,0,1,0,0,0] }], robotState: {},
      } } });
      if (pathname === '/v1/runtime') return route.fulfill({ json: { RobotID: 'test-robot', Adapter: 'mujoco' } });
      if (pathname === '/v1/tasks' && route.request().method() === 'POST') { localCreates++; await new Promise(resolve => setTimeout(resolve, 200)); return route.fulfill({ status: 400, json: { message: '本地任务测试错误' } }); }
      if (pathname === '/v1/scene/frame') return route.fulfill({ status: 404 });
      return route.fulfill({ json: {} });
    });
    await local.goto(base, { waitUntil: 'domcontentloaded' });
    await local.waitForFunction(() => document.querySelector('#adapter').value === 'mujoco');
    await local.locator('#request').fill('把红色杯子放进右侧收纳盒');
    await local.locator('#create').click(); await local.locator('#create').dispatchEvent('click');
    await local.waitForFunction(() => document.querySelector('#console-feedback').textContent.includes('本地任务测试错误'));
    assert.equal(localCreates, 1);
    await local.setViewportSize({ width: 390, height: 844 });
    assert.equal(await local.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
    await local.screenshot({ path: path.join(output, 'local-mobile.png'), fullPage: true });
    record('local mode submit guard, user errors, mobile layout');
    assert.deepEqual(errors, []);
    fs.writeFileSync(path.join(output, 'browser-checks.json'), JSON.stringify({ checks, pageErrors: errors, taskMutations: 'intercepted fixtures only', liveSource: base }, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
