// Tests for comp1/frontend/tutorial.js, the first-run tutorial.
//
// Three parts. The lesson checks run against the *real* vendored Blockly, because
// what a student sees ("is on the workspace but not snapped on") is only right if
// it matches what Blockly actually does with a dropped block. The storage helpers
// get the hostile cases buffer.js has taught us to expect. And the tutorial itself
// is clicked through end to end in jsdom over the real index.html, down to the
// flag it leaves behind, because the promises it makes - only in the simulator,
// never lose a student's blocks - are made by the wiring, not the helpers.
// Run with: node --test 'tests/js/**/*.test.js'
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const tutorial = require("../../comp1/frontend/tutorial.js");
const {
  LESSON, TUTORIAL_KEY, STASH_KEY,
  checkLesson, fixLesson, chainOf, shouldAutoStart, readFlag, writeFlag,
  saveStash, readStash, install,
} = tutorial;

const FRONTEND = path.join(__dirname, "..", "..", "comp1", "frontend");
const FIXTURE = JSON.parse(fs.readFileSync(
  path.join(__dirname, "..", "fixtures", "tutorial_lesson.json"), "utf8"));

// ── doubles ─────────────────────────────────────────────────────────────────

function fakeStorage(entries = {}) {
  const data = new Map(Object.entries(entries));
  return {
    data,
    getItem: (key) => (data.has(key) ? data.get(key) : null),
    setItem: (key, value) => { data.set(key, String(value)); },
    removeItem: (key) => { data.delete(key); },
  };
}

const hostileStorage = () => ({
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("SecurityError"); },
  removeItem() { throw new Error("SecurityError"); },
});

function fakeBus() {
  return {
    handlers: [],
    on(fn) { this.handlers.push(fn); },
    emit(msg) { this.handlers.forEach((fn) => fn(msg)); },
  };
}

/** A jsdom window with the real Blockly and blocks.js, optionally over index.html. */
function blocklyWindow(html = "<!doctype html><html><body></body></html>") {
  const dom = new JSDOM(html, { url: "http://localhost/", runScripts: "outside-only" });
  for (const name of ["vendor/blockly.min.js", "blocks.js"]) {
    dom.window.eval(fs.readFileSync(path.join(FRONTEND, name), "utf8"));
  }
  return dom.window;
}

const shared = blocklyWindow();
const Blockly = shared.Blockly;

function workspace(B = Blockly) {
  const ws = new B.Workspace();
  ws.newBlock("start");
  return ws;
}

/** Snap a chain of blocks under start, each `[type, fields, number]`. */
function build(ws, specs) {
  let prev = ws.getBlocksByType("start", false)[0];
  for (const [type, fields = {}, number] of specs) {
    const state = { type, fields };
    if (number) {
      state.inputs = { [number[0]]: { shadow: { type: "math_number", fields: { NUM: number[1] } } } };
    }
    const b = Blockly.serialization.blocks.append(state, ws);
    prev.nextConnection.connect(b.previousConnection);
    prev = b;
  }
  return ws;
}

const LESSON_SPECS = [
  ["takeoff"],
  ["move", { DIR: "forward" }, ["CM", 50]],
  ["rotate", { DIR: "cw" }, ["DEG", 180]],
  ["move", { DIR: "forward" }, ["CM", 50]],
  ["land"],
];

// Ids stripped, and copied into this realm: values built inside jsdom carry
// that window's prototypes, which deepStrictEqual would otherwise compare.
const programOf = (ws, B = Blockly) => JSON.parse(JSON.stringify(
  B === Blockly ? shared.COMP1.serializeProgram(ws) : ws.__comp1.serializeProgram(ws),
  (key, value) => (key === "id" ? undefined : value)));
const types = (ws) => chainOf(ws).map((b) => b.type);

// ── the lesson, against the real Blockly ────────────────────────────────────

test("the lesson builds exactly the program the simulator test flies", () => {
  const ws = build(workspace(), LESSON_SPECS);
  assert.ok(checkLesson(ws, LESSON.length - 1).ok);
  const { _comment, ...expected } = FIXTURE;
  assert.deepStrictEqual(programOf(ws), expected);
});

test("the lesson's numbers are ones the blocks accept without clamping", () => {
  // blocks.js clamps a literal into protocol.LIMITS and warns. A lesson that
  // triggered that warning would greet a first-time student with a ⚠.
  const ws = build(workspace(), LESSON_SPECS);
  shared.COMP1.serializeProgram(ws);
  assert.deepStrictEqual([...shared.COMP1.warnings], []);
});

test("an empty program is waiting, not wrong", () => {
  const result = checkLesson(workspace(), 0);
  assert.strictEqual(result.ok, false);
  assert.strictEqual(result.hint, "", "nothing has been tried yet, so nothing to correct");
});

test("a block dropped near the program rather than into it is called out", () => {
  // The most common beginner failure there is, and the quietest: it runs
  // nothing and says nothing. The hint has to name it.
  const ws = workspace();
  ws.newBlock("takeoff");
  const result = checkLesson(ws, 0);
  assert.strictEqual(result.ok, false);
  assert.match(result.hint, /take off.*not snapped on/);
  assert.match(result.hint, /when mission starts/);
});

test("a snapped-on block passes its step", () => {
  assert.deepStrictEqual(checkLesson(build(workspace(), [["takeoff"]]), 0), { ok: true, hint: "" });
});

test("a block in the wrong place names both the block and what belongs there", () => {
  const ws = build(workspace(), [["takeoff"], ["land"]]);
  const { ok, hint } = checkLesson(ws, 1);
  assert.strictEqual(ok, false);
  assert.match(hint, /Block 2/);
  assert.match(hint, /“land”/);
  assert.match(hint, /fly forward 50 cm/);
});

test("a wrong dropdown says what it shows and what to pick", () => {
  const ws = build(workspace(), [["takeoff"], ["move", { DIR: "back" }, ["CM", 50]]]);
  const { hint } = checkLesson(ws, 1);
  assert.match(hint, /says “back”/);
  assert.match(hint, /choose “forward”/);
});

test("the turn left on 90 says exactly what to type", () => {
  const ws = build(workspace(), [
    ["takeoff"], ["move", { DIR: "forward" }, ["CM", 50]], ["rotate", { DIR: "cw" }, ["DEG", 90]]]);
  const { ok, hint } = checkLesson(ws, 2);
  assert.strictEqual(ok, false);
  assert.match(hint, /The “turn” block has 90°/);
  assert.match(hint, /type 180/);
});

test("earlier steps still pass while later blocks are missing", () => {
  const ws = build(workspace(), [["takeoff"], ["move", { DIR: "forward" }, ["CM", 50]]]);
  assert.ok(checkLesson(ws, 1).ok);
  assert.strictEqual(checkLesson(ws, 2).ok, false);
});

test("blocks after land fail only the last step", () => {
  const ws = build(workspace(), [...LESSON_SPECS, ["takeoff"]]);
  assert.ok(checkLesson(ws, 3).ok);
  const { ok, hint } = checkLesson(ws, 4);
  assert.strictEqual(ok, false);
  assert.match(hint, /after “land”/);
});

test("Do it for me builds the whole lesson from nothing", () => {
  const ws = workspace();
  assert.strictEqual(fixLesson(ws, Blockly, LESSON.length - 1), true);
  assert.ok(checkLesson(ws, LESSON.length - 1).ok);
  const { _comment, ...expected } = FIXTURE;
  assert.deepStrictEqual(programOf(ws), expected);
});

test("Do it for me inserts what is missing and keeps the student's blocks", () => {
  const ws = build(workspace(), [["takeoff"], ["land"]]);
  const [takeoff, land] = chainOf(ws);
  fixLesson(ws, Blockly, LESSON.length - 1);
  assert.deepStrictEqual(types(ws), ["takeoff", "move", "rotate", "move", "land"]);
  assert.strictEqual(chainOf(ws)[0].id, takeoff.id, "their take off is still theirs");
  assert.strictEqual(chainOf(ws)[4].id, land.id, "their land was pushed down, not replaced");
});

test("Do it for me repairs a wrong number in place", () => {
  const ws = build(workspace(), [
    ["takeoff"], ["move", { DIR: "back" }, ["CM", 70]], ["rotate", { DIR: "ccw" }, ["DEG", 90]]]);
  const before = chainOf(ws).map((b) => b.id);
  fixLesson(ws, Blockly, 2);
  assert.ok(checkLesson(ws, 2).ok);
  assert.deepStrictEqual(chainOf(ws).map((b) => b.id), before);
});

test("Do it for me snaps in the student's own loose block", () => {
  const ws = workspace();
  const loose = ws.newBlock("takeoff");
  fixLesson(ws, Blockly, 0);
  assert.strictEqual(chainOf(ws)[0].id, loose.id);
  assert.strictEqual(ws.getBlocksByType("takeoff", false).length, 1, "no twin conjured beside it");
});

test("Do it for me moves blocks after land out of the program", () => {
  const ws = build(workspace(), [...LESSON_SPECS, ["flip"]]);
  fixLesson(ws, Blockly, LESSON.length - 1);
  assert.ok(checkLesson(ws, LESSON.length - 1).ok);
  assert.strictEqual(ws.getBlocksByType("flip", false).length, 1, "moved aside, not deleted");
});

test("Do it for me on a workspace with no start block fails quietly", () => {
  assert.strictEqual(fixLesson(new Blockly.Workspace(), Blockly, 0), false);
});

// ── storage ─────────────────────────────────────────────────────────────────

test("opens by itself only for a first run in the simulator", () => {
  const fresh = fakeStorage();
  assert.strictEqual(shouldAutoStart({ storage: fresh, mode: "sim", hasBlocks: false }), true);
  assert.strictEqual(shouldAutoStart({ storage: fresh, mode: "tello", hasBlocks: false }), false);
  assert.strictEqual(shouldAutoStart({ storage: fresh, mode: "mock", hasBlocks: false }), false);
  assert.strictEqual(shouldAutoStart({ storage: fresh, mode: "sim", hasBlocks: true }), false,
    "blocks already on screen mean somebody has been here before");
  for (const seen of ["done", "skipped"]) {
    const storage = fakeStorage({ [TUTORIAL_KEY]: seen });
    assert.strictEqual(shouldAutoStart({ storage, mode: "sim", hasBlocks: false }), false, seen);
  }
});

test("storage it cannot use never throws, and never makes it nag", () => {
  // A machine that denies storage cannot remember the tutorial was seen, so
  // opening by itself there would mean opening on every single launch.
  for (const storage of [hostileStorage(), null]) {
    assert.strictEqual(readFlag(storage), undefined);
    assert.doesNotThrow(() => writeFlag(storage, "done"));
    assert.strictEqual(shouldAutoStart({ storage, mode: "sim", hasBlocks: false }), false);
    assert.strictEqual(saveStash(storage, { blocks: {} }), !storage ? true : false);
    assert.strictEqual(readStash(storage), null);
  }
});

test("a stash round-trips, and a corrupt one is dropped", () => {
  const storage = fakeStorage();
  saveStash(storage, { blocks: { blocks: [{ type: "start" }] } });
  assert.deepStrictEqual(readStash(storage), { blocks: { blocks: [{ type: "start" }] } });
  storage.setItem(STASH_KEY, "{not json");
  assert.strictEqual(readStash(storage), null);
  assert.strictEqual(storage.data.has(STASH_KEY), false);
});

// ── the tutorial, clicked through ───────────────────────────────────────────

const INDEX = fs.readFileSync(path.join(FRONTEND, "index.html"), "utf8");

const pages = [];
test.afterEach(() => { while (pages.length) pages.pop()(); });

function page({ storage = fakeStorage(), blocks = [] } = {}) {
  const window = blocklyWindow(INDEX);
  const B = window.Blockly;
  const ws = new B.Workspace();
  ws.__comp1 = window.COMP1;
  ws.newBlock("start");
  let prev = ws.getBlocksByType("start", false)[0];
  for (const type of blocks) {
    const b = ws.newBlock(type);
    prev.nextConnection.connect(b.previousConnection);
    prev = b;
  }
  const bus = fakeBus();
  const logged = [];
  const sent = [];
  const ctl = install({
    window, document: window.document, workspace: ws, Blockly: B,
    storage, bus, log: (m) => logged.push(m), send: (m) => { sent.push(m); return true; },
  });
  const $ = (sel) => window.document.querySelector(sel);
  const step = () => ctl.state.active && ctl.state.steps[ctl.state.index].id;
  const click = (label) => {
    const btn = [...window.document.querySelectorAll("#tut-card button")]
      .find((b) => b.textContent === label);
    assert.ok(btn, `no “${label}” button on step ${step()}`);
    assert.strictEqual(btn.disabled, false, `“${label}” is disabled on step ${step()}`);
    btn.click();
  };
  const done = () => { if (ctl.state.active) ctl.finish("skipped"); window.close(); };
  // Closed even when an assertion fails first: a jsdom window left open keeps
  // its poll timer alive, and the runner then waits on it for ever.
  pages.push(done);
  return { window, B, ws, bus, logged, sent, ctl, storage, $, step, click, done };
}

/** The arena list as the server sends it, with `current` picked. */
const sceneries = (current) => ({
  type: "sceneries", current,
  sceneries: [{ id: "corridor", name: "Competition arena" }, { id: "arena", name: "Square arena" }],
});
const arenaSends = (p) => p.sent.filter((m) => m.type === "scenery").map((m) => m.name);

test("index.html offers a way back to the tutorial, loaded after app.js", () => {
  assert.match(INDEX, /id="tutorial-open"/);
  assert.match(INDEX, /src="tutorial\.js\?v=/);
  assert.ok(INDEX.indexOf('src="app.js') < INDEX.indexOf('src="tutorial.js'),
    "app.js hands over the workspace, so it has to run first");
  const app = fs.readFileSync(path.join(FRONTEND, "app.js"), "utf8");
  assert.match(app, /window\.COMP1_WORKSPACE = workspace/);
  assert.match(app, /window\.COMP1_LOG = log/);
});

test("a first run in the simulator opens on the welcome card", () => {
  const p = page();
  assert.strictEqual(p.step(), false, "nothing before the drone is known");
  p.bus.emit({ type: "drone_mode", mode: "sim", switching: false });
  assert.strictEqual(p.step(), "welcome");
  assert.strictEqual(p.$("#tut-card").hidden, false);
  assert.match(p.$("#tut-card").textContent, /simulator/);
  assert.strictEqual(p.storage.data.has(TUTORIAL_KEY), false, "not 'seen' until it ends");
  p.done();
});

test("skipping is remembered and says how to come back", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.click("Skip, I've used this before");
  assert.strictEqual(p.step(), false);
  assert.strictEqual(p.$("#tut-card").hidden, true);
  assert.strictEqual(p.storage.data.get(TUTORIAL_KEY), "skipped");
  assert.match(p.logged.join("\n"), /Press Tutorial/);
  p.done();
});

test("it does not open by itself in front of a real drone, or for a returning student", () => {
  const tello = page();
  tello.bus.emit({ type: "drone_mode", mode: "tello" });
  assert.strictEqual(tello.step(), false);
  tello.done();

  const returning = page({ blocks: ["takeoff"] });
  returning.bus.emit({ type: "drone_mode", mode: "sim" });
  assert.strictEqual(returning.step(), false);
  returning.done();
});

test("the Tutorial button in front of a real drone explains, and flies nothing", () => {
  const p = page({ storage: fakeStorage({ [TUTORIAL_KEY]: "done" }) });
  p.bus.emit({ type: "drone_mode", mode: "tello" });
  p.$("#tutorial-open").click();
  assert.strictEqual(p.step(), "notice");
  assert.match(p.$("#tut-card").textContent, /Use Simulator/);
  p.click("OK");
  assert.strictEqual(p.step(), false);
  assert.strictEqual(p.storage.data.get(TUTORIAL_KEY), "done", "a notice is not a run of the tutorial");
  p.done();
});

test("switching the drone mid-tutorial closes it", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.click("Let's go");
  p.bus.emit({ type: "drone_mode", mode: "sim", switching: true });
  assert.strictEqual(p.step(), false);
  assert.match(p.logged.join("\n"), /drone was switched/);
  p.done();
});

test("the whole lesson, from the welcome card to a finished flight", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  assert.match(p.$("#tut-card").textContent, /Your mission/, "the goal is said up front");
  p.click("Let's go");

  // The tour: every region gets its turn, in reading order - and no more of
  // them than a skimming student will sit through.
  const toured = [];
  while (p.step() !== "drawer") {
    toured.push(p.step());
    assert.ok(toured.length < 20, `stuck on ${p.step()}`);
    p.click("Next");
  }
  assert.deepStrictEqual(toured, ["workspace", "start", "stage", "camera", "console", "controls"],
    "the floor plan is hidden here, so its step is skipped; nothing to put aside either");
  assert.ok(toured.length <= 7, "the first tester called twelve cards “click, click, click”");

  // app.js is not loaded here, so the inspector tabs have no handlers of their
  // own: what is checked is that the tutorial asked for the right one.
  const tabsShown = [];
  for (const tab of p.window.document.querySelectorAll("[data-debug-view]")) {
    tab.addEventListener("click", () => tabsShown.push(tab.dataset.debugView));
  }

  // The build: Next is offered but holds until the step is done, then opens.
  for (const id of ["drawer", "takeoff", "forward", "turn", "back", "land"]) {
    assert.strictEqual(p.step(), id);
    if (id !== "drawer") {
      const nextBtn = p.$('#tut-card [data-role="next"]');
      assert.strictEqual(nextBtn.getAttribute("aria-disabled"), "true",
        `${id} looked ready before it was done`);
      nextBtn.click();
      assert.strictEqual(p.step(), id, `${id} let the student past without doing it`);
    }
    p.click("Do it for me");
    p.click("Next");
  }
  assert.deepStrictEqual(types(p.ws), ["takeoff", "move", "rotate", "move", "land"]);

  // The run: lit on Run, then on the flight, then held on the result until the
  // student moves on - it no longer slides away the moment the drone lands.
  assert.strictEqual(p.step(), "run");
  assert.match(p.$("#tut-title").textContent, /Press Run/);
  assert.deepStrictEqual(tabsShown, ["trace"], "Live execution is showing for the flight");
  p.bus.emit({ type: "running", running: true });
  assert.match(p.$("#tut-title").textContent, /flying/);
  p.bus.emit({ type: "running", running: false });
  p.bus.emit({ type: "finished", reason: "done", detail: "" });
  assert.match(p.$(".tut-status").textContent, /worked/);
  assert.match(p.$("#tut-title").textContent, /It flew/);
  assert.strictEqual(p.step(), "run", "the result stays on screen");
  p.click("Next");
  assert.strictEqual(p.step(), "python");
  assert.deepStrictEqual(tabsShown, ["trace", "python"]);

  p.click("Next");
  assert.strictEqual(p.step(), "final");
  assert.match(p.$("#tut-card").textContent, /You flew your first program/);
  assert.match(p.$("#tut-card").textContent, /Next challenge/);
  p.click("Finish");
  assert.strictEqual(p.step(), false);
  assert.strictEqual(p.storage.data.get(TUTORIAL_KEY), "done");
  p.done();
});

test("Next on an unfinished step says what is still to do", () => {
  // A disabled button swallowed the click: the tester pressed it and nothing
  // happened, and nothing said why.
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("drawer");
  p.$('#tut-card [data-role="next"]').click();
  assert.match(p.$(".tut-status").textContent, /Click Flight on the left first/);
  p.ctl.goTo("forward");
  p.$('#tut-card [data-role="next"]').click();
  assert.match(p.$(".tut-status").textContent, /fly forward 50 cm/);
  p.done();
});

test("the answer to an early Next stays on screen, and visibly answers the click", async () => {
  // The poll wiped it within half a second, so the second tester saw nothing.
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("drawer");
  p.$('#tut-card [data-role="next"]').click();
  await new Promise((resolve) => p.window.setTimeout(resolve, 1200));   // three polls
  assert.match(p.$(".tut-status").textContent, /^Not yet: Click Flight/);
  assert.ok(p.$(".tut-status").classList.contains("nudge"));
  p.done();
});

test("Tutorial pressed while still connecting says so, then opens by itself", () => {
  const p = page({ storage: fakeStorage({ [TUTORIAL_KEY]: "done" }) });
  p.$("#tutorial-open").click();
  assert.strictEqual(p.step(), "notice");
  assert.match(p.$("#tut-card").textContent, /Starting up/);
  assert.deepStrictEqual(p.logged, [], "said on screen, not buried in the console");
  p.bus.emit({ type: "drone_mode", mode: "sim", switching: false });
  assert.strictEqual(p.step(), "welcome");
  p.done();
});

test("a drone_mode sent before the tutorial loaded still reaches it", () => {
  // The first-launch race: on a cold cache tutorial.js is still downloading
  // when the socket delivers drone_mode, which the server sends only once.
  // app.js's bus replays it to late subscribers; this is the tutorial's half.
  const app = fs.readFileSync(path.join(FRONTEND, "app.js"), "utf8");
  const sticky = app.match(/STICKY_TYPES = new Set\(\s*\[([^\]]*)\]/);
  assert.ok(sticky, "app.js no longer has a list of replayed message types");
  for (const type of ["drone_mode", "settings", "scene"]) {
    assert.ok(sticky[1].includes(`"${type}"`), `${type} is not replayed to late subscribers`);
  }

  const window = blocklyWindow(INDEX);
  const ws = new window.Blockly.Workspace();
  ws.newBlock("start");
  const replay = { type: "drone_mode", mode: "sim", switching: false };
  const lateBus = { on(fn) { fn(replay); } };     // subscribes after it was sent
  const ctl = install({ window, document: window.document, workspace: ws,
    Blockly: window.Blockly, storage: fakeStorage(), bus: lateBus, log: () => {} });
  assert.strictEqual(ctl.state.active && ctl.state.steps[ctl.state.index].id, "welcome");
  ctl.finish("skipped");
  window.close();
});

test("finishing leaves the challenge in the console", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("final");
  p.click("Finish");
  const consoleText = p.logged.join("\n");
  assert.match(consoleText, /Next challenge/);
  assert.match(consoleText, /Sensing.*Control/, "where to look");
  // A goal and where to look, not the program to copy.
  assert.doesNotMatch(consoleText, /approach target and stop|keep doing until|clockwise 30/);
  p.done();
});

test("the step count holds still when a block appears mid-lesson", () => {
  // "2 of 8" became "4 of 9": a loose block dragged out during the lesson made
  // the put-aside step count again, behind the student.
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.click("Let's go");
  while (p.step() !== "drawer") p.click("Next");
  const counter = () => p.$(".tut-phase").textContent;
  assert.match(counter(), /1 of 8/);
  p.click("Do it for me");
  p.click("Next");
  assert.match(counter(), /2 of 8/);
  p.ws.newBlock("takeoff");                   // dropped, not snapped
  p.click("Do it for me");
  p.click("Next");
  assert.match(counter(), /3 of 8/);
  p.done();
});

test("a wrong block is named as soon as it is snapped on, not when Next is pressed", async () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("forward");
  p.click("Do it for me");                    // take off + fly forward
  p.ctl.goTo("turn");
  const start = p.ws.getBlocksByType("start", false)[0];
  let last = start;
  while (last.getNextBlock()) last = last.getNextBlock();
  const flip = p.ws.newBlock("flip");
  last.nextConnection.connect(flip.previousConnection);
  await new Promise((resolve) => p.window.setTimeout(resolve, 1500));
  assert.match(p.$(".tut-status").textContent, /Block 3 .*“flip/);
  p.done();
});

test("Jump to building skips the rest of the tour, via put-aside when there are blocks", () => {
  const empty = page();
  empty.bus.emit({ type: "drone_mode", mode: "sim" });
  empty.click("Let's go");
  empty.click("Jump to building ›");
  assert.strictEqual(empty.step(), "drawer");
  empty.done();

  const busy = page({ storage: fakeStorage({ [TUTORIAL_KEY]: "done" }), blocks: ["flip"] });
  busy.bus.emit({ type: "drone_mode", mode: "sim" });
  busy.$("#tutorial-open").click();
  busy.click("Let's go");
  busy.click("Jump to building ›");
  assert.strictEqual(busy.step(), "aside", "never builds over a student's own blocks");
  busy.done();
});

test("a hint actually appears while the step is polling", async () => {
  // Found in the browser, not by the checks above: the poll ran more often
  // than the "wait until they stop" delay was long and restarted it every
  // time, so a dropped-but-not-snapped block never got its hint at all.
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("takeoff");
  p.ws.newBlock("takeoff");                   // dropped near, not snapped on
  p.ws.fireChangeListener(new p.B.Events.BlockCreate(p.ws.getBlocksByType("takeoff")[0]));
  await new Promise((resolve) => p.window.setTimeout(resolve, 1500));
  assert.match(p.$(".tut-status").textContent, /not snapped on/);
  p.done();
});

test("a crashed flight is not praised, and can be flown again", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("land");
  p.click("Do it for me");
  p.click("Next");
  assert.strictEqual(p.step(), "run");
  p.bus.emit({ type: "running", running: true });
  p.bus.emit({ type: "mission", state: "crashed" });
  p.bus.emit({ type: "running", running: false });
  p.bus.emit({ type: "finished", reason: "done", detail: "" });
  assert.strictEqual(p.step(), "run", "stays put rather than moving on");
  assert.match(p.$(".tut-status").textContent, /hit something/);
  p.done();
});

test("a student's own blocks are put aside and offered back at the end", () => {
  const storage = fakeStorage({ [TUTORIAL_KEY]: "done" });
  const p = page({ storage, blocks: ["takeoff", "flip", "land"] });
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.$("#tutorial-open").click();
  p.click("Let's go");
  while (p.step() !== "aside") p.click("Next");
  p.click("Put my blocks aside");
  assert.deepStrictEqual(types(p.ws), [], "an empty program to build the lesson in");
  assert.ok(storage.data.has(STASH_KEY), "kept somewhere a closed tab cannot lose");

  p.click("Skip tutorial");
  assert.deepStrictEqual(types(p.ws), ["takeoff", "flip", "land"], "skipping gives them back");
  assert.strictEqual(storage.data.has(STASH_KEY), false);
  p.done();
});

test("a student can keep the lesson instead of their earlier blocks", () => {
  const storage = fakeStorage({ [TUTORIAL_KEY]: "done" });
  const p = page({ storage, blocks: ["flip"] });
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.$("#tutorial-open").click();
  p.click("Let's go");
  while (p.step() !== "aside") p.click("Next");
  p.click("Put my blocks aside");
  p.ctl.goTo("python");
  p.click("Next");
  assert.strictEqual(p.step(), "final");
  assert.match(p.$("#tut-card").textContent, /earlier blocks are put aside/);
  p.click("Keep this program");
  assert.strictEqual(storage.data.has(STASH_KEY), false);
  assert.strictEqual(p.ws.getBlocksByType("flip", false).length, 0);
  p.done();
});

test("blocks put aside when the tab closed come back on the next load", () => {
  const storage = fakeStorage();
  const earlier = page({ blocks: ["takeoff", "flip"] });
  saveStash(storage, earlier.B.serialization.workspaces.save(earlier.ws));
  earlier.done();

  const p = page({ storage, blocks: ["takeoff"] });   // a half-built lesson
  assert.deepStrictEqual(types(p.ws), ["takeoff", "flip"]);
  assert.strictEqual(storage.data.has(STASH_KEY), false);
  assert.match(p.logged.join("\n"), /Put back the blocks/);
  p.done();
});

test("the praise for a flight survives the server's own message order", () => {
  // Found in a real browser, not here: the server sends `running: false`
  // after `finished`, and that render wiped the ✓ line it had just shown.
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.ctl.goTo("land");
  p.click("Do it for me");
  p.click("Next");
  p.bus.emit({ type: "running", running: true });
  p.bus.emit({ type: "finished", reason: "done", detail: "" });
  p.bus.emit({ type: "running", running: false });
  assert.match(p.$(".tut-status").textContent, /worked/);
  p.done();
});

// ── the arena ───────────────────────────────────────────────────────────────

test("the tutorial flies in the square arena, and puts the competition one back after", () => {
  const p = page();
  p.bus.emit(sceneries("corridor"));
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  assert.strictEqual(p.step(), "welcome");
  assert.deepStrictEqual(arenaSends(p), ["arena"]);
  assert.match(p.logged.join("\n"), /flies in the Square arena.*back in the Competition arena/);
  p.bus.emit(sceneries("arena"));         // the server's answer
  p.click("Skip, I've used this before");
  assert.deepStrictEqual(arenaSends(p), ["arena", "corridor"]);
  assert.match(p.logged.join("\n"), /Back in the Competition arena/);
  p.done();
});

test("on a first launch the arena list can arrive after the tutorial has opened", () => {
  const p = page();
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  assert.strictEqual(p.step(), "welcome");
  assert.deepStrictEqual(arenaSends(p), [], "nothing known to switch from yet");
  p.bus.emit(sceneries("corridor"));
  assert.deepStrictEqual(arenaSends(p), ["arena"]);
  p.done();
});

test("a student already in the square arena is left there", () => {
  const p = page();
  p.bus.emit(sceneries("arena"));
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.click("Skip, I've used this before");
  assert.deepStrictEqual(arenaSends(p), []);
  p.done();
});

test("closed mid-flight, the arena goes back once the drone has landed", () => {
  // The server refuses an arena change while a mission runs.
  const p = page();
  p.bus.emit(sceneries("corridor"));
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.bus.emit(sceneries("arena"));
  p.ctl.goTo("land");
  p.click("Do it for me");
  p.click("Next");
  p.bus.emit({ type: "running", running: true });
  p.ctl.finish("skipped");
  assert.deepStrictEqual(arenaSends(p), ["arena"], "not while it is flying");
  p.bus.emit({ type: "running", running: false });
  assert.deepStrictEqual(arenaSends(p), ["arena", "corridor"]);
  p.done();
});

test("an arena the student picks mid-tutorial is theirs, and not undone", () => {
  const p = page();
  p.bus.emit(sceneries("corridor"));
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.bus.emit(sceneries("arena"));
  p.bus.emit(sceneries("corridor"));      // they used the picker
  p.click("Skip, I've used this before");
  assert.deepStrictEqual(arenaSends(p), ["arena"]);
  p.done();
});

test("closed by a drone switch, the arena goes back when the simulator does", () => {
  const p = page();
  p.bus.emit(sceneries("corridor"));
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  p.bus.emit(sceneries("arena"));
  p.bus.emit({ type: "drone_mode", mode: "sim", switching: true });
  assert.strictEqual(p.step(), false);
  p.bus.emit({ type: "drone_mode", mode: "tello" });
  assert.deepStrictEqual(arenaSends(p), ["arena"], "a Tello has no arena to change");
  p.bus.emit({ type: "drone_mode", mode: "sim", switching: true });
  p.bus.emit({ type: "drone_mode", mode: "sim" });
  assert.deepStrictEqual(arenaSends(p), ["arena", "corridor"]);
  p.done();
});
