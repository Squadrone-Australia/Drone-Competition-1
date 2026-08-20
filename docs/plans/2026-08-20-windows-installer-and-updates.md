# Windows installer and in-app updates

**Status:** Implemented 2026-08-20 · **Depends on:** the PyInstaller onedir decision in
[../architecture/platform-options.md](../architecture/platform-options.md)

## The problem

Until now the documented way onto a student laptop was: install Python 3.11+, remember to tick
"Add python.exe to PATH", clone or unzip the project, double-click `start.bat`, and wait while pip
built a venv. Two things were wrong with that.

**Python was visible.** A missed PATH tick, a school that blocks PyPI, or an existing Python 3.9
all break setup before the student has seen a block. None of it is anything to do with flying a
drone.

**There was no version, and therefore no updates.** Nothing in the codebase recorded a version
number, so nobody could say what a given laptop was running. `start.bat` re-ran
`pip install -e .` on every launch, which updates dependencies but not the program — that needed
somebody to re-download the source first.

## The shape of the answer

An Inno Setup installer wrapping the PyInstaller onedir build, plus a startup check against
GitHub Releases that offers a one-click upgrade.

```text
comp1/__init__.py  __version__      ← the one place the version lives
      ↓ read by
comp1.spec (VERSIONINFO) · build.ps1 (/DAppVersion) · update.check (compare)
```

### Version

`comp1/__init__.py` holds `__version__`. `pyproject.toml` reads it through setuptools'
`dynamic = ["version"]`; `comp1.spec` greps it into a Windows VERSIONINFO resource; `build.ps1`
passes it to Inno as `/DAppVersion` and into the installer's filename. Bumping the version is one
edit.

### Paths

[`comp1/paths.py`](../../comp1/paths.py) draws the line the installer makes necessary:
`app_dir()` is where the program lives and is **replaced wholesale by the next update**;
`data_dir()` (`%LOCALAPPDATA%\comp1`) is where the user's things live and survives both an upgrade
and an uninstall. Settings, logs and downloaded installers all go in the latter. Nothing in the
module reads or writes on import.

### Settings

[`comp1/settings.py`](../../comp1/settings.py) is a small JSON store for what the CLI flags used to
be the only way to express: which drone and scenery to start in, whether to check for updates, and
the last applied HSV bands.

Two rules:

- **Nothing raises.** A corrupt or hand-edited file falls back to defaults field by field, the same
  discipline the interpreter applies to runtime warnings.
- **An explicit flag always wins.** Every affected argparse argument defaults to `None`, which is
  the "not passed" sentinel `settings.resolve` keys off. A shortcut carrying `--drone tello` must
  not be overridden by whatever the last browser session saved.

`create_app(settings_path=...)` is opt-in, so the test suite and a developer run never write to
anybody's profile.

The one behavioural change this buys beyond convenience: **an applied venue calibration now
persists** (§3.1). It used to live only in the running process, so a re-tune was lost the moment
someone closed the program.

### Update check

[`comp1/update.py`](../../comp1/update.py), built around one rule: **every failure is silence.**
Offline, blocked DNS, a school proxy, a captive portal, GitHub rate-limiting, unparseable JSON —
`check()` returns `None` and the browser shows nothing. At a competition the laptop is joined to
`TELLO-xxxx`, which routes nowhere, and that is the *normal* case rather than an error.

There is deliberately no "you are up to date" message: it would be a lie every time the network
was simply missing.

The installer must be published with a `SHA256SUMS.txt` asset. A release without one is not
offered at all — downloading an executable over the internet and running it silently is not
something to do on an unverified digest. `download()` only moves the file into place after the
digest matches.

The swap itself is Inno's job, not this process's: a running program cannot replace its own
directory on Windows. That is the concrete reason the installer beat a portable folder.

**`/CLOSEAPPLICATIONS` alone does not work here, and finding that out took a real upgrade.** The
Restart Manager closes a GUI application by posting to its top-level window, and this program —
windowed precisely so no console appears — has no window at all. Setup logs *"some applications
could not be shut down"*, and with message boxes suppressed defaults to Abort and rolls back:

```text
Shutting down applications using our files.
Some applications could not be shut down.
Defaulting to Abort for suppressed message box (Abort/Retry/Ignore)
User canceled the installation process.
```

`/FORCECLOSEAPPLICATIONS` makes the Restart Manager terminate the process instead, which is safe
exactly because `install_update` refuses to run while anything is flying, and because
`_install_update` releases the drone *before* handing over rather than after. A force-closed
application is not one the Restart Manager will bring back, though, so the relaunch is explicit:
`/RELAUNCH` is our own switch, read by the `RelaunchRequested` check in `comp1.iss`, which starts
the program again from a `[Run]` entry.

`install_update` is refused while `app.state.interp` is set. The calibration guard was copied for
a much better reason: the installer's first act is to close this process, and doing that to a drone
in the air leaves it hovering with nothing flying it.

### Closing the program

Hiding Python took the quit affordance with it. `console=True` had one by accident — the black
window was both "this is running" and "close me", and the README said so. A windowed build has
neither, and closing the browser tab did nothing: the client was dropped from the broadcast set and
every loop carried on, invisibly, holding the aircraft's video port until the laptop was rebooted.

The browser page is the only window the program has, so it owns the program's lifetime:

- **Close program** in the app bar sends `quit`; the server broadcasts `quitting` so every open tab
  can say what happened rather than sitting on "disconnected, retrying", and then stops the uvicorn
  server. Refused mid-mission, like the update.
- `_idle_loop` closes the program once every window has been shut for `idle_timeout` (30 s). It
  counts down rather than firing at once — a refresh, a laptop waking, a tab dragged to another
  window all disconnect briefly and none of them means "finished" — and it never fires before the
  first client has *ever* connected, because the server comes up a second ahead of the browser it
  launched.

Both hang off `create_app(shutdown=...)`. With no hook the browser hides the button instead of
offering one that cannot work, which is right for `--no-browser` and for a terminal run where
Ctrl+C is the answer. `--idle-timeout N` overrides it, and `0` turns it off for a demonstration
laptop meant to sit on a stand all day.

This is why `main()` builds `uvicorn.Server` itself rather than calling `uvicorn.run`: the app needs
a handle on the serve loop to end it. Tests must patch `uvicorn.Server`, not `uvicorn.run`.

A second launch is now told, not left to die on the port bind with nothing on screen —
`_already_serving` checks first and shows a message box naming the address and how to close the
copy that is already there.

### Packaging

- `console=False` in [`comp1.spec`](../../comp1.spec) — hiding Python is the point. It comes with
  two obligations, both implemented in `comp1/__main__.py`: rotating file logging to
  `data_dir()/logs`, and a `MessageBoxW` if startup throws. A windowed exe that dies silently on a
  double-click is worse than the console window it replaced. `uvicorn.run(log_config=None)` when
  frozen for the same reason — the default config installs a `StreamHandler` on a `stdout` that is
  `None`, and `api.Drone._warn` guards its `print` likewise.
- Per-user install (`PrivilegesRequired=lowest`, `{localappdata}\Programs\comp1`). School laptops
  rarely grant admin rights, and asking for them turns an installer into a support ticket.
- A **fixed `AppId` GUID**. It is how Windows knows 0.2 replaces 0.1 instead of installing beside
  it; changing it strands every existing install and would leave the updater putting two copies on
  the machine.
- `%LOCALAPPDATA%\comp1` is deliberately *not* removed on uninstall — a venue's calibration is
  worth more than a tidy uninstall.

## Known limits

- **The installer is unsigned.** SmartScreen shows "Windows protected your PC", and a
  centrally-managed school laptop may refuse it outright — which would be a worse barrier than the
  Python prompt this replaces. A code-signing certificate is the only real fix; the published
  checksum is the interim answer. Worth deciding before the first public release.
- **~70 MB to download, ~250 MB installed** — mostly OpenCV and NumPy, and an update re-downloads
  the lot. Fine for an occasional upgrade; do not build anything that updates more often than a
  person asks it to.
- **`api.github.com` is blocked on some school networks.** By design this degrades to "no updates
  offered", not to an error.
- Windows only. macOS and Linux keep the source path (`pip install -e .`), which is unchanged.

## Verification

Built and installed for real on 2026-08-20 with Inno Setup 7: `build.ps1` produced a 70 MB
`comp1-Setup-0.1.0.exe`, which installed per-user without an admin prompt, created the Start Menu
and desktop shortcuts, and served the UI from `%LOCALAPPDATA%\Programs\comp1`. A silent in-place
upgrade over the *running* app was then driven with the updater's own switches — that is the test
that produced the `/FORCECLOSEAPPLICATIONS` finding above. With the fix in place the upgrade ran
clean: registered version 0.1.0 → 0.1.2, the old process gone, a new one started by the `/RELAUNCH`
`[Run]` entry and serving on the default port. **Give the relaunch time** — the new process imports
OpenCV before it binds, so a check made 12 s after Setup exits will report "not running" and be
wrong.

The lifecycle was checked on the packaged build too: the **Close program** button stopped it in
about a second; with a browser connected and then closed it shut itself down after the idle
timeout and released the port; with no browser ever connecting it stayed up; and a second launch
reported "already running" and left the first one untouched.

`tests/test_settings.py`, `tests/test_update.py`, `tests/test_update_flow.py` and
`tests/test_lifecycle.py` cover the store, the version maths, every silent-failure path, checksum
rejection, the mission guards, the banner's replay to a late-connecting browser, and each rule the
idle shutdown has to respect. No test touches the network or the user's profile: the settings path,
the update check and the shutdown hook are all injected through `create_app`.

What the suite cannot cover, and must be done by hand before a release: install on a clean Windows
profile with no Python, run it offline, and drive one real upgrade from an older installed version
to a newer published release.
