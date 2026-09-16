// Tests for comp1/frontend/buffer.js, the workspace auto-buffer.
//
// Two halves. The first drives createBuffer with a plain object for storage and a
// stubbed Blockly, which is where every hostile case lives — corrupt entries, a full
// quota, a browser that denies storage outright. The second loads the *real* vendored
// Blockly under jsdom and round-trips an actual workspace, because the point of the
// feature is that a student's blocks come back and only Blockly can prove that.
// Run with: node --test tests/js
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const buffer = require("../../comp1/frontend/buffer.js");
const { createBuffer, BUFFER_KEY, BUFFER_FORMAT, MAX_CHARS } = buffer;

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

// A browser with site data blocked: touching it at all raises.
const hostileStorage = () => ({
  getItem() { throw new Error("SecurityError"); },
  setItem() { throw new Error("SecurityError"); },
  removeItem() { throw new Error("SecurityError"); },
});

// A storage that reads fine but has no room left, which is what a real quota
// failure looks like.
function fullStorage(entries = {}) {
  const store = fakeStorage(entries);
  store.setItem = () => { throw new Error("QuotaExceededError"); };
  return store;
}

const stubBlockly = ({ save = () => ({ blocks: [] }), load = () => {} } = {}) =>
  ({ serialization: { workspaces: { save, load } } });

const fakeWorkspace = () => ({ cleared: 0, clear() { this.cleared += 1; } });

const envelopeIn = (store) => JSON.parse(store.data.get(BUFFER_KEY));

// ── capture ─────────────────────────────────────────────────────────────────

test("capture writes a versioned envelope around the workspace state", () => {
  const store = fakeStorage();
  const state = { blocks: { blocks: [{ type: "start" }] } };
  const b = createBuffer({ storage: store, blockly: stubBlockly({ save: () => state }) });

  assert.strictEqual(b.capture(fakeWorkspace()), true);
  const envelope = envelopeIn(store);
  assert.strictEqual(envelope.format, BUFFER_FORMAT);
  assert.deepStrictEqual(envelope.workspace, state);
  assert.ok(Date.parse(envelope.saved) > 0, "saved should be a timestamp");
});

test("capture reports failure rather than throwing when the quota is gone", () => {
  const store = fullStorage({ [BUFFER_KEY]: "previous" });
  const b = createBuffer({ storage: store, blockly: stubBlockly() });

  assert.strictEqual(b.capture(fakeWorkspace()), false);
  // The older buffer is deliberately left alone: slightly stale beats nothing.
  assert.strictEqual(store.data.get(BUFFER_KEY), "previous");
});

test("capture refuses a workspace too large to be one a student dragged", () => {
  const store = fakeStorage();
  const huge = { blocks: "x".repeat(MAX_CHARS + 1) };
  const b = createBuffer({ storage: store, blockly: stubBlockly({ save: () => huge }) });

  assert.strictEqual(b.capture(fakeWorkspace()), false);
  assert.strictEqual(store.data.has(BUFFER_KEY), false);
});

test("capture survives a Blockly that cannot serialize the workspace", () => {
  const store = fakeStorage();
  const b = createBuffer({
    storage: store,
    blockly: stubBlockly({ save: () => { throw new Error("no"); } }),
  });

  assert.strictEqual(b.capture(fakeWorkspace()), false);
  assert.strictEqual(store.data.has(BUFFER_KEY), false);
});

// ── restore ─────────────────────────────────────────────────────────────────

test("a captured workspace comes back on the next restore", () => {
  const store = fakeStorage();
  const state = { blocks: { blocks: [{ type: "start" }] } };
  let loaded = null;
  const b = createBuffer({
    storage: store,
    blockly: stubBlockly({ save: () => state, load: (s) => { loaded = s; } }),
  });

  b.capture(fakeWorkspace());
  assert.strictEqual(b.restore(fakeWorkspace()), "restored");
  assert.deepStrictEqual(loaded, state);
});

test("restore loads with recordUndo off, so Ctrl+Z cannot undo the restore", () => {
  const store = fakeStorage({
    [BUFFER_KEY]: JSON.stringify({ format: BUFFER_FORMAT, workspace: { blocks: {} } }),
  });
  let options = null;
  const b = createBuffer({
    storage: store,
    blockly: stubBlockly({ load: (_s, _w, o) => { options = o; } }),
  });

  b.restore(fakeWorkspace());
  assert.deepStrictEqual(options, { recordUndo: false });
});

test("a first run finds nothing and says so without touching the workspace", () => {
  const workspace = fakeWorkspace();
  const b = createBuffer({ storage: fakeStorage(), blockly: stubBlockly() });

  assert.strictEqual(b.restore(workspace), "none");
  assert.strictEqual(workspace.cleared, 0);
});

// Each of these is something that was stored and cannot be used. All of them owe
// the student an explanation ("unusable") and none may be left behind to be
// re-read and re-rejected on every load for the life of the machine.
for (const [name, stored] of [
  ["a truncated entry", "{\"format\": 1, \"workspa"],
  ["a entry that is not an object", "42"],
  ["a null entry", "null"],
  ["an envelope from a future format", JSON.stringify({ format: 99, workspace: {} })],
  ["an envelope with no workspace in it", JSON.stringify({ format: BUFFER_FORMAT })],
  ["an envelope whose workspace is not an object", JSON.stringify(
    { format: BUFFER_FORMAT, workspace: "blocks" })],
]) {
  test(`restore drops ${name}`, () => {
    const store = fakeStorage({ [BUFFER_KEY]: stored });
    const b = createBuffer({ storage: store, blockly: stubBlockly() });

    assert.strictEqual(b.restore(fakeWorkspace()), "unusable");
    assert.strictEqual(store.data.has(BUFFER_KEY), false, "the bad entry must be dropped");
  });
}

test("a state Blockly refuses part-way leaves no half-built workspace behind", () => {
  const store = fakeStorage({
    [BUFFER_KEY]: JSON.stringify({ format: BUFFER_FORMAT, workspace: { blocks: {} } }),
  });
  const workspace = fakeWorkspace();
  const b = createBuffer({
    storage: store,
    blockly: stubBlockly({ load: () => { throw new Error("Invalid block definition"); } }),
  });

  assert.strictEqual(b.restore(workspace), "unusable");
  assert.strictEqual(workspace.cleared, 1, "the partial load must be cleared away");
  assert.strictEqual(store.data.has(BUFFER_KEY), false);
});

// ── degrading, never throwing ───────────────────────────────────────────────

test("a browser that denies storage gets a buffer that quietly does nothing", () => {
  const b = createBuffer({ storage: hostileStorage(), blockly: stubBlockly() });

  assert.strictEqual(b.restore(fakeWorkspace()), "none");
  assert.strictEqual(b.capture(fakeWorkspace()), false);
  b.clear();  // must not throw either
});

for (const [name, options] of [
  ["no storage at all", { storage: null, blockly: stubBlockly() }],
  ["no Blockly", { storage: fakeStorage(), blockly: null }],
  ["a Blockly without the serialization API", { storage: fakeStorage(), blockly: {} }],
  ["nothing whatsoever", undefined],
]) {
  test(`a buffer built with ${name} is unavailable rather than broken`, () => {
    const b = createBuffer(options);

    assert.strictEqual(b.available, false);
    assert.strictEqual(b.capture(fakeWorkspace()), false);
    assert.strictEqual(b.restore(fakeWorkspace()), "none");
    b.clear();
  });
}

test("clear forgets the buffer", () => {
  const store = fakeStorage({ [BUFFER_KEY]: "anything" });
  createBuffer({ storage: store, blockly: stubBlockly() }).clear();

  assert.strictEqual(store.data.has(BUFFER_KEY), false);
});

// ── against the real Blockly ────────────────────────────────────────────────
// Everything above stubs the serializer. These two load the vendored bundle for
// real, because "the blocks come back" is a claim only Blockly can settle — and
// because the unknown-block-type case above is a stub imitating behaviour that
// has to be checked against the thing itself.

const FRONTEND = path.join(__dirname, "..", "..", "comp1", "frontend");

function blocklyWindow() {
  const { JSDOM } = require("jsdom");
  const dom = new JSDOM("<!doctype html><html><body></body></html>", {
    url: "http://localhost/", runScripts: "outside-only",
  });
  // Evaluated inside the jsdom window rather than required: the bundle is a
  // browser script that reaches for `document` as it initialises, and its UMD
  // wrapper would otherwise take the CommonJS branch and look for sources that
  // are not shipped.
  for (const name of ["vendor/blockly.min.js", "blocks.js"]) {
    dom.window.eval(fs.readFileSync(path.join(FRONTEND, name), "utf8"));
  }
  return dom.window;
}

test("a real workspace survives a capture and restore into a fresh workspace", () => {
  const window = blocklyWindow();
  const Blockly = window.Blockly;
  const store = fakeStorage();
  const b = createBuffer({ storage: store, blockly: Blockly });

  // What a student leaves on screen: the hat, and a command under it.
  const before = new Blockly.Workspace();
  const start = before.newBlock("start");
  const move = before.newBlock("move");
  move.setFieldValue("forward", "DIR");
  start.nextConnection.connect(move.previousConnection);
  assert.strictEqual(b.capture(before), true);

  // What they come back to: a workspace built from nothing, as on a fresh load.
  const after = new Blockly.Workspace();
  assert.strictEqual(b.restore(after), "restored");
  // Spread and JSON here are not decoration: values built inside jsdom carry
  // that window's Array and Object prototypes, and deepStrictEqual compares
  // those too. Copying into this realm compares the data rather than the realm.
  assert.deepStrictEqual(
    [...after.getAllBlocks(false)].map((block) => block.type).sort(),
    ["move", "start"],
  );
  // app.js only seeds `start` when the restore did not bring one, and block ids
  // have to survive or the run highlighting would point at blocks that are gone.
  assert.strictEqual(after.getBlocksByType("start", false).length, 1);
  assert.ok(after.getBlockById(move.id), "block ids must survive the round trip");
  assert.deepStrictEqual(
    JSON.parse(JSON.stringify(window.COMP1.serializeProgram(after))),
    { version: 2, blocks: [{ id: move.id, op: "move", dir: "forward", cm: 20 }] },
  );
});

test("a buffer naming a block this version does not have is dropped, not shown", () => {
  const window = blocklyWindow();
  const store = fakeStorage({
    [BUFFER_KEY]: JSON.stringify({
      format: BUFFER_FORMAT,
      workspace: { blocks: { languageVersion: 0, blocks: [
        { type: "start", id: "keep", x: 0, y: 0 },
        { type: "block_from_a_newer_drone_coder", id: "boom", x: 0, y: 40 },
      ] } },
    }),
  });
  const b = createBuffer({ storage: store, blockly: window.Blockly });

  const workspace = new window.Blockly.Workspace();
  assert.strictEqual(b.restore(workspace), "unusable");
  assert.strictEqual(workspace.getAllBlocks(false).length, 0,
    "a workspace Blockly only half-built must not be handed to a student");
  assert.strictEqual(store.data.has(BUFFER_KEY), false);
});
