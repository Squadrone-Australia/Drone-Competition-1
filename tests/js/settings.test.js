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

// ── Interface size ─────────────────────────────────────────────────────────
// Page zoom on the native window's WebView2 control, through pywebview's
// js_api bridge. Each test gets a fresh page, because what matters is what
// happens at load: whether a bridge exists, and what this window remembered.

function freshPage({ stored, bridge = null, bridgeLate = false, storageThrows = false } = {}) {
  const page = new JSDOM(fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8"),
    { url: "http://localhost/", runScripts: "outside-only" });
  const w = page.window;
  w.HTMLDialogElement.prototype.showModal = function () { this.open = true; };
  w.HTMLDialogElement.prototype.close = function () { this.open = false; };
  w.COMP1_SEND = () => {};
  w.COMP1_BUS = { on: () => {} };
  if (stored !== undefined) w.localStorage.setItem("comp1.uiZoom.v1", stored);
  if (storageThrows) {
    Object.defineProperty(w, "localStorage", { get() { throw new Error("denied"); } });
  }
  const calls = [];
  const api = bridge && {
    set_zoom: async (value) => { calls.push(value); return bridge === "works"; },
  };
  if (api && !bridgeLate) w.pywebview = { api };
  w.eval(fs.readFileSync(path.join(FRONTEND, "settings.js"), "utf8"));
  const arrive = () => {
    w.pywebview = { api };
    w.dispatchEvent(new w.Event("pywebviewready"));
  };
  const el = w.document.getElementById("settings-size");
  const note = () => w.document.getElementById("settings-size-note").textContent;
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));
  return { w, el, note, calls, arrive, settle };
}

test("in a plain browser the size control points at the browser's own zoom", () => {
  const { el, note } = freshPage();
  assert.strictEqual(el.disabled, true);
  assert.match(note(), /Ctrl \+ and Ctrl −/);
});

test("the bridge arriving after load enables the control and applies the saved size", async () => {
  const p = freshPage({ stored: "1.25", bridge: "works", bridgeLate: true });
  assert.strictEqual(p.el.disabled, true, "nothing to drive until the bridge exists");
  p.arrive();
  await p.settle();
  assert.strictEqual(p.el.disabled, false);
  assert.strictEqual(p.el.value, "1.25");
  assert.deepStrictEqual(p.calls, [1.25]);
});

test("with nothing saved, a Ctrl+wheel zoom is left alone", async () => {
  const p = freshPage({ bridge: "works" });
  await p.settle();
  assert.strictEqual(p.el.value, "1");
  assert.deepStrictEqual(p.calls, []);
});

test("picking a size applies it at once and remembers it", async () => {
  const p = freshPage({ bridge: "works" });
  p.el.value = "1.5";
  p.el.dispatchEvent(new p.w.Event("change"));
  await p.settle();
  assert.deepStrictEqual(p.calls, [1.5]);
  assert.strictEqual(p.w.localStorage.getItem("comp1.uiZoom.v1"), "1.5");
});

test("a stored value the menu does not offer is ignored, not applied", async () => {
  for (const junk of ["7", "abc", "", "0"]) {
    const p = freshPage({ stored: junk, bridge: "works" });
    await p.settle();
    assert.deepStrictEqual(p.calls, [], `applied ${JSON.stringify(junk)}`);
    assert.strictEqual(p.el.value, "1");
  }
});

test("a window that refuses the size says so", async () => {
  const p = freshPage({ bridge: "refuses" });
  p.el.value = "1.25";
  p.el.dispatchEvent(new p.w.Event("change"));
  await p.settle();
  assert.match(p.note(), /Could not change the size/);
});

test("storage that throws costs the memory, not the dialog", async () => {
  const p = freshPage({ bridge: "works", storageThrows: true });
  p.el.value = "1.1";
  p.el.dispatchEvent(new p.w.Event("change"));
  await p.settle();
  assert.deepStrictEqual(p.calls, [1.1]);
});
