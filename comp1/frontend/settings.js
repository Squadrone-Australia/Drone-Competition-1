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

  function showSettings(msg) {
    versionEl.textContent = `Version ${msg.version}`;
    droneEl.value = msg.settings.drone;
    sceneryEl.value = msg.settings.scenery;
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
