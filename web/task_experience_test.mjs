import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const css = await readFile(new URL("./styles.css", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

test("Fleet Console contains one plain-language mission rail beside the digital twin", () => {
  assert.match(html, /class="fleet-mission-layout"/);
  assert.match(html, /id="fleet-mission-rail"/);
  assert.match(html, /id="fleet-mission-understanding"/);
  assert.match(html, /id="fleet-step-ribbon"/);
  assert.match(html, /id="fleet-tool-activities"/);
  assert.match(html, /id="fleet-update-journey"/);
  assert.match(html, /机器人理解成了什么任务/);
  assert.match(html, /机器人现在怎么做/);
  assert.match(html, /动作与结果/);
  assert.match(html, /更新这个任务/);
  assert.match(html, /class="mission-professional"/);
  assert.match(html, /专业信息/);
});

test("digital twin contains a prominent four-stage handoff relay", () => {
  assert.match(html, /id="fleet-mission-pulse"/);
  assert.match(html, /id="fleet-relay-robot-1"/);
  assert.match(html, /id="fleet-relay-handoff"/);
  assert.match(html, /id="fleet-relay-robot-2"/);
  assert.match(html, /id="fleet-relay-target"/);
  assert.match(html, /1号机器人[\s\S]*交接区[\s\S]*2号机器人[\s\S]*右侧目标区/);
  assert.match(css, /\.fleet-mission-pulse/);
  assert.match(css, /data-state="active"/);
});

test("mission rail has one concise live region and responsive/reduced-motion rules", () => {
  assert.match(html, /id="fleet-task-experience-status"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.equal((html.match(/id="fleet-task-experience-status"/g) || []).length, 1);
  assert.match(css, /\.fleet-mission-layout\s*\{/);
  assert.match(css, /@media \(max-width: 980px\)[\s\S]*\.fleet-mission-layout\s*\{[^}]*grid-template-columns:\s*1fr/);
  assert.match(css, /@media \(prefers-reduced-motion: reduce\)/);
});

test("task UI uses the versioned API and never renders server strings with innerHTML", () => {
  assert.match(app, /\/revisions`/);
  assert.match(app, /\/confirm`/);
  assert.match(app, /\/experience`/);
  assert.match(app, /expectedRevision/);
  assert.match(app, /expectedCurrentRevision/);
  assert.match(app, /REVISION_CONFLICT/);
  assert.doesNotMatch(app, /innerHTML\s*=/);
});

test("mission rail explains learned control and recovery as a plain-language timeline", () => {
  assert.match(app, /activity\.controlMethod/);
  assert.match(app, /activity\.controlStage/);
  assert.match(app, /activity\.displayName \|\| "capability"/);
  assert.match(app, /recovery\.timeline/);
  assert.match(app, /控制方式/);
  assert.match(app, /恢复过程/);
  assert.match(css, /\.mission-recovery-timeline/);
  assert.match(css, /\.mission-control-method/);
});

test("mission rendering never displays raw policy action chunks", () => {
  assert.match(app, /action_chunk/);
  assert.match(app, /delete sanitized\.action_chunk/);
  assert.doesNotMatch(html, /action_chunk/);
});
