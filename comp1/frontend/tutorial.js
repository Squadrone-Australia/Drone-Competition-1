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
// switched underneath it.
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
// every arena. A first flight that crashes is a terrible first lesson.

const TUTORIAL_KEY = "comp1.tutorial.v1";       // "done" | "skipped"
const STASH_KEY = "comp1.tutorial.stash.v1";    // blocks put aside for the lesson
const STASH_FORMAT = 1;

// Blockly's colour for each toolbox category, so a block named in the text is
// drawn the colour the student is looking for. Mirrors `C` in blocks.js.
const HUES = { flight: 205, sense: 185, obstacle: 20, mission: 32, flow: 120 };

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

function problemWith(b, spec, n) {
  if (b.type !== spec.type) {
    return `Block ${n} under “when mission starts” is “${blockText(b)}”, but it should be `
      + `“${spec.label}”. Put “${spec.label}” in above it, or drag the wrong block away.`;
  }
  for (const [name, [want, wantText]] of Object.entries(spec.fields || {})) {
    if (b.getFieldValue(name) !== want) {
      const field = b.getField(name);
      const shown = field && field.getText ? field.getText() : b.getFieldValue(name);
      return `Block ${n} says “${shown}”. Click “${shown}” on the block and choose “${wantText}”.`;
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

/**
 * Does the program match the first `upto + 1` lesson blocks?
 *
 * `hint` is empty when there is simply nothing there yet: the step's own text
 * already says what to do, and repeating it in an alarming colour before the
 * student has touched anything reads as a telling-off.
 */
function checkLesson(ws, upto) {
  const chain = chainOf(ws);
  for (let i = 0; i <= upto; i += 1) {
    const spec = LESSON[i];
    const b = chain[i];
    if (!b) {
      const above = i === 0 ? "🚁 when mission starts" : LESSON[i - 1].label;
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
  }
  if (upto >= LESSON.length - 1 && chain.length > LESSON.length) {
    return { ok: false, hint: "Nothing should come after “land”. Drag the extra blocks to the trash can." };
  }
  return { ok: true, hint: "" };
}

function specState(spec) {
  const state = { type: spec.type };
  if (spec.fields) {
    state.fields = {};
    for (const [name, [value]] of Object.entries(spec.fields)) state.fields[name] = value;
  }
  if (spec.number) {
    state.inputs = { [spec.number.input]:
      { shadow: { type: "math_number", fields: { NUM: spec.number.value } } } };
  }
  return state;
}

/**
 * "Do it for me": make the program match the lesson up to step `upto`.
 *
 * It repairs rather than rebuilds - a right block with a wrong number keeps
 * its place and gets the number - so the student sees their own attempt
 * finished, not replaced. A block that does not belong is pushed down, not
 * deleted: Blockly re-attaches whatever was below an insertion point.
 */
function fixLesson(ws, Blockly, upto) {
  try {
    const start = startBlock(ws);
    if (!start) return false;
    for (let i = 0; i <= upto; i += 1) {
      const spec = LESSON[i];
      const chain = chainOf(ws);
      const b = chain[i];
      const prev = i === 0 ? start : chain[i - 1];
      if (!b || b.type !== spec.type) {
        // The student's own loose block, if one is lying about on its own -
        // snapping theirs in teaches more than conjuring a twin beside it.
        const loose = !b && looseBlock(ws, spec.type);
        const usable = loose && !loose.getParent() && !loose.getNextBlock()
          && !problemWith(loose, spec, i + 1);
        const block = usable ? loose : Blockly.serialization.blocks.append(specState(spec), ws);
        prev.nextConnection.connect(block.previousConnection);
        continue;
      }
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
    if (upto >= LESSON.length - 1) {
      const extra = chainOf(ws)[LESSON.length];
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
  const $ = (id) => doc.getElementById(id);

  const ui = buildUi();
  const st = {
    active: false, index: 0, steps: [],
    mode: null, running: false, autoChecked: false,
    stash: null,                 // blocks put aside for the lesson
    flight: null,                // "flying" | "done" | "failed" while on the run step
    crashed: false,
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
      if (!startBlock(ws)) seedStart();
      return true;
    } catch (_error) {
      return false;
    }
  }

  function seedStart() {
    const start = ws.newBlock("start");
    if (start.initSvg) { start.initSvg(); start.render(); }
    start.moveBy(30, 30);
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
  const visible = (node) => {
    if (!node || node.hidden) return false;
    const r = node.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  // --- steps --------------------------------------------------------------

  const P = (...lines) => lines;

  function buildStep(upto, step) {
    return {
      phase: "build", target: el("#blockly"), place: "right", kind: "check",
      check: () => checkLesson(ws, upto),
      fix: () => fixLesson(ws, Blockly, upto),
      ...step,
    };
  }

  const STEPS = [
    { id: "welcome", center: true,
      title: "Welcome to Drone Coder 👋",
      body: P(
        "You're going to program a drone by snapping blocks together. No typing needed.",
        "This takes about **3 minutes**: a quick look around the screen, then you'll build "
          + "and fly your first program.",
        "Right now you're flying the **simulator**, a practice drone on the screen. "
          + "Nothing real can crash, so try anything you like.",
      ),
      actions: () => [
        button("Skip, I've used this before", () => finish("skipped"), "link"),
        "gap",
        button("Let's go", next, "primary"),
      ] },

    { id: "workspace", phase: "tour", target: el("#blockly"), place: "right",
      title: "This is your workspace",
      body: P("You build programs here. Blocks snap together like LEGO, and the drone does "
        + "them **from top to bottom, one at a time**.") },

    { id: "toolbox", phase: "tour", target: el(".blocklyToolbox"), place: "right",
      title: "The block drawers",
      body: P(
        "Click a drawer to open it, then drag a block out onto the workspace.",
        "Each drawer has its own colour: [[Flight:flight]] blocks move the drone, "
          + "[[Sensing:sense]] blocks read the camera, [[Control:flow]] blocks repeat "
          + "and decide, [[Mission:mission]] blocks are for the competition.",
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

    { id: "help", phase: "tour", target: el("#block-help"), place: "top",
      title: "What does this block do?",
      body: P("Click any block and its explanation appears down here. You can also hover over "
        + "a block to see the same tip.") },

    { id: "stage", phase: "tour", target: el("#stage-bar"), place: "left", spotlight: el("#stage"),
      title: "The 3D view",
      body: P(
        "This is your drone in its arena. Pick a camera at the top:",
        ["**Follow** chases the drone", "**Orbit**: drag to look around, scroll to zoom",
          "**Map** shows the whole room from above", "**Trail** draws the path you flew"],
      ) },

    { id: "camera", phase: "tour", target: el("#camera-window"), place: "left",
      title: "What the drone sees",
      body: P(
        "This is the drone's own camera. **Your program only knows what's in this picture.**",
        "The drone has no map and no GPS. To find a target it has to turn and look for it, "
          + "just like you would. (Leave the Colour / AI model setting as it is for now.)",
      ) },

    { id: "telemetry", phase: "tour", target: el("#telemetry"), place: "left",
      title: "Live sensor readings",
      body: P(
        "What the camera detects right now: is a target in view, how far away it is, and "
          + "which direction (− means left, + means right).",
        "[[Sensing:sense]] blocks read exactly these numbers, so watch them to see what your "
          + "program sees.",
      ) },

    { id: "arena", phase: "tour", target: el("#arena-panel"), place: "left",
      skip: () => !visible($("arena-panel")),
      title: "The floor plan",
      body: P(
        "The room seen from above. **Red dots are targets**: the “fires” your mission has to "
          + "find. Grey dots are decoys and obstacles. **S** is the start pad, and the orange "
          + "arrow is your drone.",
        "**Shuffle** makes a new random layout. **Edit** lets you click to add or remove targets.",
      ) },

    { id: "console", phase: "tour", target: el("#console"), place: "left",
      title: "Messages for you",
      body: P(
        "Warnings, results and drone messages show up here.",
        "If a program didn't do what you expected, **look here first**. Many messages come "
          + "with a 💡 How to fix tip.",
      ) },

    { id: "run-group", phase: "tour", target: el(".run-group"), place: "bottom",
      title: "Run, Stop, Reset",
      body: P(
        "**Run** flies your program. **Stop** ends it and lands the drone safely. "
          + "**Reset** puts the drone back on its start pad.",
      ) },

    { id: "estop", phase: "tour", target: el("#estop"), place: "bottom",
      title: "Only for real emergencies",
      body: P(
        "On a real drone, **EMERGENCY STOP cuts the motors instantly and the drone falls out "
          + "of the air.**",
        "Use it only if a real drone is about to hit someone or something. For everything "
          + "else, press **Stop**.",
      ) },

    { id: "hud", phase: "tour", target: el(".hud"), place: "bottom",
      title: "Drone status",
      body: P(
        "Green **connected** means everything is working. You can also see how many targets "
          + "you've found, the battery, and which drone you're controlling: right now, the "
          + "simulator.",
        "**Use real Tello** is for later, when you're ready to fly the real thing. "
          + "**Tutorial** opens this guide again.",
      ) },

    { id: "aside", phase: "build", center: true,
      skip: () => !hasBlocks(),
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

    { id: "drawer", phase: "build", kind: "check", place: "right",
      target: () => {
        const item = toolboxItem("Flight");
        return (item && item.getDiv && item.getDiv()) || doc.querySelector(".blocklyToolbox");
      },
      title: "Let's build! Open the Flight drawer",
      body: P(
        "Your first program will fly the drone out, turn it around, and bring it home.",
        "Start by clicking **Flight** on the left.",
      ),
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

    { id: "run", phase: "build", kind: "run",
      target: () => (st.running ? doc.querySelector("#stage") : doc.querySelector("#run")),
      place: () => (st.running ? "left" : "bottom"),
      title: () => (st.running ? "It's flying! 🚁" : "Press Run"),
      body: () => (st.running
        ? P("Watch the 3D view and the camera.",
          "Look back at your workspace too: **the block that's running right now lights up.**")
        : P("Your program: take off → fly out → turn around → fly back → land.",
          "Press the orange **Run** button at the top and watch what happens.")),
      okText: "Landed back home. Your program worked!" },

    { id: "python", phase: "build", target: el("#debug-panel"), place: "right",
      enter: () => {
        const panel = $("debug-panel");
        if (panel && !panel.classList.contains("open")) $("debug-toggle").click();
        const tab = doc.querySelector('[data-debug-view="python"]');
        if (tab) tab.click();
      },
      title: "Your blocks are real code",
      body: P(
        "This is the program you just flew, written in **Python**. Each block became a line "
          + "like drone.takeoff().",
        "You never have to type code here. But this is what real drone programmers write, "
          + "and **Live execution** shows each command as the drone ran it.",
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
          "Click a block to see what it does.",
          "Stuck? Read the console. **Stop** lands the drone, **Reset** puts it back on the start pad.",
          "Your blocks are saved automatically on this computer.",
        ],
        "**Next challenge: find a target 🔥**",
        "Make the drone turn until it sees a red target, fly up to it, and celebrate:",
        [
          "[[take off:flight]]",
          "[[keep doing until target seen?:flow]] with [[turn ↻ clockwise 30°:flight]] inside",
          "[[approach target and stop:mission]]",
          "[[signal target found 🎉:mission]]",
          "[[land:flight]]",
        ],
        "“target seen?” is in Sensing, “keep doing until” is in Control.",
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

  // --- rendering ----------------------------------------------------------

  const value = (v) => (typeof v === "function" ? v() : v);

  function render() {
    const step = st.steps[st.index];
    if (!step) return;
    const { card, phase, fill, title, body, status, actions } = ui;

    const ofPhase = st.steps.filter((s) => s.phase === step.phase && !skipped(s));
    const at = ofPhase.indexOf(step) + 1;
    phase.textContent = step.phase === "tour" ? `Quick tour · ${at} of ${ofPhase.length}`
      : step.phase === "build" ? `Build & fly · ${at} of ${ofPhase.length}` : "";
    const counted = st.steps.filter((s) => !skipped(s));
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
    const out = [button("Skip tutorial", () => finish("skipped"), "link"), "gap"];
    if (previousIndex() >= 0) out.push(button("Back", back));
    if (step.kind === "check") {
      out.push(button("Do it for me", () => {
        step.fix();
        st.fixed = true;
        evaluate(true);
      }));
      const n = button("Next", next, "primary");
      n.disabled = true;
      n.dataset.role = "next";
      out.push(n);
    } else if (step.kind !== "run") {
      out.push(button("Next", next, "primary"));
    }
    return out;
  }

  function setStatus(text, ok) {
    ui.status.textContent = text || "";
    ui.status.classList.toggle("ok", Boolean(ok));
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
    const target = value(step.target);
    const lit = step.spotlight ? step.spotlight() : target;
    const W = win.innerWidth, H = win.innerHeight, gap = 14, pad = 6, edge = 8;
    if (!target || !visible(lit || target)) {
      // Nothing to point at (a panel hidden in this mode): centre the card.
      spot.hidden = true;
      card.classList.add("center");
      return;
    }
    card.classList.remove("center");
    spot.hidden = false;
    const s = (lit || target).getBoundingClientRect();
    const x0 = Math.max(s.left - pad, 2), y0 = Math.max(s.top - pad, 2);
    spot.style.left = `${x0}px`;
    spot.style.top = `${y0}px`;
    spot.style.width = `${Math.min(s.right + pad, W - 2) - x0}px`;
    spot.style.height = `${Math.min(s.bottom + pad, H - 2) - y0}px`;

    const r = target.getBoundingClientRect();
    const w = card.offsetWidth || 360, h = card.offsetHeight || 220;
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
    // A step that only asks for a click outside the workspace cannot always be
    // done for the student (a toolbox that will not select), and "Do it for
    // me" must never leave anyone stuck - so for those, pressing it is enough.
    const result = step.trustFix && st.fixed ? { ok: true, hint: "" } : step.check();
    const nextBtn = ui.actions.querySelector('[data-role="next"]');
    if (nextBtn) nextBtn.disabled = !result.ok;
    if (result.ok) {
      cancelHint();
      setStatus(`✓ ${step.okText || "Done!"}`, true);
      if (!st.okTimer) st.okTimer = win.setTimeout(() => { st.okTimer = 0; next(); }, 1100);
      return;
    }
    win.clearTimeout(st.okTimer);
    st.okTimer = 0;
    if (quiet) return;
    if (!result.hint) { cancelHint(); setStatus(""); return; }
    if (ui.status.textContent === result.hint) return;          // already showing
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

  function previousIndex() {
    for (let i = st.index - 1; i >= 0; i -= 1) {
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
    const step = st.steps[index];
    setStatus("");
    if (step.id === "run") {
      st.flight = null;
      st.crashed = false;
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

  // --- the run step -------------------------------------------------------

  function runStatus() {
    const lesson = checkLesson(ws, LESSON.length - 1);
    if (st.flight === "failed") {
      setStatus(st.crashed
        ? "The drone hit something. Read the console, then press Run to try again."
        : "That didn't go to plan. Read the console to see why, then press Run again.");
    } else if (!st.running && !lesson.ok) {
      setStatus(lesson.hint || "Your program has changed. Go Back to see what's missing.");
    } else {
      setStatus("");
    }
  }

  function onRunning(running) {
    const step = st.steps[st.index];
    st.running = running;
    if (!st.active || !step || step.id !== "run") return;
    if (running) {
      st.flight = "flying";
      st.crashed = false;
    }
    render();
    runStatus();
  }

  function onFinished(msg) {
    const step = st.steps[st.index];
    if (!st.active || !step || step.id !== "run" || st.flight !== "flying") return;
    if (msg.reason === "done" && !st.crashed) {
      st.flight = "done";
      setStatus(`✓ ${step.okText}`, true);
      st.okTimer = win.setTimeout(() => { st.okTimer = 0; next(); }, 1600);
    } else {
      st.flight = "failed";
      runStatus();
    }
  }

  // --- start and finish ---------------------------------------------------

  function start() {
    if (st.active) return true;
    if (st.mode === null) {
      // Not connected yet, or mid-switch: not the moment to explain anything.
      log("⚠ The tutorial can start once Drone Coder is connected to a drone.");
      return false;
    }
    if (st.mode !== "sim") {
      notice();
      return false;
    }
    if (st.running) {
      log("⚠ Stop the program first, then open the tutorial.");
      return false;
    }
    st.steps = STEPS;
    st.active = true;
    st.flight = null;
    ui.shade.hidden = false;
    ui.card.hidden = false;
    enter(0);
    if (win.requestAnimationFrame) st.frame = win.requestAnimationFrame(loop);
    return true;
  }

  /** The tutorial flies a drone, so it says no - kindly - to a real one. */
  function notice() {
    st.steps = [{ id: "notice", center: true,
      title: "The tutorial uses the simulator",
      body: P(
        "It ends with a real flight, so it only runs on the practice drone.",
        "Land the real Tello, press **Use Simulator** at the top, then open the tutorial again.",
      ),
      actions: () => ["gap", button("OK", () => close(), "primary")] }];
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
    close();
    if (wasNotice) return;
    // Anything still put aside goes back: see the rules at the top.
    giveBack();
    writeFlag(storage, outcome);
    log(outcome === "done"
      ? "🎓 Tutorial complete. Press Tutorial at the top any time to see it again."
      : "ℹ Tutorial closed. Press Tutorial at the top to start it again.");
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
        if (!st.autoChecked && !msg.switching) {
          st.autoChecked = true;
          if (shouldAutoStart({ storage, mode: st.mode, hasBlocks: hadBlocks })) start();
        }
      } else if (msg.type === "running") {
        if (openBtn) openBtn.disabled = msg.running && !st.active;
        onRunning(msg.running);
      } else if (msg.type === "finished") {
        onFinished(msg);
      } else if (msg.type === "mission" && msg.state === "crashed") {
        st.crashed = true;
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
    });
  } catch (_error) {
    // A tutorial that cannot start is a tutorial nobody sees. Never the page.
  }
}
if (typeof module !== "undefined") {
  module.exports = {
    LESSON, TUTORIAL_KEY, STASH_KEY,
    checkLesson, fixLesson, chainOf, shouldAutoStart, readFlag, writeFlag,
    saveStash, readStash, dropStash, hueToCss, install,
  };
}
