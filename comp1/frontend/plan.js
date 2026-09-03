/**
 * Arena panel: pick a scenery, and lay out the targets by hand.
 *
 * A flat floor plan of the same room the 3D stage draws, fed by the same
 * `{"type":"scene"}` and `{"type":"pose"}` messages. Clicking empty floor adds a
 * target, clicking one removes it; the edit goes to the server as
 * `{"type":"layout"}` and the canvas is redrawn from the `scene` that comes
 * back — never from what we asked for, because the server re-checks the spacing
 * rules and may refuse a point.
 *
 * This is authoring, not sensing. The coordinates here are the same one-way
 * feed the 3D view runs on: they never reach a block or the interpreter
 * (requirements §4).
 */
(() => {
  const panel = document.getElementById("arena-panel");
  const canvas = document.getElementById("arena-plan");
  const picker = document.getElementById("scenery");
  const editBtn = document.getElementById("plan-edit");
  const noteEl = document.getElementById("plan-note");
  const missionEl = document.getElementById("mission");
  const ctx = canvas.getContext("2d");

  const COL = {
    floor: "#16233c", grid: "#24354f", wall: "#7f9bd4",
    fire: "#dc2626", destination: "#22c55e", other: "#64748b",
    start: "#38bdf8", drone: "#f7941d",
  };
  const HIT_M = 0.35;          // click tolerance when picking a marker up

  let scene = null;
  let pose = null;
  let editing = false;
  let running = false;
  let view = null;             // {x0, y0, s} — plan pixels per metre
  // The server is the judge of whether a point is legal, so a rejected click is
  // only visible as "the scene came back with fewer targets than I asked for".
  let pending = null;

  // --- geometry ------------------------------------------------------------

  /** Fit a `w` x `d` room into the canvas, preserving its aspect. */
  function fit(w, d, cw, ch, pad) {
    const s = Math.min((cw - 2 * pad) / w, (ch - 2 * pad) / d);
    return { s, x0: (cw - w * s) / 2, y0: (ch - d * s) / 2 };
  }

  // y grows north but canvas rows grow down, so the plan is flipped vertically —
  // same convention as the camera minimap, which students see side by side.
  const toPx = (x, y) => [view.x0 + x * view.s, view.y0 + (scene.depth_m - y) * view.s];
  const toWorld = (px, py) =>
    [(px - view.x0) / view.s, scene.depth_m - (py - view.y0) / view.s];

  // --- drawing -------------------------------------------------------------

  function draw() {
    const cw = canvas.clientWidth, ch = canvas.clientHeight;
    if (!cw || !ch) return;
    const dpr = Math.min(devicePixelRatio, 2);
    if (canvas.width !== cw * dpr || canvas.height !== ch * dpr) {
      canvas.width = cw * dpr;
      canvas.height = ch * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cw, ch);
    if (!scene) return;

    const w = scene.width_m, d = scene.depth_m;
    view = fit(w, d, cw, ch, 10);
    const [rx, ry] = toPx(0, d);
    ctx.fillStyle = COL.floor;
    ctx.fillRect(rx, ry, w * view.s, d * view.s);

    ctx.strokeStyle = COL.grid;
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x <= w + 1e-6; x += 0.5) {
      const [px] = toPx(x, 0);
      ctx.moveTo(px, ry); ctx.lineTo(px, ry + d * view.s);
    }
    for (let y = 0; y <= d + 1e-6; y += 0.5) {
      const [, py] = toPx(0, y);
      ctx.moveTo(rx, py); ctx.lineTo(rx + w * view.s, py);
    }
    ctx.stroke();

    ctx.strokeStyle = COL.wall;
    ctx.lineWidth = 2;
    ctx.strokeRect(rx, ry, w * view.s, d * view.s);

    if (scene.start) {
      const [px, py] = toPx(scene.start[0], scene.start[1]);
      ctx.strokeStyle = COL.start;
      ctx.lineWidth = 2;
      ctx.beginPath(); ctx.arc(px, py, 9, 0, 2 * Math.PI); ctx.stroke();
      label("S", px, py + 20, COL.start);
    }

    for (const m of scene.markers) {
      const [px, py] = toPx(m.x, m.y);
      if (m.kind === "destination") {
        ctx.fillStyle = COL.destination;
        ctx.fillRect(px - 6, py - 6, 12, 12);
        label("D", px, py + 20, COL.destination);
      } else {
        ctx.fillStyle = m.kind === "fire" ? COL.fire : COL.other;
        ctx.beginPath(); ctx.arc(px, py, m.kind === "fire" ? 6 : 4, 0, 2 * Math.PI);
        ctx.fill();
      }
    }

    if (pose) drawDrone();
  }

  function drawDrone() {
    const [px, py] = toPx(pose.x, pose.y);
    const h = (pose.heading * Math.PI) / 180;
    ctx.strokeStyle = ctx.fillStyle = COL.drone;
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.arc(px, py, 4, 0, 2 * Math.PI); ctx.fill();
    ctx.beginPath();
    ctx.moveTo(px, py);
    ctx.lineTo(px + 13 * Math.sin(h), py - 13 * Math.cos(h));
    ctx.stroke();
  }

  function label(text, px, py, color) {
    ctx.fillStyle = color;
    ctx.font = "10px system-ui, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText(text, px, py);
  }

  // --- editing -------------------------------------------------------------

  function send(msg) {
    if (window.COMP1_SEND) window.COMP1_SEND(msg);
  }

  function fireNear(x, y) {
    return scene.markers.findIndex(
      (m) => m.kind === "fire" && Math.hypot(m.x - x, m.y - y) <= HIT_M);
  }

  function onClick(ev) {
    if (!editing || !scene || !view || running) return;
    const r = canvas.getBoundingClientRect();
    const [x, y] = toWorld(ev.clientX - r.left, ev.clientY - r.top);
    const fires = scene.markers.filter((m) => m.kind === "fire")
      .map((m) => ({ x: m.x, y: m.y }));
    const hit = fireNear(x, y);
    if (hit >= 0) {
      const m = scene.markers[hit];
      send({ type: "layout",
             fires: fires.filter((f) => f.x !== m.x || f.y !== m.y) });
      note("");
    } else {
      const wanted = fires.length + 1;
      send({ type: "layout", fires: [...fires, { x, y }] });
      pending = wanted;
    }
  }

  function note(text) {
    noteEl.textContent = text;
  }

  // --- messages ------------------------------------------------------------

  function setScene(desc) {
    scene = desc && {
      ...desc,
      width_m: desc.width_m ?? desc.size_m,
      depth_m: desc.depth_m ?? desc.size_m,
    };
    if (scene && pending !== null) {
      const n = scene.markers.filter((m) => m.kind === "fire").length;
      note(n < pending ? "⚠ too close to a wall, the start pad or another marker" : "");
      pending = null;
    }
    draw();
  }

  function setSceneries(msg) {
    const list = msg.sceneries;
    panel.hidden = !list;
    if (!list) return;
    picker.replaceChildren();
    for (const s of list) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      opt.title = s.description || "";
      picker.appendChild(opt);
    }
    if (msg.current) picker.value = msg.current;
  }

  function setMission(m) {
    const bits = [`targets ${m.found}/${m.total}`];
    if (m.needs_destination) {
      const place = m.goal === "start" ? "start" : "destination";
      bits.push(m.at_destination ? `at ${place}` : `→ ${place}`);
    }
    missionEl.textContent = m.state === "success"
      ? `🏆 mission success: ${bits.join(", ")}`
      : bits.join(" · ");
    missionEl.className = m.state === "success" ? "success" : "";
  }

  window.COMP1_BUS.on((msg) => {
    if (msg.type === "scene") setScene(msg.scene);
    else if (msg.type === "sceneries") setSceneries(msg);
    else if (msg.type === "pose") { pose = msg; draw(); }
    else if (msg.type === "mission") setMission(msg);
    else if (msg.type === "reset") { pose = null; missionEl.className = ""; }
    else if (msg.type === "running") { running = msg.running; refreshControls(); }
  });

  // --- controls ------------------------------------------------------------

  function refreshControls() {
    editBtn.disabled = running;
    picker.disabled = running;
    document.getElementById("plan-randomise").disabled = running;
    document.getElementById("plan-clear").disabled = running;
    if (running && editing) setEditing(false);
    canvas.classList.toggle("editing", editing);
  }

  function setEditing(on) {
    editing = on;
    editBtn.classList.toggle("on", on);
    note(on ? "click the plan to add a target, click a target to remove it" : "");
    refreshControls();
  }

  editBtn.onclick = () => setEditing(!editing);
  picker.onchange = () => send({ type: "scenery", name: picker.value });
  document.getElementById("plan-randomise").onclick =
    () => send({ type: "scenery", name: picker.value, randomise: true });
  document.getElementById("plan-clear").onclick =
    () => send({ type: "layout", fires: [] });
  canvas.addEventListener("click", onClick);
  new ResizeObserver(draw).observe(canvas);
})();
