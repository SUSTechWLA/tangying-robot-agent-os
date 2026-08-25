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
  assert.match(html, /任务步骤与机器人动作/);
  assert.match(html, /每个步骤下面会直接显示调用的能力/);
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

test("MuJoCo and RGB-D evidence preserve the whole physical frame and support enlargement", () => {
  assert.match(html, /id="fleet-overview-expand"/);
  assert.match(html, /id="fleet-overview-dialog"/);
  assert.match(html, /id="fleet-overview-dialog-image"/);
  assert.match(css, /\.sim-overview-card img[\s\S]*?object-fit:\s*contain/);
  assert.match(css, /\.sensor-evidence-card img[\s\S]*?object-fit:\s*contain/);
  assert.match(css, /\.sensor-evidence-grid\s*\{[^}]*grid-template-columns:\s*1fr/);
  assert.match(css, /\.fleet-overview-dialog/);
});

test("subtasks use compact disclosure cards and keep the active step prominent", () => {
  assert.match(app, /mission-step-summary/);
  assert.match(app, /mission-step-status/);
  assert.match(app, /item\.open\s*=\s*missionStepNeedsAttention/);
  assert.match(app, /mission-tool-details/);
  assert.match(css, /\.mission-step-summary/);
  assert.match(css, /\.mission-step-title/);
  assert.match(css, /\.mission-step:not\(\[open\]\) \.mission-step-title/);
});

test("an active task prioritizes its steps over the reusable create form", () => {
  assert.match(html, /<details id="fleet-create-card"[^>]*open/);
  assert.match(html, /<summary[^>]*class="mission-create-summary"/);
  assert.match(app, /\$\("#fleet-create-card"\)\.open = false/);
  assert.match(css, /\.mission-rail-header h3[\s\S]*?-webkit-line-clamp:\s*2/);
  assert.match(css, /\.mission-create-summary/);
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
