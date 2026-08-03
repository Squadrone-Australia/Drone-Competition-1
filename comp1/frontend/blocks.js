// Block definitions + the program serializer (schema v2).
// Loaded as a plain <script> in the browser; also require()-able from node for tests,
// which is why every browser global is guarded.

const C = { flight: 210, sense: 0, mission: 280, flow: 120, logic: 160, math: 230, vars: 330 };

const BLOCKS = [
  // ── flight ────────────────────────────────────────────────────────────────
  { type: "takeoff", message0: "take off", colour: C.flight,
    tooltip: "Starts the motors and rises to the drone's normal take-off height.",
    previousStatement: null, nextStatement: null },
  { type: "land", message0: "land", colour: C.flight,
    tooltip: "Descends vertically and stops the motors.",
    previousStatement: null, nextStatement: null },
  { type: "move", message0: "fly %1 %2 cm", colour: C.flight, inputsInline: true,
    args0: [
      { type: "field_dropdown", name: "DIR", options: [
        ["forward", "forward"], ["back", "back"], ["left", "left"],
        ["right", "right"], ["up", "up"], ["down", "down"]] },
      { type: "input_value", name: "CM", check: "Number" }],
    tooltip: "20 to 500 cm. Anything outside that is pulled back to the nearest limit.",
    previousStatement: null, nextStatement: null },
  { type: "rotate", message0: "turn %1 %2 °", colour: C.flight, inputsInline: true,
    args0: [
      { type: "field_dropdown", name: "DIR",
        options: [["↻ clockwise", "cw"], ["↺ counter-clockwise", "ccw"]] },
      { type: "input_value", name: "DEG", check: "Number" }],
    tooltip: "1 to 360 degrees.",
    previousStatement: null, nextStatement: null },
  { type: "flip", message0: "flip %1", colour: C.flight,
    args0: [{ type: "field_dropdown", name: "DIR", options: [
      ["forward", "forward"], ["back", "back"], ["left", "left"], ["right", "right"]] }],
    tooltip: "Performs one aerial flip in the selected direction.",
    previousStatement: null, nextStatement: null },
  { type: "sense_battery", message0: "battery %", colour: C.flight, output: "Number",
    tooltip: "Charge left, 0 to 100." },

  // ── sensing ───────────────────────────────────────────────────────────────
  { type: "marker_visible", message0: "victim marker seen?", colour: C.sense,
    tooltip: "True while the camera can see a victim marker.",
    output: "Boolean" },
  { type: "marker_position_is", message0: "victim marker is %1", colour: C.sense,
    args0: [{ type: "field_dropdown", name: "POS", options: [
      ["on the left", "left"], ["in the centre", "center"], ["on the right", "right"]] }],
    tooltip: "True when the visible victim marker is in the selected part of the camera view.",
    output: "Boolean" },
  { type: "sense_distance", message0: "distance to victim (cm)", colour: C.sense,
    tooltip: "How far away the victim is. Reads 9999 when nothing is in view, " +
             "so 'keep doing until distance < 120' keeps searching instead of stopping.",
    output: "Number" },
  { type: "sense_bearing", message0: "direction to victim (°)", colour: C.sense,
    tooltip: "Negative = to the left, positive = to the right. 0 when nothing is in view.",
    output: "Number" },
  { type: "sense_elevation", message0: "height angle to victim (°)", colour: C.sense,
    tooltip: "Negative = below the drone, positive = above. 0 when nothing is in view.",
    output: "Number" },
  { type: "sense_count", message0: "victims in view", colour: C.sense,
    tooltip: "How many victim markers the camera can see right now.",
    output: "Number" },

  // ── mission ───────────────────────────────────────────────────────────────
  { type: "approach_marker", message0: "approach victim and stop", colour: C.mission,
    tooltip: "Chooses the closest visible victim, flies toward it, and stops at the configured safe distance.",
    previousStatement: null, nextStatement: null },
  { type: "mark_found", message0: "signal victim found 🎉", colour: C.mission,
    tooltip: "Performs the required signal and adds one to the victims-found count.",
    previousStatement: null, nextStatement: null },
  { type: "sense_found_count", message0: "victims found so far", colour: C.mission,
    tooltip: "The number of times this program has used 'signal victim found'.",
    output: "Number" },
  { type: "found_count_gte", message0: "victims found ≥ %1", colour: C.mission,
    args0: [{ type: "field_number", name: "N", value: 3, min: 1, max: 20 }],
    tooltip: "True when the victims-found count is at least the selected number.",
    output: "Boolean" },
  { type: "end_mission", message0: "end mission and land 🏁", colour: C.mission,
    tooltip: "Lands immediately and finishes the program without running later blocks.",
    previousStatement: null },
  { type: "start", message0: "🚁 when mission starts", colour: C.mission,
    tooltip: "The program begins here. Connect the first command underneath this block.",
    nextStatement: null, deletable: false },

  // ── control ───────────────────────────────────────────────────────────────
  { type: "repeat_n", message0: "repeat %1 times %2", colour: C.flow, inputsInline: true,
    args0: [{ type: "input_value", name: "N", check: "Number" },
            { type: "input_statement", name: "BODY" }],
    tooltip: "0 to 50 times. 0 skips the blocks inside.",
    previousStatement: null, nextStatement: null },
  { type: "repeat_until", message0: "keep doing until %1 %2", colour: C.flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" }],
    tooltip: "Runs the blocks inside over and over, stopping once the test becomes true.",
    previousStatement: null, nextStatement: null },
  { type: "while_block", message0: "while %1 keep doing %2", colour: C.flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" }],
    tooltip: "The opposite of 'keep doing until' — runs while the test is true.",
    previousStatement: null, nextStatement: null },
  { type: "if_block", message0: "if %1 then %2 else %3", colour: C.flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" },
            { type: "input_statement", name: "ELSE" }],
    tooltip: "Runs the 'then' blocks when the test is true; otherwise runs the 'else' blocks.",
    previousStatement: null, nextStatement: null },
  { type: "break", message0: "break out of loop", colour: C.flow,
    tooltip: "Stops the nearest repeat, while, or keep-doing-until loop and continues after it.",
    previousStatement: null, nextStatement: null },
  { type: "continue", message0: "continue with next loop", colour: C.flow,
    tooltip: "Skips the remaining blocks in the nearest loop and begins its next repeat.",
    previousStatement: null, nextStatement: null },
  { type: "wait", message0: "wait %1 seconds", colour: C.flow, inputsInline: true,
    args0: [{ type: "input_value", name: "SECONDS", check: "Number" }],
    tooltip: "0 to 10 seconds. The drone hovers where it is.",
    previousStatement: null, nextStatement: null },

  // ── logic ─────────────────────────────────────────────────────────────────
  { type: "compare", message0: "%1 %2 %3", colour: C.logic, inputsInline: true,
    args0: [{ type: "input_value", name: "A", check: "Number" },
            { type: "field_dropdown", name: "OP", options: [
              ["<", "<"], [">", ">"], ["≤", "<="], ["≥", ">="], ["=", "=="], ["≠", "!="]] },
            { type: "input_value", name: "B", check: "Number" }],
    tooltip: "Compares two numbers, e.g. distance to victim < 120.",
    output: "Boolean" },
  { type: "logic_op", message0: "%1 %2 %3", colour: C.logic, inputsInline: true,
    args0: [{ type: "input_value", name: "A", check: "Boolean" },
            { type: "field_dropdown", name: "OP", options: [["and", "and"], ["or", "or"]] },
            { type: "input_value", name: "B", check: "Boolean" }],
    tooltip: "'and' needs both to be true, 'or' needs just one.",
    output: "Boolean" },
  { type: "logic_not", message0: "not %1", colour: C.logic, inputsInline: true,
    args0: [{ type: "input_value", name: "VALUE", check: "Boolean" }],
    tooltip: "Flips true into false and false into true.",
    output: "Boolean" },

  // ── maths ─────────────────────────────────────────────────────────────────
  { type: "math_binop", message0: "%1 %2 %3", colour: C.math, inputsInline: true,
    args0: [{ type: "input_value", name: "A", check: "Number" },
            { type: "field_dropdown", name: "OP", options: [
              ["+", "+"], ["−", "-"], ["×", "*"], ["÷", "/"]] },
            { type: "input_value", name: "B", check: "Number" }],
    tooltip: "Dividing by 0 gives 0 and a warning rather than crashing the mission.",
    output: "Number" },

  // ── variables ─────────────────────────────────────────────────────────────
  { type: "var_get", message0: "%1", colour: C.vars,
    args0: [{ type: "field_variable", name: "VAR", variable: "steps" }],
    tooltip: "The value stored in this box. Unset boxes read as 0.",
    output: null },
  { type: "set_var", message0: "set %1 to %2", colour: C.vars, inputsInline: true,
    args0: [{ type: "field_variable", name: "VAR", variable: "steps" },
            { type: "input_value", name: "VALUE" }],
    tooltip: "Stores a value in a named box so later blocks can read it.",
    previousStatement: null, nextStatement: null },
];

// `math_number` is Blockly's own literal block — used as the shadow inside every number socket,
// so a beginner still just sees a number to type into.
if (typeof Blockly !== "undefined") Blockly.defineBlocksWithJsonArray(BLOCKS);

const shadowNum = (v) => ({ shadow: { type: "math_number", fields: { NUM: v } } });
const blk = (type, inputs) => (inputs ? { kind: "block", type, inputs } : { kind: "block", type });
const cat = (name, colour, contents) => ({ kind: "category", name, colour: String(colour), contents });

const TOOLBOX = { kind: "categoryToolbox", contents: [
  cat("Flight", C.flight, [
    blk("takeoff"), blk("land"),
    blk("move", { CM: shadowNum(50) }), blk("rotate", { DEG: shadowNum(90) }),
    blk("flip"), blk("sense_battery")]),
  cat("Sensing", C.sense, [
    blk("marker_visible"), blk("marker_position_is"), blk("sense_distance"),
    blk("sense_bearing"), blk("sense_elevation"), blk("sense_count")]),
  cat("Mission", C.mission, [
    blk("approach_marker"), blk("mark_found"), blk("sense_found_count"),
    blk("found_count_gte"), blk("end_mission")]),
  cat("Control", C.flow, [
    blk("repeat_n", { N: shadowNum(4) }), blk("repeat_until"), blk("while_block"),
    blk("if_block"), blk("break"), blk("continue"),
    blk("wait", { SECONDS: shadowNum(2) })]),
  cat("Logic", C.logic, [
    blk("compare", { A: shadowNum(0), B: shadowNum(0) }), blk("logic_op"), blk("logic_not")]),
  cat("Maths", C.math, [
    blk("math_number"), blk("math_binop", { A: shadowNum(1), B: shadowNum(1) })]),
  cat("Variables", C.vars, [
    blk("set_var", { VALUE: shadowNum(0) }), blk("var_get")]),
] };

// ── serializer ──────────────────────────────────────────────────────────────
// Value nodes follow docs schema v2 exactly: number / sensor / var / binop / unop.

const SENSORS = {
  marker_visible: "target_visible",
  sense_distance: "target_distance_cm",
  sense_bearing: "target_bearing_deg",
  sense_elevation: "target_elevation_deg",
  sense_count: "target_count",
  sense_found_count: "found_count",
  sense_battery: "battery",
};

const num = (v) => ({ kind: "number", value: v });

// mirrors protocol.LIMITS. The backend rejects a *literal* outside its range outright (a
// computed one it clamps mid-flight), and the shadow number block has no min/max of its own,
// so clamp here: a student who types 5 into 'fly' gets a nudge, not a program that won't run.
const LIMITS = { cm: [20, 500], deg: [1, 360], n: [0, 50], seconds: [0, 10] };

const WARNINGS = [];
function warn(msg) { if (!WARNINGS.includes(msg)) WARNINGS.push(msg); }

// FieldVariable stores an id; its text is the name students see. Fall back to the raw
// field value so plain text/number fields (and test doubles) work too.
function varName(b, name) {
  const f = b.getField && b.getField(name);
  return f && f.getText ? f.getText() : String(b.getFieldValue(name));
}

// An operand inside an expression: always a full Value node, never bare.
function operand(b, name) {
  return valueJson(b.getInputTargetBlock(name)) || num(fill("part of a calculation", 0));
}

function fill(what, dflt, why) {
  warn(`${what} is empty — ${why || `filled in with ${dflt}`}`);
  return dflt;
}

// A Value in a block field (cm / deg / n / seconds / cond / set_var value).
// A plain literal is emitted as a bare number — schema v2 allows it everywhere a Value
// is allowed, and it keeps a beginner's program looking exactly like it did under v1.
function slot(b, name, what, dflt, limit, why) {
  const v = valueJson(b.getInputTargetBlock(name));
  const out = v ? (v.kind === "number" ? v.value : v) : fill(what, dflt, why);
  if (typeof out !== "number" || !LIMITS[limit]) return out; // expressions clamp at run time
  const [lo, hi] = LIMITS[limit];
  const c = Math.min(hi, Math.max(lo, out));
  if (c !== out) warn(`${what} has to be ${lo} to ${hi} — using ${c} instead of ${out}`);
  return c;
}

function valueJson(b) {
  if (!b) return null;
  const sensor = SENSORS[b.type];
  if (sensor) return { kind: "sensor", sensor };
  switch (b.type) {
    case "math_number":
      return num(Number(b.getFieldValue("NUM")));
    case "marker_position_is":
      return { kind: "sensor", sensor: "target_position_" + b.getFieldValue("POS") };
    case "found_count_gte":
      return { kind: "binop", op: ">=",
               left: { kind: "sensor", sensor: "found_count" },
               right: num(Number(b.getFieldValue("N"))) };
    case "math_binop": case "compare": case "logic_op":
      return { kind: "binop", op: b.getFieldValue("OP"),
               left: operand(b, "A"), right: operand(b, "B") };
    case "logic_not":
      return { kind: "unop", op: "not", operand: operand(b, "VALUE") };
    case "var_get":
      return { kind: "var", name: varName(b, "VAR") };
    default:
      return null;
  }
}

function blockJson(b, loopDepth = 0) {
  const base = { id: b.id };
  switch (b.type) {
    case "takeoff": case "land": case "approach_marker":
    case "mark_found": case "end_mission":
      return { ...base, op: b.type };
    case "break": case "continue":
      if (loopDepth === 0) warn(`'${b.type}' must be placed inside a loop`);
      return { ...base, op: b.type };
    case "move":
      return { ...base, op: "move", dir: b.getFieldValue("DIR"),
               cm: slot(b, "CM", "the distance in a 'fly' block", 20, "cm") };
    case "rotate":
      return { ...base, op: "rotate", dir: b.getFieldValue("DIR"),
               deg: slot(b, "DEG", "the angle in a 'turn' block", 1, "deg") };
    case "flip":
      return { ...base, op: "flip", dir: b.getFieldValue("DIR") };
    case "wait":
      return { ...base, op: "wait",
               seconds: slot(b, "SECONDS", "the time in a 'wait' block", 0, "seconds") };
    case "set_var":
      return { ...base, op: "set_var", name: varName(b, "VAR"),
               value: slot(b, "VALUE", "the value in a 'set' block", 0) };
    case "repeat_n":
      return { ...base, op: "repeat_n",
               n: slot(b, "N", "the count in a 'repeat' block", 0, "n"),
               body: chainJson(b.getInputTargetBlock("BODY"), loopDepth + 1) };
    // An empty test makes its block do nothing at all, which is the one guess that can
    // never surprise a student mid-flight — note that means 1, not 0, for repeat_until.
    case "repeat_until":
      return { ...base, op: "repeat_until",
               cond: slot(b, "COND", "the test in a 'keep doing until' block", 1, null,
                          "so the loop stops straight away"),
               body: chainJson(b.getInputTargetBlock("BODY"), loopDepth + 1) };
    case "while_block":
      return { ...base, op: "while",
               cond: slot(b, "COND", "the test in a 'while' block", 0, null,
                          "so the loop is skipped"),
               body: chainJson(b.getInputTargetBlock("BODY"), loopDepth + 1) };
    case "if_block":
      return { ...base, op: "if",
               cond: slot(b, "COND", "the test in an 'if' block", 0, null,
                          "so the 'then' part never runs"),
               body: chainJson(b.getInputTargetBlock("BODY"), loopDepth),
               else_body: chainJson(b.getInputTargetBlock("ELSE"), loopDepth) };
    default:
      return null; // value blocks are consumed by valueJson
  }
}

function chainJson(block, loopDepth = 0) {
  const out = [];
  for (let b = block; b; b = b.getNextBlock()) {
    const j = blockJson(b, loopDepth);
    if (j) out.push(j);
  }
  return out;
}

// A readable, copyable Python equivalent for teaching and debugging. It is
// display-only: the flight runtime continues to validate and interpret JSON.
function pythonValue(value) {
  if (typeof value === "number") return Number.isFinite(value) ? String(value) : "0";
  if (!value || typeof value !== "object") return "0";
  switch (value.kind) {
    case "number":
      return Number.isFinite(value.value) ? String(value.value) : "0";
    case "var":
      return `variables.get(${JSON.stringify(value.name)}, 0)`;
    case "sensor": {
      const fixed = {
        target_visible: "drone.sees_target()",
        target_distance_cm: '_target_value("distance_cm", 9999)',
        target_bearing_deg: '_target_value("bearing_deg", 0)',
        target_elevation_deg: '_target_value("elevation_deg", 0)',
        target_count: "len(drone.targets())",
        found_count: "drone.found_count",
        battery: "drone.battery",
      };
      if (fixed[value.sensor]) return fixed[value.sensor];
      const position = value.sensor.match(/^target_position_(left|center|right)$/);
      return position
        ? `_target_value("position", "") == ${JSON.stringify(position[1])}`
        : "0";
    }
    case "unop": {
      const operand = pythonValue(value.operand);
      if (value.op === "not") return `(not ${operand})`;
      if (value.op === "abs") return `abs(${operand})`;
      return `(-${operand})`;
    }
    case "binop": {
      const left = pythonValue(value.left);
      const right = pythonValue(value.right);
      if (value.op === "/") return `_safe_div(${left}, ${right})`;
      return `(${left} ${value.op} ${right})`;
    }
    default:
      return "0";
  }
}

function pythonBlocks(blocks, depth = 0) {
  const lines = [];
  const pad = "    ".repeat(depth);
  const add = (line) => lines.push(pad + line);
  const child = (items) => {
    const nested = pythonBlocks(items || [], depth + 1);
    return nested.length ? nested : ["    ".repeat(depth + 1) + "pass"];
  };

  for (const block of blocks || []) {
    add(`# block ${block.op} [${block.id}]`);
    switch (block.op) {
      case "takeoff": add("drone.takeoff()"); break;
      case "land": add("drone.land()"); break;
      case "move": add(`drone.${block.dir}(${pythonValue(block.cm)})`); break;
      case "rotate":
        add(`drone.${block.dir === "cw" ? "turn_right" : "turn_left"}(${pythonValue(block.deg)})`);
        break;
      case "flip": add(`drone.flip(${JSON.stringify(block.dir)})`); break;
      case "approach_marker": add("drone.approach_target()"); break;
      case "mark_found": add("drone.mark_found()"); break;
      case "end_mission":
        add("drone.land()");
        add("raise SystemExit  # finish without running later blocks");
        break;
      case "set_var":
        add(`variables[${JSON.stringify(block.name)}] = ${pythonValue(block.value)}`);
        break;
      case "wait": add(`drone.wait(_clamp(${pythonValue(block.seconds)}, 0, 10))`); break;
      case "break": add("break"); break;
      case "continue": add("continue"); break;
      case "repeat_n":
        add(`for _ in range(_clamp(round(${pythonValue(block.n)}), 0, 50)):`);
        lines.push(...child(block.body));
        break;
      case "repeat_until":
        add(`while not ${pythonValue(block.cond)}:`);
        lines.push(...child(block.body));
        break;
      case "while":
        add(`while ${pythonValue(block.cond)}:`);
        lines.push(...child(block.body));
        break;
      case "if":
        add(`if ${pythonValue(block.cond)}:`);
        lines.push(...child(block.body));
        if ((block.else_body || []).length) {
          add("else:");
          lines.push(...child(block.else_body));
        }
        break;
    }
  }
  return lines;
}

function programToPython(program) {
  const preamble = [
    "# Display-only Python equivalent.",
    "# The competition app executes validated JSON, not this generated text.",
    "from comp1.api import Drone",
    "",
    "drone = Drone()",
    "variables = {}",
    "",
    "def mission():",
  ];
  const body = pythonBlocks(program?.blocks || [], 1);
  const mission = body.length
    ? body
    : ["    # Connect blocks beneath 'when mission starts' to translate them."];
  const helpers = [
    "",
    "def _clamp(value, low, high):",
    "    return max(low, min(high, value))",
    "",
    "def _safe_div(left, right):",
    "    return left / right if right else 0",
    "",
    "def _target_value(name, default):",
    "    target = drone.target()",
    "    return getattr(target, name) if target else default",
    "",
    "mission()",
    "",
  ];
  return [...preamble, ...mission, ...helpers].join("\n");
}

const COMP1 = {
  blocks: BLOCKS,
  toolbox: TOOLBOX,
  warnings: WARNINGS, // filled by serializeProgram; app.js prints these to the console
  valueJson, blockJson, chainJson, programToPython,
  serializeProgram(workspace) {
    WARNINGS.length = 0;
    const start = workspace.getBlocksByType("start", false)[0];
    return { version: 2, blocks: start ? chainJson(start.getNextBlock()) : [] };
  },
};

if (typeof window !== "undefined") window.COMP1 = COMP1;
if (typeof module !== "undefined") module.exports = COMP1;
