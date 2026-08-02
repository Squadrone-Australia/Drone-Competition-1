const workspace = Blockly.inject("blockly", {
  toolbox: COMP1.toolbox, trashcan: true, zoom: { controls: true },
});
const start = workspace.newBlock("start");
start.initSvg(); start.render(); start.moveBy(30, 30);

const statusEl = document.getElementById("status");
const consoleEl = document.getElementById("console");
const videoEl = document.getElementById("video");
const foundEl = document.getElementById("found");
let ws, lastUrl;

function log(msg) {
  consoleEl.textContent += msg + "\n";
  consoleEl.scrollTop = consoleEl.scrollHeight;
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
    if (msg.type === "highlight") workspace.highlightBlock(msg.blockId);
    else if (msg.type === "found_count") foundEl.textContent = `Victims found: ${msg.count}`;
    else if (msg.type === "finished") {
      workspace.highlightBlock(null);
      log(`mission ${msg.reason}${msg.detail ? ": " + msg.detail : ""}`);
    }
    else if (msg.type === "telemetry") showTelemetry(msg);
    else if (msg.type === "error") log("⚠ " + msg.message);
    else if (msg.type === "estopped") log("⛔ EMERGENCY STOP");
  };
}
connect();

document.getElementById("run").onclick = () => {
  const program = COMP1.serializeProgram(workspace);
  // empty sockets are filled in with a harmless default rather than refusing to run —
  // say so out loud so a half-built program isn't a silent mystery
  COMP1.warnings.forEach((w) => log("⚠ " + w));
  ws.send(JSON.stringify({ type: "run", program }));
};
document.getElementById("stop").onclick = () => ws.send(JSON.stringify({ type: "stop" }));
document.getElementById("estop").onclick = () => ws.send(JSON.stringify({ type: "estop" }));
