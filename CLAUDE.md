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
venv\Scripts\python -m comp1 --vision-config vision_config.toml    # on-site re-tune, see below
venv\Scripts\pyinstaller comp1.spec --noconfirm # -> dist\comp1\comp1.exe
node --test tests/js/*.test.js                  # frontend tests
```

`--drone sim` flags: `--seed N` (repeatable arena), `--noise 0.05` (movement drift),
`--scenery {arena,corridor}` (also switchable in the browser).
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

`VisionConfig` ([comp1/vision/config.py](comp1/vision/config.py)) holds everything tunable — HSV
bands, `marker_diameter_m`, approach distances, lock thresholds. The code defaults live in the
dataclass itself; `VisionConfig.load_file(path)` overlays a TOML file on top (only the keys present
override), so a re-tune on-site (requirements §3.1) means editing a copy of
[vision_config.example.toml](vision_config.example.toml) and passing `--vision-config`, not touching
code. `DEFAULT_CONFIG` (still the hardcoded dataclass) stays the default everywhere else — tests,
the Python pathway, and any call site that doesn't thread a `cfg` through — so importing
`comp1.vision.config` never has a side effect of reading a file.

`comp1/vision/calibration.py` is the on-site re-tuning path (§3.1) and is **operator-triggered
only** — nothing in `interpreter.py` or `api.py` may call it. `auto_suggest_hsv` locates the marker
itself by running `find_targets` against a loose `RED_PRIOR`, then fits bands with the same
`suggest_hsv` a manual drag uses. `RED_PRIOR` exists solely to find a region and never becomes the
detector's config. `check_coverage` rejects any *proposal* whose mask covers more than
`MAX_MASK_COVERAGE` of the frame — without it a widened band passes its own preview and then flags
a wall, silently invalidating `test_scenery_alone_is_never_a_victim`.

[comp1/vision/camera.py](comp1/vision/camera.py) is the **single source of truth for geometry**.
Everything is normalised by frame width (`f_norm = focal_px / width`) so one constant describes both
the 960×720 Tello stream and 640×480 sim frames. The simulator imports the same intrinsics as the
hardware specifically so tuning transfers to the arena.

`find_targets` returns *all* candidate markers with `distance_m` / `bearing_deg` / `elevation_deg`
resolved. `TargetTracker` adds a frame-to-frame lock with bearing hysteresis — without it the
primary target is whichever blob is largest *this frame*, and two similar markers make the approach
controller oscillate. Use `TargetTracker` for anything stateful; `detect_red_circle` is the
stateless single-frame helper.

When `approach_marker` / `approach_target()` starts, it deliberately drops any earlier search lock
and re-acquires the nearest visible candidate once. The normal bearing lock then resumes around
that target for the maneuver. Preserve both halves: nearest-at-start prevents approaching a farther
stale lock, while locking afterward prevents similar targets from making the controller ping-pong.

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

### The simulator draws two views of one truth

The camera view ([comp1/sim/render.py](comp1/sim/render.py)) projects a real 3D room —
floor grid, four shaded walls, marker posts and contact shadows — through
[comp1/sim/scene.py](comp1/sim/scene.py), which uses the *same* `SIM_INTRINSICS` the detector
inverts. The marker billboard itself is untouched: apparent radius is still `f * R / d`, because
that one line is what every range estimate in the approach tests comes out of. Change the room
freely; leave the disc alone.

**Scenery colours must stay blue-dominant in BGR (`B >= G >= R`) and low-saturation.** Walls and
floor are most of the frame, so a warm-grey wall is a false positive on every frame.
`test_scenery_alone_is_never_a_victim` sweeps 324 poses of the square arena and
`test_corridor_scenery_alone_is_never_a_victim` another 540 of the corridor to hold this.

The third-person view is browser-side three.js ([comp1/frontend/scene3d.js](comp1/frontend/scene3d.js),
wired up by [comp1/frontend/view3d.js](comp1/frontend/view3d.js)). It is fed by two WebSocket
messages: `scene` on connect *and whenever the arena changes*, and `pose` at 30 Hz. Pose runs far
faster than the 10 fps video because a 100 ms step in heading is visible as a stutter no amount of
browser-side smoothing hides.

### Sceneries: the arena is a rectangle now

`World` ([comp1/sim/world.py](comp1/sim/world.py)) holds `size_m` (x extent) plus optional
`length_m` (y extent) and `start` (the start pad). `size_m` stays the first positional field and
means "square" when the others are unset, so every old call site still works — but **read
`width_m`/`depth_m`/`start_xy` downstream, never `size_m`**.

[comp1/sim/scenery.py](comp1/sim/scenery.py) is the registry: `catalog()`, `build(name, seed)`, and
`with_victims(world, points)`. Two exist — `arena` (the 4 m square, markers on the walls) and
`corridor` (2.5 x 10 m, drone at one end, a fixed `DESTINATION` marker at the other, victims
sprinkled free-standing down the middle by rejection sampling against `WALL_CLEARANCE_M` /
`MIN_VICTIM_SEP_M` / `PAD_CLEARANCE_M`).

**The destination sign is byte-identical to a victim in the camera** — same red circle, same
`KIND_STYLE` entry. The detector cannot tell them apart and must not learn how: distinguishing them
is the exercise. `MissionScorer` knows the difference because it reads the arena, not the frame.

Markers no longer need a wall to lean on, so **both views billboard every marker face**. The camera
renderer always did; `scene3d.js` used to rotate them to face the room centre, which is wrong for
anything standing in the open.

### Mission success is scored against sim truth

`mark_found` is a bare counter in `interpreter.py` and `api.py` — neither can know whether the drone
was actually beside a victim. [comp1/sim/mission.py](comp1/sim/mission.py) can, and `server.py`
scores every find through it: a signal credits the nearest un-credited victim within
`CREDIT_RADIUS_M`, and the mission succeeds when all victims are credited *and* the drone has landed
within `ARRIVAL_RADIUS_M` of the destination. A scenery with no destination succeeds on victims
alone; a corridor cleared of victims succeeds on arrival alone.

**Crediting is synchronous, broadcasting is not.** `server._score` runs inside the `emit` callback,
before the task that broadcasts. With `delay=0` a whole program finishes before a scheduled task
gets a turn, and scoring against `drone.pose()` at that point credits nothing.

`SimDrone` commands **interpolate** their pose over the command's duration instead of sleeping and
teleporting, which is what both views animate; each command still ends on the exact arithmetic pose,
so tests may read `drone.x` straight after the call. `delay=0` disables animation entirely and keeps
the suite instant.

`DroneAdapter.pose()` / `.scene()` are **display feeds, not sensors** — see §4 below. `MockDrone` and
`TelloDrone` return `None` and the browser hides the 3D stage and its camera-mode buttons.

**Every run resets first.** The server calls `drone.reset()` and rebuilds the `TargetTracker` and the
`MissionScorer` before starting an `Interpreter`, and broadcasts `{"type":"reset"}` so the browser
clears the trail and the found count. Students iterate by hitting Run, so an attempt that began wherever the last one ended
would make their own change impossible to judge. `SimDrone.reset()` re-seeds its RNG but leaves the
arena alone — same layout, same noise, so a `--seed` run is genuinely repeatable. `reset()` is a
no-op on hardware, which cannot teleport.

`DroneAdapter.can_reset` is what the UI reports: with a real Tello the reset clears the tracker and
the counters but the aircraft does not move, and the console says so instead of claiming "back on
the start pad". Do not make `reset()` land a real drone — a surprise flight command from a button
labelled Reset is worse than a button that does less than it looks like it does.

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

The cracks in that wall are all one-way and all live in `server.py` plus `comp1/sim/`:

- **Display** — `DroneAdapter.pose()`/`.scene()` hand arena coordinates to the browser so the 3D
  view and the plan panel can draw the room.
- **Authoring** — `DroneAdapter.scenery_catalog()`/`.load_scenery()` let the browser's arena panel
  pick a scenery and write victim coordinates back, so a teacher can lay a problem out by hand.
- **Scoring** — `MissionScorer` reads both to decide whether a find counted.

Nothing in `comp1/interpreter.py` or `comp1/api.py` may call any of them, and **none of them may
ever gain a block or a sensor**: no "fly to the destination" block, no `at_destination` sensor, no
`victims_remaining` sensor. The drone finds the destination the way it finds a victim — by looking
at it.

**The frontend must work fully offline.** Blockly and three.js are vendored under
`comp1/frontend/vendor/`; `tests/test_offline_assets.py` fails the build on any external URL in
served html/css/js. three.js is loaded as an ES module through the import map in `index.html`
(`"three"` → `./vendor/three.module.min.js`), which also needs `three.core.min.js` alongside it —
update all three files together.

## Documentation

`docs/architecture/` = decisions, `docs/plans/` = dated implementation plans, `docs/specs/` = design
specs. Accepted plans are committed here as dated markdown.

Note that `docs/plans/2026-07-28-*.md` embed their entire implementation source inline and have
already drifted from `comp1/` — treat them as historical records, not spec.
