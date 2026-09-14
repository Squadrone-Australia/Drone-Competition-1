# Test report: issues found

**Status: all but one are fixed** (2026-09-14). Each issue below keeps its
original description so the reasoning stays reviewable, and carries a
**Fixed** note saying what changed. Regression coverage is in
`tests/test_fault_recovery.py` — 21 of its 25 tests fail against the code as it
was, and the other 4 are controls that must keep passing.

The exception is [issue 11](#11-obstacles-are-mostly-absent-from-the-shipped-sceneries),
which turned out to need a product decision rather than a patch. See its note.

Testing session of 2026-09-14 against `dev` @ `ecf1b14`, simulator only (no hardware).
Hardware failures were **injected in software** through a fault-wrapping `DroneAdapter`
that needed no change to the app — see
[Appendix A](#appendix-a-reproducing-the-fault-scenarios) for the harness.

**Baseline was green before any of this**: 472 pytest tests and 49 `node --test` tests
passed, and every issue below is behaviour those suites did not cover. After the fixes the
suites stand at **497 pytest** and **49 node**, all passing.

Each issue is marked **Confirmed live** (reproduced against the running app) or
**Code review only**. The two are never blurred.

## Summary

| # | Severity | Fixed | Issue |
|---|---|---|---|
| [1](#1-stop-and-emergency-stop-cannot-interrupt-a-command-already-in-flight) | Critical | yes | Stop and EMERGENCY STOP cannot interrupt a command already in flight |
| [2](#2-a-students-nested-loop-freezes-the-app-and-disables-emergency-stop) | Critical | yes | A student's nested loop freezes the app and disables EMERGENCY STOP |
| [3](#3-the-ui-latches-into-running-for-ever-when-the-server-rejects-a-program) | Critical | yes | The UI latches into "running" for ever when the server rejects a program |
| [4](#4-most-malformed-websocket-messages-crash-the-connection-handler) | High | yes | 11 of 15 malformed WebSocket messages crash the connection handler |
| [5](#5-every-runtime-warning-is-silently-dropped-by-the-frontend) | High | yes | Every runtime warning is silently dropped by the frontend |
| [6](#6-emergency-stop-is-silently-discarded-while-the-socket-is-down) | High | yes | EMERGENCY STOP is silently discarded while the socket is down |
| [7](#7-the-emergency-stop-path-also-sends-land-in-a-non-deterministic-order) | High | yes | The emergency-stop path also sends `land`, in a non-deterministic order |
| [8](#8-estopped-is-announced-even-when-the-emergency-command-failed) | Medium | yes | `estopped` is announced even when the emergency command failed |
| [9](#9-the-drone-keeps-flying-after-the-browser-is-gone) | Medium | yes | The drone keeps flying after the browser is gone |
| [10](#10-raw-pydantic-validation-errors-are-shown-to-students) | Medium | yes | Raw pydantic validation errors are shown to students |
| [11](#11-obstacles-are-mostly-absent-from-the-shipped-sceneries) | Medium | **needs a decision** | Obstacles are mostly absent from the shipped sceneries |
| [12](#12-flying-into-a-wall-is-not-a-crash) | Medium | yes | Flying into a wall is not a crash |
| [13](#13-ws-has-no-origin-check) | Low | yes | `/ws` has no origin check |
| [14](#14-unknown-message-types-are-silently-ignored) | Low | yes | Unknown message types are silently ignored |
| [15](#15-src-is-a-stale-nested-clone-of-the-project) | Low | yes | `src/` is a stale nested clone of the project |

---

## Safety & fault handling

### 1. Stop and EMERGENCY STOP cannot interrupt a command already in flight

**Critical — Confirmed live.**

[`interpreter.py:262`](../comp1/interpreter.py#L262) issues every adapter call as
`await asyncio.to_thread(getattr(self._drone, method), *args)` with no `wait_for`. The
adapter's own timeout is the only bound. `request_stop()` sets an `asyncio.Event` that is
only checked *between* blocks ([`interpreter.py:92`](../comp1/interpreter.py#L92)), so while
a command is outstanding it has no effect at all.

The e-stop handler compounds it. [`server.py:1013`](../comp1/server.py#L1013) does
`await asyncio.to_thread(drone.emergency)` **inline in the WebSocket receive loop**, so if
that command also fails to answer, the connection stops processing *every* subsequent
message from that browser.

**Reproduction** (fault harness, `hang` fault — commands accepted, never answered):

1. `curl "http://127.0.0.1:8800/fault?hang=1"`
2. Build `takeoff → move → land` and press **Run**.
3. Press **Stop**, then **EMERGENCY STOP**, then **Run** again.

**Observed** — the adapter received exactly two commands:

```
takeoff   -> hang
emergency -> hang
```

No `finished`, no `estopped`, no "already running". The page still reads `connected`,
but Run, Stop, Reset and EMERGENCY STOP are all dead for the rest of the session.
A *fresh* WebSocket connection answered `{"type":"error","message":"already running"}`,
proving the server is alive and only that client's receive loop is stuck.
`app.state.interp` is never cleared, so no further mission can ever start.

**Real-hardware bound.** djitellopy does not hang for ever, but it is far from instant:
`RETRY_COUNT = 3` × `RESPONSE_TIMEOUT = 7 s` means an unanswered control command blocks for
**up to 21 s**, and `TAKEOFF_TIMEOUT = 20 s` pushes takeoff to ~60 s. So against a real Tello
that has dropped off Wi-Fi, EMERGENCY STOP is unresponsive and gives no feedback for up to
21 seconds, and repeat presses queue behind the blocked receive loop.

**Regression test:**
`tests/test_fault_recovery.py::test_a_command_that_never_answers_ends_the_mission`.

**Fixed.** Adapter calls now run under `asyncio.wait_for` with a per-adapter budget
(`DroneAdapter.command_timeout_s`, 45 s, overridden on `TelloDrone` from its
`FlightConfig`); a command that overruns raises `DroneTimeout` and the mission ends with an
error the student can read. `estop` is no longer awaited in the receive loop — it is
dispatched to its own task, so the connection keeps serving.

Verified live: with every command hanging, the socket answered a follow-up message one
second after EMERGENCY STOP was pressed. Before, that connection was dead for good.

---

### 2. A student's nested loop freezes the app and disables EMERGENCY STOP

**Critical — Confirmed live.**

`_run_blocks` and `_exec` never yield to the event loop on paths that make no adapter call
([`interpreter.py:90-99`](../comp1/interpreter.py#L90-L99)). `self._emit` is
`loop.create_task(...)` ([`server.py:756`](../comp1/server.py#L756)), which schedules without
suspending. A loop whose body is pure computation therefore runs as one uninterrupted block
of synchronous Python.

**Reproduction.** Run `repeat 20 { repeat 10 { while (5 > 1) { set x = 1 } } }` —
200,000 block executions, entirely within the block editor's own limits.

**Observed:**

- **6.65 s** during which the event loop ran nothing else.
- **0 video frames** delivered for the whole period — the camera freezes.
- An `estop` sent at **t = 3.37 s was never acted upon**. No `estopped` ack arrived and the
  mission ran to completion reporting `reason: "done"`.

`MAX_LOOP_ITERS = 1000` bounds each loop but not their product. The block limits allow
`repeat 50 × repeat 50 × while 1000` = 2,500,000 executions. At the measured 30,075
executions/second that is **~83 seconds** of a completely frozen, uninterruptible app
(extrapolated from the measurement, not run).

**Fixed.** `_run_blocks` yields with `await asyncio.sleep(0)` before every block, and the
`repeat_until`/`while` head yields too (a loop with an empty body never reaches the first).

Verified live on the same 200,000-execution program: E-STOP now ends it **immediately**
(`reason: "stopped"`), and 27 video frames arrived during the run against 0 before. It also
runs *faster* — the 50,000-execution case went from 1.66 s to 0.80 s, because the per-block
event tasks no longer pile up unbounded waiting for the loop to finish.

---

### 6. EMERGENCY STOP is silently discarded while the socket is down

**High — Confirmed live.**

`#estop` calls `ws.send()` directly ([`app.js:430`](../comp1/frontend/app.js#L430)) rather than
the guarded `window.COMP1_SEND` ([`app.js:198`](../comp1/frontend/app.js#L198)). The same is
true of Run, Stop and Reset ([`app.js:408,411,414`](../comp1/frontend/app.js#L408-L414)).

Note: per the WebSocket spec `send()` on a **CLOSED** socket does *not* throw — it discards
silently. So there is no error anywhere; the press simply does nothing.

**Reproduction.** Kill the server, wait for the status pill to read
`disconnected, retrying`, then press **EMERGENCY STOP**.

**Observed:** no exception, no `window.onerror`, no unhandled rejection, and **no message to
the student**. The biggest, reddest control on the page is a no-op that looks like it worked.

**Fixed.** Run, Stop, Reset and EMERGENCY STOP all go through a `sendCommand` helper that
returns whether the socket took the message and says so plainly when it did not.

Verified live, pressed while disconnected:
`⚠ EMERGENCY STOP did NOT reach the program — it is not connected. Land the drone by hand if
it is flying.`

---

### 7. The emergency-stop path also sends `land`, in a non-deterministic order

**High — Confirmed live.**

When a mission stops for any reason other than completion, `Interpreter.run` issues an
automatic `land` ([`interpreter.py:83-87`](../comp1/interpreter.py#L83-L87)). During an
emergency stop that races the `emergency` command the handler sends separately.

**Observed**, same program, two consecutive runs:

```
run 1:  takeoff -> land      -> emergency
run 2:  takeoff -> emergency -> land
```

Run 2 sends a **`land` to an aircraft whose motors were just cut**. The two commands are
also issued from different threads with no lock — `TelloDrone._cmd`
([`tello.py:292-307`](../comp1/drone/tello.py#L292-L307)) takes none — and
[`server.py:293-303`](../comp1/server.py#L293-L303) documents precisely why interleaved
commands corrupt djitellopy's positional reply pairing.

**Fixed (the land).** `request_stop(emergency=True)` now marks the stop, and both
`Interpreter` and `ScriptRun` skip their automatic `land` when it is set. An ordinary Stop
still lands, which `test_an_ordinary_stop_still_lands` guards.

**Not done: the lock.** Serialising all adapter access is a larger change to the threading
model than this pass warranted, and the race it addresses is now much narrower — the
emergency path no longer issues a competing `land`. Worth doing separately.

---

### 8. `estopped` is announced even when the emergency command failed

**Medium — Confirmed live.**

[`server.py:1026`](../comp1/server.py#L1026) broadcasts `estopped` unconditionally, after
having already broadcast an `error` when the aircraft could not be reached.

**Observed** with the `fail_emergency` fault:

```
⚠ emergency stop could not reach the drone: emergency failed
⛔ EMERGENCY STOP
```

The two lines contradict each other and the reassuring one is last and most emphatic. The
running latch is also cleared, so the UI presents a stopped mission that was never stopped.

**Fixed.** `estopped` now carries `ok`. On failure the server sends `ok: false` with the
reason, and the page prints that instead of the reassuring `⛔ EMERGENCY STOP`. The two
contradictory lines are gone.

---

### 9. The drone keeps flying after the browser is gone

**Medium — Confirmed live.**

**Reproduction.** Start a ~60 s mission, then close the socket abruptly at t = 3 s (a closed
laptop lid, a killed tab).

**Observed:** the mission continued for a further **22.5 s**, executing all 40 remaining moves,
and only landed because the program's own `land` block was reached. Nothing noticed the
operator had gone.

The idle shutdown cannot help: `_idle_loop` counts `app.state.interp is not None` as *busy*
([`server.py:360-366`](../comp1/server.py#L360-L366)), so the 30 s countdown never starts
while a mission is running. A mission with a long loop keeps the aircraft flying, unattended
and with no reachable emergency stop, indefinitely.

**Fixed.** A `_unattended_loop` watchdog stops any mission that has had no browser attached
for `UNATTENDED_STOP_S` (10 s), which lands the aircraft through the normal stop path. It is
gated on `seen_client`, so a deliberately headless run (`--no-browser --script`, the test
suite) is untouched.

Verified live: the browser vanished at t = 3 s, the watchdog fired exactly 10 s later, and the
drone landed — against 22.5 s of unattended flight before.

---

## Frontend

### 3. The UI latches into "running" for ever when the server rejects a program

**Critical — Confirmed live.**

[`app.js:409`](../comp1/frontend/app.js#L409) calls `setRunning(true)` unconditionally after
sending. Every pre-flight rejection ([`server.py:826`](../comp1/server.py#L826),
[`:838`](../comp1/server.py#L838), [`:847`](../comp1/server.py#L847)) replies with `error` and
**never creates an interpreter**, so no `finished` event is ever sent — and the client's
`error` branch ([`app.js:384`](../comp1/frontend/app.js#L384)) only logs.

Two confirmed triggers:

1. **A bare `break` block** placed directly under *when mission starts*. `blocks.js:330` only
   warns and still serialises it; `protocol.check_loop_controls` then rejects it.
2. **Pressing Run while the drone is briefly offline.** Far more likely in a classroom — a
   moment of Wi-Fi trouble is all it takes.

**Observed** after either trigger, everything disables and stays disabled:

```
use-tello: disabled   vision-calibrate: disabled
plan-edit: disabled   scenery:          disabled
```

**Stop does not help** — [`server.py:879`](../comp1/server.py#L879) no-ops when
`interp is None`. Pressing Run again re-rejects and stays locked. The lock **persists after
the drone reconnects** (status pill back to `connected`, app still dead). The only escape is
**EMERGENCY STOP**, which on real hardware cuts the motors of a flying aircraft.

So the recovery path from a typo is the one control that must never be pressed casually.

**Fixed.** `app.js` tracks a `runPending` flag between pressing Run and the server
confirming the start (its `debug_program` echo). An `error` arriving while that flag is set
means the mission never began, so the latch is released. An error *during* a real mission
still leaves the run alone.

Verified live on both triggers — the bare `break` block and Run-while-offline. The page now
unlocks the moment the rejection arrives, and the corrected plan runs straight away.

---

### 5. Every runtime warning is silently dropped by the frontend

**High — Confirmed live.**

`Interpreter._warn` emits `{"type": "warning", ...}`
([`interpreter.py:106`](../comp1/interpreter.py#L106)) and the server broadcasts it
([`server.py:711`](../comp1/server.py#L711)). **No frontend module handles `warning`** — the
dispatch chain at [`app.js:360-397`](../comp1/frontend/app.js#L360-L397) has no branch for it
and no other file subscribes.

**Confirmed on the wire.** Running `set steps = 5 / 0`:

```
server sent: {"type":"warning","blockId":"w1","message":"division by zero — using 0"}
student saw: mission done
```

Also lost this way: *"variable 'x' was never set — using 0"*, runtime clamping messages,
*"loop gave up after 1000 repeats"*, *"lost sight of the marker"*, and
*"could not get clear of the obstacle"* — exactly the messages a student needs to debug a
plan that silently did the wrong thing.

Only the *serialize-time* warnings from `COMP1.warnings` are printed
([`app.js:407`](../comp1/frontend/app.js#L407)).

**Fixed.** `app.js` handles `warning`, printing it to the console panel and the execution
trace.

Verified live: `set steps = 5 / 0` now shows `⚠ division by zero — using 0` where before the
student saw only `mission done`.

---

### 10. Raw pydantic validation errors are shown to students

**Medium — Confirmed live.**

[`server.py:847`](../comp1/server.py#L847) interpolates the raw `ValidationError`. What lands
in a child's console:

```
⚠ invalid program: 1 validation error for Program
  Value error, break block '?nbY:XseXX)YD$eN!S,B' must be inside a loop
  [type=value_error, input_value={'version': 2, 'blocks': ...}, input_type=dict]
    For further information visit https://errors.pydantic.dev/2.13/v/value_error
```

**Fixed.** `_plain_validation_error` reduces a `ValidationError` to its own sentences, so the
console now reads `⚠ this plan cannot run: break block '...' must be inside a loop` with no
`[type=...]`, no `input_value=`, and no link to pydantic's docs.

`tests/test_server.py::test_invalid_program_rejected` asserted on the old wording, so it now
asserts the intent instead: the message names what is allowed, is one line, and carries no
pydantic noise.

---

## Server & protocol

### 4. Most malformed WebSocket messages crash the connection handler

**High — Confirmed live.**

The receive loop catches only `WebSocketDisconnect`
([`server.py:1153`](../comp1/server.py#L1153)), but `msg["type"]`
([`:813`](../comp1/server.py#L813)) and `msg["program"]` ([`:842`](../comp1/server.py#L842))
are unguarded subscripts, and the scenery/layout paths do no validation at all.

**11 of 15 probes killed the connection** (close 1006), each leaving a server traceback:

| Payload | Exception |
|---|---|
| `not json at all` | `JSONDecodeError` |
| `42` / `"hello"` | `TypeError: 'int'/'string' indices …` |
| `null` | `TypeError: 'NoneType' object is not subscriptable` |
| `{}` | `KeyError: 'type'` |
| `{"type":"run"}` | `KeyError: 'program'` |
| `{"type":"scenery","name":"bogus"}` | `ValueError: unknown scenery: bogus` |
| `{"type":"layout","fires":[{}]}` | `KeyError: 'x'` |
| `{"type":"layout","fires":[["a","b"]]}` | `ValueError: could not convert string to float` |
| `{"type":"layout","fires":[5]}` | `TypeError: 'int' object is not subscriptable` |
| a binary frame | `KeyError: 'text'` (from `receive_text`) |

Handled correctly: `switch_drone` with a bad mode, `save_settings` with a non-dict,
`vision_preview` with a bad config — these reply with an error and keep the socket.

The browser reconnects after 1 s, which is what makes this easy to miss in manual testing.

**Fixed.** Each message is handled inside its own `try`: a malformed one gets an `error`
reply and the loop continues. `WebSocketDisconnect` is re-raised so a real disconnect still
ends the loop, and the failure is logged server-side rather than swallowed.

Verified live: **all 15 probes now survive** and answer with an `error`, including the binary
frame (which needed a second pass — the first version of the handler referenced `msg` before
it was bound when `receive_text` itself raised).

---

### 13. `/ws` has no origin check

**Low — Code review only.**

The socket is bound to loopback ([`__main__.py:281`](../comp1/__main__.py#L281)), but
WebSockets are not subject to CORS. Any page the student's browser visits while the program
is running can open `ws://localhost:8765/ws` and send `run`, `estop`, `layout` or `quit`.

**Fixed.** `/ws` rejects a connection whose `Origin` does not match the `Host` the page was
served from, closing with 1008. A request with no `Origin` at all is still accepted — that is
never a browser (the test suite, a student's own Python, a diagnostic script), and the
same-origin policy was not protecting those anyway.

---

### 14. Unknown message types are silently ignored

**Low — Confirmed live.**

The `if/elif` chain ending at [`server.py:1153`](../comp1/server.py#L1153) has no final
`else`, so `{"type":"does_not_exist"}` is accepted and dropped without trace. A frontend/
backend version mismatch would present as features that quietly do nothing.

**Fixed.** The chain has an `else` that replies
`this program does not understand '<type>'` to the sender.

---

## Simulator fidelity

### 11. Obstacles are mostly absent from the shipped sceneries

**Medium — Confirmed live.**

`_arena` intends to place `ARENA_OBSTACLES = 2` obstacles
([`sim/scenery.py:106`](../comp1/sim/scenery.py#L106)), but `OBSTACLE_SEP_M = 1.6 m` is a hard
constraint in a 4 × 4 m room that already holds 8 markers, so `_find_spot` usually fails.

Measured over 300 seeds:

| Scenery | 0 obstacles | 1 | 2 (intended) |
|---|---|---|---|
| `arena` | **87 (29%)** | 188 (63%) | 25 (8%) |
| `corridor` | **300 (100%)** | 0 | 0 |

`_corridor` never calls the obstacle placement at all.

Consequences: the eight-block **Obstacles** toolbox category (`obstacle_ahead`,
`avoid_obstacle`, `sense_obstacle_*`, …) reports "nothing there" in 29 % of arena launches and
in *every* corridor launch. Because `--seed` defaults to random, a class gets a different
arena each launch — a lesson built on "go around the obstacle" simply fails for some students.
Crash detection (issue 12) depends on the same obstacles and is dead wherever they are.

**Not fixed — this needs a product decision, and here is why.**

Relaxing the separation until two obstacles fit was tried and **reverted**: it breaks
`test_an_obstacle_never_crowds_a_target_out_of_reach`, which defends a real fairness rule —
an obstacle closer than `OBSTACLE_SEP_M` to a target makes that target unreachable, turning a
hard arena into an unfair one.

An exhaustive 2 cm grid scan (not random sampling) shows the spec is **geometrically
unsatisfiable**: over 100 seeds, two obstacles fit at full separation in only 11% of arenas,
and **none fit at all in 30%**. The cause is structural — `World.random` puts markers on the
walls, so the only interior region 1.6 m clear of all of them is the middle, and the start pad
sits in the middle with its own 1.2 m clearance.

So the options all change the product, and the choice is yours:

- a larger arena, or a start pad that is not central;
- fewer or physically smaller obstacles;
- apply `OBSTACLE_SEP_M` only to **targets** and the destination, and ordinary marker
  separation to distractors — nothing needs to *reach* a distractor. Measured: this yields two
  obstacles in 75% of arenas and none in 1%. It is the narrowest change, but it does weaken a
  rule the test currently states, so it should be a deliberate call.

`corridor` is deliberately untouched: its layout is fixed so "every attempt presents the same
classification task", and adding obstacles would change an official competition scenario.

---

### 12. Flying into a wall is not a crash

**Medium — Confirmed live.**

Movement is clamped to the room box in `at(t)`
([`sim/drone.py:186-190`](../comp1/sim/drone.py#L186-L190)), while `crashed` is set only by
`_first_contact` ([`:199-204`](../comp1/sim/drone.py#L199-L204)) — which returns `None`
immediately when the world has no obstacles.

**Reproduction.** In the 4 × 4 m arena, `takeoff` then `move forward 300 cm` six times (18 m).

**Observed:** `finished: reason "done"`, mission panel `{"crashed": false, "state": "flying"}`,
final pose clamped to `y = 3.8` (the wall minus the 0.2 m margin). The student is told the
mission succeeded.

This contradicts the intent stated in the code itself — *"A drone that scrapes past teaches
that ignoring an obstacle mostly works, which is the opposite of the lesson"*
([`sim/drone.py:200-203`](../comp1/sim/drone.py#L200-L203)). Since the competition workflow is
"practise in the simulator, then fly for real", a plan that would put a real Tello into a wall
passes the simulator cleanly.

**Fixed.** A move whose unclamped endpoint leaves the room box now sets `crashed`, so the
mission reports `state: "crashed"` instead of a clean success. Only the horizontal box counts
— the altitude limits stay deliberate soft ceilings rather than surfaces to hit.

Verified: six 300 cm forward moves in a 4 m arena now crash; a legitimate 20 cm move near a
wall does not.

---

## Housekeeping

### 15. `src/` is a stale nested clone of the project

**Low — Confirmed live.**

`src/comp1/` is a second, independent git checkout (its own `.git`, HEAD `d81c0a8`
dated **2026-08-04**) of an older version of this same project. The live package is `comp1/`
at the repo root — `pyproject.toml` has no src-layout and `[tool.setuptools.packages.find]`
includes `comp1*` only.

It is untracked **and not in `.gitignore`**, so it shows as `?? src/` on every `git status`
and is one `git add -A` away from being committed. It also makes `grep`/IDE search return two
hits for everything, with the stale one often first.

**Fixed: removed.** Before deleting it, everything in it was checked against this repo:

- its commit (`d81c0a8`) is an ancestor of `dev`;
- the only files it had that the root lacks, `AGENTS.md` and `CLAUDE.md`, were untracked on
  purpose in `c14cd2d` and remain in history (`git show c14cd2d^:CLAUDE.md`);
- it carried uncommitted edits to 44 files, and of the ~555 meaningful lines they added, all
  but 5 already exist in this repo's history. Those 5 are formatter re-wraps of old code, and
  the one that looks like a setting (`circularity_min = 0.85`) is the *old* value — `dev` has
  since tuned it to `0.82`.

So it was a stale working copy whose work had already been committed here. It was deleted,
and the temporary `src/` entry in `.gitignore` removed with it.

---

## Investigated, not a bug

Recorded so nobody re-files them.

- **Offline safety holds.** Across the entire browser session, **2,426 requests, all to
  `127.0.0.1`** — zero external origins. The vendored Blockly/three.js/media setup and the
  guarantee asserted by `tests/test_offline_assets.py` are sound.
- **The `TelloDrone` adapter's fault handling is genuinely robust.** Ten fault tests written
  against a fake `Tello` and a fake `av` opener all pass: failed commands clear `link_ok` and
  re-raise, commands on a closed adapter report clearly, the frozen-frame watchdog
  (`_note_silence`) clears the link, a dead decoder is released and retried, `close()`
  neutralises the retired object without ever flying the aircraft and survives an aircraft
  that has gone, and `FrameReader.close()` correctly **leaks** a container rather than closing
  it under a stuck worker. The problems found in this report are all in the layers *above*
  this adapter.
- **Mid-mission command failures are handled correctly.** With `fail_takeoff` armed the app
  reported `mission error: takeoff failed`, lost and re-established the link, and **cleared the
  running latch properly**. This is what makes issue 3 precise: only *pre-flight rejections*
  wedge the UI, because only they skip creating an interpreter and so never send `finished`.
- **Battery UI works.** Injected 12 % and 3 % readings produced `battery low` / `battery
  critical` classes, a correct fill width, and the console warning that a flip will be
  downgraded to a spin. (Note `SimDrone.battery()` is hardcoded to `return 100`
  ([`sim/drone.py:316`](../comp1/sim/drone.py#L316)), so this path is unreachable without
  injection — worth a test double in the suite.)
- **WebGL/3D renders headless.** `#view3d` populated normally; the unguarded
  `new THREE.WebGLRenderer()` at `scene3d.js:59` did not fail in this environment. It remains
  unguarded, but no defect was observed.
- **Both baseline suites are green**: 472 pytest, 49 `node --test`.

---

## Found while fixing

Two things surfaced during the fix pass that were not in the original report.

**A pre-existing flaky test.** `test_update_flow.py::test_installing_downloads_verifies_and_launches`
failed roughly one run in six — confirmed by running it repeatedly against the *untouched*
code, so it was not introduced here. The cause was a real defect: `_install_update` was
`await`ed inline in the websocket receive loop, so closing the tab mid-update cancelled the
install **after the drone had been released but before the installer was handed over** — the
update simply never happened and nothing said so. It is now dispatched to its own task behind
an `app.state.installing` latch, and the test waits for the handover inside the websocket
block. Stable over 8 consecutive runs.

**The frontend cache-busting version had to be bumped.** `index.html` loads
`app.js?v=<date>`, and until that string changes a browser keeps serving the cached copy —
the fixes were live on the server and invisible in the page. It is now `v=20260914-1`.
**Any future change to a frontend file needs the same bump**, or it will not reach a browser
that has the page cached.

## Appendix A: reproducing the fault scenarios

**The permanent record is `tests/test_fault_recovery.py`.** Each fault that could be pinned
down deterministically is a test there, and 21 of its 25 tests fail against the code as it
was before these fixes. Run it with the rest of the suite:

```bash
python3 -m pytest tests/test_fault_recovery.py -q
```

The live scenarios were driven with a throwaway harness that is **not** in the repo. The
technique is worth knowing, because it needs no change to the app: `create_app(...)` already
takes `drone`, `tello_factory` and `simulator_factory` as parameters
([`server.py`](../comp1/server.py), `create_app`), so a test can pass a wrapper adapter that
delegates to a real `SimDrone` and fails on demand. The one used here supported these faults:

| Fault | Behaviour |
|---|---|
| `hang` | a flight command is accepted and never answered |
| `slow` | a flight command takes 15 s |
| `power_off` | every command raises, video stops, the link drops |
| `fail_takeoff` / `fail_land` / `fail_emergency` | only that command raises |
| `flaky` | one command in three raises |
| `battery_fixed` / `battery_script` / `battery_raises` | pin, step down, or break the charge reading |
| `reconnect_fails` / `connect_fails` | the aircraft never comes back |
| `frame_freeze` / `no_video` | the picture freezes or disappears while control still works |

A small HTTP endpoint beside the app flipped these while a browser, driven by Playwright, held
the page — which is what made faults *mid-flight* possible. Malformed websocket traffic was
sent with a plain `websockets` client, since a browser cannot produce it.

The `TelloDrone` checks in [Investigated, not a bug](#investigated-not-a-bug) used the same
idea one layer down: a fake `djitellopy.Tello` object assigned to the adapter, and a fake
opener passed to `FrameReader(address, opener=...)`, which that class accepts for exactly this.
