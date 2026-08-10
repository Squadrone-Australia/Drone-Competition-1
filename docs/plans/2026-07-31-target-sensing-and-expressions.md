# Exposing the Target: Distance/Direction Sensing, Expression Blocks, and the A→B Competition

**Status:** Approved 2026-07-31 · **Phase 1 implemented** — see
[Target sensing design](../specs/2026-07-31-target-sensing-design.md) and
[Phase 1 outcome](#phase-1-outcome) below. Phases 0 and 2–5 outstanding.

## Context

Today the platform gives students a single opaque block — `approach_marker` — that hides all sensing.
Behind it, [`Interpreter._approach()`](comp1/interpreter.py#L101-L123) steers on a 3-bucket
`left/center/right` string and uses `area_ratio >= 0.08` as its only range cue. Students never see a
number. There is no expression system in the block language at all: `Block.cm/deg/n` are plain `int`
fields ([protocol.py:24-26](comp1/protocol.py#L24-L26)) and `Condition` is a closed 5-value enum
([protocol.py:12-16](comp1/protocol.py#L12-L16)), so a "get distance" block is *structurally
impossible* to plug in anywhere.

The competition is evolving from "find red circles" to **depart point A → search and mark every
victim → reach point B**. That needs three things the codebase has none of: metric sensing
(distance/bearing in real units), a way for student programs to *compute* with those values, and
some form of localisation so "reach point B" and "don't double-count a victim" are well-defined.

Confirmed constraints for this plan:

| Decision | Answer |
|---|---|
| Hardware | **Tello EDU confirmed** — mission pads available |
| Markers | **Red circle only** — no fiducial tag (revisit later) |
| Arena ops | **One team at a time** — sequential rounds, no RF contention |
| Block language | **Full expression system** — value blocks, arithmetic, variables |

Target outcome: students read real numbers (`distance to victim = 180 cm`, `direction = -12°`),
build their own approach logic out of primitives instead of calling one magic block, and the
platform can score a full A→B search-and-rescue run.

---

## Verdict: is the competition doable?

**Yes — with five risks that must be retired early.** Ranked by how likely they are to sink a
competition day:

1. **Detection range is currently ~4 m and nobody has noticed.** `min_area_ratio = 0.002`
   ([config.py:11](comp1/vision/config.py#L11)) sets a floor of ~1380 px² on a 960×720 Tello frame →
   ~42 px minimum diameter. For a 0.25 m marker at Tello focal ≈684 px, that caps detection at
   **≈4.1 m**. In a 6 m arena a drone can be staring straight at a victim and see nothing. *Fix: use
   A3/A2 markers (0.35–0.5 m) and set this gate deliberately from a chosen max range, not by feel.*

2. **The simulator's camera is wider than the real Tello, so sim-tuned parameters will not
   transfer.** [render.py:8](comp1/sim/render.py#L8) sets `FOV_H = 83°` as *horizontal*, but the DJI
   spec figure of 82.6° is the **diagonal** FOV — the true horizontal is ≈70°. Every bearing the sim
   produces is ~18% off. *Fix: calibrate the real camera (chessboard) and drive both sim and
   detector from one intrinsics constant.*

3. **Mission pads are unproven on this stack.** djitellopy 2.5.0 has the full API and none of it is
   wired. Pads are 20×20 cm, need the downward camera, and only detect within roughly 0.3–1.2 m
   altitude — the drone must pass nearly overhead. Worse, enabling `mon` alongside `streamon` has a
   reputation for destabilising the video feed, and that would break victim detection outright.
   *This must be hardware-validated before any design depends on it.*

4. **`mark_found` does a back-flip next to a tripod.** [interpreter.py:80](comp1/interpreter.py#L80)
   signals a find with `flip("back")` at ~1 m altitude beside a marker stand. Flips need >50%
   battery, ~1.5 m of clearance, and drift unpredictably. *Recommend replacing with a bob/wiggle
   signal and keeping flip as an opt-in.*

5. **Dead-reckoning drift.** Tello `move forward 100` lands within roughly ±10–20%, and yaw drifts
   every rotation. Over a multi-turn A→B mission, accumulated error can exceed a metre — which is
   also the accuracy the victim-dedup registry depends on. Pads correct this only when overflown.

Also true but manageable: battery (~7 min realistic manoeuvring, so cap runs at 3–4 min and budget
4+ batteries per drone), and HSV fragility under gym lighting (the deferred tuning panel should be
promoted to must-have).

None of these are fatal. All five are cheaper to discover now than on competition morning.

---

## Architecture

Nine layers, bottom-up. Layers 1–3 are what make "expose the target" possible; 4–6 are what make
"A→B with scoring" possible; 7–9 are platform maturity.

### 1. Camera intrinsics — new `comp1/vision/camera.py`

One resolution-independent source of truth, replacing the lone `FOV_H` constant buried in the
renderer.

```python
@dataclass(frozen=True)
class CameraIntrinsics:
    f_norm: float          # focal_px / frame_width — invariant across resolutions
    @classmethod
    def from_hfov(cls, hfov_deg) -> "CameraIntrinsics"
    @classmethod
    def from_dfov(cls, dfov_deg, aspect_w, aspect_h) -> "CameraIntrinsics"
    def bearing_deg(self, cx_norm) -> float           # atan((cx-0.5)/f_norm)
    def elevation_deg(self, cy_norm, aspect) -> float
    def distance_m(self, radius_norm, real_radius_m) -> float   # f_norm*R/r
```

Ship `TELLO_INTRINSICS` (from `from_dfov(82.6, 4, 3)` → `f_norm ≈ 0.713`) as a placeholder, and a
`calibrate.py` script that produces the real value from chessboard images. **Point the sim at the
same object** so sim and hardware agree.

*Sanity check of the maths:* 0.25 m marker, `f_norm=0.713`, 960 px wide → 171 px at 1 m, 43 px at
4 m. A ±2 px segmentation error is ±2.3% at 2 m and ±7% at 6 m. Usable to ~5 m; honest to say so.

### 2. Detector: metric output + multi-target locking — `comp1/vision/detector.py`

The current detector silently discards every blob but the largest, every frame — which is exactly
the multi-red oscillation problem. Restructure:

```python
@dataclass
class Target:
    cx: float
    cy: float
    radius_norm: float  # from cv2.minEnclosingCircle — a direct linear measure
    area_ratio: float
    circularity: float
    bearing_deg: float  # signed, + = right
    elevation_deg: float  # signed, + = above
    distance_m: float
    position: str  # kept for back-compat


@dataclass
class Detection:
    found: bool
    targets: list[Target]  # all candidates, nearest first
    target: Target | None  # the locked primary
```

Keep `cx`, `area_ratio`, `position` as `@property` delegates to `.target` so **every existing test
stays green** — this is the cheap way to make a breaking change non-breaking.

Add **target locking with hysteresis**: prefer the candidate whose bearing is closest to the
previously locked target's, not merely the biggest. This is what stops the drone ping-ponging
between two similarly-sized red circles. Lock decays after N lost frames.

Use `radius_norm` (not `sqrt(area)`) for distance — more directly linear and less sensitive to the
morphological open.

### 3. Rewrite `_approach()` as proportional control — `comp1/interpreter.py`

With real bearing and distance, the bang-bang loop becomes a readable controller — and, more
importantly, becomes something a student can *rebuild themselves* out of the new blocks:

```text
if |bearing| > BEARING_DEADBAND (≈8°):  rotate by clamp(|bearing|, 10, 45)
elif distance > stop_distance:          move forward clamp(distance-stop, 20, 100) cm
else:                                    done
```

Two hardware realities to encode: Tello ignores rotations below ~10°, and its minimum translation is
20 cm — so the deadband and the `clamp(...,20,...)` floor are not arbitrary, and final position
error is bounded at ~20 cm. Replace `approach_stop_area` with `approach_stop_distance_m`.

### 4. Adapter: telemetry + mission pads — `comp1/drone/base.py` and all three adapters

The ABC currently exposes nothing but `battery()`. Add, with **default implementations on the ABC**
(raise `NotSupported` or return a sentinel) so existing adapters don't all have to change at once:

| Method | Purpose |
|---|---|
| `height_cm()`, `attitude()`, `tof_cm()` | telemetry — all already in djitellopy, all unused |
| `supports_mission_pads` (property) | capability flag; `False` on Mock/standard Tello |
| `enable_mission_pads(on, direction=0)` | `direction=0` (downward) keeps the forward camera free |
| `mission_pad_id()` → `int` (−1 = none) | localisation fix |
| `mission_pad_xyz()` → `(x, y, z)` cm | offset from pad centre |

`enable_mission_pads` must be called **after** `connect()` ([tello.py:12-14](comp1/drone/tello.py#L12-L14)).

### 5. Localisation — new `comp1/nav/pose.py`

A dead-reckoning integrator that consumes issued `move`/`rotate` commands to maintain
`(x, y, z, heading)` in the arena frame, and **snaps to truth whenever a mission pad is seen**. This
one component unlocks three separate features:

- **Victim dedup** — project `pose + bearing + distance` into arena coordinates, and merge
  detections within a ~0.8 m radius. Without this, "mark *all* the victims" is unscoreable, because
  a patrolling drone re-finds victim #1 on every pass.
- **Point B** — `distance_to_destination` / `bearing_to_destination` as sensed values.
- **Drift visibility** — show students their accumulated error, which is a genuinely good teaching
  moment about why real robots need localisation.

**On anti-hardcoding (requirements §4):** exposing *relative, sensed* quantities (distance/bearing to
target, to home, to destination) is fully compatible with the rule — these are measurements, not
coordinates. Do **not** add a `fly to (x, y)` block. A `go to pad N` block is the borderline case and
should be deferred until the organiser rules on §3.4. The strongest existing defence stays intact:
marker positions change between rounds, so memorising them is worthless.

### 6. Expression system — `comp1/protocol.py` (v2) + `comp1/interpreter.py` + `frontend/blocks.js`

The headline change. Introduce a discriminated-union value node:

```python
class NumberLit(BaseModel):
    kind: Literal["number"]
    value: float


class SensorRead(BaseModel):
    kind: Literal["sensor"]
    sensor: Literal[...]


class VarRead(BaseModel):
    kind: Literal["var"]
    name: str


class BinOp(BaseModel):
    kind: Literal["binop"]
    op: ...
    left: Value
    right: Value


class UnOp(BaseModel):
    kind: Literal["unop"]
    op: ...
    operand: Value


Value = Annotated[
    NumberLit | SensorRead | VarRead | BinOp | UnOp, Field(discriminator="kind")
]
```

Sensor vocabulary: `target_visible`, `target_distance_cm`, `target_bearing_deg`,
`target_elevation_deg`, `target_count`, `found_count`, `battery`, `height_cm`,
`distance_to_home_cm`, `bearing_to_home_deg`.

Changes that follow:
- `Block.cm/deg/n` become `int | Value`; `Condition` is replaced by a `Value` that evaluates truthy.
- New ops: `set_var`, `while`, `wait`.
- Interpreter gains the missing `_eval(node, scope)` with a **recursion-depth cap**; `_cond`
  delegates to it.
- **Range validation moves from parse time to run time.** Today pydantic enforces `cm ∈ [20,500]`
  statically; a computed `cm` can't be checked that way. Add a `_clamp_cm` that clamps and emits a
  warning event rather than raising — a student whose arithmetic yields `move forward 3` should get
  a visible nudge, not a crashed mission.
- `version: Literal[1]` → `2`, with a v1 loader kept so saved programs survive.
- Frontend: `condJson` ([blocks.js:63-71](comp1/frontend/blocks.js#L63-L71)) is the flattening
  chokepoint — replace it with a **recursive** `valueJson`, add `output: "Number"` block defs, and
  switch `move`/`rotate` from `getFieldValue` to value sockets with shadow number blocks (so the
  default UX is unchanged for beginners).

### 7. Python pathway — new `comp1/api.py`

A synchronous student-facing facade over the same primitives:

```python
drone = Drone()
drone.takeoff()
while not drone.sees_target():
    drone.turn_right(20)
t = drone.target()  # .distance_m  .bearing_deg  .elevation_deg
drone.mark_found()
```

**Design constraint from the existing docs:** the platform must never `exec()` generated Python
([implementation-plan.md:17](docs/plans/2026-07-28-implementation-plan.md)). So the Python pathway is
*not* a code box in the browser — it is the student's own `.py` file, launched by a new
`python -m comp1 --script my_mission.py`. Running it in-process alongside the server keeps the video
feed, telemetry panel, and — critically — the **EMERGENCY STOP button** live. Every `api.Drone`
method checks a stop flag so e-stop can interrupt a student loop.

### 8. Mission & scoring — new `comp1/mission.py`

Nothing in the repo currently knows what a "run" is. `found_count` is an int on an interpreter
instance that is discarded when the run ends.

- `VictimRegistry` — world-position dedup (see layer 5), the basis for a real score.
- `Run` — timer, start/end, event log, persisted to JSON (the first persistence in the project).
- Scoring: victims correctly marked, false positives, reached B, time.
- Judge/replay view.

### 9. Simulator upgrades — `comp1/sim/`

To keep the sim a trustworthy stand-in:

- **Drive the renderer from `CameraIntrinsics`**, fixing the FOV error in risk #2.
- **Get the minimap out of the sensor path.** [render.py:55-71](comp1/sim/render.py#L55-L71) burns a
  minimap — including red dots — into the very frame the detector consumes. It's currently harmless
  only because the dots fall under `min_area_ratio`; that is a tripwire waiting for someone to tune
  that constant. `get_frame()` should return the clean camera image and the minimap should be
  composited server-side for display only.
- Add `size_m` to `Marker` ([world.py:8-12](comp1/sim/world.py#L8-L12)) instead of the global
  `VICTIM_RADIUS`.
- Add mission pads, a start point A and destination B to `World`; `SimDrone` reports a pad id when
  within the detection cone and altitude band.
- Add a battery model so students meet the real constraint in the sim first.
- Optional realism: marker foreshortening by incidence angle (currently billboards always face the
  camera, which flatters distance estimation on off-normal markers).

### Deferred: the "train your own model" pathway

Not in this plan, but don't foreclose it. Make the detector a strategy interface
(`TargetDetector` protocol) with `HSVCircleDetector` as the default, so a `LearnedDetector` can slot
in later without touching the interpreter. Add frame-capture-with-labels to the sim now, since it is
nearly free and produces the training set that phase would need.

---

## Phased implementation

| Phase | Scope | Why this order |
|---|---|---|
| **0. Hardware truth** | Calibrate the Tello camera (chessboard → `f_norm`). Bench-test mission pads: does `mon` + `streamon` coexist? What is the real detection altitude band and cone? Measure `move`/`rotate` accuracy over 10 trials. | Retires risks #2, #3, #5. Every later phase's constants come from here. **Do not skip.** |
| **1. Metric sensing** | Layers 1–3: `camera.py`, `Target`/`Detection` rework with locking, proportional `_approach()`. Sim renderer onto shared intrinsics; minimap out of the sensor path. | Self-contained, testable in the sim, immediately improves the existing product. |
| **2. Expression blocks** | Layer 6: protocol v2, `_eval`, runtime clamping, recursive `valueJson`, new Number/variable blocks. | The user-visible payoff — "get distance"/"get direction" become real. Depends only on Phase 1. |
| **3. Python pathway** | Layer 7: `comp1/api.py` + `--script` runner + e-stop interruption. | Cheap once Phase 1 exists; serves the cohort most likely to enter. |
| **4. Localisation & A→B** | Layers 4, 5, 9-pads: adapter telemetry + pads, `nav/pose.py`, sim pads/A/B. | Gated on Phase 0's pad verdict. Have a dead-reckoning-only fallback ready. |
| **5. Competition ops** | Layer 8: registry, scoring, run log, replay. Plus the HSV tuning panel (promote from deferred — it is an on-site necessity) and program save/load. | Needed for a real event, not for a demo. |

---

## Critical files

**New:** `comp1/vision/camera.py`, `comp1/vision/calibrate.py`, `comp1/nav/pose.py`,
`comp1/api.py`, `comp1/mission.py`.

**Heavily modified:**
- [comp1/protocol.py](comp1/protocol.py) — value-node union, v2 bump, `Block` field types
- [comp1/interpreter.py](comp1/interpreter.py) — `_eval`, rewritten `_cond`/`_exec`/`_approach`
- [comp1/vision/detector.py](comp1/vision/detector.py) — `Target`, multi-target, locking, metrics
- [comp1/vision/config.py](comp1/vision/config.py) — marker size, intrinsics ref, distance thresholds replacing area thresholds
- [comp1/frontend/blocks.js](comp1/frontend/blocks.js) — recursive `valueJson`, Number blocks, value sockets
- [comp1/sim/render.py](comp1/sim/render.py) / [world.py](comp1/sim/world.py) / [drone.py](comp1/sim/drone.py) — intrinsics, marker sizes, pads, clean frames
- [comp1/drone/base.py](comp1/drone/base.py) + [tello.py](comp1/drone/tello.py) / [mock.py](comp1/drone/mock.py) — telemetry, pads, capability flags
- [comp1/server.py](comp1/server.py) — a `telemetry` event type; today **no numeric reading ever reaches the frontend**, so distance/bearing need a channel

**Reuse rather than rebuild:** `djitellopy` already implements every mission-pad and telemetry call
needed (`get_mission_pad_id`, `go_xyz_speed_mid`, `get_height`, `get_distance_tof`, …) — the adapter
just has to call them. The sim's existing pinhole projection at
[render.py:42](comp1/sim/render.py#L42) is already the correct `f·R/d` relation; invert it rather
than writing new geometry. `VisionConfig` is already the central tuning point.

---

## Verification

**Unit / integration (extends the existing 33-test pytest suite, `asyncio_mode=auto`):**
- `camera.py` round-trip: `distance_m(radius_norm(d)) == d` across 0.5–6 m; bearing at frame edge equals half the HFOV.
- Detector: two red circles in frame → both in `.targets`, lock stays on the same one across frames where sizes cross over (the regression test for the oscillation bug).
- Detector on synthetic frames at known simulated distances → estimate within 5% out to 4 m.
- Protocol v2: nested `BinOp` parses; a v1 program still loads; `move forward (5)` clamps and emits a warning rather than raising.
- Interpreter `_eval`: arithmetic, comparison, variables, deep-nesting depth cap.
- Pose: dead-reckon a square path, assert closure error; assert a pad sighting snaps pose to truth.
- Registry: same victim seen from three poses → count 1; two victims 1.5 m apart → count 2.

**Sim end-to-end** — extend [tests/test_sim_e2e.py](tests/test_sim_e2e.py), which already runs the
real detector + real interpreter with no hardware:
- Full A→B mission: takeoff at A, search, mark all 3 victims exactly once, reach B, land. Assert `found_count == 3` **and** registry size 3 (catches double-counting).
- Same mission expressed via the Python API — assert identical outcome. This is the check that both pathways really share one engine.
- A student program using expression blocks (`repeat until target_distance_cm < 120`) completes.
- Multi-red arena: victim beside a red distractor → the distractor is never marked.

**Manual / hardware:**
- Phase 0 bench tests above, recorded as a checked-in results doc.
- `python -m comp1 --drone sim` — confirm the UI shows live distance/bearing telemetry, that value blocks drag into `move`, and that E-STOP interrupts both a block program and a Python script.
- Full arena dry-run at competition scale before finalising `min_area_ratio` and marker size.

---

## Open items needing an external answer

1. **Organiser, §3.4:** are mission pads permitted in the arena, and does a `go to pad` block violate §4? Phase 4's shape depends on this.
2. **Organiser, §3.2:** confirm the "safe distance" threshold — this plan replaces the area proxy with metres, so the number becomes directly specifiable.
3. **Organiser:** marker physical size. This plan recommends A3 or larger; detection range scales linearly with it and 0.25 m only reaches ~4 m.
4. **Safety:** confirm whether `mark_found` may stop using a back-flip (risk #4).
5. The requirements document is external and not in the repo — worth vendoring a copy under `docs/` so these citations stop being guesses.

Per existing convention, the accepted version of this plan should be committed to
`docs/plans/` as a dated markdown file alongside the existing planning docs.

---

## Phase 1 outcome

**Delivered 2026-07-31.** Test suite 33 → 78, all passing; full mission verified end-to-end over the
WebSocket against `SimDrone`. Design written up in
[Target sensing design](../specs/2026-07-31-target-sensing-design.md).

Built as planned: `comp1/vision/camera.py`; `Target`/`Detection` rework with multi-target output and
`TargetTracker` bearing-hysteresis lock; proportional `_approach()`; simulator on shared intrinsics;
`DroneAdapter.annotate()` keeping the minimap out of the sensor path; a `telemetry` event and live
distance/direction readout in the UI.

### What implementation changed about the plan

- **Three of the five risks turned out to be live bugs, not just design gaps**, and were fixed
  rather than merely documented: the ~4 m detection ceiling (risk #1) is now computable via
  `VisionConfig.max_detect_range_m` and invertible via `min_area_ratio_for_range`; the sim's
  horizontal-vs-diagonal FOV error (risk #2) is gone and pinned by a test.
- **A fourth bug surfaced that the plan did not anticipate.** `render.py` truncated marker radii
  with `int()` instead of rounding, losing up to a full pixel and biasing every simulated range
  estimate long — ~6% at 19 px. Found only because the new accuracy tests compared estimates against
  rendered ground truth, which is a good argument for keeping that test class.
- **`Detection` back-compat properties worked as hoped.** Delegating `.cx`/`.area_ratio`/`.position`
  to `.target` meant the detector rewrite touched no existing detector test. The tests that did need
  changing were the ones testing behaviour that deliberately changed — the approach algorithm, the
  sim's field of view, and the e2e stop distance.
- **`tests/__init__.py` was added** so `tests/helpers.py` is importable as a package module.

### Carried into later phases

- `TELLO_DFOV_DEG = 82.6` is still a spec figure. Phase 0's chessboard calibration remains the
  highest-value outstanding task — every distance the platform reports inherits its error.
- Risks #3 (mission pads unwired and unproven), #4 (`mark_found` back-flip beside a tripod), and #5
  (dead-reckoning drift) are untouched and still need hardware time.
- The five open items above are unchanged; §3.2's safe distance is now `approach_stop_distance_m`
  and can be answered in metres.
