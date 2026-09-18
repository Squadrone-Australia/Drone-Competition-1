# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Squadrone "Drone Coder": a local FastAPI server plus a vendored-Blockly web UI that lets
students fly a DJI Tello — or a built-in simulator — by dragging blocks. It ships as an
unsigned Windows installer and students never see a terminal. Setup and user-facing docs
are in [README.md](README.md); this file is the developer's shortcut.

## Commands

```bash
python -m pytest -q                        # 561 tests, ~60s
python -m pytest tests/test_fault_recovery.py -q
python -m pytest tests/test_interpreter.py::test_stop_flag_halts_and_lands -q  # one test
python -m pytest -q -k "estop or switch"   # one theme, across files
python -m ruff check .                     # the exact lint gate CI runs
python -m pytest tests/test_update.py tests/test_update_release.py -q  # CI's updater gate
python -m ruff check . --fix               # imports, unused names
node --test tests/js                       # 71 tests; needs `npm ci` once, for jsdom
node --test tests/js/blocks.test.js        # one file
python -m comp1                            # simulator on http://localhost:8765
python -m comp1 --drone sim --seed 42 --no-browser
```

Prefix with `venv\Scripts\` on a Windows checkout built by `start.ps1`. A checkout carrying
a `.conda/` env instead has that interpreter first on PATH already, so the bare commands
above work as written.

Never `pip install -r requirements.txt` — it is a UTF-16 `pip freeze` taken on Windows and
carries Windows-only pins, so it does not install on Linux or macOS. Use
`pip install -e .[dev]`, which reads `pyproject.toml`.

The Windows shipping path (`start.ps1`, `build.ps1`, PyInstaller, Inno Setup) is covered in
the README.

## Layout

- `comp1/server.py` (~1.4k lines) — the whole app. `create_app()` holds the WebSocket
  endpoint and every background loop (video, pose, link watchdog, battery, updates, idle
  shutdown) as closures over `app.state`. Start here.
- `comp1/interpreter.py` — runs a parsed block program against a `DroneAdapter`.
- `comp1/protocol.py` — pydantic models for the block-program wire format.
- `comp1/api.py` — the student-facing `Drone` class (the Python pathway, `--script`).
- `comp1/drone/` — `base.DroneAdapter` ABC plus `tello`/`mock`; `config.FlightConfig`.
- `comp1/sim/` — the hardware-free simulator: world, scene, scenery, render, mission scoring.
- `comp1/vision/` — HSV marker detection, distance/bearing estimation, obstacles, auto-calibration.
- `comp1/frontend/` — plain JS with Blockly and three.js vendored under `vendor/`. No build step.
  `buffer.js` auto-saves the Blockly workspace to `localStorage` and restores it as
  `app.js` injects. Deliberately client-side: no socket message, nothing in
  `paths.data_dir()`, and losing a buffer is never allowed to break the page.
- `comp1/window.py` — the pywebview front door. `usable()` proves a window is possible or the
  launcher opens the system browser instead; `NativeWindow` owns the main thread and the
  two-way close protocol. Knows nothing about drones: close *policy* is `request_close` in
  `__main__.py`, where `app.state` is.
- `comp1/paths.py`, `settings.py`, `update.py` — install-time concerns.
- `docs/specs/` and `docs/plans/` — read the matching spec before changing vision, the
  protocol, or drone switching. `docs/ISSUES.md` is the 2026-09-14 fault-injection report.

## How it fits together

The HTTP surface is two things: `StaticFiles` mounted at `/`, and a single WebSocket at
`/ws`. There is no REST API — every runtime interaction is a message on that socket, so
tracing a feature means finding its `msg["type"]` branch in the receive loop
(`run`, `stop`, `estop`, `reset`, `scenery`, `vision_*`, `switch_drone`, `reconnect_drone`,
`save_settings`, `install_update`, `quit`) and the matching `ws.send`/`onmessage` in
`comp1/frontend/app.js`. The server pushes the other way by fanning out over
`app.state.clients`: `_broadcast_json` for telemetry, pose, link, battery, mission and
settings; `_broadcast_bytes` for JPEG video frames.

The one exception to "every interaction is a socket message" is `app.state.request_quit`,
set up in the lifespan: the native window's close button fires on a GUI thread outside the
event loop, so it needs a thread-safe door in. It lands a flying mission before quitting,
where the `quit` message refuses one — a button may be refused, a window's X may not.

`app.state` is the single shared mutable world — the live adapter, the latest frame and
`Detection`, the running interpreter, the mission scorer, every background task handle.
The background loops and the socket handler all reach it, and none of them own it, so read
what a loop already maintains rather than polling an adapter a second time.

Two front doors converge on one engine. Blocks arrive as JSON, are validated by
`protocol.Program`, and are walked by `interpreter.py`; a `--script` file runs as ordinary
Python against `api.Drone`. Both drive the same `DroneAdapter` and the same vision code —
`api.py` is not a reimplementation, and a behaviour change usually belongs in neither but
in the adapter or `vision/`.

`DroneAdapter` has three implementations, picked by `--drone`: `mock` (nothing flies),
`tello` (real hardware over `djitellopy`), `sim` (the `comp1/sim/` world, the default).
Switching between them at runtime is staged, not a swap — `_begin_switch` → `_activate` →
`_abandon_switch` on failure — because a half-connected Tello must never become the thing
a student's next block talks to. `docs/specs/2026-08-03-drone-mode-switching.md` is the
contract.

## Invariants

- **The version lives only in `comp1/__init__.py`.** pyproject, `build.ps1`, the installer
  and the updater all read it; the release workflow fails if tag `v<version>` disagrees.
- **A release needs both `comp1-Setup-<v>.exe` and `SHA256SUMS.txt`.** `comp1/update.py`
  looks the installer up by bare filename inside that file and refuses a digest mismatch,
  so a release missing it is one no installed copy can ever update to. That contract spans
  `update.py`, `build.ps1`, `installer/comp1.iss` and `release.yml` — including the
  `/RELAUNCH` switch the updater sends and `comp1.iss` reads back — and otherwise only ever
  meets during a real tagged build, so `tests/test_update_release.py` checks the seams as
  text. Change any one of those four and run it.
- **The block wire format is a two-sided contract.** `comp1/protocol.py` and
  `comp1/frontend/blocks.js` change together, per `docs/specs/2026-07-31-program-schema-v2.md`.
- **The stop flag is only read between blocks** (`interpreter.py`). Every adapter call must
  therefore stay bounded by `command_timeout_s`, and the e-stop path must never run inline
  in the WebSocket receive loop — an unanswered command would otherwise be a mission nobody
  can stop. `tests/test_fault_recovery.py` guards this.
- **Settings are defaults, never overrides.** `settings.resolve()` reads `None` as "flag not
  passed"; an explicit CLI flag always wins. Nothing in `settings.py` may raise — a corrupt
  file falls back to defaults rather than refusing to start on competition day.
- **Never write into the application directory.** An update replaces it wholesale; user
  state belongs in `paths.data_dir()` — including the native window's own profile
  (`paths.window_dir()`), which is where the block buffer's `localStorage` actually lives.
- **The window is a front door, never a dependency.** Every failure in `window.py` is a browser
  launch. `--no-window`, `--no-browser` and plain `http://localhost:8765` must keep working:
  they are the Chromebook hub deployment (`docs/architecture/platform-options.md` §4) and the
  only recovery path a stuck venue has.
- **Warn, don't raise, at runtime.** Out-of-range values are clamped with a warning to the
  student; a mission that dies mid-flight is worse than a nudged one.
- Ruff's version is pinned in both `.github/workflows/ci.yml` and pyproject's `dev` extra —
  bump the two together. Line length 100, rule sets `E,F,W,I,UP,B`.

## Conventions

Comments explain *why*, often at length, and that is deliberate — match the surrounding
density rather than trimming it. Module docstrings carry the design rules (`settings.py`
and `paths.py` are the clearest examples). Student-facing strings are plain language.

## Git

Feature work branches off `dev`, the integration branch. `main` trails it — 24 commits
behind as of 2026-09-16 — so confirm the base before opening a PR.
