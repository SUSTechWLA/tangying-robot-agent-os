import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

// The replay is where a person finds out what happened. An observer's findings
// are only useful if they appear there, so the rail and the renderer are pinned
// here rather than left to manual inspection.

test("mission rail has one agent observation section", () => {
  assert.match(html, /id="fleet-mission-agents"/);
  assert.match(html, /id="fleet-agents-heading"/);
  assert.match(html, /id="fleet-agent-events"/);
  assert.equal((html.match(/id="fleet-agent-events"/g) || []).length, 1);
});

test("the rail states that the observer is read-only", () => {
  // A reader must not have to infer the observer's authority from its output.
  assert.match(html, /观察 agent 只读地报告/);
  assert.match(html, /不修改任务状态/);
});

test("agent events are rendered from the experience payload", () => {
  assert.match(app, /function renderMissionAgentEvents\(/);
  assert.match(app, /experience\.agentEvents/);
  assert.match(app, /renderMissionAgentEvents\(experience\.agentEvents\)/);
});

test("agent events never reach the DOM as markup", () => {
  const start = app.indexOf("function renderMissionAgentEvents(");
  assert.notEqual(start, -1, "renderer is missing");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  // Every string from the server is placed through textContent. innerHTML here
  // would let an agent's message become page structure.
  assert.doesNotMatch(body, /innerHTML/);
  assert.doesNotMatch(body, /insertAdjacentHTML/);
  assert.match(body, /makeTextElement|textContent/);
});

test("severity drives the only visual difference between findings", () => {
  const start = app.indexOf("function renderMissionAgentEvents(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  assert.match(body, /severity/);
  assert.match(body, /mission-agent-event/);
});

test("a replay with no agent events still explains itself", () => {
  const start = app.indexOf("function renderMissionAgentEvents(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  // An empty list must say "nothing yet", not render a blank card.
  assert.match(body, /events\.length/);
  assert.match(body, /这里会列出执行 agent 与观察 agent 的事件/);
});

// The supervisor's output is only useful if the part a person can act on is
// actually rendered. "Detected but no advice shown" is the failure this guards.
test("the rail shows the recommended actions, not just the diagnosis", () => {
  assert.match(app, /event\.recommendedActions/);
  assert.match(app, /建议动作/);
});

test("the retry prohibition is rendered as a warning, not a list item", () => {
  // It is the one piece of advice that must never be softened, so it gets its
  // own band rather than a place in the ordered list.
  assert.match(app, /event\.automaticRetryForbidden/);
  assert.match(app, /禁止自动重试/);
  assert.match(app, /mission-agent-forbidden/);
});

test("missing evidence is shown so an incomplete diagnosis says what is absent", () => {
  assert.match(app, /event\.missingEvidence/);
  assert.match(app, /还缺什么/);
});

test("advice and evidence are still placed as text, never as markup", () => {
  const start = app.indexOf("function renderMissionAgentEvents(");
  const end = app.indexOf("\nfunction ", start + 10);
  const body = app.slice(start, end === -1 ? app.length : end);
  assert.doesNotMatch(body, /innerHTML/);
  assert.doesNotMatch(body, /insertAdjacentHTML/);
});
