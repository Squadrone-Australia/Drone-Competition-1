const C = { hue_flight: 210, hue_vision: 0, hue_flow: 120, hue_mission: 280 };

Blockly.defineBlocksWithJsonArray([
  { type: "takeoff", message0: "take off", colour: C.hue_flight,
    previousStatement: null, nextStatement: null },
  { type: "land", message0: "land", colour: C.hue_flight,
    previousStatement: null, nextStatement: null },
  { type: "move", message0: "fly %1 %2 cm", colour: C.hue_flight,
    args0: [
      { type: "field_dropdown", name: "DIR", options: [
        ["forward", "forward"], ["back", "back"], ["left", "left"],
        ["right", "right"], ["up", "up"], ["down", "down"]] },
      { type: "field_number", name: "CM", value: 50, min: 20, max: 500 }],
    previousStatement: null, nextStatement: null },
  { type: "rotate", message0: "turn %1 %2 °", colour: C.hue_flight,
    args0: [
      { type: "field_dropdown", name: "DIR",
        options: [["↻ clockwise", "cw"], ["↺ counter-clockwise", "ccw"]] },
      { type: "field_number", name: "DEG", value: 90, min: 1, max: 360 }],
    previousStatement: null, nextStatement: null },
  { type: "flip", message0: "flip %1", colour: C.hue_flight,
    args0: [{ type: "field_dropdown", name: "DIR", options: [
      ["forward", "forward"], ["back", "back"], ["left", "left"], ["right", "right"]] }],
    previousStatement: null, nextStatement: null },
  { type: "marker_visible", message0: "victim marker seen?", colour: C.hue_vision,
    output: "Boolean" },
  { type: "marker_position_is", message0: "victim marker is %1", colour: C.hue_vision,
    args0: [{ type: "field_dropdown", name: "POS", options: [
      ["on the left", "left"], ["in the centre", "center"], ["on the right", "right"]] }],
    output: "Boolean" },
  { type: "approach_marker", message0: "approach victim and stop", colour: C.hue_vision,
    previousStatement: null, nextStatement: null },
  { type: "mark_found", message0: "signal victim found 🎉", colour: C.hue_vision,
    previousStatement: null, nextStatement: null },
  { type: "found_count_gte", message0: "victims found ≥ %1", colour: C.hue_vision,
    args0: [{ type: "field_number", name: "N", value: 3, min: 1, max: 20 }],
    output: "Boolean" },
  { type: "repeat_n", message0: "repeat %1 times %2", colour: C.hue_flow,
    args0: [{ type: "field_number", name: "N", value: 4, min: 1, max: 50 },
            { type: "input_statement", name: "BODY" }],
    previousStatement: null, nextStatement: null },
  { type: "repeat_until", message0: "keep doing until %1 %2", colour: C.hue_flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" }],
    previousStatement: null, nextStatement: null },
  { type: "if_block", message0: "if %1 then %2 else %3", colour: C.hue_flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" },
            { type: "input_statement", name: "ELSE" }],
    previousStatement: null, nextStatement: null },
  { type: "end_mission", message0: "end mission and land 🏁", colour: C.hue_mission,
    previousStatement: null },
  { type: "start", message0: "🚁 when mission starts", colour: C.hue_mission,
    nextStatement: null, deletable: false },
]);

const TOOLBOX = { kind: "flyoutToolbox", contents: [
  "takeoff", "land", "move", "rotate", "flip",
  "marker_visible", "marker_position_is", "approach_marker", "mark_found", "found_count_gte",
  "repeat_n", "repeat_until", "if_block", "end_mission",
].map(t => ({ kind: "block", type: t })) };

function condJson(block, name) {
  const target = block.getInputTargetBlock(name);
  if (!target) return { sensor: "marker_visible", value: 0 };
  if (target.type === "found_count_gte")
    return { sensor: "found_count_gte", value: Number(target.getFieldValue("N")) };
  if (target.type === "marker_position_is")
    return { sensor: "marker_position_" + target.getFieldValue("POS"), value: 0 };
  return { sensor: "marker_visible", value: 0 };
}

function blockJson(b) {
  const base = { id: b.id };
  switch (b.type) {
    case "takeoff": case "land": case "approach_marker":
    case "mark_found": case "end_mission":
      return { ...base, op: b.type };
    case "move":
      return { ...base, op: "move", dir: b.getFieldValue("DIR"),
               cm: Number(b.getFieldValue("CM")) };
    case "rotate":
      return { ...base, op: "rotate", dir: b.getFieldValue("DIR"),
               deg: Number(b.getFieldValue("DEG")) };
    case "flip":
      return { ...base, op: "flip", dir: b.getFieldValue("DIR") };
    case "repeat_n":
      return { ...base, op: "repeat_n", n: Number(b.getFieldValue("N")),
               body: chainJson(b.getInputTargetBlock("BODY")) };
    case "repeat_until":
      return { ...base, op: "repeat_until", cond: condJson(b, "COND"),
               body: chainJson(b.getInputTargetBlock("BODY")) };
    case "if_block":
      return { ...base, op: "if", cond: condJson(b, "COND"),
               body: chainJson(b.getInputTargetBlock("BODY")),
               else_body: chainJson(b.getInputTargetBlock("ELSE")) };
    default:
      return null; // value blocks are consumed by condJson
  }
}

function chainJson(block) {
  const out = [];
  for (let b = block; b; b = b.getNextBlock()) {
    const j = blockJson(b);
    if (j) out.push(j);
  }
  return out;
}

window.COMP1 = {
  toolbox: TOOLBOX,
  serializeProgram(workspace) {
    const start = workspace.getBlocksByType("start", false)[0];
    return { version: 1, blocks: start ? chainJson(start.getNextBlock()) : [] };
  },
};
