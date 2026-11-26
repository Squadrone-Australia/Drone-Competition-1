// The first-run tutorial: a tour of the screen, then one program built and flown.
//
// Written for the student who has never seen the app, has no teacher at their
// elbow, and is not going to read a manual. Everything it says is aimed at the
// questions that student actually has, in the order they have them: what is
// this, can I break it, where do the blocks come from, why did nothing happen,
// what is the big red button. The answers live on the screen next to the thing
// they are about, because a paragraph about "the console" means nothing until
// the console is lit up in front of you.
//
// The build half is checked, not narrated. Each step reads the real workspace
// and says what is wrong in the words the student can see on the blocks: "is on
// the workspace but not snapped on", "has 90 - click the 90 and type 180". The
// single most common beginner failure is a block dropped near the program
// rather than into it, which runs nothing and says nothing, so that is the
// first thing every check looks for. Every step also has "Do it for me": a
// student who cannot make a trackpad drag work should still get to see the
// drone fly.
//
// Four rules hold the design together.
//
// **It only ever flies the simulator.** The lesson ends with a real Run, and a
// tutorial must never be the reason a physical drone took off. It will not
// start against anything else, and it closes itself the moment the drone is
// switched underneath it. Inside the simulator it moves to the square arena,
// and puts the student back in the arena they came from when it ends.
//
// **A student's work is never thrown away.** The lesson needs an empty
// program. If there are blocks already, they are put aside - in memory and in
// localStorage, so a closed tab cannot lose them - and offered back at the end.
// When in doubt (skipped, closed, the drone changed) they come back, because
// five tutorial blocks are easy to rebuild and somebody's own program is not.
//
// **Nothing here may throw**, for the same reason as buffer.js: this is a
// convenience, and a tutorial that breaks the page has cost the student the
// whole session. Storage that is missing or denied just means the tutorial
// cannot remember it was seen, so it does not open by itself.
//
// **The lesson is a contract with the simulator.** The program it teaches -
// out 50 cm, turn round, back 50 cm, land - never comes near anything solid,
// because nothing is placed within 1.2 m of the start pad (sim/scenery.py).
// tests/fixtures/tutorial_lesson.json pins the program; tests/js checks this
// file still builds exactly that, and tests/test_tutorial_lesson.py flies it in
// every arena. A first flight that crashes is a terrible first lesson. The
// optional camera lesson (VISION_LESSON) is held the same way, by
// tutorial_vision_lesson.json, and flown with both marker finders.

const TUTORIAL_KEY = "comp1.tutorial.v1";       // "done" | "skipped"
const STASH_KEY = "comp1.tutorial.stash.v1";    // blocks put aside for the lesson
const STASH_FORMAT = 1;
// The arena the tutorial is flown in: the square room (sim/scenery.py), not the
// competition one students start in. See useLessonArena.
const LESSON_ARENA = "arena";

// Blockly's colour for each toolbox category, so a block named in the text is
// drawn the colour the student is looking for. Mirrors `C` in blocks.js.
const HUES = { flight: 205, sense: 185, mission: 32, flow: 120 };

// ── the lesson ──────────────────────────────────────────────────────────────
// `fields` pairs each value with the text the dropdown shows, because the hint
// has to name what the student will actually click on.

const FORWARD_50 = {
  type: "move", label: "fly forward 50 cm",
  fields: { DIR: ["forward", "forward"] }, number: { input: "CM", value: 50, unit: " cm" },
};
const LESSON = [
  { type: "takeoff", label: "take off" },
  FORWARD_50,
  { type: "rotate", label: "turn ↻ clockwise 180°",
    fields: { DIR: ["cw", "↻ clockwise"] }, number: { input: "DEG", value: 180, unit: "°" } },
  FORWARD_50,
  { type: "land", label: "land" },
];

// The camera lesson: the optional second half, offered after the first flight
// and from the welcome card. It is the old final-card challenge made into
// checked steps, because a list of block names on a card never taught the two
// things a vision program needs that a stack of moves does not: a test dropped
// into a slot, and blocks put *inside* a loop rather than under it. Measured in
// both arenas with both detectors before it was chosen (see
// tests/test_tutorial_lesson.py): it finds a target every time and never
// crashes, because "approach target and stop" stops short of the sign.
//
// `cond` is a block that goes into a value slot, `body` the blocks that go
// inside a loop. A step can ask for a block's shell first and its insides
// later - SHELL, then COND, then FULL - so each new idea gets a step of its own.
const SHELL = 0, COND = 1, FULL = 2;
const TURN_30 = {
  type: "rotate", label: "turn ↻ clockwise 30°",
  fields: { DIR: ["cw", "↻ clockwise"] }, number: { input: "DEG", value: 30, unit: "°" },
};
const VISION_LESSON = [
  { type: "takeoff", label: "take off" },
  { type: "repeat_until", label: "keep doing until",
    cond: { input: "COND", type: "marker_visible", label: "target seen?" },
    body: { input: "BODY", blocks: [TURN_30] } },
  { type: "approach_marker", label: "approach target and stop" },
  { type: "mark_found", label: "signal target found 🎉" },
  { type: "land", label: "land" },
];

// The final card's challenge, as the console keeps it after the card is gone.
// Logged when a student finishes without taking the camera lesson.
//
// A goal and where to look, never the program. This once listed the five
// blocks of a search in order: a recipe to copy, with none of the why. The
// camera lesson is where the search is taught, step by step and with reasons,
// so the console points there instead.
const CHALLENGE_LOG = [
  "🔥 Next challenge: make the drone find a target on its own.",
  "   Your program flies the same moves every time. Can it look around until the camera sees red?",
  "   Look in Sensing for questions the camera can answer, and in Control for blocks that repeat.",
  "   Want it step by step? Press Tutorial at the top and choose the camera lesson.",
];

// What the console keeps after the camera lesson: the puzzle it leaves open.
const CAMERA_LOG = [
  "🔥 Next challenge: find all three targets.",
  "   After signalling, the drone is still looking at the target it just found.",
  "   How could you make it look somewhere new before it searches again?",
  "   Click any Sensing block to read what it measures: distance, direction, left or right.",
];

function startBlock(ws) {
  try {
    return ws.getBlocksByType("start", false)[0] || null;
  } catch (_error) {
    return null;
  }
}

/** The blocks snapped under the start block, top to bottom. */
function chainOf(ws) {
  const out = [];
  const start = startBlock(ws);
  for (let b = start && start.getNextBlock(); b; b = b.getNextBlock()) out.push(b);
  return out;
}

function blockText(b) {
  try {
    const text = b.toString();
    if (text) return text.replace(/\s+/g, " ").trim();
  } catch (_error) {
    // fall through to the type name
  }
  return b.type;
}

function numberIn(b, input) {
  const target = b.getInputTargetBlock(input);
  if (!target || target.type !== "math_number") return null;
  const n = Number(target.getFieldValue("NUM"));
  return Number.isFinite(n) ? n : null;
}

/** A block of `type` that is on the workspace but not part of the program. */
function looseBlock(ws, type) {
  try {
    return ws.getAllBlocks(false).find((b) => b.type === type
      && b.getRootBlock().type !== "start") || null;
  } catch (_error) {
    return null;
  }
}

/**
 * What is wrong with block `b` as lesson block `spec`, in words, or "".
 *
 * `place` names a block that is not in the main stack - the one inside a
 * loop. Otherwise it is named by its position under the start block.
 */
function problemWith(b, spec, n, place) {
  const is = place || `Block ${n} under “when mission starts”`;
  const says = place || `Block ${n}`;
  if (b.type !== spec.type) {
    return `${is} is “${blockText(b)}”, but it should be `
      + `“${spec.label}”. Put “${spec.label}” in above it, or drag the wrong block away.`;
  }
  for (const [name, [want, wantText]] of Object.entries(spec.fields || {})) {
    if (b.getFieldValue(name) !== want) {
      const field = b.getField(name);
      const shown = field && field.getText ? field.getText() : b.getFieldValue(name);
      return `${says} says “${shown}”. Click “${shown}” on the block and choose “${wantText}”.`;
    }
  }
  if (spec.number) {
    const { input, value, unit } = spec.number;
    // Named by its first word: the block's own text repeats the very number
    // being corrected ("turn ↻ clockwise 90 ° has 90°").
    const name = spec.label.split(" ")[0];
    const got = numberIn(b, input);
    if (got === null) return `Put a plain number back into the “${name}” block.`;
    if (got !== value) {
      return `The “${name}” block has ${got}${unit}. Click the ${got} and type ${value}.`;
    }
  }
  return "";
}

/** The blocks inside a loop's statement input, top to bottom. */
function insideOf(b, input) {
  const out = [];
  for (let x = b.getInputTargetBlock(input); x; x = x.getNextBlock()) out.push(x);
  return out;
}

/**
 * What is wrong with what goes *into* block `b`: the test in its slot (from
 * COND) and the blocks inside it (at FULL). A failed check, or null.
 *
 * These exist for the two mistakes a stack of moves never had: a test left
 * lying beside its slot, and a block snapped below a loop when it belongs
 * inside it. Below, it runs once after the loop instead of every time round,
 * and the loop, left empty, never looks round at all.
 */
function innerProblem(ws, b, spec, part) {
  if (spec.cond && part >= COND) {
    const { input, type, label } = spec.cond;
    const got = b.getInputTargetBlock(input);
    if (!got) {
      return { ok: false, hint: looseBlock(ws, type)
        ? `“${label}” is on the workspace but not in the slot. Drag it onto the gap at the `
          + `end of “${spec.label}” until it clicks in.`
        : "" };
    }
    if (got.type !== type) {
      return { ok: false, hint: `The slot in “${spec.label}” holds “${blockText(got)}”. `
        + `Drag that out and put “${label}” in instead.` };
    }
  }
  if (spec.body && part >= FULL) {
    const inside = insideOf(b, spec.body.input);
    const want = spec.body.blocks;
    for (let j = 0; j < want.length; j += 1) {
      const x = inside[j];
      if (!x) {
        const below = j === 0 && b.getNextBlock();
        if (below && below.type === want[j].type) {
          return { ok: false, hint: `“${blockText(below)}” is below “${spec.label}”, not `
            + "inside it. Drag it up into the gap inside the loop: only the blocks inside "
            + "are repeated." };
        }
        const loose = looseBlock(ws, want[j].type);
        return { ok: false, hint: loose
          ? `“${blockText(loose)}” is on the workspace but not snapped on. Drag it into the `
            + `gap inside “${spec.label}” until it clicks into place.`
          : "" };
      }
      const problem = problemWith(x, want[j], j + 1, `The block inside “${spec.label}”`);
      if (problem) return { ok: false, hint: problem };
    }
    if (inside.length > want.length) {
      return { ok: false, hint: `“${blockText(inside[want.length])}” is inside “${spec.label}”, `
        + "so it would run every time round the loop. Drag it out and snap it below the loop." };
    }
  }
  return null;
}

/**
 * Does the program match the first `upto + 1` blocks of `lesson`?
 *
 * Every block before `upto` is checked whole; block `upto` only as far as
 * `part` - its shell, its test, or everything inside it too.
 *
 * `hint` is empty when there is simply nothing there yet: the step's own text
 * already says what to do, and repeating it in an alarming colour before the
 * student has touched anything reads as a telling-off.
 */
function checkLesson(ws, upto, lesson = LESSON, part = FULL) {
  const chain = chainOf(ws);
  for (let i = 0; i <= upto; i += 1) {
    const spec = lesson[i];
    const b = chain[i];
    if (!b) {
      const above = i === 0 ? "🚁 when mission starts" : lesson[i - 1].label;
      const loose = looseBlock(ws, spec.type);
      return {
        ok: false,
        hint: loose
          ? `“${blockText(loose)}” is on the workspace but not snapped on. `
            + `Drag it until it clicks into place under “${above}”.`
          : "",
      };
    }
    const problem = problemWith(b, spec, i + 1);
    if (problem) return { ok: false, hint: problem };
    const inner = innerProblem(ws, b, spec, i === upto ? part : FULL);
    if (inner) return inner;
  }
  if (upto >= lesson.length - 1 && chain.length > lesson.length) {
    return { ok: false, hint: "Nothing should come after “land”. Drag the extra blocks to the trash can." };
  }
  return { ok: true, hint: "" };
}

function specState(spec, part = FULL) {
  const state = { type: spec.type };
  if (spec.fields) {
    state.fields = {};
    for (const [name, [value]] of Object.entries(spec.fields)) state.fields[name] = value;
  }
  const inputs = {};
  if (spec.number) {
    inputs[spec.number.input] =
      { shadow: { type: "math_number", fields: { NUM: spec.number.value } } };
  }
  if (spec.cond && part >= COND) inputs[spec.cond.input] = { block: { type: spec.cond.type } };
  if (spec.body && part >= FULL) {
    let inner = null;
    for (const w of [...spec.body.blocks].reverse()) {
      const s = specState(w);
      if (inner) s.next = { block: inner };
      inner = s;
    }
    inputs[spec.body.input] = { block: inner };
  }
  if (Object.keys(inputs).length) state.inputs = inputs;
  return state;
}

/** Put right a block of the right type: its dropdowns and its number. */
function repairBlock(b, spec) {
  for (const [name, [value]] of Object.entries(spec.fields || {})) {
    if (b.getFieldValue(name) !== value) b.setFieldValue(value, name);
  }
  if (spec.number && numberIn(b, spec.number.input) !== spec.number.value) {
    const { input, value } = spec.number;
    const plugged = b.getInputTargetBlock(input);
    if (plugged && !plugged.isShadow()) plugged.unplug();
    b.getInput(input).connection.setShadowState(
      { type: "math_number", fields: { NUM: value } });
  }
}

/** A loose block that can serve as `spec` just as it is, or null. */
function usableLoose(ws, spec, n) {
  const loose = looseBlock(ws, spec.type);
  return loose && !loose.getParent() && !loose.getNextBlock()
    && !problemWith(loose, spec, n) ? loose : null;
}

/** "Do it for me" for what goes into `b`, as far as `part`: see innerProblem. */
function fixInner(ws, Blockly, b, spec, part) {
  if (spec.cond && part >= COND) {
    const { input, type } = spec.cond;
    const got = b.getInputTargetBlock(input);
    if (!got || got.type !== type) {
      if (got) got.unplug();
      const loose = looseBlock(ws, type);
      const block = loose && !loose.getParent() ? loose
        : Blockly.serialization.blocks.append({ type }, ws);
      b.getInput(input).connection.connect(block.outputConnection);
    }
  }
  if (spec.body && part >= FULL) {
    const want = spec.body.blocks;
    want.forEach((w, j) => {
      const inside = insideOf(b, spec.body.input);
      const x = inside[j];
      if (x && x.type === w.type) {
        repairBlock(x, w);
        return;
      }
      // Their own block, wherever it went: below the loop is the usual place.
      const below = j === 0 && !x && b.getNextBlock();
      let block;
      if (below && below.type === w.type) {
        below.unplug(true);          // whatever was under it closes the gap
        block = below;
      } else {
        block = (!x && usableLoose(ws, w, j + 1))
          || Blockly.serialization.blocks.append(specState(w), ws);
      }
      const into = j === 0 ? b.getInput(spec.body.input).connection : inside[j - 1].nextConnection;
      into.connect(block.previousConnection);
      repairBlock(block, w);
    });
    const extra = insideOf(b, spec.body.input)[want.length];
    if (extra) extra.unplug(false);
  }
}

/**
 * "Do it for me": make the program match `lesson` up to step `upto`, and
 * block `upto` as far as `part` (see checkLesson).
 *
 * It repairs rather than rebuilds - a right block with a wrong number keeps
 * its place and gets the number - so the student sees their own attempt
 * finished, not replaced. A block that does not belong is pushed down, not
 * deleted: Blockly re-attaches whatever was below an insertion point.
 */
function fixLesson(ws, Blockly, upto, lesson = LESSON, part = FULL) {
  try {
    const start = startBlock(ws);
    if (!start) return false;
    for (let i = 0; i <= upto; i += 1) {
      const spec = lesson[i];
      const depth = i === upto ? part : FULL;
      const chain = chainOf(ws);
      let b = chain[i];
      const prev = i === 0 ? start : chain[i - 1];
      if (!b || b.type !== spec.type) {
        // The student's own loose block, if one is lying about on its own -
        // snapping theirs in teaches more than conjuring a twin beside it.
        b = (!b && usableLoose(ws, spec, i + 1))
          || Blockly.serialization.blocks.append(specState(spec, depth), ws);
        prev.nextConnection.connect(b.previousConnection);
      } else {
        repairBlock(b, spec);
      }
      fixInner(ws, Blockly, b, spec, depth);
    }
    if (upto >= lesson.length - 1) {
      const extra = chainOf(ws)[lesson.length];
      if (extra) extra.unplug(false);
    }
    return true;
  } catch (_error) {
    return false;
  }
}

// ── storage: whether it was seen, and blocks put aside ──────────────────────

/** null = readable and never seen; undefined = cannot read storage at all. */
function readFlag(storage) {
  try {
    return storage ? storage.getItem(TUTORIAL_KEY) : undefined;
  } catch (_error) {
    return undefined;
  }
}

function writeFlag(storage, value) {
  try {
    if (storage) storage.setItem(TUTORIAL_KEY, value);
  } catch (_error) {
    // It will offer itself again next time. Annoying, never broken.
  }
}

/**
 * Should the tutorial open by itself?
 *
 * Only in the simulator, only on a workspace with nothing on it, and only when
 * storage can remember the answer. Blocks already on screen mean somebody has
 * been here before. And on a machine where storage is denied, a tutorial that
 * opened itself every launch would be nagging a whole venue.
 */
function shouldAutoStart({ storage, mode, hasBlocks }) {
  if (mode !== "sim" || hasBlocks) return false;
  return readFlag(storage) === null;
}

function saveStash(storage, state) {
  try {
    if (storage) storage.setItem(STASH_KEY, JSON.stringify({ format: STASH_FORMAT, workspace: state }));
    return true;
  } catch (_error) {
    return false;   // still held in memory for this page's lifetime
  }
}

function readStash(storage) {
  try {
    const raw = storage && storage.getItem(STASH_KEY);
    if (!raw) return null;
    const envelope = JSON.parse(raw);
    if (envelope && envelope.format === STASH_FORMAT && envelope.workspace
        && typeof envelope.workspace === "object") return envelope.workspace;
  } catch (_error) {
    // unreadable: dropped below
  }
  dropStash(storage);
  return null;
}

function dropStash(storage) {
  try {
    if (storage) storage.removeItem(STASH_KEY);
  } catch (_error) {
    // nothing left to try
  }
}

// ── text ────────────────────────────────────────────────────────────────────

/** Blockly's own block colour for a hue: HSV with its default S 0.45, V 0.65. */
function hueToCss(hue) {
  const s = 0.45, v = 0.65;
  const f = (n) => {
    const k = (n + hue / 60) % 6;
    return Math.round(255 * (v - v * s * Math.max(0, Math.min(k, 4 - k, 1))));
  };
  return `rgb(${f(5)}, ${f(3)}, ${f(1)})`;
}

/**
 * Lay out a line of step text. `**bold**` is bold, and `[[take off:flight]]`
 * is a block drawn in its category's colour - so "find the blue block" is a
 * thing the student can actually do. Built from text nodes, never innerHTML.
 */
function rich(doc, text) {
  const frag = doc.createDocumentFragment();
  for (const part of String(text).split(/(\*\*.+?\*\*|\[\[.+?\]\])/)) {
    if (!part) continue;
    if (part.startsWith("**")) {
      const strong = doc.createElement("strong");
      strong.textContent = part.slice(2, -2);
      frag.append(strong);
    } else if (part.startsWith("[[")) {
      const [label, cat] = part.slice(2, -2).split(":");
      const chip = doc.createElement("span");
      chip.className = "tut-block";
      chip.textContent = label;
      if (HUES[cat] !== undefined) chip.style.background = hueToCss(HUES[cat]);
      frag.append(chip);
    } else {
      frag.append(doc.createTextNode(part));
    }
  }
  return frag;
}

// ── the tour itself ─────────────────────────────────────────────────────────

function install(env) {
  const { window: win, document: doc, workspace: ws, Blockly, storage, bus } = env;
  const log = env.log || (() => {});
  // The socket, for the one message the tutorial sends itself: the arena (see
  // useLessonArena). Returns whether it went, like window.COMP1_SEND.
  const send = env.send || (() => false);
  const $ = (id) => doc.getElementById(id);

  const ui = buildUi();
  const st = {
    active: false, index: 0, steps: [],
    mode: null, running: false, autoChecked: false,
    stash: null,                 // blocks put aside for the lesson
    flight: null,                // "flying" | "done" | "failed" while on a run step
    crashed: false,
    credited: false,             // the scorer counted a find during this flight
    camAsideNeeded: false,       // decided once, as the camera lesson starts
    sceneries: null,             // the server's arena list and current pick, as last sent
    arenaPending: false,         // waiting for the arena list to move to the lesson's
    arenaToRestore: null,        // where the student was before the tutorial moved them
    okTimer: 0, hintTimer: 0, pollTimer: 0, frame: 0,
  };

  // A tab closed mid-lesson left blocks put aside: they come straight back.
  const leftover = readStash(storage);
  if (leftover) {
    if (loadState(leftover)) log("↺ Put back the blocks you had before the tutorial.");
    dropStash(storage);
  }
  const hadBlocks = hasBlocks();

  function hasBlocks() {
    try {
      return ws.getAllBlocks(false).some((b) => b.type !== "start");
    } catch (_error) {
      return false;
    }
  }

  function loadState(state) {
    try {
      Blockly.serialization.workspaces.load(state, ws);
      // Blocks put aside before start was undeletable may hold a copy of it.
      if (win.COMP1 && win.COMP1.removeExtraStarts) win.COMP1.removeExtraStarts(ws);
      if (!startBlock(ws)) seedStart();
      return true;
    } catch (_error) {
      return false;
    }
  }

  function seedStart() {
    const start = ws.newBlock("start");
    if (start.initSvg) { start.initSvg(); start.render(); }
    // The same place app.js seeds it, clear of every open drawer (blocks.js).
    const pos = (win.COMP1 && win.COMP1.START_POSITION) || { x: 340, y: 30 };
    start.moveBy(pos.x, pos.y);
  }

  function putAside() {
    try {
      const state = Blockly.serialization.workspaces.save(ws);
      st.stash = state;
      saveStash(storage, state);
      ws.clear();
      seedStart();
      return true;
    } catch (_error) {
      return false;
    }
  }

  function giveBack() {
    if (!st.stash) return;
    if (loadState(st.stash)) log("↺ Your earlier blocks are back.");
    else log("⚠ Could not bring back your earlier blocks.");
    st.stash = null;
    dropStash(storage);
  }

  function keepLesson() {
    st.stash = null;
    dropStash(storage);
  }

  // --- DOM ----------------------------------------------------------------

  function buildUi() {
    const shade = doc.createElement("div");
    shade.id = "tut-shade";
    shade.hidden = true;
    const spot = doc.createElement("div");
    spot.id = "tut-spot";
    shade.append(spot);

    const card = doc.createElement("section");
    card.id = "tut-card";
    card.hidden = true;
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-labelledby", "tut-title");
    const phase = doc.createElement("p");
    phase.className = "tut-phase";
    const meter = doc.createElement("div");
    meter.className = "tut-meter";
    const fill = doc.createElement("span");
    meter.append(fill);
    const title = doc.createElement("h2");
    title.id = "tut-title";
    const body = doc.createElement("div");
    body.className = "tut-body";
    const status = doc.createElement("p");
    status.className = "tut-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    const actions = doc.createElement("div");
    actions.className = "tut-actions";
    card.append(phase, meter, title, body, status, actions);
    doc.body.append(shade, card);
    return { shade, spot, card, phase, fill, title, body, status, actions };
  }

  function button(label, onClick, kind) {
    const b = doc.createElement("button");
    b.type = "button";
    b.textContent = label;
    if (kind) b.className = kind;
    b.onclick = onClick;
    return b;
  }

  // --- targets ------------------------------------------------------------

  function toolboxItem(name) {
    try {
      const toolbox = ws.getToolbox();
      return toolbox.getToolboxItems().find((item) => item.getName && item.getName() === name);
    } catch (_error) {
      return null;
    }
  }

  const el = (selector) => () => doc.querySelector(selector);
  // Several regions lit as one: a step about "the camera and what it detects"
  // has to show both, or the card talks about numbers the student cannot see.
  const els = (...selectors) => () => selectors.map((s) => doc.querySelector(s)).filter(Boolean);
  const visible = (node) => {
    if (!node || node.hidden) return false;
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  /** The smallest rectangle around every visible node, or null. */
  function unionRect(nodes) {
    const rects = [].concat(nodes).filter(visible).map((n) => n.getBoundingClientRect());
    if (!rects.length) return null;
    return {
      left: Math.min(...rects.map((r) => r.left)), top: Math.min(...rects.map((r) => r.top)),
      right: Math.max(...rects.map((r) => r.right)),
      bottom: Math.max(...rects.map((r) => r.bottom)),
    };
  }

  function showDebugView(view) {
    const panel = $("debug-panel");
    if (panel && !panel.classList.contains("open")) $("debug-toggle").click();
    const tab = doc.querySelector(`[data-debug-view="${view}"]`);
    if (tab) tab.click();
  }

  // --- steps --------------------------------------------------------------

  const P = (...lines) => lines;

  function buildStep(upto, step) {
    const above = upto === 0 ? "🚁 when mission starts" : LESSON[upto - 1].label;
    return {
      phase: "build", target: el("#blockly"), place: "right", kind: "check",
      check: () => checkLesson(ws, upto),
      fix: () => fixLesson(ws, Blockly, upto),
      need: `Snap “${LESSON[upto].label}” under “${above}” first, or press Do it for me.`,
      ...step,
    };
  }

  // The tour was twelve cards long, and the first student to try it called it
  // "click, click, click" and skimmed to the blocks. Seven now, each covering
  // a region rather than a widget, and every card can jump straight to building.
  const STEPS = [
    { id: "welcome", center: true,
      title: "Welcome to Drone Coder 👋",
      body: P(
        "**Your mission:** program a drone to find red targets (the “fires”) and signal each "
          + "one it finds. You do it by snapping blocks together. No typing needed.",
        // Both testers took far longer than the 3 minutes this used to promise.
        "First a quick look around the screen, then you'll build and fly your first program. "
          + "About **5 to 10 minutes**.",
        "This is the **simulator**, a practice drone on the screen. Nothing real can crash, "
          + "so try anything you like.",
      ),
      actions: () => [
        button("Skip, I've used this before", () => finish("skipped"), "link"),
        // For the student who has done the basics and came back for this.
        button("Camera lesson ›", startCamera, "link"),
        "gap",
        button("Let's go", next, "primary"),
      ] },

    { id: "workspace", phase: "tour", target: el("#blockly"),
      spotlight: els("#blockly", "#block-help"), place: "right",
      title: "Your workspace and the block drawers",
      body: P(
        "You build programs here. Open a drawer on the left, drag a block out, and snap blocks "
          + "together like LEGO. The drone does them **from top to bottom, one at a time**.",
        "The colour tells you the drawer: [[Flight:flight]] moves the drone, [[Sensing:sense]] "
          + "reads the camera, [[Control:flow]] repeats and decides, [[Mission:mission]] is for "
          + "the competition.",
        "Not sure what a block does? Click it, and the bar under the workspace explains it.",
      ) },

    { id: "start", phase: "tour", place: "right",
      target: () => {
        const start = startBlock(ws);
        const svg = start && start.getSvgRoot && start.getSvgRoot();
        return svg || doc.querySelector("#blockly");
      },
      title: "Every program starts here",
      body: P(
        "Only blocks **snapped underneath** [[🚁 when mission starts:mission]] will run.",
        "A block left floating on its own is ignored. That's the number one reason a "
          + "program “does nothing”.",
      ) },

    { id: "stage", phase: "tour", target: el("#stage-bar"), place: "left", spotlight: el("#stage"),
      title: "The 3D view",
      body: P(
        "This is your drone in its arena. Pick a camera at the top:",
        ["**Follow** chases the drone", "**Orbit**: drag to look around, scroll to zoom",
          "**Map** shows the whole room from above", "**Trail** draws the path you flew"],
      ) },

    { id: "camera", phase: "tour", target: els("#camera-window", "#telemetry"), place: "left",
      title: "What the drone sees",
      body: P(
        "The drone's own camera, and under it what the camera detects right now: is a target "
          + "in view, how far away, and which way (− left, + right).",
        "**Your program only knows what's in this picture.** No map, no GPS: to find a target "
          + "it has to turn and look, just like you would. [[Sensing:sense]] blocks read these "
          + "numbers.",
        "Colour / AI model is how the drone spots red. Leave it as it is.",
      ) },

    { id: "arena", phase: "tour", target: el("#arena-panel"), place: "left",
      skip: () => !visible($("arena-panel")),
      title: "The floor plan",
      body: P(
        "The room seen from above. **Red dots are the targets** your mission has to find. Grey "
          + "dots are decoys. **S** is the start pad, and the orange arrow is your "
          + "drone.",
        "**Shuffle** makes a new random layout. **Edit** lets you click to add or remove targets.",
      ) },

    { id: "console", phase: "tour", target: el("#console"), place: "left",
      title: "Messages for you",
      body: P(
        "Warnings, results and drone messages show up here.",
        "If a program didn't do what you expected, **look here first**. Many messages come "
          + "with a 💡 How to fix tip.",
      ) },

    { id: "controls", phase: "tour", target: el("header"), place: "bottom",
      title: "The top bar",
      body: P(
        [
          "**Run** flies your program. **Stop** ends it and lands the drone safely. **Reset** "
            + "puts it back on the start pad.",
          "Green **connected** means everything is working. Next to it: targets found, and "
            + "the battery.",
          "**Tutorial** opens this guide again.",
        ],
        "**EMERGENCY STOP is only for real emergencies.** On a real drone it cuts the motors and "
          + "the drone falls out of the air. For everything else, press Stop.",
      ) },

    { id: "aside", phase: "build", center: true,
      // Decided once, as the tour ends - not re-asked on every render. Asked
      // live, a block dragged out mid-lesson made this step appear in the count
      // behind the student, and "2 of 8" became "4 of 9" with nothing between.
      skip: () => !st.asideNeeded,
      title: "You already have blocks here",
      body: P(
        "To build the lesson program, the workspace needs to be empty.",
        "Your blocks will be **put aside, not deleted**. At the end you choose whether to get "
          + "them back.",
      ),
      actions: () => [
        button("Skip the building part", () => goTo("final"), "link"),
        "gap",
        button("Put my blocks aside", () => { if (putAside()) next(); }, "primary"),
      ] },

    // Lit on the Flight row, but the card sits beside the whole workspace: next
    // to the row it would land on the start block the next step drops onto.
    { id: "drawer", phase: "build", kind: "check", place: "right", target: el("#blockly"),
      spotlight: () => {
        const item = toolboxItem("Flight");
        return (item && item.getDiv && item.getDiv()) || doc.querySelector(".blocklyToolbox");
      },
      title: "Let's build! Open the Flight drawer",
      body: P(
        "Your first program will fly the drone out, turn it around, and bring it home.",
        "Start by clicking **Flight** on the left.",
      ),
      need: "Click Flight on the left first, or press Do it for me.",
      okText: "That's the Flight drawer. These blocks move the drone.",
      trustFix: true,
      check: () => {
        try {
          const item = ws.getToolbox().getSelectedItem();
          if (item && item.getName && item.getName() === "Flight") return { ok: true, hint: "" };
        } catch (_error) {
          // fall through: already past this step counts too
        }
        return { ok: checkLesson(ws, 0).ok, hint: "" };
      },
      fix: () => {
        try {
          ws.getToolbox().setSelectedItem(toolboxItem("Flight"));
        } catch (_error) {
          // the next step can still be done by hand
        }
      } },

    buildStep(0, { id: "takeoff",
      title: "Snap on “take off”",
      body: P(
        "Drag [[take off:flight]] out of the drawer and drop it just under "
          + "[[🚁 when mission starts:mission]]. When it's close, it **snaps into place**.",
        "Wrong block? Drag it onto the trash can, or click it and press Delete. Ctrl+Z undoes.",
      ),
      okText: "Snapped in! The drone will take off first." }),

    buildStep(1, { id: "forward",
      title: "Fly forward",
      body: P(
        "Open **Flight** again and snap [[fly forward 50 cm:flight]] under [[take off:flight]].",
        "50 cm is about the length of your arm. The room is 4 metres across.",
      ),
      okText: "Nice. Take off, then fly forward." }),

    buildStep(2, { id: "turn",
      title: "Turn around",
      body: P(
        "Add [[turn ↻ clockwise 90°:flight]] next.",
        "Then **click the 90 and type 180**. Half a full circle turns the drone to face back "
          + "where it came from.",
      ),
      okText: "180° means it's now facing home." }),

    buildStep(3, { id: "back",
      title: "Fly back home",
      body: P(
        "Add another [[fly forward 50 cm:flight]] under the turn. It's facing home now, so "
          + "**forward** takes it back.",
        "Shortcut: right-click your first fly block and choose **Duplicate**.",
      ),
      okText: "Almost there." }),

    buildStep(4, { id: "land",
      title: "Land",
      body: P("Finish with [[land:flight]]. Every flight has to end with a landing."),
      okText: "Your program is complete!" }),

    // Stays put once the drone lands. It used to move on by itself after a
    // second and a half, and the next card covered the console just as the
    // result arrived: the student reached "You flew your first program!"
    // without having seen it fly.
    //
    // The card sits in one place throughout, just above the code inspector at
    // the bottom left: under the Run button it covered half the program the
    // student wanted to check before flying, and beside the 3D view it covered
    // the blocks lighting up. Only the spotlight moves.
    { id: "run", phase: "build", kind: "run",
      lesson: () => checkLesson(ws, LESSON.length - 1),
      enter: () => showDebugView("trace"),
      target: el("#debug-panel"), place: "top",
      spotlight: () => (st.flight === "done" ? $("debug-panel")
        : st.running ? $("stage") : $("run")),
      title: () => (st.flight === "done" ? "It flew! 🎉"
        : st.running ? "It's flying! 🚁" : "Press Run"),
      body: () => (st.flight === "done"
        ? P("Your drone took off, flew out, turned around and landed back on its start pad.",
          "**Live execution**, under your blocks, lists every command the drone just ran, in "
            + "order. Want to watch it again? Press **Run** again.")
        : st.running
          ? P("Watch the 3D view and the camera.",
            "**The block that's running right now lights up** in your workspace, and Live "
              + "execution lists each command as it happens.")
          : P("Your program: take off → fly out → turn around → fly back → land.",
            "Press the orange **Run** button at the top and watch the 3D view.")),
      okText: "Landed back home. Your program worked!" },

    { id: "python", phase: "build", target: el("#debug-panel"), place: "top",
      enter: () => showDebugView("python"),
      title: "Your blocks are real code",
      body: P(
        "This is the program you just flew, written in **Python**. Each block became a line "
          + "like drone.takeoff(). Scroll down a little to see them.",
        // The second tester read the ids as garbage, and as the "real code".
        "The **# block** lines with jumbled letters are labels the app uses to light up the "
          + "running block. You can ignore them.",
        "You never have to type code here. But this is what real drone programmers write.",
      ) },

    { id: "final", center: true,
      title: () => (st.flight === "done" ? "🎉 You flew your first program!" : "That's the tour!"),
      body: () => [
        ...(st.flight === "done"
          ? ["Your drone took off, flew out, turned around and came home. That's a real "
            + "program: a list of steps a computer follows in order."]
          : []),
        "**Things to remember**",
        [
          "Only blocks snapped under [[🚁 when mission starts:mission]] run.",
          "Stuck? Read the console. **Stop** lands the drone, **Reset** puts it back on the start pad.",
          "Your blocks are saved automatically on this computer.",
        ],
        // The challenge that used to be listed here is the camera lesson now:
        // a list of block names never taught a test in a slot or a block
        // inside a loop. Finish still leaves the list in the console.
        "**Next: use the camera 📷** (optional, about 5 minutes)",
        "So far the drone flies the same moves wherever the targets are. Next you'll build a "
          + "program that **looks**: it turns until it sees a target, flies to it and signals.",
        ...(st.stash ? ["**Your earlier blocks are put aside.** Do you want them back?"] : []),
      ],
      // The camera lesson clears this program away, and leaves the choice
      // about blocks put aside to its own last card.
      actions: () => (st.stash
        ? [
          button("Keep this program", () => { keepLesson(); finish("done"); }),
          button("Bring back my earlier blocks", () => { giveBack(); finish("done"); }),
          "gap",
          button("Camera lesson ›", startCamera, "primary"),
        ]
        : [
          button("Finish", () => finish("done")),
          "gap",
          button("Camera lesson ›", startCamera, "primary"),
        ]) },

    // ── the camera lesson ───────────────────────────────────────────────
    // Optional, and never reached by Next: only by "Camera lesson ›" on the
    // welcome card or the card above. See VISION_LESSON for why these blocks.

    { id: "cam-intro", phase: "camera", chapter: "camera",
      target: els("#camera-window", "#telemetry"), place: "left",
      title: "Use the camera 📷",
      body: P(
        "A program of fixed moves only works in one room. Move the targets, and it flies "
          + "straight past them. A program that **looks** works anywhere.",
        "[[Sensing:sense]] blocks ask the camera a question. [[target seen?:sense]] is true "
          + "while a red target is in this picture. The readings under it are what the other "
          + "Sensing blocks give you.",
        "You'll build: take off, **turn until a target is seen**, fly to it, signal, land.",
      ) },

    { id: "cam-aside", phase: "camera", chapter: "camera", center: true,
      // Decided once, as the lesson starts: see startCamera.
      skip: () => !st.camAsideNeeded,
      title: "You already have blocks here",
      body: P(
        "To build the camera program, the workspace needs to be empty.",
        "Your blocks will be **put aside, not deleted**. At the end you choose whether to get "
          + "them back.",
      ),
      actions: () => [
        button("Not now", () => finish("skipped"), "link"),
        "gap",
        button("Put my blocks aside", () => { if (putAside()) next(); }, "primary"),
      ] },

    cameraStep(0, FULL, { id: "cam-takeoff",
      title: "Start with “take off”",
      body: P("Open **Flight** and snap [[take off:flight]] under "
        + "[[🚁 when mission starts:mission]]. You know this one."),
      need: "Snap “take off” under “🚁 when mission starts” first, or press Do it for me.",
      okText: "Up it goes." }),

    cameraStep(1, SHELL, { id: "cam-loop",
      title: "A loop that waits",
      body: P(
        "Open **Control** and snap [[keep doing until:flow]] under [[take off:flight]].",
        "It repeats the blocks **inside** it, over and over, until the test at its end is true.",
      ),
      need: "Snap “keep doing until” under “take off” first, or press Do it for me.",
      okText: "That's the loop. Now it needs a test." }),

    cameraStep(1, COND, { id: "cam-seen",
      title: "Ask the camera",
      body: P(
        "Open **Sensing** and drag [[target seen?:sense]] onto the gap at the end of "
          + "[[keep doing until:flow]]. It clicks in like a puzzle piece.",
        "Now it reads: keep doing this **until a target is seen**.",
      ),
      need: "Put “target seen?” into the gap at the end of “keep doing until” first, "
        + "or press Do it for me.",
      okText: "The loop stops the moment the camera sees red." }),

    cameraStep(1, FULL, { id: "cam-turn",
      title: "Look around",
      body: P(
        "Drag [[turn ↻ clockwise 90°:flight]] **inside** [[keep doing until:flow]], into the "
          + "gap in its middle. Then **click the 90 and type 30**.",
        "Each time round, the drone turns a little and the camera looks again. Small turns, so "
          + "a target can't slip past between two looks.",
      ),
      need: "Put “turn ↻ clockwise 30°” inside “keep doing until” first, or press Do it for me.",
      okText: "Turn and look, turn and look, until it sees one." }),

    cameraStep(2, FULL, { id: "cam-approach",
      title: "Fly to it",
      body: P(
        "Open **Mission** and snap [[approach target and stop:mission]] **below** the loop, "
          + "not inside it.",
        "It steers by the camera's distance and direction readings, and stops a safe distance "
          + "short, so the drone never flies into the sign.",
      ),
      need: "Snap “approach target and stop” below the loop first, or press Do it for me.",
      okText: "Once it sees a target, it flies over to it." }),

    cameraStep(3, FULL, { id: "cam-signal",
      title: "Signal the find",
      body: P(
        "Add [[signal target found 🎉 by flipping:mission]] under it.",
        "A find only counts if the drone signals it, close to the target.",
      ),
      need: "Snap “signal target found” under “approach target and stop” first, "
        + "or press Do it for me.",
      okText: "That's what scores." }),

    cameraStep(4, FULL, { id: "cam-land",
      title: "Land",
      body: P("Finish with [[land:flight]]."),
      need: "Snap “land” at the bottom first, or press Do it for me.",
      okText: "Your camera program is complete!" }),

    // The same run machinery as the first flight, and the card in the same
    // place. Lit on the camera while it flies: watching "target seen?" come
    // true is the whole lesson. A flight only counts if the scorer credited
    // the signal, because the signal block runs whether the approach got
    // there or not.
    { id: "cam-run", phase: "camera", chapter: "camera", kind: "run", needsCredit: true,
      lesson: () => checkLesson(ws, VISION_LESSON.length - 1, VISION_LESSON),
      enter: () => showDebugView("trace"),
      target: el("#debug-panel"), place: "top",
      spotlight: () => (st.running || st.flight === "done"
        ? els("#camera-window", "#telemetry")() : $("run")),
      title: () => (st.flight === "done" ? "It found one! 🎉"
        : st.running ? "Watch the camera 👀" : "Press Run"),
      body: () => (st.flight === "done"
        ? P("It turned until “target seen?” came true, flew to the target and signalled.",
          "Press **Reset** and **Run** again: wherever the drone ends up facing, it looks "
            + "until it finds one. That's what a program that looks can do.")
        : st.running
          ? P("The drone turns, a bit at a time. Watch the reading under the camera: the "
            + "moment it sees a target, the loop ends and the drone flies to it.")
          : P("Your program: take off → turn until a target is seen → fly to it → signal → land.",
            "Press the orange **Run** button at the top, and watch the camera.")),
      okText: "Found and signalled. Your program used the camera!" },

    { id: "cam-final", chapter: "camera", center: true,
      title: () => (st.flight === "done" ? "📷 Your drone used its camera!"
        : "That's the camera lesson!"),
      body: () => [
        "**Things to remember**",
        [
          "[[target seen?:sense]] and the other Sensing blocks are questions for the camera. "
            + "They go into the slots of other blocks.",
          "Blocks **inside** a loop repeat. Blocks **below** it run once, after it ends.",
          "Click any Sensing block to read what it measures: distance, direction, left or right.",
        ],
        // Deliberately a question, not a recipe. Wrapping the search in
        // "repeat 3" mostly re-finds the target it is still looking at, and the
        // obvious fix (turn away first) found all three only half the time in
        // the simulator - so there is no honest one-liner to hand out here.
        "**Next challenge: find all three 🔥** After signalling, the drone is still looking at "
          + "the target it just found. How could you make it look somewhere new before it "
          + "searches again? (This stays in the console after you close this.)",
        ...(st.stash ? ["**Your earlier blocks are put aside.** Do you want them back?"] : []),
      ],
      actions: () => (st.stash
        ? [
          button("Keep this program", () => { keepLesson(); finish("done"); }),
          "gap",
          button("Bring back my earlier blocks", () => { giveBack(); finish("done"); }, "primary"),
        ]
        : ["gap", button("Finish", () => finish("done"), "primary")]) },
  ];

  function cameraStep(upto, part, step) {
    return {
      phase: "camera", chapter: "camera", target: el("#blockly"), place: "right", kind: "check",
      check: () => checkLesson(ws, upto, VISION_LESSON, part),
      fix: () => fixLesson(ws, Blockly, upto, VISION_LESSON, part),
      ...step,
    };
  }

  /**
   * Into the camera lesson, from the welcome card or the first lesson's end.
   *
   * The workspace has to be empty, and what is on it decides how. The first
   * lesson's own program goes quietly: it is the tutorial's, and the student
   * has just been shown how to rebuild it. Blocks of their own are put aside
   * like the first lesson does, on a card that asks first - unless some are
   * already put aside, in which case what is on screen is the tutorial's and
   * a second put-aside would overwrite their real work.
   */
  function startCamera() {
    let onlyLesson = false;
    try {
      onlyLesson = ws.getTopBlocks(false).length === 1
        && checkLesson(ws, LESSON.length - 1).ok;
    } catch (_error) {
      // not provably the lesson: treated as theirs
    }
    st.camAsideNeeded = hasBlocks() && !onlyLesson && !st.stash;
    if (hasBlocks() && !st.camAsideNeeded) {
      try {
        ws.clear();
        seedStart();
      } catch (_error) {
        // the first build step says what is in the way
      }
    }
    st.flight = null;
    goTo("cam-intro");
  }

  // --- rendering ----------------------------------------------------------

  const value = (v) => (typeof v === "function" ? v() : v);

  function render() {
    const step = st.steps[st.index];
    if (!step) return;
    const { card, phase, fill, title, body, status, actions } = ui;

    const ofPhase = st.steps.filter((s) => s.phase === step.phase && !skipped(s));
    const at = ofPhase.indexOf(step) + 1;
    phase.textContent = step.phase === "tour" ? `Quick tour · ${at} of ${ofPhase.length}`
      : step.phase === "build" ? `Build & fly · ${at} of ${ofPhase.length}`
        : step.phase === "camera" ? `Camera lesson · ${at} of ${ofPhase.length}` : "";
    // The bar fills per chapter: the camera lesson is optional, so the first
    // one must not end at half full.
    const counted = st.steps.filter((s) => !skipped(s) && chapterOf(s) === chapterOf(step));
    fill.style.width = `${Math.round((100 * (counted.indexOf(step) + 1)) / counted.length)}%`;

    title.textContent = value(step.title);
    body.replaceChildren();
    for (const line of value(step.body) || []) {
      if (Array.isArray(line)) {
        const ul = doc.createElement("ul");
        for (const item of line) {
          const li = doc.createElement("li");
          li.append(rich(doc, item));
          ul.append(li);
        }
        body.append(ul);
      } else {
        const p = doc.createElement("p");
        p.append(rich(doc, line));
        body.append(p);
      }
    }

    actions.replaceChildren();
    const list = step.actions ? step.actions() : standardActions(step);
    for (const item of list) {
      if (item === "gap") {
        const gap = doc.createElement("span");
        gap.className = "tut-gap";
        actions.append(gap);
      } else actions.append(item);
    }

    card.classList.toggle("center", Boolean(step.center));
    ui.shade.classList.toggle("modal", Boolean(step.center));
    ui.spot.hidden = Boolean(step.center);
    ui.spot.classList.toggle("live", step.kind === "check" || step.kind === "run");
    layout();
  }

  function standardActions(step) {
    const out = [button(chapterOf(step) === "camera" ? "Close" : "Skip tutorial",
      () => finish("skipped"), "link")];
    if (step.phase === "tour") out.push(button("Jump to building ›", jumpToBuild, "link"));
    out.push("gap");
    if (previousIndex() >= 0) out.push(button("Back", back));
    if (step.kind === "check") {
      out.push(button("Do it for me", () => {
        step.fix();
        st.fixed = true;
        evaluate(true);
      }));
      // Not `disabled`: a disabled button swallows the click, and a student
      // who clicked it was left with nothing happening and nothing said. It
      // looks unavailable, and pressing it says what is still to do.
      const n = button("Next", () => {
        const result = resultOf(step);
        if (result.ok) next();
        else nudge(`Not yet: ${result.hint || step.need}`);
      }, "primary waiting");
      n.setAttribute("aria-disabled", "true");
      n.dataset.role = "next";
      out.push(n);
    } else if (step.kind !== "run" || st.flight === "done") {
      out.push(button("Next", next, "primary"));
    }
    return out;
  }

  function setStatus(text, ok) {
    ui.status.textContent = text || "";
    ui.status.classList.toggle("ok", Boolean(ok));
  }

  /**
   * An early Next, answered. Kept on screen until the step changes - the poll
   * used to wipe it within half a second, so the tester's click looked ignored
   * - and the line gives a small shake, because the same words already showing
   * did not read as a reply to the click either.
   */
  function nudge(text) {
    cancelHint();
    st.nudged = true;
    setStatus(text);
    ui.status.classList.remove("nudge");
    void ui.status.offsetWidth;          // restart the animation on a second click
    ui.status.classList.add("nudge");
  }

  // --- placement ----------------------------------------------------------

  function layout() {
    const step = st.steps[st.index];
    if (!st.active || !step) return;
    const { card, spot } = ui;
    if (step.center) {
      card.style.left = card.style.top = "";
      return;
    }
    // Both may be several regions; the card sits beside the whole of `target`,
    // so it never covers part of what it is describing.
    const target = value(step.target);
    const r = target && unionRect(target);
    const s = unionRect(step.spotlight ? step.spotlight() : target);
    const W = win.innerWidth, H = win.innerHeight, gap = 14, pad = 6, edge = 8;
    if (!r || !s) {
      // Nothing to point at (a panel hidden in this mode): centre the card.
      spot.hidden = true;
      card.classList.add("center");
      return;
    }
    card.classList.remove("center");
    spot.hidden = false;
    const x0 = Math.max(s.left - pad, 2), y0 = Math.max(s.top - pad, 2);
    spot.style.left = `${x0}px`;
    spot.style.top = `${y0}px`;
    spot.style.width = `${Math.min(s.right + pad, W - 2) - x0}px`;
    spot.style.height = `${Math.min(s.bottom + pad, H - 2) - y0}px`;

    const w = card.offsetWidth || 392, h = card.offsetHeight || 220;
    const clampX = (x) => Math.min(Math.max(x, edge), Math.max(W - w - edge, edge));
    const clampY = (y) => Math.min(Math.max(y, edge), Math.max(H - h - edge, edge));
    const options = {
      right: () => (r.right + gap + w <= W - edge ? [r.right + gap, clampY(r.top)] : null),
      left: () => (r.left - gap - w >= edge ? [r.left - gap - w, clampY(r.top)] : null),
      bottom: () => (r.bottom + gap + h <= H - edge ? [clampX(r.left), r.bottom + gap] : null),
      top: () => (r.top - gap - h >= edge ? [clampX(r.left), r.top - gap - h] : null),
    };
    const first = value(step.place) || "right";
    let pos = null;
    for (const side of [first, "right", "left", "bottom", "top"]) {
      pos = options[side]();
      if (pos) break;
    }
    // Nowhere outside it fits (the workspace on a narrow screen): sit inside
    // its top-right corner, clear of the drawers and the start block.
    if (!pos) pos = [clampX(Math.min(r.right, W) - w - 16), clampY(Math.max(r.top, 0) + 16)];
    card.style.left = `${pos[0]}px`;
    card.style.top = `${pos[1]}px`;
  }

  function loop() {
    if (!st.active) return;
    layout();
    st.frame = win.requestAnimationFrame(loop);
  }

  // --- checking -----------------------------------------------------------

  function dragging() {
    try {
      return Boolean(ws.isDragging && ws.isDragging());
    } catch (_error) {
      return false;
    }
  }

  function typing() {
    try {
      return Boolean(Blockly.WidgetDiv && Blockly.WidgetDiv.isVisible());
    } catch (_error) {
      return false;
    }
  }

  /**
   * Re-check the current build step. Success shows at once and moves on by
   * itself, even with the number editor still open - Blockly takes the value
   * as it is typed, and a student who has typed 180 has done what was asked.
   * A hint waits until the editor is closed and the student has stopped, so
   * typing 1-8-0 does not flash "has 1°" and "has 18°" on the way there.
   */
  function evaluate(now) {
    const step = st.steps[st.index];
    if (!st.active || !step || step.kind !== "check") return;
    if (!now && dragging()) return;    // mid-drag, the program is half-detached
    const quiet = !now && typing();
    const result = resultOf(step);
    const nextBtn = ui.actions.querySelector('[data-role="next"]');
    if (nextBtn) {
      nextBtn.classList.toggle("waiting", !result.ok);
      nextBtn.setAttribute("aria-disabled", String(!result.ok));
    }
    if (result.ok) {
      cancelHint();
      st.nudged = false;
      setStatus(`✓ ${step.okText || "Done!"}`, true);
      if (!st.okTimer) st.okTimer = win.setTimeout(() => { st.okTimer = 0; next(); }, 1100);
      return;
    }
    win.clearTimeout(st.okTimer);
    st.okTimer = 0;
    if (quiet) return;
    if (!result.hint) {
      cancelHint();
      if (!st.nudged) setStatus("");           // an answer to Next stays put
      return;
    }
    if (ui.status.textContent === result.hint) return;          // already showing
    if (st.nudged && ui.status.textContent.endsWith(result.hint)) return;
    st.nudged = false;
    if (now) { cancelHint(); setStatus(result.hint); return; }
    // Already on its way. Restarting the wait here would mean the poll, which
    // runs more often than the wait is long, could put a hint off for ever.
    if (st.pendingHint === result.hint) return;
    cancelHint();
    st.pendingHint = result.hint;
    st.hintTimer = win.setTimeout(() => {
      st.pendingHint = null;
      setStatus(result.hint);
    }, 700);
  }

  // A step that only asks for a click outside the workspace cannot always be
  // done for the student (a toolbox that will not select), and "Do it for
  // me" must never leave anyone stuck - so for those, pressing it is enough.
  function resultOf(step) {
    return step.trustFix && st.fixed ? { ok: true, hint: "" } : step.check();
  }

  function cancelHint() {
    win.clearTimeout(st.hintTimer);
    st.pendingHint = null;
  }

  if (ws && ws.addChangeListener) ws.addChangeListener(() => evaluate(false));

  // --- navigation ---------------------------------------------------------

  function skipped(step) {
    try {
      return Boolean(step.skip && step.skip());
    } catch (_error) {
      return false;
    }
  }

  const chapterOf = (step) => step.chapter || "basics";

  /** The step Back goes to: never a modal card, and never out of the chapter. */
  function previousIndex() {
    const here = st.steps[st.index];
    for (let i = st.index - 1; i >= 0; i -= 1) {
      if (here && chapterOf(st.steps[i]) !== chapterOf(here)) return -1;
      if (!skipped(st.steps[i]) && !st.steps[i].center) return i;
    }
    return -1;
  }

  function enter(index) {
    win.clearTimeout(st.okTimer);
    win.clearTimeout(st.hintTimer);
    win.clearInterval(st.pollTimer);
    st.okTimer = 0;
    st.index = index;
    st.fixed = false;
    st.pendingHint = null;
    st.nudged = false;
    const step = st.steps[index];
    // Until building begins, keep asking whether there is anything to put
    // aside; from then on the answer is fixed (see the "aside" step). The
    // camera lesson decides its own once, in startCamera.
    if (step.phase !== "build" && chapterOf(step) !== "camera") st.asideNeeded = hasBlocks();
    setStatus("");
    if (step.kind === "run") {
      st.flight = null;
      st.crashed = false;
      st.credited = false;
    }
    try {
      if (step.enter) step.enter();
    } catch (_error) {
      // a step that cannot set itself up still explains itself
    }
    render();
    if (step.kind === "check") {
      evaluate(true);
      // Toolbox clicks are not workspace changes, so they need polling.
      st.pollTimer = win.setInterval(() => evaluate(false), 400);
    } else if (step.kind === "run") {
      runStatus();
    }
    // Keyboard users land on the card. Not on build steps: Blockly needs the focus.
    if (step.kind !== "check" && step.kind !== "run") {
      const primary = ui.actions.querySelector(".primary");
      if (primary) primary.focus({ preventScroll: true });
    }
  }

  function next() {
    for (let i = st.index + 1; i < st.steps.length; i += 1) {
      if (!skipped(st.steps[i])) { enter(i); return; }
    }
    finish("done");
  }

  function back() {
    const i = previousIndex();
    if (i >= 0) enter(i);
  }

  function goTo(id) {
    const i = st.steps.findIndex((s) => s.id === id);
    if (i >= 0) enter(i);
  }

  /** "Jump to building": past the rest of the tour, through "aside" if it is needed. */
  function jumpToBuild() {
    st.asideNeeded = hasBlocks();
    goTo(st.asideNeeded ? "aside" : "drawer");
  }

  // --- the run step -------------------------------------------------------

  function runStatus() {
    const step = st.steps[st.index];
    const lesson = step.lesson();
    if (st.flight === "failed") {
      setStatus(st.crashed
        ? "The drone hit something. Read the console, then press Run to try again."
        : st.uncredited
          // Usually a second Run without Reset: the target in front of it was
          // already counted on the first.
          ? "It signalled, but not next to a target it hadn't already found, so it didn't "
            + "count. Press Reset, then Run again."
          : "That didn't go to plan. Read the console to see why, then press Run again.");
    } else if (st.flight === "done") {
      // Said again, not left alone: the server sends `running: false` after
      // `finished`, and that render used to wipe the praise it had just shown.
      setStatus(`✓ ${step.okText}`, true);
    } else if (!st.running && !lesson.ok) {
      setStatus(lesson.hint || "Your program has changed. Go Back to see what's missing.");
    } else {
      setStatus("");
    }
  }

  function onRunning(running) {
    const step = st.steps[st.index];
    st.running = running;
    if (!st.active || !step || step.kind !== "run") return;
    if (running) {
      st.flight = "flying";
      st.crashed = false;
      st.credited = false;
    }
    render();
    runStatus();
  }

  function onFinished(msg, late = false) {
    const step = st.steps[st.index];
    if (!st.active || !step || step.kind !== "run" || st.flight !== "flying") return;
    if (step.needsCredit && !st.credited && !late && msg.reason === "done" && !st.crashed) {
      // The credit rides on a mission message the server sends from a task of
      // its own, so it can arrive just after "finished". Give it a moment
      // before telling a student their find did not count.
      win.setTimeout(() => onFinished(msg, true), 600);
      return;
    }
    st.uncredited = msg.reason === "done" && !st.crashed && step.needsCredit && !st.credited;
    if (msg.reason === "done" && !st.crashed && !st.uncredited) {
      // No timer on to the next card: the student moves on when they have seen it.
      st.flight = "done";
      render();
      setStatus(`✓ ${step.okText}`, true);
    } else {
      st.flight = "failed";
      runStatus();
    }
  }

  // --- the arena ----------------------------------------------------------
  // The tutorial is flown in the square arena and the student is put back
  // where they were when it ends - the competition arena, unless they had
  // picked otherwise. The square room is the one its text describes ("4
  // metres across"); the competition room is where they practise afterwards.
  //
  // The server refuses an arena change mid-flight or mid-switch, so both
  // directions wait for a moment it will be taken rather than send a message
  // that comes back as a red error: the way in until the arena list has
  // arrived (on a first launch the tutorial can open before it does), the way
  // out until the drone has landed and the simulator is back.

  function sceneryName(id) {
    const list = (st.sceneries && st.sceneries.sceneries) || [];
    const found = list.find((s) => s.id === id);
    return found ? found.name : id;
  }

  function useLessonArena() {
    st.arenaPending = true;
    tryLessonArena();
  }

  function tryLessonArena() {
    if (!st.arenaPending || !st.sceneries || !st.active) return;
    st.arenaPending = false;
    const { sceneries: list, current } = st.sceneries;
    if (!list || !current || current === LESSON_ARENA || st.running) return;
    if (!list.some((s) => s.id === LESSON_ARENA)) return;
    if (!send({ type: "scenery", name: LESSON_ARENA })) return;
    st.arenaToRestore = current;
    log(`ℹ The tutorial flies in the ${sceneryName(LESSON_ARENA)}. You'll be back in the `
      + `${sceneryName(current)} when it ends.`);
  }

  function restoreArena() {
    const back = st.arenaToRestore;
    if (!back || st.active || st.running || st.mode !== "sim") return;
    if (!send({ type: "scenery", name: back })) return;   // tried again on the next chance
    st.arenaToRestore = null;
    log(`↺ Back in the ${sceneryName(back)}.`);
  }

  function onSceneries(msg) {
    st.sceneries = msg;
    if (!st.active) return;
    if (st.arenaPending) tryLessonArena();
    // Picked by the student mid-tutorial: theirs now, and not undone at the end.
    else if (st.arenaToRestore && msg.current && msg.current !== LESSON_ARENA) {
      st.arenaToRestore = null;
    }
  }

  // --- start and finish ---------------------------------------------------

  function start() {
    if (st.active) return true;
    if (st.mode === null) {
      // Not connected yet, or mid-switch. Said on screen rather than in the
      // console, where a first-time student is not yet looking, and the
      // tutorial opens by itself as soon as it can.
      st.pendingStart = true;
      notice("Starting up…", P(
        "Drone Coder is still connecting to the drone.",
        "The tutorial will open by itself in a moment.",
      ));
      return false;
    }
    if (st.mode !== "sim") {
      notice("The tutorial uses the simulator", P(
        "It ends with a real flight, so it only runs on the practice drone.",
        "Land the real Tello, press **Use Simulator** at the top, then open the tutorial again.",
      ));
      return false;
    }
    if (st.running) {
      log("⚠ Stop the program first, then open the tutorial.");
      return false;
    }
    st.steps = STEPS;
    st.active = true;
    st.flight = null;
    useLessonArena();
    ui.shade.hidden = false;
    ui.card.hidden = false;
    enter(0);
    if (win.requestAnimationFrame) st.frame = win.requestAnimationFrame(loop);
    return true;
  }

  /**
   * A one-card "not now": the tutorial flies a drone, so it says no - kindly -
   * to a real one, and it waits for one that has not connected yet.
   */
  function notice(title, body) {
    st.steps = [{ id: "notice", center: true, title, body,
      actions: () => ["gap", button("OK", () => { st.pendingStart = false; close(); }, "primary")] }];
    st.active = true;
    ui.shade.hidden = false;
    ui.card.hidden = false;
    enter(0);
  }

  function close() {
    st.active = false;
    win.clearTimeout(st.okTimer);
    win.clearTimeout(st.hintTimer);
    win.clearInterval(st.pollTimer);
    st.okTimer = 0;
    if (st.frame && win.cancelAnimationFrame) win.cancelAnimationFrame(st.frame);
    ui.shade.hidden = true;
    ui.card.hidden = true;
  }

  function finish(outcome) {
    const wasNotice = st.steps[0] && st.steps[0].id === "notice";
    const here = st.steps[st.index];
    const fromCamera = Boolean(here) && chapterOf(here) === "camera";
    close();
    if (wasNotice) return;
    st.arenaPending = false;      // closed before the arena list ever came
    restoreArena();
    // Anything still put aside goes back: see the rules at the top.
    giveBack();
    writeFlag(storage, outcome);
    log(outcome === "done"
      ? "🎓 Tutorial complete. Press Tutorial at the top any time to see it again."
      : "ℹ Tutorial closed. Press Tutorial at the top to start it again.");
    // The challenge went with the card: the second tester pressed Finish and
    // then had to build it from memory. The console keeps it.
    if (outcome === "done") (fromCamera ? CAMERA_LOG : CHALLENGE_LOG).forEach((line) => log(line));
  }

  // --- wiring -------------------------------------------------------------

  const openBtn = $("tutorial-open");
  if (openBtn) openBtn.onclick = () => start();

  if (bus && bus.on) {
    bus.on((msg) => {
      if (msg.type === "drone_mode") {
        const changed = st.mode !== null && msg.mode !== st.mode;
        st.mode = msg.switching ? null : msg.mode;
        if (st.active && (changed || msg.switching) && st.steps[0].id !== "notice") {
          finish("skipped");
          log("ℹ The tutorial closed because the drone was switched.");
        }
        if (st.pendingStart && !msg.switching) {
          // Asked for while it was still connecting: open now, as promised.
          st.pendingStart = false;
          st.autoChecked = true;
          close();
          start();
        } else if (!st.autoChecked && !msg.switching) {
          st.autoChecked = true;
          if (shouldAutoStart({ storage, mode: st.mode, hasBlocks: hadBlocks })) start();
        }
        // Closed by a drone switch: the simulator kept the tutorial's arena,
        // so it goes back the moment the simulator does.
        restoreArena();
      } else if (msg.type === "sceneries") {
        onSceneries(msg);
      } else if (msg.type === "running") {
        if (openBtn) openBtn.disabled = msg.running && !st.active;
        onRunning(msg.running);
        // Closed mid-flight: the arena could not change until it landed.
        if (!msg.running) restoreArena();
      } else if (msg.type === "finished") {
        onFinished(msg);
      } else if (msg.type === "mission") {
        if (msg.state === "crashed") st.crashed = true;
        // Only during a flight: the replayed last mission message must not
        // count a find from before this run.
        if (msg.signal === "credited" && st.flight === "flying") st.credited = true;
      }
    });
  }

  if (win.addEventListener) win.addEventListener("resize", layout);

  return { start, finish, goTo, get state() { return st; }, ui };
}

function browserStorage() {
  try {
    return window.localStorage || null;
  } catch (_error) {
    return null;
  }
}

if (typeof window !== "undefined" && window.COMP1_WORKSPACE && !window.COMP1_TUTORIAL) {
  try {
    window.COMP1_TUTORIAL = install({
      window, document,
      workspace: window.COMP1_WORKSPACE,
      Blockly: window.Blockly,
      storage: browserStorage(),
      bus: window.COMP1_BUS,
      log: window.COMP1_LOG,
      send: window.COMP1_SEND,
    });
  } catch (_error) {
    // A tutorial that cannot start is a tutorial nobody sees. Never the page.
  }
}
if (typeof module !== "undefined") {
  module.exports = {
    LESSON, VISION_LESSON, SHELL, COND, FULL, TUTORIAL_KEY, STASH_KEY, LESSON_ARENA,
    checkLesson, fixLesson, chainOf, shouldAutoStart, readFlag, writeFlag,
    saveStash, readStash, dropStash, hueToCss, install,
  };
}
