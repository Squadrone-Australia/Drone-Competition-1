# Drone-Competition-1

Platform for an autonomous search-and-rescue drone competition (secondary school division): students use block-based programming to make a DJI Tello patrol a caged arena, detect red circular "victim" markers with an onboard-video OpenCV pipeline, signal each find, and return to start.

## Quick start

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]

# start in the simulator (the default; no hardware needed)
venv\Scripts\python -m comp1

# The simulator has a randomly generated arena with victim markers and
# distractors. Use --seed N for a repeatable layout or --noise 0.05 for drift.
# To fly hardware, join the TELLO-xxxx WiFi and click "Use real Tello" in the UI.
```

The app starts a local server on port 8765 and opens the block-coding UI in your browser (`--no-browser` to skip, `--port` to change). Drag blocks under **🚁 when mission starts** and press **Run**; the currently executing block is highlighted, and the right-hand panel shows the drone camera with the red-circle detection overlay, plus a live readout of how far away the victim is and in which direction. Open **Debug: translation and execution** to inspect the validated JSON program and the exact simulator or Tello adapter calls made while it runs.

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

`CLAUDE.md` holds the working notes for AI coding assistants (architecture invariants, the
four-places rule for adding a block, and the project constraints that are easy to violate).
