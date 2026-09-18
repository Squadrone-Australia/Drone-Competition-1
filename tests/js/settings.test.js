// DOM-level tests for comp1/frontend/settings.js, the startup-preferences dialog.
//
// The scenery list is the point of this file. index.html used to hard-code the
// options, and the copy went stale: it still offered "Corridor (A to B)" long
// after that scenery had been replaced by the fixed competition arena, so the
// menu named a room the simulator would never build. Nothing caught it, because
// no test ever opened the dialog. These tests render it from a `settings`
// message and check it matches the catalogue the server actually sends.
// Run with: node --test 'tests/js/**/*.test.js'
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const FRONTEND = path.join(__dirname, "..", "..", "comp1", "frontend");

const dom = new JSDOM(fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8"),
  { url: "http://localhost/" });
const { window } = dom;
const { document } = window;

// jsdom implements <dialog> but not showModal/close in every version; the
// dialog is opened by a click handler at load time, so stub them defensively.
window.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
window.HTMLDialogElement.prototype.close = function () { this.open = false; };

let lastSent = null;
window.COMP1_SEND = (message) => { lastSent = message; };
let busHandler = null;
window.COMP1_BUS = { on: (fn) => { busHandler = fn; } };

eval(fs.readFileSync(path.join(FRONTEND, "settings.js"), "utf8"));

// What comp1/sim/scenery.py's catalog() sends today.
const CATALOG = [
  { id: "arena", name: "Square arena", description: "4 m room, markers around the walls." },
  { id: "corridor", name: "Competition arena", description: "Fixed 6 x 4 x 3 m Search and Find layout." },
];

const settingsMessage = (overrides = {}) => ({
  type: "settings",
  version: "0.2.0",
  installed: true,
  persisted: true,
  can_quit: true,
  sceneries: CATALOG,
  settings: { drone: "sim", scenery: "arena", check_updates: true },
  ...overrides,
});

const sceneryEl = () => document.getElementById("settings-scenery");
const options = () => [...sceneryEl().options].map((o) => ({ value: o.value, text: o.textContent }));

test("index.html hard-codes no scenery options of its own", () => {
  // The regression that started this: a second copy of the names, free to drift
  // away from the catalogue. The dialog must be empty until the server fills it.
  const markup = fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8");
  const select = markup.match(/<select id="settings-scenery">([\s\S]*?)<\/select>/);
  assert.ok(select, "the settings dialog still needs a #settings-scenery select");
  assert.strictEqual(select[1].trim(), "", "scenery options belong to the server, not the markup");
});

test("a settings message renders the server's catalogue", () => {
  assert.strictEqual(typeof busHandler, "function");
  busHandler(settingsMessage());
  assert.deepStrictEqual(options(), [
    { value: "arena", text: "Square arena" },
    { value: "corridor", text: "Competition arena" },
  ]);
});

test('the stale "Corridor" label is gone for good', () => {
  busHandler(settingsMessage());
  const text = options().map((o) => o.text).join(" ");
  assert.ok(!/corridor/i.test(text), `the dialog still offers a corridor: ${text}`);
});

test("the saved preference is selected, not just listed", () => {
  busHandler(settingsMessage({ settings: { drone: "sim", scenery: "corridor", check_updates: true } }));
  assert.strictEqual(sceneryEl().value, "corridor");
});

test("descriptions ride along as tooltips", () => {
  busHandler(settingsMessage());
  const corridor = [...sceneryEl().options].find((o) => o.value === "corridor");
  assert.match(corridor.title, /Search and Find/);
});

test("a message with no catalogue leaves the previous list alone", () => {
  // Defensive: an older server, or a message that lost the field, must not
  // empty the dialog and strand the student with no choice at all.
  busHandler(settingsMessage());
  busHandler(settingsMessage({ sceneries: undefined }));
  assert.deepStrictEqual(options().map((o) => o.value), ["arena", "corridor"]);
});

test("saving sends the id the catalogue supplied", () => {
  busHandler(settingsMessage());
  sceneryEl().value = "corridor";
  lastSent = null;
  document.getElementById("settings-save").click();
  assert.strictEqual(lastSent.type, "save_settings");
  assert.strictEqual(lastSent.settings.scenery, "corridor");
});
