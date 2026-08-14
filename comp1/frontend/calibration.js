(function () {
  "use strict";

  const dialog = document.getElementById("vision-dialog");
  const openButton = document.getElementById("vision-calibrate");
  const frame = document.getElementById("vision-frame");
  const preview = document.getElementById("vision-preview");
  const video = document.getElementById("video");
  const status = document.getElementById("vision-status");
  const context = frame.getContext("2d");
  const frozen = document.createElement("canvas");
  const frozenContext = frozen.getContext("2d");
  const profileKey = "comp1.visionProfiles.v1";
  const fields = {
    h1Low: document.getElementById("vision-h1-low"),
    h1High: document.getElementById("vision-h1-high"),
    h2Low: document.getElementById("vision-h2-low"),
    h2High: document.getElementById("vision-h2-high"),
    sat: document.getElementById("vision-sat"),
    value: document.getElementById("vision-value"),
  };
  const outputIds = {
    h1Low: "h1-low", h1High: "h1-high", h2Low: "h2-low", h2High: "h2-high",
    sat: "sat", value: "value",
  };
  let dragStart = null;
  let selection = null;
  let running = false;
  let previewTimer = null;

  function tell(message, kind = "") {
    status.textContent = message;
    status.className = kind;
  }

  function updateOutputs() {
    Object.entries(fields).forEach(([name, input]) => {
      document.getElementById(`vision-${outputIds[name]}-out`).value = input.value;
    });
  }

  function controlsToConfig() {
    const sat = Number(fields.sat.value);
    const value = Number(fields.value.value);
    return {
      lower1: [Number(fields.h1Low.value), sat, value],
      upper1: [Number(fields.h1High.value), 255, 255],
      lower2: [Number(fields.h2Low.value), sat, value],
      upper2: [Number(fields.h2High.value), 255, 255],
    };
  }

  function configToControls(config) {
    fields.h1Low.value = config.lower1[0];
    fields.h1High.value = config.upper1[0];
    fields.h2Low.value = config.lower2[0];
    fields.h2High.value = config.upper2[0];
    fields.sat.value = Math.min(config.lower1[1], config.lower2[1]);
    fields.value.value = Math.min(config.lower1[2], config.lower2[2]);
    updateOutputs();
  }

  function captureFrame() {
    if (!video.complete || !video.naturalWidth) {
      tell("Waiting for the first camera frame…", "bad");
      return false;
    }
    frozen.width = video.naturalWidth;
    frozen.height = video.naturalHeight;
    frozenContext.drawImage(video, 0, 0, frozen.width, frozen.height);
    frame.width = frozen.width;
    frame.height = frozen.height;
    selection = null;
    redraw();
    tell("Press “Find marker for me”, or drag a box tightly inside the coloured marker.");
    return true;
  }

  function redraw(active = selection) {
    context.drawImage(frozen, 0, 0, frame.width, frame.height);
    if (!active) return;
    const [x0, y0, x1, y1] = active;
    context.fillStyle = "rgba(56, 189, 248, .16)";
    context.strokeStyle = "#38bdf8";
    context.lineWidth = Math.max(2, frame.width / 320);
    context.fillRect(x0, y0, x1 - x0, y1 - y0);
    context.strokeRect(x0, y0, x1 - x0, y1 - y0);
  }

  function point(event) {
    const box = frame.getBoundingClientRect();
    return [
      Math.min(frame.width, Math.max(0, (event.clientX - box.left) * frame.width / box.width)),
      Math.min(frame.height, Math.max(0, (event.clientY - box.top) * frame.height / box.height)),
    ];
  }

  frame.addEventListener("pointerdown", (event) => {
    if (!frozen.width) return;
    dragStart = point(event);
    frame.setPointerCapture(event.pointerId);
  });
  frame.addEventListener("pointermove", (event) => {
    if (!dragStart) return;
    const end = point(event);
    redraw([dragStart[0], dragStart[1], end[0], end[1]]);
  });
  frame.addEventListener("pointerup", (event) => {
    if (!dragStart) return;
    const end = point(event);
    selection = [
      Math.min(dragStart[0], end[0]), Math.min(dragStart[1], end[1]),
      Math.max(dragStart[0], end[0]), Math.max(dragStart[1], end[1]),
    ];
    dragStart = null;
    redraw();
    const roi = [selection[0] / frame.width, selection[1] / frame.height,
      selection[2] / frame.width, selection[3] / frame.height];
    tell("Analysing the selected marker pixels…");
    window.COMP1_SEND({ type: "vision_sample", roi });
  });

  function requestPreview() {
    tell("Building mask preview…");
    window.COMP1_SEND({ type: "vision_preview", config: controlsToConfig() });
  }

  function profiles() {
    try { return JSON.parse(localStorage.getItem(profileKey)) || {}; }
    catch (_error) { return {}; }
  }

  function showProfiles(selected = "") {
    const select = document.getElementById("vision-profile");
    const names = Object.keys(profiles()).sort((a, b) => a.localeCompare(b));
    select.innerHTML = '<option value="">Choose a profile</option>';
    names.forEach((name) => {
      const option = document.createElement("option");
      option.value = option.textContent = name;
      select.appendChild(option);
    });
    select.value = selected;
  }

  function toml(config) {
    const row = (key) => `${key} = [${config[key].join(", ")}]`;
    return ["# COMP1 vision colour profile", "", row("lower1"), row("upper1"),
      row("lower2"), row("upper2"), ""].join("\n");
  }

  Object.values(fields).forEach((input) => input.addEventListener("input", () => {
    updateOutputs();
    clearTimeout(previewTimer);
    if (dialog.open) previewTimer = setTimeout(requestPreview, 250);
  }));
  openButton.onclick = () => {
    if (running) return;
    dialog.showModal();
    requestAnimationFrame(captureFrame);
  };
  document.getElementById("vision-close").onclick = () => dialog.close();
  document.getElementById("vision-refresh").onclick = captureFrame;
  document.getElementById("vision-auto").onclick = () => {
    // recapture first so the canvas shows roughly the frame the server will
    // sample, and so the returned region lands on the right picture
    if (!captureFrame()) return;
    tell("Looking for a red marker…");
    window.COMP1_SEND({ type: "vision_auto" });
  };
  document.getElementById("vision-preview-button").onclick = requestPreview;
  document.getElementById("vision-apply").onclick = () => {
    window.COMP1_SEND({ type: "vision_apply", config: controlsToConfig() });
    tell("Applying settings…");
  };
  document.getElementById("vision-reset").onclick = () => {
    window.COMP1_SEND({ type: "vision_reset" });
    tell("Restoring the settings used at startup…");
  };
  document.getElementById("vision-profile-save").onclick = () => {
    const input = document.getElementById("vision-profile-name");
    const name = input.value.trim();
    if (!name) { tell("Enter a name for this venue profile.", "bad"); return; }
    const saved = profiles();
    saved[name] = controlsToConfig();
    localStorage.setItem(profileKey, JSON.stringify(saved));
    showProfiles(name);
    tell(`Saved “${name}” in this browser.`, "ok");
  };
  document.getElementById("vision-profile-load").onclick = () => {
    const name = document.getElementById("vision-profile").value;
    const config = profiles()[name];
    if (!config) { tell("Choose a saved profile first.", "bad"); return; }
    configToControls(config);
    requestPreview();
  };
  document.getElementById("vision-profile-download").onclick = () => {
    const blob = new Blob([toml(controlsToConfig())], { type: "text/plain" });
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = "vision_config.toml";
    link.click();
    URL.revokeObjectURL(link.href);
  };

  window.COMP1_BUS.on((message) => {
    if (message.type === "running") {
      running = message.running;
      openButton.disabled = running;
      if (running && dialog.open) dialog.close();
    } else if (message.type === "vision_config") {
      configToControls(message.config);
      tell("These settings are active in the detector.", "ok");
      if (dialog.open) requestPreview();
    } else if (message.type === "vision_suggestion") {
      configToControls(message.config);
      if (message.roi && frozen.width) {
        selection = [message.roi[0] * frame.width, message.roi[1] * frame.height,
          message.roi[2] * frame.width, message.roi[3] * frame.height];
        redraw();
      }
      preview.src = `data:image/jpeg;base64,${message.preview_jpeg}`;
      tell("Suggested ranges are ready. Check the highlighted pixels, then Apply.", "ok");
    } else if (message.type === "vision_preview") {
      preview.src = `data:image/jpeg;base64,${message.preview_jpeg}`;
      tell("Preview updated. Apply when the marker is isolated cleanly.", "ok");
    } else if (message.type === "vision_error") {
      tell(message.message, "bad");
    }
  });
  showProfiles();
})();
