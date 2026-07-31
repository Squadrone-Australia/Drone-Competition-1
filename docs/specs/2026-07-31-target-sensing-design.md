# Target Sensing Design — Metric Distance, Bearing, and Multi-Target Tracking

**Status:** Implemented 2026-07-31 (Phase 1) · **Plan:** [2026-07-31-target-sensing-and-expressions.md](../plans/2026-07-31-target-sensing-and-expressions.md)

## Purpose

Turn the victim marker from an opaque thing the `approach_marker` block chases into a **measured
object** students can read: how far away it is, and in which direction. This is the sensing
foundation for value-returning blocks (Phase 2), the Python pathway (Phase 3), and victim dedup for
A→B scoring (Phase 4).

Previously the only range cue anywhere in the system was `area_ratio >= 0.08` — a frame-area
fraction with no physical meaning — and steering used a three-bucket `left`/`center`/`right` string.

## Camera model (`comp1/vision/camera.py`)

A pinhole model, with every quantity **normalised by frame width** so one constant describes the
960×720 Tello stream and the 640×480 simulator frame identically:

```
f_norm = focal_length_px / frame_width
```

| Quantity | Formula |
|---|---|
| bearing (+ = right) | `atan((cx_norm - 0.5) / f_norm)` |
| elevation (+ = up) | `-atan((cy_norm - 0.5) * aspect_hw / f_norm)` |
| distance | `f_norm * R / radius_norm` |
| apparent radius | `f_norm * R / distance` |

where `R` is the marker's real radius in metres and `radius_norm` its apparent radius over frame
width. Frame width cancels in the distance relation, which is why the estimate is
resolution-independent (`test_distance_is_resolution_independent`).

### DJI's 82.6° is a *diagonal* FOV

`CameraIntrinsics.from_dfov(82.6, 4, 3)` → `f_norm ≈ 0.711`, i.e. **~70° horizontal**, ~55° vertical,
focal ≈ 684 px on a 960-wide stream.

The simulator previously hard-coded `FOV_H = math.radians(83)` as *horizontal*, making its camera
~13° too wide. Anything tuned against the old simulator would not have transferred to the arena.
Both now import the same `TELLO_INTRINSICS`, and `test_diagonal_fov_is_not_the_horizontal_fov` pins
the distinction.

**`TELLO_DFOV_DEG = 82.6` is a spec sheet figure, not a measurement.** Every distance the platform
reports inherits its error until a real chessboard calibration replaces it. This is the single
highest-value item in Phase 0.

### Accuracy

Range error is proportional to segmentation error over apparent radius: `σ_d/d = σ_r/r`. For a
0.25 m marker on a 960-wide stream, apparent diameter is 171 px at 1 m and 43 px at 4 m, so a
realistic ±2 px error gives roughly ±2% at 2 m and ±7% at 6 m. **Usable to about 5 m; degrading
beyond.** Accuracy improves linearly with marker size.

### `min_area_ratio` is a range limit

The speck filter also sets the smallest detectable blob, and therefore the furthest detectable
marker:

```
max_range = f_norm * R / sqrt(min_area_ratio * aspect_hw / pi)
```

At the default `0.002` a 0.25 m marker **disappears at ~4.1 m** — in a 6 m arena a drone can face a
victim and see nothing. `VisionConfig.max_detect_range_m` surfaces this and
`CameraIntrinsics.min_area_ratio_for_range` inverts it, so the gate can be chosen from a wanted
range rather than by feel. Marker size is the physical lever: 0.5 m roughly doubles the range.

## Detector (`comp1/vision/detector.py`)

### `Target`

One candidate marker: `cx`, `cy` (normalised centroid), `radius_norm`, `area_ratio`, `circularity`,
`bearing_deg`, `elevation_deg`, `distance_m`, `position`.

Apparent size comes from `cv2.minEnclosingCircle` — a direct linear measure — rather than
`sqrt(area/pi)`. The circularity gate (≥ 0.85) runs first, so the blob is near-circular by the time
it is measured and the two agree.

### `find_targets` returns everything

The old detector kept only the largest contour and silently discarded the rest **every frame**. It
now returns all candidates that clear the area and circularity gates, sorted nearest-first.
`Detection` carries `.targets` (all) and `.target` (the primary), and exposes `.cx` / `.area_ratio`
/ `.position` as back-compat properties.

### `TargetTracker` — frame-to-frame lock

With two similar red markers in view, "largest blob this frame" flips between them as noise moves
their apparent sizes, and the approach controller oscillates instead of committing.

The tracker keeps the candidate closest in **bearing** to the previous lock, provided the jump is
under `lock_max_bearing_jump_deg` (25°); otherwise it re-acquires the nearest. The lock survives
`lock_lost_frames` (5) missed frames before decaying, so a brief occlusion does not drop it.

`test_lock_survives_the_other_target_becoming_larger` is the regression test: it asserts the primary
target stays put *and* is genuinely no longer the nearest candidate.

Use `TargetTracker` for anything stateful; `detect_red_circle` remains the stateless single-frame
helper used by tests and one-shot checks.

## Approach controller (`Interpreter._approach`)

Proportional on measured error, replacing fixed 15°/30 cm bang-bang steps:

```
if |bearing| > 8°:            rotate by clamp(|bearing|, 10, 45) degrees
elif distance > stop (1.0 m): move forward clamp(distance - stop, 20, 100) cm
else:                          done
```

The clamps encode **hardware limits, not preferences**: the Tello ignores rotations below ~10° and
refuses translations below 20 cm. Because of the 20 cm floor, the controller stops rather than
lurching when less than one step of range remains — bounding final position error at ~20 cm
(`test_stops_without_overshooting_below_the_minimum_step`).

`approach_stop_area` (frame fraction) is replaced by `approach_stop_distance_m` (metres), so the
§3.2 "safe distance" threshold is now something organisers can state directly in real units.

## Sensor-path purity

`DroneAdapter.get_frame()` must return only what a real camera would see. Debug graphics go through
the new `DroneAdapter.annotate()`, applied by the server to a **display copy** after detection has
run.

This exists because `SimDrone` was drawing its minimap — including dots in exactly the victim's red
`(0,0,220)` — into the very frame the detector consumed. It was harmless only because the 4 px dots
fell under `min_area_ratio`, which made an unrelated tuning constant load-bearing for correctness.

## Simulator corrections

- Camera driven by the shared `SIM_INTRINSICS` (= `TELLO_INTRINSICS`) instead of a local `FOV_H`.
- `Marker` gained `size_m` and `height_m`; apparent size no longer uses one global `VICTIM_RADIUS`
  for every marker kind.
- Marker radius is **rounded, not truncated**. `int()` lost up to a full pixel off every radius,
  which read back as a systematic over-estimate of range (~6% at 19 px) — the sim reported targets
  as further away than they were.

## Telemetry

New `{"type": "telemetry", visible, count, distance_cm, bearing_deg, elevation_deg}` event,
broadcast on change. Before this **no numeric sensor reading reached the frontend at all** — the
student only ever saw values burned into the video overlay. The UI shows them live, which is also
the channel Phase 2's value blocks will read.

## Tuning constants (`comp1/vision/config.py`)

| Constant | Default | Note |
|---|---|---|
| `marker_diameter_m` | 0.25 | drives distance; larger extends range linearly |
| `min_area_ratio` | 0.002 | ~4.1 m ceiling at the default marker size |
| `circularity_min` | 0.85 | circle ≈ 0.95, square ≈ 0.785 |
| `approach_stop_distance_m` | 1.0 | §3.2, unconfirmed |
| `approach_bearing_deadband_deg` | 8.0 | ~14 cm lateral at 1 m |
| `approach_min/max_turn_deg` | 10 / 45 | 10° is the Tello's usable floor |
| `approach_min/max_step_cm` | 20 / 100 | 20 cm is the Tello's floor |
| `lock_max_bearing_jump_deg` | 25.0 | lock hysteresis |
| `lock_lost_frames` | 5 | frames before the lock decays |

## Testing

`tests/helpers.py` builds synthetic `Detection`s whose geometry is self-consistent with the
configured camera, so control-logic tests need no pixels.

- `test_camera.py` — FOV round-trips, the diagonal-vs-horizontal distinction, distance round-trip
  across 0.5–6 m, resolution independence, the area-gate range relation.
- `test_distance_estimation.py` — estimates against **rendered ground truth** at 1.0–3.5 m within
  6%; elevation sign; detection dying past the gate range; a larger marker extending it.
- `test_multi_target.py` — all candidates reported, nearest-first ordering, bearing signs, the lock
  regression, re-acquisition after loss, and that the minimap is absent from the sensor frame.
- `test_approach.py` — proportional turn and step sizing, clamping at both ends, deadband, the
  no-overshoot stop, and lost-target abort.

## Out of scope for Phase 1

Mission pads and telemetry beyond `battery()` on the adapters; pose estimation and victim dedup;
the expression system; the Python pathway; marker foreshortening at oblique angles (billboards still
always face the camera, which flatters distance estimation on off-normal wall markers).
