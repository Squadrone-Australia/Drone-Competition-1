// Tests for comp1/frontend/highlight.js: the console and code inspector colouring.
// The tokenizers are pure, so most of this runs without a DOM; the two render
// tests use jsdom, because "server text never becomes markup" is a DOM property.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const HL = require("../../comp1/frontend/highlight.js");
const COMP1 = require("../../comp1/frontend/blocks.js");

const FRONTEND = path.join(__dirname, "../../comp1/frontend");
// A program with a bit of everything: a loop, a sensor test, nesting, numbers.
const FIXTURE = {
  version: 2,
  blocks: [
    { op: "takeoff" },
    { op: "repeat_until", cond: { kind: "sensor", sensor: "target_visible" },
      body: [{ op: "rotate", dir: "cw", deg: 30 }] },
    { op: "approach_marker" },
    { op: "mark_found", signal: "flip" },
    { op: "land" },
  ],
};

const text = (tokens) => tokens.map(([, t]) => t).join("");
const classOf = (tokens, t) => (tokens.find(([, s]) => s === t) || [])[0];

// ── lossless: Copy reads textContent, so colouring must never change the text ──
test("every tokenizer gives back exactly the text it was given", () => {
  const python = COMP1.programToPython(FIXTURE);
  const json = JSON.stringify(FIXTURE, null, 2);
  assert.strictEqual(text(HL.python(python)), python);
  assert.strictEqual(text(HL.json(json)), json);
  for (const line of [
    "001  block rotate  [abc]",
    '002    ↳ SimDrone.move("forward", 50) → null',
    "003    ↳ SimDrone.land()",
    "004  finished: error (the drone did not answer 'land' within 7s)",
    "anything else at all",
  ]) assert.strictEqual(text(HL.trace(line)), line);
  for (const line of [
    "⚠ battery below 50% — the drone will refuse to flip",
    "   💡 How to fix: drag a block until it clicks under “when mission starts”",
    "🎯 Target found! 2 of 3",
    "",
  ]) assert.strictEqual(text(HL.consoleLine(line)), line);
});

test("half-typed input is still lossless: an unterminated string", () => {
  assert.strictEqual(text(HL.python('x = "open')), 'x = "open');
  assert.strictEqual(text(HL.json('{"a": "b')), '{"a": "b');
});

// ── Python ──────────────────────────────────────────────────────────────────
test("Python: keywords, definitions, calls, strings, numbers, comments", () => {
  const t = HL.python([
    "# Display-only Python equivalent.",
    "def mission():",
    "    while not drone.sees_target():",
    '        drone.mark_found("flip")',
    "        drone.turn_right(30)",
    "    return max(low, min(high, value)) if True else None",
  ].join("\n"));
  assert.strictEqual(classOf(t, "# Display-only Python equivalent."), "hl-com");
  assert.strictEqual(classOf(t, "def"), "hl-kw");
  assert.strictEqual(classOf(t, "mission"), "hl-def");
  assert.strictEqual(classOf(t, "while"), "hl-kw");
  assert.strictEqual(classOf(t, "not"), "hl-kw");
  assert.strictEqual(classOf(t, "sees_target"), "hl-fn");
  assert.strictEqual(classOf(t, '"flip"'), "hl-str");
  assert.strictEqual(classOf(t, "30"), "hl-num");
  assert.strictEqual(classOf(t, "max"), "hl-builtin");
  assert.strictEqual(classOf(t, "True"), "hl-const");
  assert.strictEqual(classOf(t, "None"), "hl-const");
});

test("Python: a # inside a string is not a comment", () => {
  const t = HL.python('print("#1 target")');
  assert.strictEqual(classOf(t, '"#1 target"'), "hl-str");
  assert.ok(!t.some(([c]) => c === "hl-com"));
});

test("Python: a number inside a name is part of the name", () => {
  const t = HL.python("step2 = 3");
  assert.strictEqual(classOf(t, "3"), "hl-num");
  assert.ok(!t.some(([c, s]) => c === "hl-num" && s === "2"));
});

// ── JSON ────────────────────────────────────────────────────────────────────
test("JSON: keys and string values are told apart", () => {
  const t = HL.json('{"op": "rotate", "deg": -30, "ok": true, "x": null}');
  assert.strictEqual(classOf(t, '"op"'), "hl-key");
  assert.strictEqual(classOf(t, '"rotate"'), "hl-str");
  assert.strictEqual(classOf(t, "-30"), "hl-num");
  assert.strictEqual(classOf(t, "true"), "hl-const");
  assert.strictEqual(classOf(t, "null"), "hl-const");
});

// ── trace ───────────────────────────────────────────────────────────────────
test("trace: a block line, a call line and how the run finished", () => {
  const block = HL.trace("001  block rotate  [abc]");
  assert.strictEqual(classOf(block, "001"), "hl-ln");
  assert.strictEqual(classOf(block, "block"), "hl-kw");
  assert.strictEqual(classOf(block, "rotate"), "hl-fn");

  const call = HL.trace('002    ↳ SimDrone.move("forward", 50) → null');
  assert.strictEqual(classOf(call, "SimDrone"), "hl-obj");
  assert.strictEqual(classOf(call, "move"), "hl-fn");
  assert.strictEqual(classOf(call, '"forward"'), "hl-str");
  assert.strictEqual(classOf(call, "50"), "hl-num");
  assert.strictEqual(classOf(call, "null"), "hl-const");

  assert.strictEqual(classOf(HL.trace("003  finished: done"), "done"), "tone-ok");
  assert.strictEqual(classOf(HL.trace("003  finished: stopped"), "stopped"), "tone-warn");
  assert.strictEqual(classOf(HL.trace("003  finished: error (x)"), "error"), "tone-error");
});

// ── console ─────────────────────────────────────────────────────────────────
test("console: the leading symbol sets the tone of the whole line", () => {
  const cases = {
    "⚠ no target close enough to that signal": "tone-warn",
    "⛔ EMERGENCY STOP": "tone-error",
    "💥 The drone crashed.": "tone-error",
    "   💡 How to fix: check the battery": "tone-hint",
    "🎯 Target found! 1 of 3": "tone-ok",
    "🏆 mission success": "tone-ok",
    "▶ Running your program…": "tone-info",
    "mission done": "tone-ok",
    "mission stopped": "tone-warn",
    "mission error: the drone did not answer": "tone-error",
    "switched to the simulator": null,
  };
  for (const [line, tone] of Object.entries(cases)) {
    assert.strictEqual(HL.consoleTone(line), tone, line);
  }
});

test("console: block names and measurements stand out, words do not", () => {
  const t = HL.consoleLine("⚠ 2 blocks aren't snapped under “when mission starts” — 50 cm, 90°, 20%");
  assert.strictEqual(classOf(t, "“when mission starts”"), "hl-quote");
  assert.strictEqual(classOf(t, "2"), "hl-num");
  assert.strictEqual(classOf(t, "50 cm"), "hl-num");
  assert.strictEqual(classOf(t, "90°"), "hl-num");
  assert.strictEqual(classOf(t, "20%"), "hl-num");
  // A version or an address is not a measurement.
  assert.ok(!HL.consoleLine("update 0.3.1 ready").some(([c]) => c === "hl-num"));
});

// ── DOM ─────────────────────────────────────────────────────────────────────
function dom() {
  const { JSDOM } = require("jsdom");
  return new JSDOM("<!doctype html><pre id='p'></pre>").window.document;
}

test("server text is never parsed as markup", () => {
  const doc = dom();
  const pre = doc.getElementById("p");
  const evil = '⚠ <img src=x onerror="alert(1)"> “<b>bold</b>”';
  HL.appendLine(pre, HL.consoleLine(evil), HL.consoleTone(evil));
  assert.strictEqual(pre.querySelector("img"), null);
  assert.strictEqual(pre.querySelector("b"), null);
  assert.strictEqual(pre.textContent, evil + "\n");
});

test("render replaces, appendLine appends one toned line at a time", () => {
  const doc = dom();
  const pre = doc.getElementById("p");
  HL.render(pre, HL.python("x = 1"));
  HL.render(pre, HL.python("y = 2"));
  assert.strictEqual(pre.textContent, "y = 2");
  assert.strictEqual(pre.querySelector(".hl-num").textContent, "2");

  pre.replaceChildren();
  HL.appendLine(pre, HL.consoleLine("⚠ one"), "tone-warn");
  HL.appendLine(pre, HL.consoleLine("two"));
  const lines = pre.querySelectorAll(".hl-line");
  assert.strictEqual(lines.length, 2);
  assert.ok(lines[0].classList.contains("tone-warn"));
  assert.strictEqual(lines[1].className, "hl-line");
  assert.strictEqual(pre.textContent, "⚠ one\ntwo\n");
});

test("index.html loads the highlighter before app.js, which uses it", () => {
  const index = fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8");
  assert.match(index, /src="highlight\.js\?v=/);
  assert.ok(index.indexOf('src="highlight.js') < index.indexOf('src="app.js'));
  // Optional by design: app.js must still run if the file is missing.
  const app = fs.readFileSync(path.join(FRONTEND, "app.js"), "utf8");
  assert.match(app, /window\.COMP1_HIGHLIGHT \|\| null/);
});
