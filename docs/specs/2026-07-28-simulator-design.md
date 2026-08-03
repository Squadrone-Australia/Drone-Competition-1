# Simulator Design — Random Arena for Hardware-Free Testing

**Status:** Approved 2026-07-28, updated 2026-08-03 · **Depends on:** platform MVP (`feat/platform-mvp`)

> **Partly superseded 2026-07-31** by
> [Target sensing design](2026-07-31-target-sensing-design.md). Three details below are now wrong:
> the camera is no longer a local 83° horizontal FOV (it shares `CameraIntrinsics` with the real
> hardware, ~70° horizontal); `Marker` carries per-marker `size_m`/`height_m` rather than one global
> victim radius; and the minimap is **not** drawn into `get_frame()` — it moved to
> `SimDrone.annotate()` so the detector never sees it.

## Purpose

Test student block programs and the full vision/approach pipeline (real HSV+contour detector, real interpreter) without a Tello or a venue. The arena is randomly generated per launch — mirroring the competition's "marker locations change between rounds" rule — with a `--seed` option for reproducible layouts.

## Approach

2.5D "billboard" simulator rendered with OpenCV (no new dependencies). Rejected alternatives: a full 3D renderer (heavy dependency, no testing benefit) and a state-only fake feeding `Detection` objects directly (bypasses the real vision code, defeating the purpose).

## Components (`comp1/sim/`)

### `world.py`
- `Marker(x, y, kind)` — a billboard on an arena wall. Kinds: `victim` (red circle, 0.25 m diameter ≈ A4), distractors `red_square`, `blue_circle`, `green_triangle`, `yellow_square`.
- `World.random(seed=None, n_victims=3, n_distractors=4, size_m=4.0)` — square arena; markers placed along the four walls at height 1.0 m, ≥ 0.6 m apart, ≥ 0.5 m from corners. `seed=None` → different world each launch.

### `drone.py`
- `SimDrone(world, noise=0.0, delay=0.3)` implements the existing `DroneAdapter` ABC.
- Pose: `x, y` (m), `z`, `heading` (deg, 0 = +y, clockwise positive). Starts at arena centre.
- `move`/`rotate` update pose exactly (cm-accurate); position clamped 0.2 m from walls. `takeoff` → z = 1.0 (same height as markers), `land`/`emergency` → z = 0. Flight commands before takeoff raise `RuntimeError` (surfaces as a mission error, like a real Tello).
- `noise > 0` adds Gaussian drift (σ = noise × distance per move, σ = 2° per rotate) for venue rehearsal; default 0 keeps tests deterministic.
- `delay` seconds per command simulates flight pacing (so block highlighting is watchable); tests pass `delay=0`.

### `render.py`
- `render(world, pose, w=640, h=480)` — pinhole projection with Tello-like 83° horizontal FOV: marker frame-x from relative bearing, apparent size ∝ 1/distance, vertical offset from z difference; painter's algorithm (far → near); flat wall/floor background split at the horizon.
- Top-down **minimap inset** (top-right): arena outline, marker dots (red = victim, grey = distractor), drone arrow showing heading.

## Integration

- CLI: `python -m comp1 --drone sim [--seed N] [--noise 0.05]`; simulator mode is the default.
- Frames flow through the same server video loop, detector, telemetry feed, and overlay as Tello
  frames. The browser also receives simulator-only `scene` and `pose` display data for its plan and
  3D views.
- The header's drone button switches from the simulator to a real Tello and then becomes **Use
  Simulator**. The server retains the configured `SimDrone`, so switching back preserves the seed,
  scenery, and teacher-edited layout. See
  [Runtime drone switching](2026-08-03-drone-mode-switching.md) for the protocol and safety rules.

## Testing

1. World: seeded generation is reproducible; counts and wall placement respected.
2. Drone: pose math (rotate 90° then forward moves along +x); pre-takeoff commands raise; clamping at walls.
3. Render→detect integration: facing a victim → `found=True` with correct left/centre/right; facing each distractor kind → `found=False`.
4. End-to-end: interpreter runs `takeoff → repeat until marker seen [turn cw 20°] → approach → signal found → land` against `SimDrone(delay=0)`; assert `found_count` reaches 1 and final pose is within ~0.7 m of a victim marker — proving search, detection, and approach converge with zero hardware.

## Out of scope

- Collision/obstacle simulation, battery model, wind. Flips are a no-op on pose (signal only).
- Rendering marker foreshortening at oblique angles (billboards always face the camera).
