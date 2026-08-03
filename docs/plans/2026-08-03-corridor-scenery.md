# Corridor scenery, hand-placed victims, and mission success

**Date:** 2026-08-03 · **Status:** implemented

## Why

The simulator had exactly one arena: a 4 m square room with markers glued to the four walls, the
drone starting dead centre. `World` held a single `size_m` scalar and both renderers hardcoded that
square. There was no scenario concept, no way to choose one, and no way to place a marker by hand.

This adds a second scenery — a **long corridor** with the drone at one end and a **fixed destination
sign** at the other — which turns §3.4's point A → point B navigation into something students can
actually fly, layered on top of the existing search task. Alongside it, a **floor-plan editor** so a
teacher can lay out the victims, and a **mission-success indicator** that only fires when the
victims were genuinely found.

## Decisions

| | |
|---|---|
| Destination | An ordinary 0.25 m red circle at a fixed spot. Indistinguishable from a victim to the detector — that *is* the exercise. |
| Corridor | 2.5 m wide × 10 m long. The destination sits ~2.5 detection ranges from the start pad, so it cannot be seen from the start. |
| Victim placement | Free-standing in the corridor interior, never on a wall. Hand-editable. |
| Editor | A 2D floor-plan panel in the sidebar, independent of the 3D camera mode. |
| Find scoring | Verified against sim truth: a signal only counts near an un-credited victim. |
| Persistence | Session only — restarting `comp1` gives a fresh random layout. |

## What was built

### `World` is a rectangle with a start pad — `comp1/sim/world.py`

`size_m` stays the first positional field (it means "square" on its own, so every existing caller and
test is untouched); `length_m`, `start` and `name` are new and optional. Downstream code reads the
derived `width_m` / `depth_m` / `start_xy`. `DESTINATION` joins `VICTIM` as a marker kind, with a
`World.destination` accessor.

### `comp1/sim/scenery.py` — the registry

`catalog()`, `names()`, `build(name, seed)`, `with_victims(world, points)`, `is_free(...)`.

- `arena` — `World.random`, unchanged.
- `corridor` — 2.5 × 10 m, start pad at `(1.25, 0.6)`, a fixed `DESTINATION` at `(1.25, 9.4)`, and
  3 victims + 2 distractors placed by rejection sampling against `WALL_CLEARANCE_M = 0.5`,
  `MIN_VICTIM_SEP_M = 1.2` and `PAD_CLEARANCE_M = 1.2`.

`with_victims` replaces only the victims and keeps the destination, the distractors and the start
pad exactly where they were — editing one thing must not shuffle the room under the user. Illegal
points are **dropped, not clamped**, and the caller learns by reading back what it got.

### `comp1/sim/mission.py` — `MissionScorer`

Built from the `scene()` payload, fed `pose()`. `signal(pose)` credits the nearest un-credited
victim within `CREDIT_RADIUS_M = 1.5`; `state(pose)` reports `found` / `total` / `at_destination` /
`state`. Success needs every victim credited **and** the drone landed within `ARRIVAL_RADIUS_M` of
the destination. A scenery with no destination succeeds on victims alone (the square arena predates
the corridor and stays winnable); a corridor cleared of victims succeeds on arrival alone (a teacher
setting up a pure A-to-B lesson).

### Renderers

Both views became rectangle-aware: `_draw_room` / `_draw_floor_grid` / `_draw_ceiling_panels` take
`(width, depth)`, the ceiling strip lights **tile** every ~3 m instead of spanning the room once (one
9 m smear down a corridor gives no parallax at all), and the minimap takes the room's own aspect.
`scene3d.js` swaps `GridHelper` for a rectangle-capable `buildGrid`, resizes the sun's shadow
frustum to the room, caps the follow camera's stand-off at 6 m, and fits **both** axes in top-down.

`KIND_STYLE[DESTINATION] == KIND_STYLE[VICTIM]`, byte for byte, and `MARKER_COL.destination` matches
`MARKER_COL.victim`. Pinned by `test_the_destination_sign_reads_exactly_like_a_victim`.

**Marker faces are now billboarded in both views.** The camera renderer always did; `scene3d.js`
rotated them to face the room centre, which is wrong for anything standing in the open — a marker in
the middle of a corridor was edge-on from exactly the angle you approach it.

### Protocol

`scene` is no longer a connect-only message; it is re-broadcast after every arena change.

| Direction | Message |
|---|---|
| → client | `sceneries` — `{sceneries: [...] \| null, current}` on connect and after a change |
| → client | `mission` — `{found, total, at_destination, needs_destination, state, signal}` |
| ← client | `scenery` — `{name, randomise}` |
| ← client | `layout` — `{victims: [{x, y}]}` |

Both client messages are refused while a mission is running, and refused politely on an adapter with
no arena.

**Crediting is synchronous; broadcasting is not.** `server._score` runs inside the `emit` callback,
before the task that broadcasts. This is not a style choice: with `delay=0` an entire program
completes before a scheduled task gets a turn, and scoring against `drone.pose()` at that point
credits nothing. The first draft did exactly that and the end-to-end test failed.

### Frontend

`comp1/frontend/plan.js` — a canvas floor plan showing walls, grid, start pad, destination, markers
and the live drone. `✎ Edit` turns on click-to-add / click-to-remove; `🎲` re-rolls; `↺` clears. The
scenery `<select>` is populated from the server's catalog. Every edit is sent as `layout` and the
canvas redraws from the `scene` that comes back — never from what it asked for, because the server
re-validates. The whole panel hides itself when `sceneries` is `null`.

The message bus in `app.js` grew a `sticky` map (`scene` + `sceneries`) so panels that subscribe
late still get both, and a `COMP1_SEND` helper plus a synthetic `running` message so the panel greys
itself out before the server has to refuse anything.

## §4 anti-hardcoding

Three one-way feeds now cross the wall, all confined to `server.py` and `comp1/sim/`: **display**
(`pose`/`scene`), **authoring** (`scenery_catalog`/`load_scenery`) and **scoring** (`MissionScorer`).
None may be called from `comp1/interpreter.py` or `comp1/api.py`, and none may ever gain a block or
a sensor — no "fly to the destination" block, no `at_destination` sensor. `protocol.py`,
`interpreter.py`, `api.py` and `blocks.js` were deliberately not touched.

## Tests

New: `tests/test_sim_scenery.py` + `tests/test_mission.py` (43 between them), plus additions to
`tests/test_server.py` and `tests/test_sim_render.py` — including a 540-pose false-positive sweep of
the empty corridor, since a rectangle presents the red detector with wall angles and distances the
square arena never did.

Full suite: 258 passing (was 202), plus the 33 node tests.
