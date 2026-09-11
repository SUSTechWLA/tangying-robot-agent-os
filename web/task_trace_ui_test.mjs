// The replay has to be reachable and stay current, otherwise a developer will
// fall back to correlating the event log by hand — which is the problem this
// feature exists to remove.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");
const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const trace = await readFile(new URL("./task_trace.js", import.meta.url), "utf8");

test("the workspace has a dedicated task replay panel", () => {
  assert.match(html, /id="local-replay-panel"/);
  assert.match(html, /id="local-replay-title"/);
  assert.match(html, /id="local-replay-body"/);
  assert.match(html, /id="local-replay-state"/);
  assert.match(html, /id="refresh-local-replay"/);
  assert.match(html, /任务全过程回放/);
});

test("the panel is on the workspace page, not hidden behind developer mode", () => {
  const panel = html.slice(html.indexOf('id="local-replay-panel"'));
  const tag = panel.slice(0, panel.indexOf(">") + 1);
  assert.match(tag, /data-page-panel="workspace"/);
  assert.doesNotMatch(tag, /data-developer/);
});

test("the trace module is loaded before the app that calls it", () => {
  const traceIndex = html.indexOf("./task_trace.js");
  const appIndex = html.indexOf("./app.js");
  assert.ok(traceIndex > 0, "task_trace.js must be loaded");
  assert.ok(appIndex > traceIndex, "task_trace.js must load before app.js");
  assert.match(html.slice(traceIndex - 40, traceIndex + 40), /defer/);
});

test("opening a task renders the replay", () => {
  assert.match(app, /await Promise\.all\(\[loadLocalTaskExperience\(taskId\), loadLocalRecovery\(taskId\), loadLocalEvidence\(taskId\)\]\);\s*\n\s*renderLocalReplay\(\);/);
});

test("the replay reuses loaded state instead of refetching it", () => {
  const body = app.slice(app.indexOf("function renderLocalReplay()"), app.indexOf("let localReplayTimer"));
  assert.match(body, /localEvidenceRecords\.filter/);
  assert.match(body, /localExperiencePayload/);
  assert.match(body, /localRecovery/);
  // No new endpoints: the four sources are all already loaded on task open.
  assert.doesNotMatch(body, /fetch\(/);
});

test("live events keep the replay current without thrashing the DOM", () => {
  assert.match(app, /function scheduleLocalReplay/);
  // Throttled: repeated events during one step must not repaint every time.
  assert.match(app, /localReplayTimer = setTimeout/);
  assert.match(app, /if \(localReplayTimer\) return;/);
  // A finished task renders immediately so the final trace is never stale.
  assert.match(app, /LOCAL_TERMINAL_STATES\.has\(task\.state\)\) scheduleLocalReplay\(\{ immediate: true \}\)/);
});

test("selecting another task clears the previous replay", () => {
  assert.match(app, /function clearLocalReplay/);
  assert.match(app, /clearLocalReplay\(\);/);
  assert.match(app, /local-replay-body/);
});

test("the replay only rebuilds when something it displays changed", () => {
  // The task poll runs continuously; an unconditional rebuild would reload
  // every evidence thumbnail on each cycle.
  assert.match(app, /const renderKey = \[/);
  assert.match(app, /if \(body\.dataset\.renderKey === renderKey && body\.childElementCount > 0\) return;/);
  assert.match(app, /body\.dataset\.renderKey = renderKey;/);
  // A different task must not inherit the previous key.
  assert.match(app, /delete body\.dataset\.renderKey/);
});

test("the replay degrades gracefully when the module is missing", () => {
  assert.match(app, /if \(!traceApi\) \{[\s\S]{0,120}回放组件未能加载/);
  assert.match(app, /这个任务还没有可复盘的事件。/);
});

test("the module exposes everything the app uses", () => {
  for (const name of ["buildTaskTrace", "renderTaskTrace"]) {
    assert.match(trace, new RegExp(`globalThis\\.TangyingTaskTrace = \\{[\\s\\S]*${name}`), name);
  }
  assert.match(app, /globalThis\.TangyingTaskTrace/);
});

test("the replay panel is styled as a full-width block", () => {
  assert.match(css, /\.local-replay-panel \{ grid-column: 1 \/ -1; \}/);
  assert.match(css, /\.task-trace \{/);
  assert.match(css, /\.trace-evidence img \{/);
  // The verdict colour is the at-a-glance answer.
  assert.match(css, /\.trace-verdict\.(ok|bad|warn) \{/);
});

test("evidence thumbnails are sized so a long trace stays scannable", () => {
  assert.match(css, /\.trace-evidence img \{[^}]*width: 160px/);
  assert.match(css, /@media \(max-width: 720px\)[\s\S]*\.trace-evidence img/);
});

test("the replay never claims an unverified step is complete", () => {
  // The step markup derives its status from the trace, and the "no evidence"
  // warning is rendered inline rather than only in the summary.
  assert.match(trace, /改变了物理世界，但没有关联观测证据/);
  assert.match(trace, /一致性检查/);
});
