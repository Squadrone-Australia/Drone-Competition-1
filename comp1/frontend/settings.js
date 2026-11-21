/**
 * Startup preferences and the update banner.
 *
 * Packaged as an installed program there is no command line to type flags into,
 * so the choices `--drone`, `--scenery` and friends express have to be
 * reachable from here. These are *preferences*, not live controls: nothing in
 * this file touches the drone that is currently connected. Switching drone or
 * arena mid-session already has its own buttons, and quietly reconnecting
 * hardware from a settings panel would be a surprise nobody asked for — so the
 * dialog says "next time the program starts" and means it.
 *
 * The banner is the whole update story from the browser's side: the server
 * says `update_available` or it does not. Offline — which is the normal state
 * at a venue, joined to the aircraft's own Wi-Fi — it never arrives and nothing
 * is shown. There is deliberately no "you are up to date" message: it would be
 * a lie every time the network was simply missing.
 */
(() => {
  const banner = document.getElementById("update-banner");
  const bannerText = document.getElementById("update-text");
  const bannerNotes = document.getElementById("update-notes");
  const installBtn = document.getElementById("update-install");
  const laterBtn = document.getElementById("update-later");
  const openBtn = document.getElementById("settings-open");
  const dialog = document.getElementById("settings-dialog");
  const droneEl = document.getElementById("settings-drone");
  const sceneryEl = document.getElementById("settings-scenery");
  const updatesEl = document.getElementById("settings-updates");
  const versionEl = document.getElementById("settings-version");
  const noteEl = document.getElementById("settings-note");

  let running = false;
  let release = null;

  /**
   * Rebuild the scenery list from the server's catalogue.
   *
   * The names live in comp1/sim/scenery.py and nowhere else. This dialog used
   * to carry its own copy in index.html, which quietly went stale: it still
   * offered "Corridor (A to B)" after that scenery had been replaced by the
   * fixed competition arena, so the menu named a room the simulator would not
   * build. Rendering from the message keeps one source of truth.
   */
  function setSceneries(list, selected) {
    if (!Array.isArray(list) || !list.length) return;
    sceneryEl.replaceChildren();
    for (const s of list) {
      const opt = document.createElement("option");
      opt.value = s.id;
      opt.textContent = s.name;
      opt.title = s.description || "";
      sceneryEl.appendChild(opt);
    }
    // Only after the options exist: assigning .value to a <select> that has no
    // matching <option> silently leaves it blank.
    if (selected) sceneryEl.value = selected;
  }

  function showSettings(msg) {
    versionEl.textContent = `Version ${msg.version}`;
    droneEl.value = msg.settings.drone;
    setSceneries(msg.sceneries, msg.settings.scenery);
    updatesEl.checked = msg.settings.check_updates !== false;
    // A source checkout has nowhere to save to and nothing to install, so say
    // so rather than offering controls that quietly do nothing.
    noteEl.textContent = msg.persisted
      ? "Saved on this computer. Takes effect the next time the program starts."
      : "Running from a source checkout — these choices are not saved. "
        + "Use the command-line flags instead.";
    droneEl.disabled = sceneryEl.disabled = updatesEl.disabled = !msg.persisted;
  }

  function send() {
    window.COMP1_SEND({
      type: "save_settings",
      settings: {
        drone: droneEl.value,
        scenery: sceneryEl.value,
        check_updates: updatesEl.checked,
      },
    });
  }

  function showUpdate(msg) {
    release = msg;
    bannerText.textContent =
      `Version ${msg.version} is available — you have ${msg.current}.`;
    bannerNotes.textContent = msg.notes || "";
    bannerNotes.hidden = !msg.notes;
    banner.hidden = false;
    installBtn.disabled = running;
  }

  function showProgress(msg) {
    if (msg.state === "downloading") {
      installBtn.disabled = true;
      bannerText.textContent =
        `Downloading version ${msg.version}… this can take a few minutes.`;
      bannerNotes.hidden = true;
    } else if (msg.state === "installing") {
      bannerText.textContent =
        "Installing. The program will close and reopen by itself.";
    } else if (msg.state === "failed") {
      installBtn.disabled = false;
      bannerText.textContent = `Update failed: ${msg.message}`;
    }
  }

  // --- Interface size -------------------------------------------------------
  //
  // Page zoom, and not a startup preference like everything above: it applies
  // the moment it is picked. It is real browser zoom on the native window's
  // WebView2 control, asked for through `window.pywebview.api.set_zoom`
  // (comp1/window.py). CSS `zoom` was tried first and is not usable here:
  // Blockly 13 drags a block further than the mouse moves under it, and the
  // floor plan, the camera window and the tutorial spotlight all mis-place
  // themselves the same way.
  //
  // Kept in this window's localStorage rather than settings.json: it is a fact
  // about this screen, the server has no use for it, and the native window's
  // storage lives under data_dir(), so it survives an update. In an ordinary
  // browser tab there is no bridge — and no need for one, because the browser's
  // own Ctrl + and Ctrl − already do this — so the control says that instead.
  const sizeEl = document.getElementById("settings-size");
  const sizeNoteEl = document.getElementById("settings-size-note");
  const SIZE_KEY = "comp1.uiZoom.v1";
  const SIZES = [...sizeEl.options].map((o) => Number(o.value));

  function storage() {
    // Touching localStorage can itself throw (denied site data). A size that
    // cannot be remembered is a size that resets, never a broken dialog.
    try { return window.localStorage || null; } catch (_error) { return null; }
  }

  function storedSize() {
    try {
      const value = Number(storage()?.getItem(SIZE_KEY));
      return SIZES.includes(value) ? value : null;
    } catch (_error) {
      return null;
    }
  }

  function zoomApi() {
    const api = window.pywebview && window.pywebview.api;
    return api && typeof api.set_zoom === "function" ? api : null;
  }

  async function applySize(value) {
    const api = zoomApi();
    if (!api) return false;
    try { return (await api.set_zoom(value)) === true; } catch (_error) { return false; }
  }

  function showSizeControl() {
    const native = Boolean(zoomApi());
    sizeEl.disabled = !native;
    sizeEl.value = String(storedSize() ?? 1);
    sizeNoteEl.textContent = native
      ? "Changes right away, on this computer. Ctrl + mouse wheel works too, "
        + "outside the blocks (over them it zooms just the blocks)."
      : "In a web browser, use Ctrl + and Ctrl − to make everything bigger or smaller.";
  }

  sizeEl.onchange = async () => {
    const value = Number(sizeEl.value);
    try { storage()?.setItem(SIZE_KEY, String(value)); } catch (_error) { /* resets next time */ }
    if (!(await applySize(value))) sizeNoteEl.textContent = "Could not change the size of this window.";
  };

  // The bridge arrives after the page has loaded, announced by an event. Only a
  // size the student actually chose is applied: with nothing stored, a zoom
  // they set with Ctrl+wheel is left alone.
  function onBridge() {
    showSizeControl();
    const size = storedSize();
    if (size !== null) applySize(size);
  }
  showSizeControl();
  if (zoomApi()) onBridge();
  else window.addEventListener("pywebviewready", onBridge, { once: true });

  openBtn.onclick = () => dialog.showModal();
  document.getElementById("settings-close").onclick = () => dialog.close();
  document.getElementById("settings-save").onclick = () => { send(); dialog.close(); };
  laterBtn.onclick = () => { banner.hidden = true; };
  installBtn.onclick = () => {
    if (!release) return;
    // Spelled out because the installer really does close the program: a
    // student who clicks this mid-lesson should know the window will vanish.
    const ok = window.confirm(
      `Update to version ${release.version}?\n\n`
      + "The program will close, install the new version, and reopen. "
      + "Make sure the drone is landed first.");
    if (ok) window.COMP1_SEND({ type: "install_update" });
  };

  window.COMP1_BUS.on((message) => {
    if (message.type === "settings") showSettings(message);
    else if (message.type === "update_available") showUpdate(message);
    else if (message.type === "update_progress") showProgress(message);
    else if (message.type === "running") {
      running = message.running;
      // Never offer to close the program out from under a flying drone. The
      // server refuses it too; a button that only greys out after the refusal
      // is a button that looks broken.
      installBtn.disabled = running || !release;
      if (running && dialog.open) dialog.close();
    }
  });
})();
