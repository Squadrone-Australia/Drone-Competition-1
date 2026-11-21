# Drone Competition

[![CI](https://github.com/Squadrone-Australia/Drone-Competition-1/actions/workflows/ci.yml/badge.svg)](https://github.com/Squadrone-Australia/Drone-Competition-1/actions/workflows/ci.yml)

This is a program that lets students fly a small drone (a DJI Tello) and teach it to search a
room, find red targets, and signal each one it finds - by dragging together colourful
puzzle-piece blocks, like Scratch. No coding experience is needed to get started, and there's a
built-in flight simulator so students can practice and compete without ever touching a real drone.

<p align="center">
  <a href="https://github.com/Squadrone-Australia/Drone-Competition-1/releases/latest/download/comp1-Setup.exe">
    <img alt="Download for Windows" src="https://img.shields.io/badge/Download_for_Windows-comp1--Setup.exe-2ea44f?style=for-the-badge&logo=windows&logoColor=white" height="48">
  </a>
  <br>
  <a href="https://github.com/Squadrone-Australia/Drone-Competition-1/releases/latest">
    <img alt="Latest release" src="https://img.shields.io/github/v/release/Squadrone-Australia/Drone-Competition-1?label=latest&style=flat-square">
  </a>
  <br>
  <sub>Windows 10 or 11 · no administrator needed · then see <a href="#installing-the-program">Installing the program</a></sub>
</p>

This page is written for anyone setting the program up for the first time - teachers, students,
or helpers — with no assumption you've used a terminal or installed developer tools before.

---

## What you'll end up with

A program that opens in a window of its own. (On a machine that cannot show one, it opens in
your normal web browser instead — same screen, same everything.) It shows:

- A **block-coding area** where students drag blocks together to build a flight plan.
- A **live camera view** — either a simulated drone flying around a virtual room, or the video
  feed from a real Tello drone.
- A **Run** button and a big red **EMERGENCY STOP** button.

Nothing is installed onto the internet or "in the cloud" — everything runs on the one computer,
and nobody outside the room can see or reach it.

---

## Before you start

**Windows 10 or 11.** That is the whole list — the installer brings everything else with it. You
do not need Python, and you do not need a terminal. (On Mac or Linux, see the bottom of this
page.)

---

## Installing the program

1. Click **[Download for Windows](https://github.com/Squadrone-Australia/Drone-Competition-1/releases/latest/download/comp1-Setup.exe)**
   (the green button at the top of this page). It always fetches the newest installer,
   `comp1-Setup.exe`. The same file, named with its version (`comp1-Setup-<version>.exe`), is on
   the [latest release](https://github.com/Squadrone-Australia/Drone-Competition-1/releases/latest)
   page.
2. Double-click it. Windows may show a blue **"Windows protected your PC"** box, because the
   installer is not signed by a paid certificate authority. Click **More info**, then **Run
   anyway**. (If you want to check the download first, the release page publishes a
   `SHA256SUMS.txt` you can compare against `Get-FileHash comp1-Setup.exe`. Both names are
   listed, with the same digest.)
3. Follow the prompts. It installs for the current user only, so it does **not** ask for an
   administrator password.
4. Start it from the **Start Menu** entry (or the desktop shortcut, if you asked for one). The
   block-coding screen opens.

There is no black terminal window and nothing else to install. To remove it later, use
**Settings → Apps** in Windows as you would for any other program.

### Closing the program

- Close the window, the way you close anything else. If a flight is still going, it asks first and
  lands the drone before it closes.
- Or click **Close program** at the top of the page. That stops it immediately, and the page says
  so. (This one asks you to stop the flight yourself first.)
- If you are using it in a browser tab instead of a window, closing the tab is enough: about half
  a minute after the last Drone Coder tab is gone, the program notices nobody is watching and
  closes itself.

Refreshing the page, or having two tabs open, doesn't close anything — and it will never close
itself in the middle of a flight without asking.

If you click the Start Menu shortcut while it's already running, it tells you so rather than
starting a second copy.

### Keeping it up to date

When the computer has internet, the program quietly checks for a newer version as it starts. If
there is one, a bar appears across the top of the page: click **Update and restart** and it
downloads, installs and reopens itself. Nothing is downloaded until you click.

At a competition the laptop is usually joined to the drone's own Wi-Fi, which reaches nothing
else — the check simply finds nothing and says nothing. You can also turn it off entirely under
**Settings** in the app.

---

## Using the program

When Drone Coder opens, you'll see the block-coding screen. A few things to try:

- Drag blocks from the left-hand palette under **🚁 when mission starts** to build a flight
  plan — for example, take off, turn, move forward, check whether a marker is visible.
- Click **Run** to fly the plan. The block currently being run is highlighted so you can follow
  along.
- The right-hand panel shows what the drone "sees" through its camera, with the detected red
  marker circled, plus how far away it is and which direction to turn.
- The big **EMERGENCY STOP** button immediately halts the drone, no matter what it's doing.

Your blocks are kept for you. There's no Save button and nothing to remember: the program
quietly keeps a copy of whatever is on the block screen, and puts it back the next time you
open Drone Coder — after a reload, after closing the window, or after the computer has been
turned off. The copy lives on that computer, with whatever opened it, so blocks built in the
Drone Coder window come back in the window, and blocks built in a browser tab come back in that
browser — not in each other, and not on a different machine.

By default the drone is a **simulator** — a virtual drone in a virtual room — so there's nothing
to break and no real hardware needed. This is the best way to build and test a flight plan before
trying it on a real Tello.

### Teaching the camera which red to recognise

The drone camera finds targets with the **AI model** by default — the selector above the
drone-camera picture. It was trained on real footage of the targets, so it needs no tuning for the
room's light. If the AI model cannot load on a computer, the program says why and uses **Colour**
instead.

Colour is the other choice in that selector. It looks for a range of reds, and room lighting
changes how red looks to a camera, so before using Colour with a real drone in a new room, click
**Tune colour** above the drone-camera picture. Capture a frame, drag a box inside the red marker,
and the program will suggest suitable settings. The preview should highlight the marker while the
floor, walls, and other objects stay dark. Click **Apply to detector** only after checking the
preview. What you apply is remembered, so a venue tuned in the morning is still tuned after
lunch — **Restore startup settings** in the same panel forgets it again.

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

**Windows says "Windows protected your PC" and won't run the installer.**
Click **More info**, then **Run anyway**. The warning is about the installer being unsigned, not
about anything it found. If the button isn't there at all, the computer is managed by the school
and someone with admin rights has to allow it.

**Nothing opens.**
Give it a few extra seconds the first time. If it still doesn't open, go to
`http://localhost:8765` in your browser while the program is running — that reaches the same
program, and always works.

**It opens in a browser instead of its own window.**
That's the fallback, and nothing is wrong: this computer doesn't have the Microsoft Edge WebView2
runtime that the window needs. Everything works the same in the tab. The installer offers to
download WebView2 when it's missing (no administrator needed); if that was skipped or couldn't
connect, running the installer again, or installing the WebView2 Runtime from Microsoft, brings
the window back.

**It says Drone Coder is already running.**
It is — one copy serves the page, and a second would only fight it for the same address. Look for
its window, open `http://localhost:8765`, or close the running one first.

**I closed the tab but the program is still running.**
Give it about half a minute: with the last tab gone it closes itself. Use **Close program** if you
want it gone immediately. (Closing the Drone Coder *window* stops it straight away.)

**It won't start and I can't see any error.**
The program writes what happened to `%LOCALAPPDATA%\comp1\logs\comp1.log` — paste that path
into File Explorer's address bar. Send that file to whoever is supporting you.

**The browser can't reach the real Tello / video is choppy.**
Make sure the computer is connected to the Tello's own Wi-Fi network (not your normal home/school
Wi-Fi) — the Tello doesn't use the internet at all, it creates its own local network.

**It says "connected, no video".**
The drone is connected and will fly, but its video isn't reaching the laptop. First turn the
drone off and on again, then click **Reconnect**. If another program (a Python notebook or
another drone app) turned the drone's video off, it stays off until the drone restarts. If
there's still no video, the laptop's firewall is blocking it. The program tries to open the
video path itself, but that doesn't get through every firewall. On your own computer, Windows may
have asked whether to allow Drone Coder on networks: allow it on **Public** networks too, because
the drone's Wi-Fi counts as public. An antivirus with its own firewall (Kaspersky, Bitdefender,
Norton…) ignores Windows' rules and needs the same exception in its own settings. On a school or
university laptop, ask IT to allow incoming video from the drone. The rule they need is:

```bat
netsh advfirewall firewall add rule name="Drone Coder camera" dir=in action=allow protocol=UDP localport=11111 profile=any
```

The rule only accepts the drone's video on port 11111. Flying doesn't need it. If the policy
ignores rules added on the laptop itself, IT can push the same rule through Group Policy or Intune.

**I want to start fresh.**
Close the program and delete the folder `%LOCALAPPDATA%\comp1` — that holds the saved settings
and the venue colour calibration, nothing else. The program rebuilds it on the next start.

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
.\build.ps1                                     # tests + exe + installer + SHA256SUMS.txt
node --test tests\js\blocks.test.js             # frontend serializer tests
```

`--drone sim` flags: `--seed N` (repeatable arena), `--noise 0.05` (movement drift),
`--scenery {arena,corridor}` (also switchable in the browser). Server flags: `--port`,
`--no-window` (use the system browser rather than a window of our own), `--no-browser` (open
nothing at all), `--no-check-updates`, `--idle-timeout N` (seconds after the last window closes
before the program stops itself; `0` never stops). Default port `8765`. Anything not passed falls
back to what the Settings panel last saved, then to the code default.

The window is [pywebview](https://pywebview.flowrl.com/) over the Edge WebView2 runtime, and it is
the default front door wherever one can be opened. Everything else falls back to the browser —
including the hub deployment in
[docs/architecture/platform-options.md](docs/architecture/platform-options.md) §4, where students
on Chromebooks reach one bridging laptop over the LAN. The window keeps its own profile under
`%LOCALAPPDATA%\comp1\webview`, so its `localStorage` (the block buffer, saved vision profiles)
is separate from any browser's.

### Continuous integration

Every push to `main` or `dev`, and every pull request, runs
[`.github/workflows/ci.yml`](.github/workflows/ci.yml): `ruff check` for lint, a fast updater-contract
job, the pytest suite on Windows and Linux against Python 3.14 — the version the installer is built
on — and the `node --test` frontend tests. Two things to know before you push:

```powershell
venv\Scripts\ruff check .               # the exact lint gate CI runs
venv\Scripts\ruff check . --fix         # fix the mechanical ones (imports, unused names)
```

Lint settings live in `pyproject.toml` under `[tool.ruff]` — a 100-column budget and the `E,F,W,I,UP,B`
rule sets. The CI job pins its ruff version, so bump both places together when you upgrade.

### Releasing

Pushing a tag `v<version>` runs [`.github/workflows/release.yml`](.github/workflows/release.yml),
which does everything `build.ps1` does on a Windows runner and attaches both assets to a **draft**
GitHub release. It refuses to build if the tag disagrees with `comp1/__init__.py`. Review the draft,
then publish it yourself — nothing is released automatically.

`build.ps1` remains the local shipping path: it refuses to build from a tree whose tests fail, stamps the
version from `comp1/__init__.py`, and produces `dist\comp1-Setup-<version>.exe` plus
`dist\SHA256SUMS.txt`. Publish **both** on a GitHub release tagged `v<version>` — the in-app
updater looks the installer up by name inside `SHA256SUMS.txt` and refuses to run one whose
digest does not match, so a release missing that file is a release nobody can update to. The
installer step needs [Inno Setup](https://jrsoftware.org/isdl.php) 6 or newer (7 is what this was
built and tested with); without it the script still leaves a working `dist\comp1\comp1.exe` and
says so. Expect roughly a 70 MB installer and a 250 MB install.

The installer is unsigned, which is what produces the SmartScreen warning in the install
instructions above. A code-signing certificate is the only real fix; until there is one, the
published checksum is what a cautious teacher can check instead.

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

- **Python 3.14+** — the only hard requirement for running from source, and the version CI tests
  against and the installer is built on. The packaged installer brings its own interpreter, so an
  installed copy needs nothing.
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

For teams writing their own vision code, `drone.frame()` returns the raw camera picture as an
OpenCV-ready BGR array. [`examples/04_pilot.py`](examples/04_pilot.py) shows it in use: fly by
keyboard in an OpenCV window while it records video and snapshots of the markers to `recordings\`.

```powershell
venv\Scripts\python -m comp1 --script examples\04_pilot.py --drone sim
```

### Documentation

- [Python pathway (2026-07-31)](docs/specs/2026-07-31-python-pathway.md): the student-facing
  `comp1.api` surface, how `--script` runs alongside the server, and the stop mechanism.
- [First-run tutorial (2026-11-18)](docs/plans/2026-11-18-first-run-tutorial.md): the guided tour
  and first program a new student sees, the questions it answers, and why the lesson program
  cannot crash.
- [Tutorial camera lesson (2026-11-20)](docs/plans/2026-11-20-tutorial-camera-lesson.md): the
  optional second chapter that teaches the vision blocks, what it checks, and how it was measured.
