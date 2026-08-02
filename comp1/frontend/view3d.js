/**
 * Wires the 3D stage (scene3d.js) to the WebSocket feed and its toolbar.
 *
 * Kept apart from scene3d.js so the renderer stays a pure "given a scene and a
 * pose, draw it" module with no knowledge of the protocol or the DOM around it.
 */
import { createStage } from "./scene3d.js";

const container = document.getElementById("view3d");
const empty = document.getElementById("stage-empty");
const stage = createStage(container);

const tools = document.getElementById("view-tools");

window.COMP1_BUS.on((msg) => {
  if (msg.type === "scene") {
    stage.setScene(msg.scene);
    const has = Boolean(msg.scene);
    empty.hidden = has;
    tools.hidden = !has;          // no arena, nothing for the camera modes to do
  } else if (msg.type === "pose") {
    stage.setPose(msg);
  } else if (msg.type === "reset") {
    // drop the old flight path rather than leaving the last attempt's line
    // hanging over the new one
    stage.clearTrail();
  }
});

// --- camera-mode toolbar ----------------------------------------------------

const modeButtons = [...document.querySelectorAll("#view-tools [data-mode]")];
for (const btn of modeButtons) {
  btn.onclick = () => {
    stage.setMode(btn.dataset.mode);
    modeButtons.forEach((b) => b.classList.toggle("on", b === btn));
  };
}

const trailBtn = document.getElementById("trail");
trailBtn.onclick = () => {
  const on = !trailBtn.classList.contains("on");
  trailBtn.classList.toggle("on", on);
  stage.setTrailVisible(on);
};

// --- camera window ----------------------------------------------------------

const camWindow = document.getElementById("camera-window");
const caption = camWindow.querySelector("figcaption");

document.getElementById("cam-dock").onclick = (e) => {
  e.stopPropagation();
  camWindow.classList.toggle("docked");
  camWindow.style.left = camWindow.style.top = "";   // drop any dragged offset
};

// Drag by the title bar. Offsets are clamped to the stage so the window can
// never be dropped somewhere it cannot be grabbed again.
caption.addEventListener("pointerdown", (e) => {
  if (e.target.tagName === "BUTTON" || camWindow.classList.contains("docked")) return;
  const box = camWindow.getBoundingClientRect();
  const stageBox = container.parentElement.getBoundingClientRect();
  const grabX = e.clientX - box.left, grabY = e.clientY - box.top;
  caption.setPointerCapture(e.pointerId);

  const move = (ev) => {
    const maxX = stageBox.width - box.width, maxY = stageBox.height - box.height;
    camWindow.style.left =
      `${Math.min(Math.max(ev.clientX - stageBox.left - grabX, 0), Math.max(maxX, 0))}px`;
    camWindow.style.top =
      `${Math.min(Math.max(ev.clientY - stageBox.top - grabY, 0), Math.max(maxY, 0))}px`;
    camWindow.style.right = camWindow.style.bottom = "auto";
  };
  const up = () => {
    caption.removeEventListener("pointermove", move);
    caption.releasePointerCapture(e.pointerId);
  };
  caption.addEventListener("pointermove", move);
  caption.addEventListener("pointerup", up, { once: true });
});
