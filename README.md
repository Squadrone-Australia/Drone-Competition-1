# Drone Competition — Search & Rescue

This is a program that lets students fly a small drone (a DJI Tello) and teach it to search a
room, find red targets, and signal each one it finds — by dragging together colourful
puzzle-piece blocks, like Scratch. No coding experience is needed to get started, and there's a
built-in flight simulator so students can practice and compete without ever touching a real drone.

This page is written for anyone setting the program up for the first time — teachers, students,
or helpers — with no assumption you've used a terminal or installed developer tools before.

---

## What you'll end up with

A local website that opens in your normal web browser (Chrome, Edge, Firefox). It shows:

- A **block-coding area** where students drag blocks together to build a flight plan.
- A **live camera view** — either a simulated drone flying around a virtual room, or the video
  feed from a real Tello drone.
- A **Run** button and a big red **EMERGENCY STOP** button.

Nothing is installed onto the internet or "in the cloud" — everything runs on the one computer,
and nobody outside the room can see or reach it.

---

## Before you start

You need two things on the computer:

1. **Windows** (10 or 11). This guide is written for Windows; see the bottom of this page if
   you're on Mac or Linux.
2. **Python**, version 3.11 or newer. This is free software that the program runs on top of.

### Do you already have Python?

Open the **Start Menu**, type `cmd`, press Enter to open a black command-prompt window, then
type:

```text
python --version
```

- If you see something like `Python 3.12.4`, you're set — skip to [Setting up the
  program](#setting-up-the-program).
- If you see an error, or a version older than `3.11`, you need to install it (next step).

### Installing Python

1. Go to [python.org/downloads](https://www.python.org/downloads/) and download the latest
   version for Windows.
2. Run the installer. **Important:** on the very first screen, tick the box that says **"Add
   python.exe to PATH"** before clicking Install. If you miss this, the rest of this guide won't
   work and you'll need to reinstall.
3. Once it finishes, close and reopen the command-prompt window and run `python --version` again
   to confirm it worked.

---

## Setting up the program

You only need to do this once per computer.

1. Download this project onto the computer. If you were sent a `.zip` file, right-click it and
   choose **Extract All...**, then open the folder it creates. (If you're comfortable with `git`,
   `git clone` works too.)
2. Open that folder in File Explorer.
3. Double-click **`start.bat`**.

A black window will pop up and do some work automatically — this is normal, and the first time
can take a few minutes while it downloads what it needs. You'll see it:

- create a private Python environment just for this program (so it doesn't affect anything else
  on the computer),
- install everything the program depends on,
- then start the program itself and open your web browser to the block-coding screen.

**That's it — setup is done.** From now on, double-clicking `start.bat` again starts the program
in seconds (it skips the download step once everything's already installed).

> Leave the black window open while you use the program — closing it shuts the program down.
> When you're finished, just close that window.

---

## Using the program

When your browser opens, you'll see the block-coding screen. A few things to try:

- Drag blocks from the left-hand palette under **🚁 when mission starts** to build a flight
  plan — for example, take off, turn, move forward, check whether a marker is visible.
- Click **Run** to fly the plan. The block currently being run is highlighted so you can follow
  along.
- The right-hand panel shows what the drone "sees" through its camera, with the detected red
  marker circled, plus how far away it is and which direction to turn.
- The big **EMERGENCY STOP** button immediately halts the drone, no matter what it's doing.

By default the drone is a **simulator** — a virtual drone in a virtual room — so there's nothing
to break and no real hardware needed. This is the best way to build and test a flight plan before
trying it on a real Tello.

### Teaching the camera which red to recognise

Room lighting changes how red looks to a camera. Before using a real drone in a new room, click
**Tune colour** above the drone-camera picture. Capture a frame, drag a box inside the red marker,
and the program will suggest suitable settings. The preview should highlight the marker while the
floor, walls, and other objects stay dark. Click **Apply to detector** only after checking the
preview.

You do not need to understand OpenCV or choose numbers by hand. If you want to adjust the sliders,
the panel includes a plain-language explanation. The full [colour-calibration guide](docs/vision-calibration.md)
explains every control, gives a safe venue setup checklist, and shows what to change when detection
is unreliable.

### Flying a real Tello drone

1. Turn on the Tello and, on the computer, connect its Wi-Fi network — it will be named something
   like `TELLO-A1B2C3`.
2. In the browser window, click **Use real Tello** near the top.
3. Fly as normal — the same blocks, the same Run button, the same EMERGENCY STOP.
4. To switch back to practicing in the simulator, click **Use Simulator**. Land the real drone
   first — switching away from it doesn't automatically land it.

### Trying the example flight plans

The `examples\` folder has a few ready-made flight plans if you want to see something working
right away without building it from scratch — open the app, then use the **Load** option (or ask
whoever is running the session to load one for you).

---

## Troubleshooting

**"start.bat" flashes and closes immediately.**
Right-click `start.bat`, choose **Edit**, and check the file matches the version in this repo —
or open a command prompt in the project folder and run `start.bat` from there so any error message
stays on screen instead of disappearing.

**It says Python isn't recognized / isn't found.**
Python isn't on your PATH. Reinstall Python from
[python.org/downloads](https://www.python.org/downloads/) and make sure to tick **"Add python.exe
to PATH"** during install.

**Nothing opens in the browser.**
Give it a few extra seconds the first time. If it still doesn't open, manually go to
`http://localhost:8765` in your browser while the black window is still open.

**The browser can't reach the real Tello / video is choppy.**
Make sure the computer is connected to the Tello's own Wi-Fi network (not your normal home/school
Wi-Fi) — the Tello doesn't use the internet at all, it creates its own local network.

**I want to start fresh.**
Delete the `venv` folder inside the project folder and double-click `start.bat` again — it will
rebuild everything from scratch.

---

## For technically-inclined readers

The section above is intentionally simplified. If you're comfortable with a terminal and want more
control — manual setup, running tests, building a standalone `.exe`, developing on macOS/Linux, or
understanding the architecture — see below.

### What `start.bat` actually does

It's a small wrapper that runs `start.ps1`, a PowerShell script equivalent to:

```powershell
python -m venv venv                     # create an isolated environment
venv\Scripts\pip install -e .[dev]      # install dependencies
venv\Scripts\python -m comp1            # run the app
```

It's safe to re-run any time; it skips steps that are already done. Any extra arguments are
passed straight through to the app, e.g.:

```powershell
.\start.ps1 --drone sim --seed 42
.\start.ps1 --drone tello
```

### Manual setup (Windows)

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]
venv\Scripts\pytest                     # run the test suite
venv\Scripts\python -m comp1            # simulator (default)
venv\Scripts\python -m comp1 --drone sim        # explicit simulator
venv\Scripts\python -m comp1 --drone tello      # real Tello (join its WiFi first)
venv\Scripts\python -m comp1 --script examples\02_search_and_mark.py --drone sim   # Python pathway
venv\Scripts\pyinstaller comp1.spec --noconfirm # -> dist\comp1\comp1.exe
node --test tests\js\blocks.test.js             # frontend serializer tests
```

`--drone sim` flags: `--seed N` (repeatable arena), `--noise 0.05` (movement drift),
`--scenery {arena,corridor}` (also switchable in the browser). Server flags: `--port`,
`--no-browser`. Default port `8765`.

### Manual setup (Linux / macOS)

The project is developed and tested on Windows, but every dependency in `pyproject.toml` is a
pure cross-platform pip package (OpenCV, NumPy, FastAPI, etc. all ship Linux/macOS wheels):

```bash
python3 -m venv venv
source venv/bin/activate
pip install -e .[dev]
pytest
python -m comp1
```

Don't use `requirements.txt` on Linux/macOS — it's a `pip freeze` snapshot taken on Windows and
includes Windows-only packages (`pywin32-ctypes`, `colorama`) that PyInstaller pulls in on that
platform. `pip install -e .[dev]` reads `pyproject.toml` instead, which has no platform-specific
pins.

OpenCV's GUI/video dependencies occasionally need system libraries on minimal Linux images (e.g.
`libgl1` on Debian/Ubuntu); if `import cv2` fails, install that first. Flying real hardware needs
a Wi-Fi adapter that can join the `TELLO-xxxx` network — not guaranteed on headless servers or
containers.

### Requirements recap

- **Python 3.11+** — the only hard runtime requirement.
- **Windows** with PowerShell is the primary target; Linux/macOS work with the POSIX commands
  above.
- **No Node.js/npm needed to run the app** — Blockly and three.js are vendored under
  `comp1/frontend/vendor/` and served as static files. Node is only needed to run the frontend
  serializer tests (`node --test tests\js\blocks.test.js`), via Node's built-in `node:test`
  runner — no npm packages required.
- A **DJI Tello EDU** is only required for `--drone tello`; `--drone sim` (or the default
  `MockDrone`) needs no hardware.

### Two ways to fly

Blocks and Python are peers — the same engine, the same sensing, the same emergency stop. Students
who already know Python aren't held back by the block interface:

```python
from comp1.api import Drone

drone = Drone()
drone.takeoff()
while not drone.sees_target():
    drone.turn_right(20)
print(drone.target())  # target 204 cm away, -20 deg (left)
drone.approach_target()
drone.mark_found()
drone.land()
```

```powershell
venv\Scripts\python -m comp1 --script examples\02_search_and_mark.py
```

The script runs alongside the server, so the video feed, telemetry panel, and **EMERGENCY STOP**
button stay live while the student's own code flies the drone. See [`examples/`](examples/) for
annotated starting points.

### Documentation

- [Colour calibration for beginners](docs/vision-calibration.md): how to tune marker detection in
  a new room, understand the preview and sliders, save venue profiles, and fix common problems.
- [Platform architecture — options, comparison, and decision](docs/architecture/platform-options.md):
  why the platform is a custom Blockly web frontend + local Python service (FastAPI + djitellopy +
  OpenCV) packaged with PyInstaller, which alternatives were rejected, and the open items still to
  confirm (Chromebook support, safe-distance judging threshold).
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
