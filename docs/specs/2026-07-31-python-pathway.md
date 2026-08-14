# Python Pathway — `comp1.api`, the `--script` Runner, and Cooperative Stop

**Status:** Implemented 2026-07-31 (Phase 3) · **Plan:** [2026-07-31-target-sensing-and-expressions.md](../plans/2026-07-31-target-sensing-and-expressions.md) §7

## Purpose

Give students who already know some Python a first-class way into the competition, without forking
the platform. The project lead expects most entrants to choose this over blocks, so the API is
optimised for being *obvious* rather than expressive: `drone.forward(50)`, not
`drone.move("forward", 50)`; `drone.distance_cm()`, not `drone.detection().target.distance_m * 100`.

The pathway is the student's own `.py` file, launched by them:

```powershell
venv\Scripts\python -m comp1 --script my_mission.py --drone sim
```

The script runs **in-process alongside the server**, so the video feed, the telemetry panel and —
critically — the EMERGENCY STOP button all stay live while their code flies the drone.

## One engine, two front doors

```
              blocks (JSON)                 my_mission.py
                    |                             |
              Interpreter                     ScriptRun
                    |                             |
                    +---------- Drone ------------+     comp1/api.py facade
                                  |
                          DroneAdapter  ·  TargetTracker  ·  VisionConfig
                                  |
                    MockDrone / SimDrone / TelloDrone
```

`api.Drone` is a facade, not a reimplementation. `approach_target()` is `Interpreter._approach()`
made synchronous, reading the same `approach_stop_distance_m`, `approach_bearing_deadband_deg` and
min/max turn/step clamps from `VisionConfig`. `mark_found()` emits the same `found_count` event and
the same §2.1 flip. Sensing goes through the same `TargetTracker`, so a Python script gets the same
frame-to-frame lock as a block program and does not chase whichever red blob happens to be biggest
this frame.

`ScriptRun` deliberately mirrors `Interpreter`'s surface — `request_stop()` plus `await run()` — so
the server holds either one in the single `app.state.interp` slot. Two consequences fall out for
free: stop/e-stop routing is pathway-agnostic, and a block run and a script run **cannot run
simultaneously** (the existing `"already running"` guard covers both).

### Is this the `exec()` the architecture forbids? No.

`docs/architecture/platform-options.md` §2A rejects "generating raw Python for `exec()`" as the
*block execution model*, and the implementation plan restates it as "the interpreter executes
validated JSON programs only". Both are about the compilation step: blocks must never become Python.
That ban buys live block highlighting, a stop flag checked between steps, and the §4 anti-hardcoding
property that only blocks which exist can be expressed.

Running a file the student wrote and launched from their own command line is a different thing — it
is what `python my_mission.py` does anyway, with no new authority. The block pathway is untouched:
`Interpreter._exec` is still a `match` on validated JSON, and nothing in the browser can inject
Python. `ScriptRun._load` uses `runpy.run_path`, which is precisely "run this file as `__main__`".

The anti-hardcoding rule still binds this pathway, though — `api.Drone` exposes only relative sensed
quantities (distance, bearing, elevation to the *seen* target). There is no arena coordinate on the
facade, and `SimDrone.x/y` stay inside `comp1/sim/`.

## API surface

```python
from comp1.api import Drone

drone = Drone()  # picks up the server's drone under --script
drone = Drone(sim=True)  # standalone: own simulator, no UI, no e-stop button
```

| Group | Members |
|---|---|
| Flight | `takeoff()` · `land()` · `wait(seconds)` |
| Move (cm) | `forward` · `back` · `left` · `right` · `up` · `down` |
| Turn (deg) | `turn_right` · `turn_left` |
| Signal | `flip(direction="back")` · `mark_found()` · `found_count` |
| Sense | `sees_target()` · `target()` · `targets()` · `distance_cm()` · `bearing_deg()` · `elevation_deg()` |
| Mission | `approach_target(stop_distance_cm=None) -> bool` |
| Telemetry | `battery` · `height` |

`target()` returns a `TargetView` — `distance_m`, `distance_cm`, `bearing_deg` (+ = right),
`elevation_deg` (+ = above), `position` — or `None`. It is a deliberate narrowing of the detector's
`Target`: students get real units and nothing in pixels. `targets()` returns them nearest-first.

`approach_target()` returns `True` when it is holding at the safe distance, `False` if the target was
lost or it ran out of steps, so a mission can branch on the outcome.

**Movement arguments are clamped, not rejected.** `forward(5)` becomes `forward(20)` — the Tello's
floor — with a warning printed to the terminal and a `{"type":"warning"}` event emitted. A student
whose arithmetic yields a too-small step should get a visible nudge, not a crashed mission (plan §6).

`height` is best-effort: `DroneAdapter` has no height reading yet, so it falls back to the
simulator's pose and returns `0.0` on `MockDrone`. It becomes real when Phase 4 adds adapter
telemetry.

## The stop mechanism

**The requirement:** a student's `while True:` loop must not be able to make the drone unstoppable.

### Layer 1 — a flag every call checks

One `threading.Event` lives on the run's `Session`. *Every* public method on `Drone` — including
property reads like `battery` and predicate reads like `sees_target()` — calls `_check()` before it
does anything, and movement calls check again immediately *after* the adapter returns, so the
student's next line does not run after a command that finished post-stop.

Checking the readers matters as much as checking the movers: `while not drone.sees_target(): pass`
has a check point even though it never commands the drone.

### Layer 2 — `EmergencyStop` is a `BaseException`

```python
class EmergencyStop(BaseException): ...
```

This is the single most important line in the file. Had it derived from `Exception`, this student
code would be *unstoppable*:

```python
while True:
    try:
        drone.turn_right(20)
    except Exception:  # swallows the stop, loops forever
        pass
```

`BaseException` puts it in the same category as `KeyboardInterrupt`: `except Exception` does not
catch it. `test_students_except_exception_cannot_swallow_the_stop` pins this.

### Layer 3 — `wait()` is interruptible

`drone.wait(30)` is `stop.wait(timeout=30)`, so a stop mid-hover returns immediately rather than
after the full sleep. (A student's own `time.sleep(30)` is not interruptible; see the limits below.)

### Layer 4 — the script thread is a daemon of our own

`ScriptRun` spawns `threading.Thread(daemon=True)` rather than using `asyncio.to_thread`. The
default executor's threads are joined at interpreter exit, so a runaway loop in a pooled thread
would wedge process shutdown. A daemon thread cannot.

### Layer 5 — the abandon watchdog

Nothing cooperative can interrupt `while True: pass`. So after `request_stop()` a watchdog fires
`grace_s` (3 s) later; if the worker has not finished, the run resolves as `"abandoned"`, the drone
is landed, and the `app.state.interp` slot is freed so the platform stays usable.

The abandoned thread keeps spinning, but it is **powerless**: its `Drone` captured its `Session` at
construction, that session's stop flag is set, and every method raises before touching the adapter.
Confirmed by `test_a_loop_with_no_drone_calls_is_abandoned_and_left_powerless`, which lets the
abandoned thread out of its loop afterwards and asserts its `forward(100)` never reaches the drone.

### Layer 6 — the drone is stopped independently of the script

E-STOP calls `drone.emergency()` from the server's WebSocket handler, on the event loop, with no
involvement from the script thread at all. Even in the abandoned case the aircraft is already cut.
The script flag is about ending the *program*; the aircraft's safety never depends on it.

### What this does not cover

- A blocking adapter call already in flight cannot be interrupted mid-command — a Tello `move`
  takes as long as it takes. The check lands the moment it returns.
- `time.sleep(60)` in student code delays the *script's* termination by up to 60 s. The drone is
  stopped regardless, and the watchdog frees the slot after 3 s.
- `except BaseException:` in student code defeats layer 2. Layers 5 and 6 still hold: the drone is
  stopped and the script can never command it again.

## Server integration

`create_app(drone, cfg, *, script=None, script_delay=1.5)`.

- On startup, `_script_loop` waits `script_delay`, then for the first WebSocket client (up to
  `SCRIPT_CLIENT_WAIT` = 5 s) — the mission should not start flying before the student's video panel
  is up — then installs a `ScriptRun` into `app.state.interp` and awaits it.
- `{"type":"stop"}` and `{"type":"estop"}` already call `app.state.interp.request_stop()`; because
  `ScriptRun` mirrors `Interpreter`, no branching was needed.
- Lifecycle events: `{"type":"script","state":"started"|"done"|"stopped"|"error"|"abandoned",…}`
  for structure, plus the existing `{"type":"finished","reason":…}` so today's frontend logs a
  script run exactly as it logs a block run. `mark_found()` emits `found_count` on the same channel,
  so the victim counter works for both pathways.
- Events raised on the script's worker thread are marshalled onto the event loop with
  `call_soon_threadsafe`; events raised on the loop are emitted directly. The server keeps the loop,
  the student keeps a thread.
- On any non-`done` outcome the runner lands the drone, mirroring `Interpreter.run`.

## Examples

`examples/` doubles as documentation:

| File | Teaches |
|---|---|
| `01_hello_drone.py` | takeoff, a scan of the arena, telemetry, land |
| `02_search_and_mark.py` | the competition in miniature: search, approach, mark, land |
| `03_distance_decisions.py` | branching on `bearing_deg()` / `distance_cm()` — `approach_target()` taken apart |

## Testing (`tests/test_api.py`)

- Each movement maps to the expected `DroneAdapter` call, in order, against `MockDrone`'s log.
- Out-of-range moves clamp rather than raise.
- `sees_target()` / `target()` / `targets()` reflect the detection, including that the primary
  target follows the `TargetTracker` lock rather than this frame's nearest candidate.
- `approach_target()` converges in the simulator to within one minimum step of
  `approach_stop_distance_m`, from a start heading facing away from the victim.
- **Stop:** a runaway `while True:` loop is interrupted within seconds and the drone lands; the same
  loop wrapped in `except Exception` is *still* interrupted; `wait(30)` aborts immediately; a stop
  requested before the run even starts still wins; a loop with no API calls at all is abandoned,
  landed, and permanently barred from commanding the drone.
- **Server:** a script runs to completion over a live WebSocket; a block program is refused while a
  script is flying; e-stop over the WebSocket stops a runaway script and fires `emergency()`.

## Out of scope

Editing or launching scripts from the browser (the file is the student's, and their editor is
theirs); a Python equivalent of block highlighting; `height`/`attitude` telemetry beyond the
simulator's pose; anything depending on localisation (Phase 4).
