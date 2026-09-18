// A field that has not been read yet must not read as a healthy one.
//
// This is the failure the console keeps having to be argued out of: an empty
// element renders as reassurance, and "the console could not read it" becomes
// indistinguishable from "it is fine". It was true of the problems panel (a
// thrown renderer looked like no problems) and it was true of the E-Stop field.

import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const html = await readFile(new URL("./index.html", import.meta.url), "utf8");
const app = await readFile(new URL("./app.js", import.meta.url), "utf8");

test("the E-Stop field starts as unconfirmed rather than safe", () => {
  const match = html.match(/<strong id="estop-state">([^<]*)<\/strong>/);
  assert.ok(match, "the E-Stop field is missing from the diagnostics row");
  const placeholder = match[1].trim();
  assert.notEqual(placeholder, "安全",
    "a page that has not read telemetry yet must not report the robot as safe");
  assert.equal(placeholder, "待确认",
    "the placeholder must be the same word the renderer uses for missing data");
});

test("the E-Stop renderer keeps three states, not two", () => {
  // Stopped, not stopped, and not known. Collapsing the third into either of the
  // others is the whole defect: a snapshot that never arrived would otherwise
  // render as "not stopped".
  assert.match(app, /snapshot\?\.emergencyStopped \? "已停止" : snapshot \? "未报告急停" : "待确认"/);
});

test("every diagnostics placeholder is a dash rather than a value", () => {
  // The field sits in a row whose other entries all use the same "—" for "not
  // read". One of them asserting a fact was the anomaly, not the pattern.
  for (const id of ["mode", "robot-id", "telemetry-adapter", "software-version"]) {
    assert.match(html, new RegExp(`<strong id="${id}">—</strong>`), `${id} placeholder`);
  }
});
