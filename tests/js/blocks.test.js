// Serializer tests for comp1/frontend/blocks.js.
// Blockly itself is never loaded — blocks.js no-ops its Blockly calls when the global is
// missing, and every block here is a duck-typed stand-in with just the four methods the
// serializer actually calls. Run with: node --test tests/js
const test = require("node:test");
const assert = require("node:assert");

const COMP1 = require("../../comp1/frontend/blocks.js");
const { valueJson, serializeProgram, programToPython, warnings, blocks, toolbox } = COMP1;

let nextId = 0;
function fake(type, opts = {}) {
  const { fields = {}, inputs = {}, id = `${type}#${++nextId}` } = opts;
  const b = {
    type, id, next: null,
    getFieldValue: (n) => fields[n],
    getInputTargetBlock: (n) => inputs[n] || null,
    getNextBlock: () => b.next,
  };
  if (!opts.noGetField)
    b.getField = (n) => (n in fields ? { getText: () => String(fields[n]) } : null);
  return b;
}
const n = (v) => fake("math_number", { fields: { NUM: v } });
function chain(...bs) {
  bs.forEach((b, i) => { b.next = bs[i + 1] || null; });
  return bs[0];
}
const ws = (first) => ({
  getBlocksByType: () => [{ type: "start", id: "start", getNextBlock: () => first }],
});
const run = (first) => serializeProgram(ws(first));

// ── envelope ────────────────────────────────────────────────────────────────
test("empty workspace serializes to an empty v2 program", () => {
  assert.deepStrictEqual(run(null), { version: 2, blocks: [] });
});

test("no start block at all still yields a valid program", () => {
  assert.deepStrictEqual(serializeProgram({ getBlocksByType: () => [] }),
    { version: 2, blocks: [] });
});

// ── statements ──────────────────────────────────────────────────────────────
test("a beginner program looks exactly like it did under v1, but version 2", () => {
  const prog = run(chain(
    fake("takeoff", { id: "a" }),
    fake("move", { id: "b", fields: { DIR: "forward" }, inputs: { CM: n(50) } }),
    fake("rotate", { id: "c", fields: { DIR: "cw" }, inputs: { DEG: n(90) } }),
    fake("flip", { id: "d", fields: { DIR: "back" } }),
    fake("land", { id: "e" })));
  assert.deepStrictEqual(prog, { version: 2, blocks: [
    { id: "a", op: "takeoff" },
    { id: "b", op: "move", dir: "forward", cm: 50 },
    { id: "c", op: "rotate", dir: "cw", deg: 90 },
    { id: "d", op: "flip", dir: "back" },
    { id: "e", op: "land" },
  ] });
  assert.deepStrictEqual(warnings, []);
});

test("plain ops carry only id and op", () => {
  for (const op of ["takeoff", "land", "approach_marker", "mark_found", "end_mission"])
    assert.deepStrictEqual(run(fake(op, { id: "x" })).blocks, [{ id: "x", op }]);
});

test("student-facing block text consistently calls markers targets", () => {
  const visibleText = blocks.flatMap((block) => [block.message0, block.tooltip])
    .filter(Boolean).join(" ");
  assert.doesNotMatch(visibleText, /\bfire(?:s)?\b/i);
  assert.match(visibleText, /\btarget(?:s)?\b/i);
});

test("Python translation uses the public Drone API and keeps block ids", () => {
  const program = run(chain(
    fake("takeoff", { id: "launch" }),
    fake("move", { id: "fly", fields: { DIR: "forward" }, inputs: { CM: n(50) } }),
    fake("rotate", { id: "turn", fields: { DIR: "cw" }, inputs: { DEG: n(90) } }),
    fake("mark_found", { id: "found" }),
    fake("land", { id: "finish" })));
  const python = programToPython(program);
  assert.match(python, /from comp1\.api import Drone/);
  assert.match(python, /# block takeoff \[launch\]\s+drone\.takeoff\(\)/);
  assert.match(python, /# block move \[fly\]\s+drone\.forward\(50\)/);
  assert.match(python, /drone\.turn_right\(90\)/);
  assert.match(python, /drone\.mark_found\(\)/);
  assert.match(python, /drone\.land\(\)/);
});

test("Python translation renders expressions, variables, branches, and loops", () => {
  const python = programToPython({ version: 2, blocks: [
    { id: "set", op: "set_var", name: "steps taken", value: 2 },
    { id: "loop", op: "repeat_until",
      cond: { kind: "sensor", sensor: "target_visible" },
      body: [{ id: "turn", op: "rotate", dir: "ccw",
               deg: { kind: "var", name: "steps taken" } }] },
    { id: "if", op: "if",
      cond: { kind: "binop", op: "<",
              left: { kind: "sensor", sensor: "target_distance_cm" },
              right: { kind: "number", value: 120 } },
      body: [{ id: "mark", op: "mark_found" }],
      else_body: [{ id: "wait", op: "wait", seconds: 1 }] },
  ] });
  assert.match(python, /variables\["steps taken"\] = 2/);
  assert.match(python, /while not drone\.sees_target\(\):/);
  assert.match(python, /drone\.turn_left\(variables\.get\("steps taken", 0\)\)/);
  assert.match(python, /if \(_target_value\("distance_cm", 9999\) < 120\):/);
  assert.match(python, /else:\s+# block wait \[wait\]\s+drone\.wait/);
});

test("wait takes a Value", () => {
  assert.deepStrictEqual(run(fake("wait", { id: "w", inputs: { SECONDS: n(3) } })).blocks,
    [{ id: "w", op: "wait", seconds: 3 }]);
});

test("set_var emits name + value", () => {
  assert.deepStrictEqual(
    run(fake("set_var", { id: "s", fields: { VAR: "steps" }, inputs: { VALUE: n(4) } })).blocks,
    [{ id: "s", op: "set_var", name: "steps", value: 4 }]);
});

test("set_var reads the variable name from the field when there is no getField", () => {
  assert.deepStrictEqual(
    run(fake("set_var", { id: "s", noGetField: true, fields: { VAR: "steps" },
                          inputs: { VALUE: n(1) } })).blocks,
    [{ id: "s", op: "set_var", name: "steps", value: 1 }]);
});

test("repeat_n nests its body", () => {
  const body = chain(fake("takeoff", { id: "t" }), fake("land", { id: "l" }));
  assert.deepStrictEqual(
    run(fake("repeat_n", { id: "r", inputs: { N: n(4), BODY: body } })).blocks,
    [{ id: "r", op: "repeat_n", n: 4,
       body: [{ id: "t", op: "takeoff" }, { id: "l", op: "land" }] }]);
});

test("repeat_n accepts an expression instead of a literal count", () => {
  const times = fake("math_binop", { fields: { OP: "*" },
    inputs: { A: fake("sense_found_count"), B: n(2) } });
  assert.deepStrictEqual(
    run(fake("repeat_n", { id: "r", inputs: { N: times } })).blocks,
    [{ id: "r", op: "repeat_n", body: [],
       n: { kind: "binop", op: "*",
            left: { kind: "sensor", sensor: "found_count" },
            right: { kind: "number", value: 2 } } }]);
});

test("repeat_until / while / if take a Value cond", () => {
  const cond = () => fake("marker_visible");
  const body = fake("mark_found", { id: "m" });
  assert.deepStrictEqual(
    run(fake("repeat_until", { id: "u", inputs: { COND: cond(), BODY: body } })).blocks,
    [{ id: "u", op: "repeat_until", cond: { kind: "sensor", sensor: "target_visible" },
       body: [{ id: "m", op: "mark_found" }] }]);
  assert.deepStrictEqual(
    run(fake("while_block", { id: "w", inputs: { COND: cond(), BODY: body } })).blocks,
    [{ id: "w", op: "while", cond: { kind: "sensor", sensor: "target_visible" },
       body: [{ id: "m", op: "mark_found" }] }]);
  assert.deepStrictEqual(
    run(fake("if_block", { id: "i", inputs: { COND: cond(), BODY: body } })).blocks,
    [{ id: "i", op: "if", cond: { kind: "sensor", sensor: "target_visible" },
       body: [{ id: "m", op: "mark_found" }], else_body: [] }]);
});

test("if keeps its else branch", () => {
  const prog = run(fake("if_block", { id: "i", inputs: {
    COND: fake("marker_visible"),
    BODY: fake("mark_found", { id: "m" }),
    ELSE: fake("land", { id: "l" }) } }));
  assert.deepStrictEqual(prog.blocks[0].else_body, [{ id: "l", op: "land" }]);
});

test("break and continue serialize inside loops", () => {
  const body = chain(fake("break", { id: "b" }), fake("continue", { id: "c" }));
  const prog = run(fake("repeat_n", { id: "r", inputs: { N: n(2), BODY: body } }));
  assert.deepStrictEqual(prog.blocks[0].body, [
    { id: "b", op: "break" }, { id: "c", op: "continue" },
  ]);
  assert.deepStrictEqual(warnings, []);
});

test("loop control outside a loop produces a clear warning", () => {
  assert.deepStrictEqual(run(fake("break", { id: "b" })).blocks,
    [{ id: "b", op: "break" }]);
  assert.ok(warnings.some((w) => w.includes("must be placed inside a loop")));
});

// ── value blocks ────────────────────────────────────────────────────────────
test("every sensor block maps to its schema sensor name", () => {
  const expected = {
    marker_visible: "target_visible",
    sense_distance: "target_distance_cm",
    sense_bearing: "target_bearing_deg",
    sense_elevation: "target_elevation_deg",
    sense_count: "target_count",
    sense_found_count: "found_count",
    sense_battery: "battery",
  };
  for (const [type, sensor] of Object.entries(expected))
    assert.deepStrictEqual(valueJson(fake(type)), { kind: "sensor", sensor });
});

test("number literals", () => {
  assert.deepStrictEqual(valueJson(n(30)), { kind: "number", value: 30 });
  assert.deepStrictEqual(valueJson(fake("math_number", { fields: { NUM: "7" } })),
    { kind: "number", value: 7 });
});

test("variable get", () => {
  assert.deepStrictEqual(valueJson(fake("var_get", { fields: { VAR: "steps" } })),
    { kind: "var", name: "steps" });
});

test("arithmetic, comparison and logic all emit binop", () => {
  const cases = [
    ["math_binop", "+"], ["math_binop", "-"], ["math_binop", "*"], ["math_binop", "/"],
    ["compare", "<"], ["compare", ">"], ["compare", "<="], ["compare", ">="],
    ["compare", "=="], ["compare", "!="],
    ["logic_op", "and"], ["logic_op", "or"],
  ];
  for (const [type, op] of cases)
    assert.deepStrictEqual(
      valueJson(fake(type, { fields: { OP: op }, inputs: { A: n(1), B: n(2) } })),
      { kind: "binop", op,
        left: { kind: "number", value: 1 }, right: { kind: "number", value: 2 } });
});

test("not emits unop", () => {
  assert.deepStrictEqual(
    valueJson(fake("logic_not", { inputs: { VALUE: fake("marker_visible") } })),
    { kind: "unop", op: "not", operand: { kind: "sensor", sensor: "target_visible" } });
});

test("statement blocks are not values", () => {
  assert.strictEqual(valueJson(fake("takeoff")), null);
  assert.strictEqual(valueJson(null), null);
});

// ── recursion ───────────────────────────────────────────────────────────────
test("expressions nest to arbitrary depth", () => {
  // not( (distance / 2) + battery > found_count * 3  and  fire seen? )
  const expr = fake("logic_not", { inputs: { VALUE:
    fake("logic_op", { fields: { OP: "and" }, inputs: {
      A: fake("compare", { fields: { OP: ">" }, inputs: {
        A: fake("math_binop", { fields: { OP: "+" }, inputs: {
          A: fake("math_binop", { fields: { OP: "/" }, inputs: {
            A: fake("sense_distance"), B: n(2) } }),
          B: fake("sense_battery") } }),
        B: fake("math_binop", { fields: { OP: "*" }, inputs: {
          A: fake("sense_found_count"), B: n(3) } }) } }),
      B: fake("marker_visible") } }) } });
  assert.deepStrictEqual(valueJson(expr), {
    kind: "unop", op: "not", operand: {
      kind: "binop", op: "and",
      left: {
        kind: "binop", op: ">",
        left: {
          kind: "binop", op: "+",
          left: { kind: "binop", op: "/",
                  left: { kind: "sensor", sensor: "target_distance_cm" },
                  right: { kind: "number", value: 2 } },
          right: { kind: "sensor", sensor: "battery" },
        },
        right: { kind: "binop", op: "*",
                 left: { kind: "sensor", sensor: "found_count" },
                 right: { kind: "number", value: 3 } },
      },
      right: { kind: "sensor", sensor: "target_visible" },
    },
  });
});

test("an expression can be nested 20 deep", () => {
  let e = n(1);
  for (let i = 0; i < 20; i++)
    e = fake("math_binop", { fields: { OP: "+" }, inputs: { A: n(1), B: e } });
  let v = valueJson(e), depth = 0;
  while (v.kind === "binop") { v = v.right; depth++; }
  assert.strictEqual(depth, 20);
  assert.deepStrictEqual(v, { kind: "number", value: 1 });
});

test("expressions inside loop bodies inside expressions still resolve", () => {
  const prog = run(fake("while_block", { id: "w", inputs: {
    COND: fake("compare", { fields: { OP: "<" },
      inputs: { A: fake("sense_distance"), B: n(120) } }),
    BODY: fake("move", { id: "m", fields: { DIR: "forward" }, inputs: {
      CM: fake("math_binop", { fields: { OP: "/" },
        inputs: { A: fake("sense_distance"), B: n(2) } }) } }) } }));
  assert.deepStrictEqual(prog.blocks[0].body[0].cm, {
    kind: "binop", op: "/",
    left: { kind: "sensor", sensor: "target_distance_cm" },
    right: { kind: "number", value: 2 } });
});

// ── v1 beginner blocks keep working ─────────────────────────────────────────
test("marker_position_is maps per the v1 compatibility table", () => {
  for (const pos of ["left", "center", "right"])
    assert.deepStrictEqual(valueJson(fake("marker_position_is", { fields: { POS: pos } })),
      { kind: "sensor", sensor: `target_position_${pos}` });
});

test("found_count_gte expands to the documented binop", () => {
  assert.deepStrictEqual(valueJson(fake("found_count_gte", { fields: { N: 3 } })), {
    kind: "binop", op: ">=",
    left: { kind: "sensor", sensor: "found_count" },
    right: { kind: "number", value: 3 } });
});

// ── empty sockets ───────────────────────────────────────────────────────────
test("an empty test makes its block inert and warns", () => {
  const ifProg = run(fake("if_block", { id: "i" }));
  assert.deepStrictEqual(ifProg.blocks,
    [{ id: "i", op: "if", cond: 0, body: [], else_body: [] }]);
  assert.strictEqual(warnings.length, 1);
  assert.match(warnings[0], /'if' block is empty/);

  assert.strictEqual(run(fake("while_block", { id: "w" })).blocks[0].cond, 0);
  // a never-true test would spin repeat_until forever, so it defaults the other way
  assert.strictEqual(run(fake("repeat_until", { id: "u" })).blocks[0].cond, 1);
});

test("an empty number socket falls back to the smallest legal value and warns", () => {
  const prog = run(fake("move", { id: "m", fields: { DIR: "up" } }));
  assert.deepStrictEqual(prog.blocks, [{ id: "m", op: "move", dir: "up", cm: 20 }]);
  assert.strictEqual(warnings.length, 1);

  assert.strictEqual(run(fake("rotate", { id: "r", fields: { DIR: "cw" } })).blocks[0].deg, 1);
  assert.strictEqual(run(fake("repeat_n", { id: "r" })).blocks[0].n, 0);
  assert.strictEqual(run(fake("wait", { id: "w" })).blocks[0].seconds, 0);
  assert.strictEqual(run(fake("set_var", { id: "s", fields: { VAR: "x" } })).blocks[0].value, 0);
});

// protocol.py rejects an out-of-range *literal* outright, and the shadow number block has no
// min/max, so the serializer must never emit one.
test("out-of-range literals are clamped, not sent", () => {
  const cases = [
    ["move", "CM", "cm", 5, 20], ["move", "CM", "cm", 900, 500],
    ["rotate", "DEG", "deg", 0, 1], ["rotate", "DEG", "deg", 400, 360],
    ["repeat_n", "N", "n", -1, 0], ["repeat_n", "N", "n", 99, 50],
    ["wait", "SECONDS", "seconds", 60, 10], ["wait", "SECONDS", "seconds", -2, 0],
  ];
  for (const [type, input, field, typed, expected] of cases) {
    const prog = run(fake(type, { id: "b", fields: { DIR: "forward" },
                                  inputs: { [input]: n(typed) } }));
    assert.strictEqual(prog.blocks[0][field], expected, `${type} ${typed}`);
    assert.strictEqual(warnings.length, 1, `${type} ${typed} should warn once`);
  }
});

test("in-range literals pass through untouched, decimals included", () => {
  assert.strictEqual(run(fake("wait", { id: "w", inputs: { SECONDS: n(0.5) } }))
    .blocks[0].seconds, 0.5);
  assert.deepStrictEqual(warnings, []);
});

test("an expression in a ranged socket is left for the interpreter to clamp", () => {
  const huge = fake("math_binop", { fields: { OP: "*" }, inputs: { A: n(999), B: n(999) } });
  const prog = run(fake("move", { id: "m", fields: { DIR: "up" }, inputs: { CM: huge } }));
  assert.strictEqual(prog.blocks[0].cm.kind, "binop");
  assert.deepStrictEqual(warnings, []);
});

test("an empty operand inside an expression becomes the number node 0", () => {
  assert.deepStrictEqual(valueJson(fake("math_binop", { fields: { OP: "+" } })), {
    kind: "binop", op: "+",
    left: { kind: "number", value: 0 }, right: { kind: "number", value: 0 } });
  assert.deepStrictEqual(valueJson(fake("logic_not")), {
    kind: "unop", op: "not", operand: { kind: "number", value: 0 } });
});

test("warnings reset on each serialize and never duplicate", () => {
  run(chain(fake("move", { fields: { DIR: "up" } }), fake("move", { fields: { DIR: "up" } })));
  assert.strictEqual(warnings.length, 1);
  run(fake("takeoff"));
  assert.deepStrictEqual(warnings, []);
});

// ── toolbox ─────────────────────────────────────────────────────────────────
test("toolbox is a category toolbox with coloured, non-empty categories", () => {
  assert.strictEqual(toolbox.kind, "categoryToolbox");
  assert.ok(toolbox.contents.length >= 5);
  for (const c of toolbox.contents) {
    assert.strictEqual(c.kind, "category");
    assert.ok(c.name && c.colour, `category ${c.name} needs a name and colour`);
    assert.ok(c.contents.length > 0, `category ${c.name} is empty`);
  }
});

test("every toolbox block is defined, and every defined block is reachable", () => {
  const defined = new Set(blocks.map((b) => b.type));
  const builtin = new Set(["math_number"]); // Blockly's own literal block
  const used = new Set();
  for (const c of toolbox.contents)
    for (const b of c.contents) {
      assert.ok(defined.has(b.type) || builtin.has(b.type), `undefined block ${b.type}`);
      used.add(b.type);
      for (const inp of Object.values(b.inputs || {}))
        assert.ok(defined.has(inp.shadow.type) || builtin.has(inp.shadow.type));
    }
  // `start` is placed by app.js, not dragged out of the toolbox
  for (const t of defined) if (t !== "start") assert.ok(used.has(t), `${t} is not in the toolbox`);
});

test("every custom block has a useful description", () => {
  for (const block of blocks) {
    assert.strictEqual(typeof block.tooltip, "string", `${block.type} has no tooltip`);
    assert.ok(block.tooltip.trim().length >= 12, `${block.type} tooltip is too short`);
  }
});

test("number sockets ship a shadow so beginners still see a typeable number", () => {
  const shadows = {};
  for (const c of toolbox.contents)
    for (const b of c.contents)
      for (const [name, inp] of Object.entries(b.inputs || {}))
        shadows[`${b.type}.${name}`] = inp.shadow.fields.NUM;
  assert.deepStrictEqual(shadows, {
    "move.CM": 50, "rotate.DEG": 90, "repeat_n.N": 4, "wait.SECONDS": 2,
    "compare.A": 0, "compare.B": 0, "math_binop.A": 1, "math_binop.B": 1, "set_var.VALUE": 0,
  });
});

test("every block that can be dragged out serializes to something", () => {
  const values = new Set(blocks.filter((b) => "output" in b).map((b) => b.type));
  for (const c of toolbox.contents)
    for (const b of c.contents) {
      if (b.type === "math_number") continue;
      const f = fake(b.type, { fields: { NUM: 1, N: 1, DIR: "forward", POS: "left",
                                         OP: "+", VAR: "steps" } });
      const out = values.has(b.type) ? valueJson(f) : COMP1.blockJson(f);
      assert.ok(out, `${b.type} serialized to nothing`);
      if (!values.has(b.type)) assert.ok(out.op, `${b.type} has no op`);
      else assert.ok(out.kind, `${b.type} has no kind`);
    }
});
