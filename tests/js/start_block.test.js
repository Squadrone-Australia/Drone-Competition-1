// Tests for the "🚁 when mission starts" block: it can never be deleted or copied.
//
// Run against the real vendored Blockly, because the bug this guards was a
// Blockly one: `deletable: false` in a JSON block definition is silently
// ignored, so the block looked protected in blocks.js and could still be
// dragged to the bin - from where, not being in the toolbox, the bin was the
// only place to get it back - or duplicated into a second stack that ran nothing.
// Run with: node --test 'tests/js/**/*.test.js'
"use strict";
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");

const FRONTEND = path.join(__dirname, "..", "..", "comp1", "frontend");
const dom = new JSDOM("<!doctype html><html><body></body></html>", { runScripts: "outside-only" });
for (const name of ["vendor/blockly.min.js", "blocks.js"]) {
  dom.window.eval(fs.readFileSync(path.join(FRONTEND, name), "utf8"));
}
const { Blockly, COMP1 } = dom.window;

const contextItem = (id, block) =>
  Blockly.ContextMenuRegistry.registry.getItem(id).preconditionFn({ block });

test("the start block cannot be deleted", () => {
  const start = new Blockly.Workspace().newBlock("start");
  assert.strictEqual(start.isDeletable(), false);
  assert.strictEqual(contextItem("blockDelete", start), "hidden");
});

test("the start block offers no Duplicate", () => {
  const start = new Blockly.Workspace().newBlock("start");
  assert.strictEqual(contextItem("blockDuplicate", start), "hidden");
});

test("a start block loaded from a save without the flag is still protected", () => {
  // Every workspace buffered before the fix was saved without `deletable`.
  const ws = new Blockly.Workspace();
  Blockly.serialization.workspaces.load(
    { blocks: { languageVersion: 0, blocks: [{ type: "start", x: 0, y: 0 }] } }, ws);
  assert.strictEqual(ws.getBlocksByType("start", false)[0].isDeletable(), false);
});

test("other blocks can still be deleted and duplicated", () => {
  const takeoff = new Blockly.Workspace().newBlock("takeoff");
  assert.strictEqual(takeoff.isDeletable(), true);
  assert.strictEqual(contextItem("blockDuplicate", takeoff), "enabled");
});

test("a copy saved before the fix goes, and the blocks under it stay as loose blocks", () => {
  const ws = new Blockly.Workspace();
  Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
    { type: "start", id: "first", x: 0, y: 0, next: { block: { type: "takeoff", id: "a" } } },
    { type: "start", id: "copy", x: 0, y: 200,
      next: { block: { type: "land", id: "b", next: { block: { type: "flip", id: "c" } } } } },
  ] } }, ws);
  assert.strictEqual(COMP1.removeExtraStarts(ws), 1);
  const starts = ws.getBlocksByType("start", false);
  // Spread: an array built inside jsdom carries that window's prototype.
  assert.deepStrictEqual([...starts.map((b) => b.id)], ["first"], "the one that was running is kept");
  assert.strictEqual(starts[0].getNextBlock().id, "a");
  const land = ws.getBlockById("b");
  assert.ok(land && !land.getParent(), "its stack is loose on the canvas, not deleted");
  assert.strictEqual(land.getNextBlock().id, "c");
  assert.strictEqual(COMP1.unattachedCount(ws), 2, "and reported as not attached");
});

test("removing copies leaves nothing for Ctrl+Z to bring back", () => {
  const ws = new Blockly.Workspace();
  Blockly.serialization.workspaces.load({ blocks: { languageVersion: 0, blocks: [
    { type: "start", x: 0, y: 0 }, { type: "start", x: 0, y: 200 },
  ] } }, ws, { recordUndo: false });
  COMP1.removeExtraStarts(ws);
  ws.undo(false);
  assert.strictEqual(ws.getBlocksByType("start", false).length, 1);
});

test("a workspace with one start block, or none, is left alone", () => {
  const one = new Blockly.Workspace();
  one.newBlock("start");
  assert.strictEqual(COMP1.removeExtraStarts(one), 0);
  assert.strictEqual(one.getBlocksByType("start", false).length, 1);
  assert.strictEqual(COMP1.removeExtraStarts(new Blockly.Workspace()), 0);
});
