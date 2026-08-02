# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```powershell
venv\Scripts\pip install -e .[dev]      # setup
venv\Scripts\pytest                     # all tests
venv\Scripts\pytest tests\test_approach.py::test_gives_up_when_marker_lost   # one test
venv\Scripts\python -m comp1                    # run with MockDrone (no hardware)
venv\Scripts\python -m comp1 --drone sim        # run against the simulator
venv\Scripts\python -m comp1 --drone tello      # real Tello (join TELLO-xxxx WiFi first)
venv\Scripts\python -m comp1 --script examples\02_search_and_mark.py --drone sim   # Python pathway
venv\Scripts\pyinstaller comp1.spec --noconfirm # -> dist\comp1\comp1.exe
node --test tests\js\blocks.test.js             # frontend serializer tests
```

`--drone sim` flags: `--seed N` (repeatable arena), `--noise 0.05` (movement drift).
Server flags: `--port`, `--no-browser`. Default port 8765.

There is no linter, formatter, or CI configured. `pytest-asyncio` runs in `asyncio_mode = "auto"`,
so `async def test_*` needs no decorator. Every test is hardware-free — `djitellopy` is
monkeypatched and `SimDrone` substitutes for a real Tello.

## What this is

A search-and-rescue drone competition platform for secondary-school students. Students drag Blockly
blocks to program a DJI Tello EDU to patrol an arena, find red circular "victim" markers via
onboard-camera OpenCV, signal each find, and (planned) navigate point A → point B.

## Architecture

**One WebSocket carries everything.** `/ws` handles JSON control messages *and* binary JPEG video
frames on the same socket ([comp1/server.py](comp1/server.py)). There are zero REST endpoints. The
server is single-session by construction: one global `app.state.interp`, one broadcast client set,
no auth, bound to 127.0.0.1.

**Detection and control are decoupled by polling, not events.** The video loop runs the detector
every ~0.1 s and writes to `app.state.latest_detection`; the `Interpreter` is constructed with
`lambda: app.state.latest_detection` and reads it whenever it needs to sense. Nothing pushes.

**One engine, three drones.** `MockDrone` (call recorder), `SimDrone` (pose model + synthetic
camera), and `TelloDrone` all implement `DroneAdapter` ([comp1/drone/base.py](comp1/drone/base.py)),
and the *same* interpreter and *same* detector run against all three. The simulator renders frames
that go through the real `find_targets`, so it is a genuine hardware stand-in rather than a mock —
`tests/test_sim_e2e.py` flies a complete mission with no hardware and no mocked vision.

**Programs are validated JSON, never generated Python.** `Program`/`Block` are pydantic models
([comp1/protocol.py](comp1/protocol.py)) and `Interpreter._exec` is a `match` on `b.op`. This is a
deliberate architectural choice (see `docs/architecture/platform-options.md`) — it buys live block
highlighting, instant e-stop, and no arbitrary-code surface. **Do not add `exec()` of student code.**

**Block ids are Blockly's own ids.** `serializeProgram` in
[comp1/frontend/blocks.js](comp1/frontend/blocks.js) hand-walks the block chain (Blockly's
`javascriptGenerator` is not used) and preserves `block.id`, which is what lets the backend's
`{"type":"highlight","blockId":...}` events map straight back onto the workspace.

### Adding a drone capability touches four places

1. [comp1/protocol.py](comp1/protocol.py) — the `Block.op` Literal + the `check_params` validator
2. [comp1/interpreter.py](comp1/interpreter.py) — a new `case` arm in `_exec`
3. [comp1/drone/base.py](comp1/drone/base.py) ABC + all three adapters
4. [comp1/frontend/blocks.js](comp1/frontend/blocks.js) — block definition, toolbox category entry,
   and a `blockJson` case

Add it to [comp1/api.py](comp1/api.py) too if students should reach it from the Python pathway.

Adding a *value* (reporter) block is a different path: the `SENSORS` Literal in `protocol.py`, a
`_sensor` arm in `interpreter.py`, a block with `output:` plus a `valueJson` case in `blocks.js`, and
a reader on `api.Drone`.

**`LIMITS` is duplicated on purpose and must be kept in sync.** `protocol.LIMITS` and the `LIMITS`
const in `blocks.js` hold the same ranges. The backend rejects an out-of-range *literal* at parse
time and clamps a *computed* value at run time; the frontend clamps literals client-side so a
student typing `5` into a fly block gets a warning rather than a program that won't load. Change one,
change the other.

### Program schema v2 and the expression system

Blocks are not a flat action list. `Value` ([comp1/protocol.py](comp1/protocol.py)) is a recursive
discriminated union (`number` / `sensor` / `var` / `binop` / `unop`) that `cm`, `deg`, `n`,
`seconds`, `value`, and `cond` all accept; a bare JSON number is shorthand for a `number` node at any
depth. `Interpreter._eval` evaluates it, capped at `MAX_EXPR_DEPTH`.

v1 programs still load — `Program.upgrade_v1` converts old `Condition` objects by **shape** (a `cond`
dict with no `"kind"`), not just by version number. Full contract:
[docs/specs/2026-07-31-program-schema-v2.md](docs/specs/2026-07-31-program-schema-v2.md).

**With no target in view, `target_distance_cm` reads `9999`, not `0`.** A student writing
`repeat until (distance < 120) [turn right 15]` must keep searching; `0` would exit the loop and read
as "arrived". Preserve this convention in any new distance-like sensor.

Runtime problems (clamped value, division by zero, unset variable) emit
`{"type":"warning","blockId":...}` and continue. They must never raise — a mission that dies
mid-flight is worse than one that warns.

### Two pathways, one engine

Blocks and Python are peers, not layers. [comp1/api.py](comp1/api.py) drives the same
`DroneAdapter` and `TargetTracker` the `Interpreter` does; `--script` runs the student's file on a
worker thread while the server keeps the event loop, so video, telemetry and EMERGENCY STOP stay
live.

`app.state.interp` is one slot holding *either* an `Interpreter` or a `ScriptRun` — both expose
`request_stop()`, so stop/e-stop routing is pathway-agnostic and the two can't run at once.

`api.EmergencyStop` derives from `BaseException` deliberately: a student wrapping their flight loop
in `try/except Exception` must not be able to swallow the stop and leave the drone flying.

### Vision pipeline

[comp1/vision/camera.py](comp1/vision/camera.py) is the **single source of truth for geometry**.
Everything is normalised by frame width (`f_norm = focal_px / width`) so one constant describes both
the 960×720 Tello stream and 640×480 sim frames. The simulator imports the same intrinsics as the
hardware specifically so tuning transfers to the arena.

`find_targets` returns *all* candidate markers with `distance_m` / `bearing_deg` / `elevation_deg`
resolved. `TargetTracker` adds a frame-to-frame lock with bearing hysteresis — without it the
primary target is whichever blob is largest *this frame*, and two similar markers make the approach
controller oscillate. Use `TargetTracker` for anything stateful; `detect_red_circle` is the
stateless single-frame helper.

`Detection` exposes `.cx` / `.area_ratio` / `.position` as back-compat properties delegating to
`.target`.

**`min_area_ratio` is a range limit, not just a speck filter.** It sets the smallest detectable
blob, which caps detection distance for a given marker size. `VisionConfig.max_detect_range_m`
computes it; `CameraIntrinsics.min_area_ratio_for_range` inverts it. Change it deliberately.

### The sensor path must stay clean

`DroneAdapter.get_frame()` returns only what a real camera would see. Debug graphics go through
`DroneAdapter.annotate()`, which the server applies to a *display copy* after detection has run.
`SimDrone` uses this for its minimap — which is drawn in victim-red and would otherwise be clutter
in the detector's own input.

## Project constraints

The **requirements document is external** (not in this repo). Code and docs cite it as `§N`:

| § | Subject |
|---|---|
| §2.1 | Find must be signalled by a visible drone action (currently a back-flip) |
| §2.2 | Detection must expose approximate marker position |
| §3.1 | Red-marker detection; HSV must be re-tunable on-site |
| §3.2 | "Approach and stop at a safe distance" — threshold unconfirmed by organisers |
| §3.4 | Return to start / mission pads — pending organiser decision |
| §4 | **Anti-hardcoding** |

**§4 anti-hardcoding is load-bearing.** No "fly to fixed coordinate" block may exist. Relative
*sensed* values (distance/bearing to target or home) are fine — they are measurements. Absolute
arena coordinates exist only inside `comp1/sim/` and must not be exposed to the block layer. Any
mission-pad block must not be repurposable to skip the search phase.

**The frontend must work fully offline.** Blockly is vendored at
`comp1/frontend/vendor/blockly.min.js`; `tests/test_offline_assets.py` fails the build on any
external URL in served html/css/js.

## Documentation

`docs/architecture/` = decisions, `docs/plans/` = dated implementation plans, `docs/specs/` = design
specs. Accepted plans are committed here as dated markdown.

Note that `docs/plans/2026-07-28-*.md` embed their entire implementation source inline and have
already drifted from `comp1/` — treat them as historical records, not spec.
