# Drone-Competition-1

Platform for an autonomous search-and-rescue drone competition (secondary school division): students use block-based programming to make a DJI Tello patrol a caged arena, detect red circular "victim" markers with an onboard-video OpenCV pipeline, signal each find, and return to start.

## Requirements

- **Python 3.11+** — the only hard runtime requirement. Everything else (FastAPI, OpenCV, NumPy,
  djitellopy, etc.) is a pip package pulled in by the install step below.
- **Windows** with PowerShell — the commands in this README target `venv\Scripts\...`. The same
  packages work cross-platform (`source venv/bin/activate` instead of `venv\Scripts\Activate.ps1`
  on macOS/Linux), but this project is developed and tested on Windows.
- **No Node.js/npm install needed to run the app.** The block-coding UI's dependencies —
  [Blockly](comp1/frontend/vendor/blockly.min.js) and
  [three.js](comp1/frontend/vendor/three.module.min.js) (plus `three.core.min.js` and
  `OrbitControls.js`) — are vendored under `comp1/frontend/vendor/` and served as static files by
  the Python backend. There is no `npm install` step and no build/bundle step for the frontend.
- **Node.js** (any recent LTS) is only needed if you want to run the frontend serializer tests
  (`node --test tests\js\blocks.test.js`). They use Node's built-in `node:test` runner, so there
  are no npm packages to install there either.
- A **DJI Tello EDU** is only required for `--drone tello`; `--drone sim` (or the default
  `MockDrone`) needs no hardware.

## Installation

1. **Clone the repo** and `cd` into it.

2. **Create a virtual environment** (Python 3.11+ must already be on `PATH`):

   ```powershell
   python -m venv venv
   ```

3. **Install the package.** `-e .[dev]` installs `comp1` in editable mode plus the `dev` extras
   (pytest, httpx, PyInstaller) needed for testing and building:

   ```powershell
   venv\Scripts\pip install -e .[dev]
   ```

   This alone pulls in every backend dependency — FastAPI, Uvicorn, Pydantic, djitellopy,
   OpenCV, NumPy — there is nothing else to install for the Python side.

   > If `venv\Scripts\Activate.ps1` refuses to run in a fresh PowerShell (execution-policy
   > error), you don't need to activate it at all — every command in this README calls the
   > venv's `python`/`pip`/`pytest` binaries directly by path.

4. **The frontend needs no install step.** Blockly and three.js
   (`comp1/frontend/vendor/blockly.min.js`, `three.module.min.js`, `three.core.min.js`,
   `OrbitControls.js`) are already committed to the repo and are served as static files — skip
   straight past this step if you were expecting an `npm install`.

5. **(Optional) Install Node.js** if you want to run the frontend serializer tests. Any recent
   LTS works; no `npm install` is needed since the tests use Node's built-in `node:test` runner:

   ```powershell
   node --test tests\js\blocks.test.js
   ```

6. **Verify the install** by running the Python test suite:

   ```powershell
   venv\Scripts\pytest
   ```

   All tests are hardware-free (`djitellopy` is monkeypatched and `SimDrone` stands in for a
   real Tello), so this works offline with no drone or Wi-Fi connection.

7. **Run the app**:

   ```powershell
   venv\Scripts\python -m comp1
   ```

   This starts a local server on port 8765 and opens the block-coding UI in your browser, using
   the built-in simulator — no hardware required. See [Quick start](#quick-start) below for what
   to do next, and [`--drone tello`](#switching-between-the-simulator-and-tello) for flying real
   hardware.

## Quick start

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]

# start in the simulator (the default; no hardware needed)
venv\Scripts\python -m comp1

# The simulator has a randomly generated arena with victim markers and
# distractors. Use --seed N for a repeatable layout or --noise 0.05 for drift.
# To fly hardware, join the TELLO-xxxx WiFi and click "Use real Tello" in the UI.
# Once connected, the same button becomes "Use Simulator" so you can switch back.
```

The app starts a local server on port 8765 and opens the block-coding UI in your browser (`--no-browser` to skip, `--port` to change). Drag blocks under **🚁 when mission starts** and press **Run**; the currently executing block is highlighted, and the right-hand panel shows the drone camera with the red-circle detection overlay, plus a live readout of how far away the victim is and in which direction. The **Code inspector** below the blocks shows display-only Python, the validated JSON that actually runs, and the exact simulator or Tello adapter calls made during execution.

### Switching between the simulator and Tello

The header shows the active drone. From the simulator, click **Use real Tello** after joining the
`TELLO-xxxx` Wi-Fi network. The simulator remains active if the Tello connection fails. After a
successful connection, the button changes to **Use Simulator**; clicking it restores the same
simulator instance, including its seed, selected scenery, and edited victim layout.

Switching is available only while no mission is running. Before switching away from a real Tello,
land it safely: changing to the simulator changes where future programs are sent, but deliberately
does not issue an automatic landing or emergency command to the physical aircraft.

## Two ways to fly

Blocks and Python are peers — the same engine, the same sensing, the same emergency stop. Students who already know Python are not held back by the block interface:

```python
from comp1.api import Drone

drone = Drone()
drone.takeoff()
while not drone.sees_target():
    drone.turn_right(20)
print(drone.target())          # victim 204 cm away, -20 deg (left)
drone.approach_target()
drone.mark_found()
drone.land()
```

```powershell
venv\Scripts\python -m comp1 --script examples\02_search_and_mark.py
```

The script runs alongside the server, so the video feed, telemetry panel and **EMERGENCY STOP** button stay live while the student's own code flies the drone. See [`examples/`](examples/) for annotated starting points.

Run the tests with `venv\Scripts\pytest`. Build the standalone Windows bundle with `venv\Scripts\pyinstaller comp1.spec --noconfirm` (output in `dist\comp1\comp1.exe`).

## Documentation

- [Platform architecture — options, comparison, and decision](docs/architecture/platform-options.md): why the platform is a custom Blockly web frontend + local Python service (FastAPI + djitellopy + OpenCV) packaged with PyInstaller, which alternatives were rejected, and the open items still to confirm (Chromebook support, safe-distance judging threshold).
- [Architecture planning notes (2026-07-28)](docs/plans/2026-07-28-platform-architecture.md)
- [Implementation plan (2026-07-28)](docs/plans/2026-07-28-implementation-plan.md)
- [Simulator design (2026-07-28)](docs/specs/2026-07-28-simulator-design.md) — the hardware-free
  random arena; partly superseded by the sensing work below.
- [Target sensing, expression blocks, and the A→B competition (2026-07-31)](docs/plans/2026-07-31-target-sensing-and-expressions.md):
  the roadmap for exposing distance/direction to students, giving the block language real
  expressions, adding a Python pathway, and scoring a point A → point B search-and-rescue run.
  Phase 1 is implemented; includes an assessment of what still needs hardware validation.
- [Target sensing design (2026-07-31)](docs/specs/2026-07-31-target-sensing-design.md): the camera
  model, how pixels become metres, multi-target tracking, and the tuning constants — read this
  before changing anything in `comp1/vision/`.
- [Program schema v2 (2026-07-31)](docs/specs/2026-07-31-program-schema-v2.md): the block program
  wire format — value expressions, sensors, runtime clamping, and the v1 compatibility path. The
  contract shared by `comp1/protocol.py` and `comp1/frontend/blocks.js`.
- [Python pathway (2026-07-31)](docs/specs/2026-07-31-python-pathway.md): the student-facing
  `comp1.api` surface, how `--script` runs alongside the server, and the stop mechanism.
- [Runtime drone switching (2026-08-03)](docs/specs/2026-08-03-drone-mode-switching.md): the
  reversible Simulator/Tello UI, WebSocket messages, retained simulator state, and safety rules.

`CLAUDE.md` holds the working notes for AI coding assistants (architecture invariants, the
four-places rule for adding a block, and the project constraints that are easy to violate).
