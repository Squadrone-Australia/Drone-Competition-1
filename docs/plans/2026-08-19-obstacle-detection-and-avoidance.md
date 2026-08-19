# Obstacle detection and avoidance

## Context

The arena contains printed markers that are **not** red circles — red squares, green triangles,
blue circles, yellow squares. Today the vision pipeline is built to *reject* every one of them:
`circularity_min = 0.82` and `solidity_min = 0.85` in [config.py](comp1/vision/config.py) exist
precisely so a red square is invisible, and `tests/test_detector.py::test_ignores_red_square`
pins that behaviour. The consequence is that a student's drone has no way to know an obstacle is
there — it cannot sense one, cannot decide to go round one, and in the simulator it flies straight
through it with no feedback that anything went wrong.

This change adds the missing half: a second detector that reports **everything that is a coloured
shape but is not an accepted red circle** as an obstacle, sensor blocks that expose it, an
`avoid obstacle` action block, and simulator collision so ignoring an obstacle has a visible
consequence.

Decisions already made with the user:
- Both **sensor blocks and an `avoid obstacle` action block**.
- The simulator **enforces collisions** and reports a crashed mission.
- Obstacle range is estimated from apparent size using an assumed printed size, configurable via a
  new `obstacle_diameter_m` key (default 0.25 m, same as the markers).

**§4 (anti-hardcoding) holds.** Obstacle bearing/distance are camera measurements, so they are
legal sensors. The collision and crashed state live entirely in `comp1/sim/` + `server.py` and must
never gain a block or a sensor — same rule as `pose()`/`scene()`/`MissionScorer`.

---

## Approach

### 1. The obstacle detector — new file [comp1/vision/obstacles.py](comp1/vision/obstacles.py)

The definition is *negative*: an obstacle is any sufficiently large, saturated, coloured blob that
`find_targets` did not accept. So the new detector **reuses `find_targets` verbatim** rather than
re-implementing the shape gates:

```python
@dataclass(frozen=True)
class Obstacle:
    cx: float; cy: float
    radius_norm: float
    area_ratio: float
    circularity: float
    solidity: float
    bearing_deg: float
    elevation_deg: float
    distance_m: float
    position: str          # "left" | "center" | "right"
    shape: str             # "circle" | "square" | "triangle" | "blob"  (informational)
    color: str             # "red" | "green" | "blue" | "yellow" | "other" (informational)

def saturated_mask(frame_bgr, cfg) -> np.ndarray
def find_obstacles(frame_bgr, cfg=DEFAULT_CONFIG, targets=None) -> list[Obstacle]
```

`saturated_mask` thresholds on **saturation and value only** (`obstacle_sat_min` / `obstacle_val_min`),
not hue — that is what makes it catch red, green, blue and yellow shapes with one mask. It leans on
an invariant the codebase already guarantees and tests: scenery colours are blue-dominant and
**low-saturation** ([render.py](comp1/sim/render.py) module docstring), so walls and floor fall below
the floor. Apply the same `MORPH_CLOSE` → `MORPH_OPEN` 5x5 sequence as `color_mask`, and for the
same reasons.

`find_obstacles` then:
1. calls `find_targets(frame_bgr, cfg)` (or takes the already-computed list) to learn which blobs are
   accepted red circles;
2. contours `saturated_mask`, drops anything outside `obstacle_min_area_ratio` /
   `obstacle_max_area_ratio`;
3. **excludes** any contour whose centroid falls inside an accepted target's `minEnclosingCircle` —
   that, and only that, is what makes a red circle "not an obstacle";
4. resolves geometry with the *same* `cfg.intrinsics` helpers `find_targets` uses
   (`bearing_deg`, `elevation_deg`, `distance_m(radius_norm, cfg.obstacle_radius_m)`) and the same
   `center_band` rule for `position`;
5. classifies `shape` from `cv2.approxPolyDP` vertex count + hull circularity and `color` from the
   median hue of the masked pixels — **display and teaching only**, no sensor reads them;
6. sorts nearest-first, like `find_targets`.

**Deliberate, documented consequence:** a *partially occluded* red marker fails the target shape
gates (hull circularity ~0.75) and is therefore reported as an obstacle. That is the fail-safe
direction — the drone gives a half-seen thing a wide berth rather than flying at it — and gets its
own test so a future change cannot flip it silently by accident.

### 2. Carrying obstacles through `Detection` — [comp1/vision/detector.py](comp1/vision/detector.py)

Add `obstacles: list = field(default_factory=list)` to `Detection`, plus `obstacle` (nearest, or
`None`) and `obstacle_count` properties. `found` keeps meaning *target* found — no existing semantics
change.

Fill it in the three places a `Detection` is built: `Detection.of` (new keyword arg),
`detect_red_circle`, and **both** `TargetTracker.update` and `TargetTracker._miss` — the miss path is
easy to forget and is exactly when obstacles matter most (no target visible, still flying).
`find_targets` is already computed in `update`, so pass it to `find_obstacles` to avoid a second pass.

No tracker/lock for obstacles: they are consulted per-frame, and there is no approach controller that
needs stability across frames.

### 3. Config — [comp1/vision/config.py](comp1/vision/config.py) + [vision_config.example.toml](vision_config.example.toml)

New flat scalar keys (the loader only supports flat scalars), with a comment block explaining each:

| key | default | note |
|---|---|---|
| `obstacle_sat_min` | `80` | above the scenery's low-saturation palette |
| `obstacle_val_min` | `50` | matches the red bands' value floor |
| `obstacle_min_area_ratio` | `0.004` | ~2.8 m range; obstacles matter close in |
| `obstacle_max_area_ratio` | `0.9` | only rejects a frame-filling wash |
| `obstacle_diameter_m` | `0.25` | assumed printed size, per the decision above |
| `obstacle_clear_distance_m` | `1.0` | closer than this and centred = "in the way" |
| `avoid_sidestep_cm` | `60` | one lateral step; ≥ the Tello's 20 cm floor |
| `avoid_max_steps` | `6` | bound on the avoid controller |

Plus an `obstacle_radius_m` property mirroring `marker_radius_m`.

These are a separate mask from the red bands, so the `_PRIOR_BANDS` invariant in
[calibration.py](comp1/vision/calibration.py) is untouched — note that explicitly in the config
comments so a future reader does not go looking.

### 4. Blocks — protocol, interpreter, api, frontend

**[comp1/protocol.py](comp1/protocol.py)** — add to `SENSORS`:
`obstacle_visible`, `obstacle_ahead`, `obstacle_distance_cm`, `obstacle_bearing_deg`,
`obstacle_count`, `obstacle_position_left|center|right`. Add `avoid_obstacle` to the `Block.op`
Literal. It takes no parameters, so it needs **no** `need` row in `check_params` (ops absent from
`need` pass) and no `LIMITS` entry.

`obstacle_ahead` is the one students will actually reach for: true when the nearest obstacle is
within `obstacle_clear_distance_m` *and* its `position == "center"`.

**[comp1/interpreter.py](comp1/interpreter.py)** — new `_sensor` arms mirroring the target arms.
Reuse `NO_TARGET_DISTANCE_CM = 9999.0` for `obstacle_distance_cm` when nothing is visible, for the
same reason it exists for targets: `repeat until (obstacle distance < 100)` must keep flying, not
read "arrived". Rename the constant's comment to cover both; do not introduce a second 9999.

New `_exec` arm `case "avoid_obstacle": await self._avoid()`. `_avoid` is structured like the
existing `_approach` (same `_detect()` / `_call_drone` / `_warn` idiom):

- read `self._detect()`; if there is no obstacle, or the nearest is beyond
  `obstacle_clear_distance_m`, or it is not `center`, return immediately — the block is a no-op when
  the way is clear;
- otherwise, up to `avoid_max_steps` times: step **away** from the obstacle's bearing
  (`"left"` when `bearing_deg >= 0`, `"right"` otherwise — a dead-centre obstacle deterministically
  goes right), re-read, stop as soon as the centre band is clear;
- if it never clears, `self._warn("could not get past the obstacle", b.id)` and return — it must
  never raise, per the runtime-warning rule.

No new `DroneAdapter` method: `_avoid` is built out of `move`, so all three adapters are untouched.

**[comp1/api.py](comp1/api.py)** — `ObstacleView` alongside `TargetView` (`distance_m`,
`distance_cm`, `bearing_deg`, `position`, `shape`, `color`), an `_obstacle_view()` builder, and
`Drone.sees_obstacle()`, `.obstacle()`, `.obstacles()`, `.obstacle_distance_cm()`,
`.avoid_obstacle() -> bool`. Follow the pathway's existing convention: sensing returns `None` when
nothing is visible (the 9999 belongs to the block pathway only), and every action goes through
`_act` so EMERGENCY STOP is honoured.

**[comp1/frontend/blocks.js](comp1/frontend/blocks.js)** — the usual four-place edit:
- `BLOCKS`: `obstacle_visible` / `obstacle_ahead` / `obstacle_position_is` (Boolean, dropdown, copy
  the `marker_position_is` pattern), `sense_obstacle_distance` / `sense_obstacle_bearing` (Number,
  `output`), and `avoid_obstacle` (statement, `previousStatement`/`nextStatement`).
  Give the obstacle reporters their own hue so they read as a distinct family — add
  `obstacle: 20` to the `C` palette.
- `TOOLBOX`: reporters into `cat("Sensing", ...)`, `avoid_obstacle` into `cat("Mission", ...)`.
- Serializer: 1:1 rows in the `SENSORS` table; a `valueJson` case for `obstacle_position_is`
  (mirroring `marker_position_is`); `avoid_obstacle` appended to `blockJson`'s parameterless arm.
- Python codegen: rows in `pythonValue`'s `fixed` map (`obstacle_distance_cm` →
  `_obstacle_value("distance_cm", 9999)`, etc. — add the `_obstacle_value` helper next to the
  existing `_target_value` preamble) and a `pythonBlocks` case → `drone.avoid_obstacle()`.

Keep `LIMITS` untouched — nothing new is ranged.

### 5. Simulator: obstacles that are solid

**[comp1/sim/world.py](comp1/sim/world.py)** — `OBSTACLE_KINDS = ["obstacle_red_square",
"obstacle_green_triangle", "obstacle_blue_circle", "obstacle_yellow_square"]`. The
`*_square`/`*_triangle`/`*_circle` suffix is load-bearing — `markerGeometry` in `scene3d.js` selects
geometry by suffix, so these names get the right 3D shape for free. Add `Marker.is_obstacle`
(`kind.startswith("obstacle_")`) and `World.obstacles`. Leave `DISTRACTOR_KINDS` alone: wall-mounted
decorations stay non-colliding, and they are now correctly *seen* as obstacles by the detector
without needing to be solid.

**[comp1/sim/render.py](comp1/sim/render.py)** — `KIND_STYLE` entries for the four obstacle kinds
(an unknown kind is a hard `KeyError` here), and a distinct amber dot in `draw_minimap` so a teacher
can see them on the plan. Do not touch the billboard radius line.

**[comp1/sim/scenery.py](comp1/sim/scenery.py)** — place free-standing obstacles in both sceneries
via the existing `_find_spot`/`is_free` machinery, with a new `OBSTACLE_SEP_M` so an obstacle never
lands on a fire. `with_fires` already preserves non-fire markers, so teacher-authored fire edits keep
the obstacles. *Not in scope:* an authoring path for placing obstacles by hand from the browser.

**[comp1/sim/drone.py](comp1/sim/drone.py)** — collision in `move`. Inside `step(t)`, after the
existing room-box clamp, test the drone against each obstacle as a cylinder: horizontal distance
`< m.radius_m + DRONE_CLEARANCE_M` **and** `abs(z - m.height_m) < m.radius_m + DRONE_CLEARANCE_M`.
On contact, stop the animation at the last free position and set `self.crashed = True`.
Using the real vertical extent rather than a floor-to-ceiling column is deliberate: flying *over* an
obstacle stays a legitimate student strategy. `reset()` clears `crashed`; `pose()` reports it.

**[comp1/sim/mission.py](comp1/sim/mission.py)** — `MissionScorer` reads `crashed` off the pose dict
it is already given, latches it, and `state()` returns `"crashed"` with success forced False. No new
data channel.

**[comp1/server.py](comp1/server.py)** — `_telemetry(det)` gains obstacle distance/bearing/count so
the browser can show them; `_publish_mission` already flows off pose, so `crashed` reaches the UI
with no new plumbing.

**[comp1/frontend/scene3d.js](comp1/frontend/scene3d.js)** — `MARKER_COL` entries for the four
obstacle kinds (unknown kinds only fall back to grey, so this is cosmetic but wanted); geometry is
automatic via the name suffix. Surface the crashed state in the telemetry/mission panel
(`app.js` + `index.html`).

### 6. Tests

| file | covers |
|---|---|
| `tests/test_obstacles.py` *(new)* | red square / green triangle / blue circle / yellow square are obstacles; a red circle is **not**; an empty white frame and a low-saturation grey wall yield none; a half-occluded red marker *is* an obstacle (the documented fail-safe); nearest-first ordering; distance uses `obstacle_diameter_m`. Build frames with OpenCV on a white canvas, exactly like `test_detector.py`'s `frame_with()`. |
| `tests/test_sim_render.py` | extend the existing 324- and 540-pose empty-scenery sweeps to assert `find_obstacles(...) == []` too — the low-saturation palette invariant now guards two detectors. |
| `tests/test_sim_drone.py` | forward into an obstacle stops short and sets `crashed`; the same move at a different altitude passes over cleanly; `reset()` clears `crashed`. |
| `tests/test_avoid.py` *(new)* | `avoid_obstacle` is a no-op when clear; sidesteps away from the obstacle's bearing; warns instead of raising when it gives up after `avoid_max_steps`. |
| `tests/test_protocol.py`, `tests/test_expressions.py` | the new sensor names and `avoid_obstacle` parse. |
| `tests/js/blocks.test.js` | serializer round-trip and Python codegen for every new block. |
| `tests/test_mission.py` | a crashed pose forces `state == "crashed"` and blocks success. |
| `tests/test_api.py` | the new `Drone` obstacle methods. |

### 7. Docs

- Write the accepted plan to `docs/plans/2026-08-19-obstacle-detection-and-avoidance.md`.
- `CLAUDE.md`: a new subsection under **Vision pipeline** stating that obstacle detection is defined
  negatively (anything not an accepted red circle), that the exclusion is by centroid-inside-target
  and nothing else, and that `saturated_mask` depends on the blue-dominant/low-saturation scenery
  palette rule — loosen that palette and the obstacle detector starts flagging walls. Add `crashed`
  to the §4 list of sim-only facts that may never gain a block or a sensor.
- `vision_config.example.toml`: the eight new keys with their comments.

---

## Verification

```powershell
.venv\Scripts\pytest                                  # whole suite, hardware-free
.venv\Scripts\pytest tests\test_obstacles.py -v        # the new detector
.venv\Scripts\pytest tests\test_sim_render.py -v       # the pose sweeps must still be clean
node --test tests/js/*.test.js                         # serializer + codegen
```

End to end in the simulator:

```powershell
.venv\Scripts\python -m comp1 --drone sim --scenery corridor --seed 1
```

In the browser: obstacles appear in the 3D stage and the camera view; drag
`repeat until (obstacle ahead)` → `fly forward 50` → `avoid obstacle` and confirm the drone
sidesteps rather than stopping dead. Then run a program that flies straight into an obstacle and
confirm the mission panel reports **crashed** and the drone stops at contact instead of passing
through. Re-run with the same `--seed` to confirm the layout repeats.

## Risks

- **`obstacle_sat_min` on real hardware.** In the simulator the palette invariant guarantees walls
  are desaturated; a real cage with a warm-lit wall or a saturated banner will read as an obstacle.
  This is why it is a tunable config key and not a constant — it belongs in the on-site re-tune pass
  alongside the HSV bands. Flag it in `vision_config.example.toml`.
- **The simulator cannot validate the obstacle gates any more than it validates the target gates.**
  `render.py` draws flawless flat shapes. A green suite is a regression guard, not evidence the
  obstacle detector survives the cage — only captured footage is that.
- **Obstacle range assumes a 0.25 m printed size.** An obstacle printed larger reads as nearer and
  one printed smaller reads as further. `obstacle_clear_distance_m` is generous enough to absorb the
  usual variation, and the error is conservative for anything oversized.

---

## As built

Accepted and implemented on 2026-08-19. The plan above is the design as approved; these are the
places the implementation departed from it, and why.

- **Collision is resolved over the swept path, not inside `step(t)`.** The plan put the obstacle
  test in the animation callback. That tunnels: with `delay=0` — which is every test in the suite,
  and the `--delay 0` flag — `_animate` runs a single step at `t=1`, so any obstacle strictly
  between the start and the end point is skipped entirely. `SimDrone._first_contact` now samples the
  whole path at 2 cm before animating and clamps travel to the last clear point. Three tests in
  `test_sim_drone.py` pin this, including one whose move *ends* in clear air beyond the obstacle.
- **`DEFAULT_OBSTACLE_DIAMETER_M` equals the marker diameter.** Obstacles were briefly drawn at
  0.4 m while the detector assumed 0.25 m, which made every simulated range read ~2× too near. Range
  is derived from apparent size, so the two constants have to agree or the simulator stops being a
  stand-in for the arena.
- **`is_in_the_way(obstacle, cfg)` is a shared helper**, not logic duplicated per call site. The
  `obstacle_ahead` sensor, `Interpreter._avoid`, `Drone.avoid_obstacle`, `Drone.obstacle_ahead`, the
  overlay and the telemetry payload all call it, so the block a student *tests* with and the block
  that *acts* can never disagree about what "in the way" means.
- **The blocks got their own "Obstacles" toolbox category** rather than being split across Sensing
  and Mission, and their own hue. "Is a target there?" and "is something in the way?" are opposite
  intentions and should not be the same colour. A `sense_obstacle_count` reporter was added
  alongside the sensors named in the plan.
- **`scenery.is_free`/`_find_spot` gained a `sep_m` parameter** so obstacles can claim the wider
  `OBSTACLE_SEP_M` bubble while markers keep `MIN_FIRE_SEP_M`. An already-placed obstacle keeps its
  wider bubble whoever is asking.
- **The telemetry strip went from three readings to five**, so its `nth-child` column rules and
  `grid-template-columns` were extended; the rule now reads as "a pair's column is ceil(n/2)".

Not done, and deliberately: there is no authoring path for placing obstacles by hand from the
browser's arena panel (it still edits targets only), and no worked example under `examples/`.

Six tests fail on this branch — `test_approach.py` (3), `test_api.py` (2) and
`test_blocks_serializer.py` (1). All six fail identically on `main` at 6c2d65e and are unrelated to
this work.
