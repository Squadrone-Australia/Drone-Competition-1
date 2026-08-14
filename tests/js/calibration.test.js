// DOM-level tests for comp1/frontend/calibration.js. calibration.js has no exports and no
// framework: it is an IIFE that wires event handlers straight onto elements from index.html at
// load time. jsdom stands in for the browser so the real handlers run against real elements,
// which is what catches a mis-typed id, a handler that never calls window.COMP1_SEND, or ROI
// maths that scales a y coordinate by the canvas *width* — none of which the string-presence
// checks in tests/test_offline_assets.py would notice. Run with: node --test tests/js
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const FRONTEND = path.join(__dirname, "..", "..", "comp1", "frontend");

const dom = new JSDOM(fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8"),
  { url: "http://localhost/" }); // a real origin, so localStorage (used by profile save) works
const { window } = dom;
const { document } = window;

// jsdom ships no canvas backend: getContext("2d") returns null, and calibration.js calls
// frame.getContext("2d") at load time, so leaving it null would throw before any test runs.
// Stand in a fake 2D context per canvas (frame gets one, the offscreen "frozen" canvas gets its
// own, exactly like a real browser) that just records the draw calls we assert on.
const contextsByCanvas = new WeakMap();
window.HTMLCanvasElement.prototype.getContext = function (type) {
  if (type !== "2d") return null;
  if (!contextsByCanvas.has(this))
    contextsByCanvas.set(this, {
      calls: { drawImage: [], fillRect: [], strokeRect: [] },
      drawImage(...args) { this.calls.drawImage.push(args); },
      fillRect(...args) { this.calls.fillRect.push(args); },
      strokeRect(...args) { this.calls.strokeRect.push(args); },
    });
  return contextsByCanvas.get(this);
};
const frameContext = () => document.getElementById("vision-frame").getContext("2d");

// captureFrame() bails out unless the video looks loaded. jsdom never decodes a real image, so
// naturalWidth/naturalHeight are permanently 0 with no setter; shadow them with own properties
// (complete already defaults true for an <img> with no src, but we pin it too, defensively).
const video = document.getElementById("video");
Object.defineProperty(video, "naturalWidth", { value: 640, configurable: true });
Object.defineProperty(video, "naturalHeight", { value: 480, configurable: true });
Object.defineProperty(video, "complete", { value: true, configurable: true });

// Stub the two globals calibration.js talks to the rest of the app through.
let lastSent = null;
window.COMP1_SEND = (message) => { lastSent = message; };
let busHandler = null;
window.COMP1_BUS = { on: (fn) => { busHandler = fn; } };

// calibration.js is loaded via <script src>, not require() — it has no module.exports and
// expects `document`/`window`/`localStorage` as bare identifiers, the way a browser provides
// them. Give eval that scope (the destructured `window`/`document` above, plus `localStorage`
// on the global object since showProfiles() reads it once immediately at load) and evaluate the
// file's source directly.
global.localStorage = window.localStorage;
eval(fs.readFileSync(path.join(FRONTEND, "calibration.js"), "utf8"));

// ── clicking the button ─────────────────────────────────────────────────────
test('clicking "Find marker for me" sends exactly {type: "vision_auto"}', () => {
  lastSent = null;
  document.getElementById("vision-auto").click();
  assert.deepStrictEqual(lastSent, { type: "vision_auto" });
});

// ── drawing the server's suggestion back onto the frame ────────────────────
test("a vision_suggestion message draws the selection in canvas pixel coordinates", () => {
  // Prime the frozen/frame canvases directly, independent of the click test above.
  document.getElementById("vision-refresh").click();
  assert.strictEqual(document.getElementById("vision-frame").width, 640);
  assert.strictEqual(document.getElementById("vision-frame").height, 480);

  assert.strictEqual(typeof busHandler, "function",
    "calibration.js should have registered a handler with window.COMP1_BUS.on");
  busHandler({
    type: "vision_suggestion",
    config: { lower1: [0, 120, 120], upper1: [10, 255, 255],
              lower2: [170, 120, 120], upper2: [180, 255, 255] },
    roi: [0.25, 0.5, 0.75, 1.0],
    preview_jpeg: "",
  });

  // x is scaled by canvas width, y by canvas height: this is the assertion that would catch
  // frame.width being used for a y coordinate.
  assert.deepStrictEqual(frameContext().calls.strokeRect.at(-1), [160, 240, 320, 240]);
});

// ── surfacing a server-side failure ─────────────────────────────────────────
test('a vision_error message puts its text on #vision-status with class "bad"', () => {
  busHandler({ type: "vision_error", message: "No red marker found in the frame." });
  const status = document.getElementById("vision-status");
  assert.strictEqual(status.textContent, "No red marker found in the frame.");
  assert.strictEqual(status.className, "bad");
});
