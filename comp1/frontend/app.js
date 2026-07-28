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
    else if (msg.type === "error") log("⚠ " + msg.message);
    else if (msg.type === "estopped") log("⛔ EMERGENCY STOP");
  };
}
connect();

document.getElementById("run").onclick = () =>
  ws.send(JSON.stringify({ type: "run", program: COMP1.serializeProgram(workspace) }));
document.getElementById("stop").onclick = () => ws.send(JSON.stringify({ type: "stop" }));
document.getElementById("estop").onclick = () => ws.send(JSON.stringify({ type: "estop" }));
