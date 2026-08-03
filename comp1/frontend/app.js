const workspace = Blockly.inject("blockly", {
  toolbox: COMP1.toolbox, trashcan: true, zoom: { controls: true },
});
const start = workspace.newBlock("start");
start.initSvg(); start.render(); start.moveBy(30, 30);

const statusEl = document.getElementById("status");
const consoleEl = document.getElementById("console");
const videoEl = document.getElementById("video");
const foundEl = document.getElementById("found");
const blockHelpEl = document.querySelector("#block-help span");
const droneModeEl = document.getElementById("drone-mode");
const useTelloEl = document.getElementById("use-tello");
const debugNoteEl = document.getElementById("debug-note");
const debugPythonEl = document.getElementById("debug-python");
const debugProgramEl = document.getElementById("debug-program");
const debugTraceEl = document.getElementById("debug-trace");
const debugViewButtons = document.querySelectorAll("[data-debug-view]");
let ws, lastUrl;
let missionRunning = false;
let droneMode = null;
let droneSwitching = false;
let debugSequence = 0;
let debugHasTrace = false;
let debugView = "python";

const blockDescriptions = new Map(COMP1.blocks.map((block) => [block.type, block.tooltip]));
workspace.addChangeListener((event) => {
  if (!blockHelpEl || event.type !== Blockly.Events.SELECTED) return;
  const block = event.newElementId ? workspace.getBlockById(event.newElementId) : null;
  blockHelpEl.textContent = block
    ? blockDescriptions.get(block.type) || "This is a built-in Blockly value block."
    : "Select a block to see what it does. You can also hover over any block.";
});

/**
 * Message bus for panels that live in their own module (view3d.js).
 *
 * `scene` arrives once, immediately on connect, which can be before a deferred
 * module has subscribed — so the last one is replayed to late subscribers rather
 * than lost, leaving an empty 3D stage for the rest of the session.
 */
const bus = window.COMP1_BUS = {
  handlers: [],
  sticky: {},                       // last `scene` / `sceneries`, replayed on subscribe
  on(fn) {
    this.handlers.push(fn);
    Object.values(this.sticky).forEach(fn);
  },
  emit(msg) {
    if (msg.type === "scene" || msg.type === "sceneries") this.sticky[msg.type] = msg;
    this.handlers.forEach((fn) => fn(msg));
  },
};

function log(msg) {
  consoleEl.textContent += msg + "\n";
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function showDebugProgram(program) {
  debugPythonEl.textContent = COMP1.programToPython(program);
  debugProgramEl.textContent = JSON.stringify(program, null, 2);
}

function updateDebugProgram() {
  try {
    const program = COMP1.serializeProgram(workspace);
    showDebugProgram(program);
    const hasLooseBlocks = workspace.getAllBlocks(false)
      .some((block) => block.type !== "start");
    debugNoteEl.textContent = hasLooseBlocks && program.blocks.length === 0
      ? "Blocks are present, but none is connected beneath “when mission starts”."
      : "Display only. Only blocks connected beneath “when mission starts” are translated.";
  } catch (error) {
    debugPythonEl.textContent = `Could not translate workspace: ${error.message}`;
    debugProgramEl.textContent = `Could not translate workspace: ${error.message}`;
  }
}

function clearDebugTrace() {
  debugSequence = 0;
  debugHasTrace = false;
  debugTraceEl.textContent = "Run a program to see adapter calls.";
}

function appendDebugLine(line) {
  if (!debugHasTrace) {
    debugTraceEl.textContent = "";
    debugHasTrace = true;
  }
  debugSequence += 1;
  debugTraceEl.textContent += `${String(debugSequence).padStart(3, "0")}  ${line}\n`;
  debugTraceEl.scrollTop = debugTraceEl.scrollHeight;
}

function showExecution(msg) {
  if (msg.kind === "block") {
    appendDebugLine(`block ${msg.op}  [${msg.blockId}]`);
    return;
  }
  if (msg.kind === "call") {
    const args = (msg.args || []).map((arg) => JSON.stringify(arg)).join(", ");
    const result = Object.prototype.hasOwnProperty.call(msg, "result")
      ? ` → ${JSON.stringify(msg.result)}` : "";
    appendDebugLine(`  ↳ ${msg.adapter}.${msg.method}(${args})${result}`);
  }
}

function selectDebugView(view) {
  debugView = view;
  debugPythonEl.hidden = view !== "python";
  debugProgramEl.hidden = view !== "program";
  debugTraceEl.hidden = view !== "trace";
  debugViewButtons.forEach((button) => {
    const selected = button.dataset.debugView === view;
    button.classList.toggle("on", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  document.getElementById("debug-clear").hidden = view !== "trace";
}

debugViewButtons.forEach((button) => {
  button.onclick = () => selectDebugView(button.dataset.debugView);
});
document.getElementById("debug-clear").onclick = clearDebugTrace;
document.getElementById("debug-toggle").onclick = () => {
  const panel = document.getElementById("debug-panel");
  const open = panel.classList.toggle("open");
  document.getElementById("debug-toggle").setAttribute("aria-expanded", String(open));
  requestAnimationFrame(() => Blockly.svgResize(workspace));
};
document.getElementById("debug-copy").onclick = async () => {
  const panes = { python: debugPythonEl, program: debugProgramEl, trace: debugTraceEl };
  const button = document.getElementById("debug-copy");
  try {
    await navigator.clipboard.writeText(panes[debugView].textContent);
    button.textContent = "Copied";
    setTimeout(() => { button.textContent = "Copy"; }, 1200);
  } catch (_error) {
    log("⚠ could not copy the code inspector text");
  }
};
workspace.addChangeListener((event) => {
  const isUiEvent = typeof event.isUiEvent === "function"
    ? event.isUiEvent()
    : Boolean(event.isUiEvent);
  if (!isUiEvent) requestAnimationFrame(updateDebugProgram);
});
selectDebugView("python");
updateDebugProgram();

/** The one socket, shared with panels that live in their own file. */
window.COMP1_SEND = (msg) => {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(msg));
};

// Whether something is flying, as a bus message. The server refuses arena edits
// mid-mission, and a panel that only greys itself out after the refusal is a
// panel that looks broken — so both pathways announce themselves here.
function setRunning(running) {
  missionRunning = running;
  useTelloEl.disabled = running || droneSwitching || !droneMode;
  bus.emit({ type: "running", running });
}

function showDroneMode(msg) {
  const previousMode = droneMode;
  droneMode = msg.mode;
  droneSwitching = Boolean(msg.switching);
  const names = { sim: "simulator", tello: "real Tello", mock: "mock drone" };
  droneModeEl.textContent = `Drone: ${names[droneMode] || droneMode}`;
  useTelloEl.textContent = droneSwitching
    ? "Switching…"
    : (droneMode === "tello" ? "Use Simulator" : "Use real Tello");
  useTelloEl.title = droneMode === "tello"
    ? "Switch back to the simulator"
    : "Connect to a real DJI Tello";
  useTelloEl.disabled = missionRunning || droneSwitching || !droneMode;
  if (previousMode && previousMode !== droneMode) {
    foundEl.textContent = "Victims found: 0";
    missionState = null;
    log(`switched to ${names[droneMode] || droneMode}`);
  }
}

// the same numbers the "distance to victim" / "direction to victim" blocks read,
// shown live so students can see what their program is reacting to
function showTelemetry(t) {
  const visible = document.getElementById("t-visible");
  visible.textContent = t.visible
    ? (t.count > 1 ? `yes (${t.count} seen)` : "yes")
    : "not seen";
  visible.className = t.visible ? "ok" : "";
  document.getElementById("t-distance").textContent =
    t.visible ? `${t.distance_cm} cm` : "—";
  document.getElementById("t-bearing").textContent =
    t.visible ? `${t.bearing_deg > 0 ? "+" : ""}${t.bearing_deg}°` : "—";
}

// The simulator scores a find against where the victims actually are, so with an
// arena in play the header shows the *credited* count rather than the number of
// times the student pressed the button.
let missionState = null;
function showMission(m) {
  foundEl.textContent = `Victims found: ${m.found} / ${m.total}`;
  if (m.signal === "no victim nearby") {
    log("⚠ no victim close enough to that signal — it did not count");
  }
  if (m.state === "success" && missionState !== "success") {
    log("🏆 mission success — every victim found and landed at the destination");
  }
  missionState = m.state;
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "blob";
  ws.onopen = () => { statusEl.textContent = "connected"; statusEl.className = "status ok"; };
  ws.onclose = () => {
    statusEl.textContent = "disconnected — retrying"; statusEl.className = "status bad";
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    if (ev.data instanceof Blob) {
      const url = URL.createObjectURL(ev.data);
      videoEl.src = url;
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url;
      return;
    }
    const msg = JSON.parse(ev.data);
    bus.emit(msg);
    if (msg.type === "highlight") workspace.highlightBlock(msg.blockId);
    else if (msg.type === "debug_program") showDebugProgram(msg.program);
    else if (msg.type === "execution") showExecution(msg);
    else if (msg.type === "found_count") foundEl.textContent = `Victims found: ${msg.count}`;
    else if (msg.type === "finished") {
      workspace.highlightBlock(null);
      log(`mission ${msg.reason}${msg.detail ? ": " + msg.detail : ""}`);
      appendDebugLine(`finished: ${msg.reason}${msg.detail ? ` (${msg.detail})` : ""}`);
      setRunning(false);
    }
    else if (msg.type === "script") setRunning(msg.state === "started");
    else if (msg.type === "mission") showMission(msg);
    else if (msg.type === "telemetry") showTelemetry(msg);
    else if (msg.type === "drone_mode") showDroneMode(msg);
    else if (msg.type === "error") log("⚠ " + msg.message);
    else if (msg.type === "estopped") { log("⛔ EMERGENCY STOP"); setRunning(false); }
    else if (msg.type === "reset") {
      workspace.highlightBlock(null);
      foundEl.textContent = "Victims found: 0";
      missionState = null;
      // On real hardware reset() cannot move the aircraft, so say what actually
      // happened. Telling a student the drone is on its pad while it hovers
      // where they left it is worse than saying nothing.
      log(msg.repositioned
        ? "↺ back on the start pad"
        : "↺ counters cleared — the drone has not moved");
    }
  };
}
connect();

document.getElementById("run").onclick = () => {
  const program = COMP1.serializeProgram(workspace);
  showDebugProgram(program);
  clearDebugTrace();
  // empty sockets are filled in with a harmless default rather than refusing to run —
  // say so out loud so a half-built program isn't a silent mystery
  COMP1.warnings.forEach((w) => log("⚠ " + w));
  ws.send(JSON.stringify({ type: "run", program }));
  setRunning(true);
};
document.getElementById("stop").onclick = () => ws.send(JSON.stringify({ type: "stop" }));
// Run resets on the server anyway; this button is for putting the drone back
// after a stopped or crashed attempt without flying another one.
document.getElementById("reset").onclick = () => ws.send(JSON.stringify({ type: "reset" }));
useTelloEl.onclick = () => {
  const useSimulator = droneMode === "tello";
  const confirmed = window.confirm(useSimulator
    ? "Switch to the simulator? Make sure the real Tello is safely landed first. Programs will stop controlling the physical drone."
    : "Switch to the real Tello? Join the TELLO Wi-Fi first. Programs will control the physical drone.");
  if (confirmed) {
    window.COMP1_SEND({ type: "switch_drone", mode: useSimulator ? "sim" : "tello" });
  }
};
document.getElementById("estop").onclick = () => ws.send(JSON.stringify({ type: "estop" }));
