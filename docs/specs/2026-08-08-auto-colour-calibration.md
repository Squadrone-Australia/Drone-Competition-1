# Automatic Marker Colour Calibration

**Date:** 2026-08-08 · **Status:** Design

## Purpose

On-site HSV re-tuning (requirements §3.1) currently requires an operator to drag a box tightly
inside a marker in the calibration dialog. `suggest_hsv` then does the rest: it finds the dominant
colour on the circular hue axis, discards glare and shadow pixels, and proposes robust bands.

The drag is the only manual step, and it is the one most likely to go wrong under time pressure at a
venue — a loose box picks up floor, a box over a specular highlight picks up white.

This design removes the drag. The operator points the drone at any red marker and presses one
button; the system locates the marker itself and proposes bands from it. Everything downstream —
preview, sliders, Apply, venue profiles, TOML download — is unchanged.

**The victim marker is guaranteed red.** That guarantee is what makes this simple, and it is the
premise the whole design rests on. If markers ever stop being red, `find_marker_roi` below must be
replaced, not widened.

## Scope

Operator-triggered only. Calibration stays where it is today: an explicit setup action, refused
while a mission is running, never invoked from `comp1/interpreter.py` or `comp1/api.py`.

Explicitly rejected:

- **Self-calibration at mission start.** Detection behaviour would change silently between runs,
  which defeats the purpose of resetting every run so a student can judge their own change.
- **Continuous adaptation in flight.** It would put band-fitting on the live sensor path, where its
  failure modes — slow drift, locking onto a wall — are near-impossible to diagnose from a flying
  aircraft.

No block, no sensor, no change to `protocol.py`, `interpreter.py`, `api.py`, or `blocks.js`. §4
anti-hardcoding is untouched: this calibrates *appearance*, not position.

## Approach

A wide red prior finds the marker; the existing `suggest_hsv` tightens the bands to it.

Because red is guaranteed, no colour-agnostic circle finder is needed. `find_targets` already is a
marker finder — colour mask, area gate, circularity gate, results sorted nearest-first. Running it
against a deliberately loose HSV config turns it into the region locator with no new vision code.

Nearest-first is the correct pick order here for a specific reason: `distance_m` is derived from
apparent radius, so `targets[0]` is also the **largest** blob in the frame, which is the candidate
with the most pixels available for computing statistics.

## Components

All four additions live in `comp1/vision/calibration.py`.

### `RED_PRIOR`

A module-level `VisionConfig` built with `dataclasses.replace(DEFAULT_CONFIG, ...)`, widening only
the colour bands:

| Field | Default | Prior |
|---|---|---|
| `lower1` / `upper1` | `(0,100,80)` / `(10,255,255)` | `(0,60,40)` / `(15,255,255)` |
| `lower2` / `upper2` | `(170,100,80)` / `(180,255,255)` | `(160,60,40)` / `(180,255,255)` |

The saturation and value floors can be this generous because the *hue* gate is what excludes
scenery. Sceneries are blue-dominant in BGR by construction (`B >= G >= R`, low saturation), so a
lower saturation floor does not bring walls or floor into red hue range. The area and circularity
gates are inherited from `DEFAULT_CONFIG` unchanged.

### `find_marker_roi(frame_bgr) -> tuple[float, float, float, float]`

Runs `find_targets(frame_bgr, RED_PRIOR)` and takes `targets[0]`. Converts that target into a
normalised ROI box centred on `(cx, cy)`, so the sample sits well inside the disc and clear of its
anti-aliased rim. Coordinates are clamped to `[0, 1]`.

The two axes do not get the same half-extent. `radius_norm` is normalised by frame **width**
(§ camera.py is the single source of truth for geometry), while `suggest_hsv` scales `y0`/`y1` by
frame height. So:

```
half_x = 0.7 * radius_norm
half_y = 0.7 * radius_norm * width / height
```

Using `half_x` on both axes would produce a box 33% too short vertically on a 4:3 frame — still
inside the marker, but sampling fewer pixels than intended.

The 0.7 factor is below the inscribed square's `1/sqrt(2)` ≈ 0.707, so the box stays strictly
interior even for a slightly off-centre centroid.

Raises `CalibrationError("no red marker in view — point the camera at one and try again")` when no
candidate clears the gates.

### `auto_suggest_hsv(frame_bgr) -> tuple[dict, list[float]]`

`find_marker_roi` followed by `suggest_hsv`, returning both the proposed bands and the ROI they came
from. The ROI is returned so the browser can draw it: an auto-calibrator that will not show what it
looked at is hard to trust at the moment it gets something wrong.

### `check_coverage(frame_bgr, cfg) -> None`

Builds `color_mask(frame_bgr, cfg)` and raises
`CalibrationError("these ranges match too much of the scene — try a tighter shot of the marker")`
when the mask covers more than `MAX_MASK_COVERAGE = 0.25` of the frame.

This is the guard that keeps calibration from quietly invalidating
`test_scenery_alone_is_never_a_victim`. Over-wide saturation or value bounds produce a candidate
that looks correct in its own preview — the marker *is* isolated in the shot being calibrated on —
and then flags a wall on the next frame from a different angle.

The gate runs in `server.py` on the candidate config produced by `vision_auto` and by
`vision_sample`, before either responds. It deliberately does **not** run on `vision_preview` or
`vision_apply`: those are the operator moving sliders by hand, and a person deliberately widening a
band to see what happens should get the preview they asked for rather than an error. The gate
guards *proposals*, which are the ones an operator is likely to accept without inspecting.

## WebSocket contract

One new message type, handled inside the existing guarded branch in `server.py` alongside
`vision_sample` / `vision_preview` / `vision_apply` / `vision_reset`, and subject to the same
refusals (drone switching in progress, mission active).

Request:

```json
{"type": "vision_auto"}
```

Response reuses the existing suggestion message, with one added field:

```json
{"type": "vision_suggestion", "config": {...}, "preview_jpeg": "...", "roi": [x0, y0, x1, y1]}
```

`roi` is normalised `0..1` and is also added to the response for `vision_sample`, where it simply
echoes the ROI the operator dragged. Failures return the existing
`{"type": "vision_error", "message": ...}`.

Because the response type is unchanged, the accept / preview / Apply / profile flow downstream needs
no protocol work.

## Frontend

In the calibration dialog, one button beside **Refresh frame**, labelled **Find marker for me**. It
sends `{"type": "vision_auto"}` and sets the status line to "Looking for a red marker…".

The existing `vision_suggestion` handler additionally draws `message.roi` onto the frozen canvas
using the same selection rectangle style as a manual drag, so the automatic and manual paths look
identical once complete and the operator can see the sampled region either way.

The instruction text on frame capture changes to name both routes: "Press *Find marker for me*, or
drag a box tightly inside the coloured marker."

## Testing

Simulator frames are the fixtures. `SimDrone` renders genuine red discs through the real renderer
using the same intrinsics the detector inverts, so a frame from it is a real detector input, not a
mock.

| Test | Asserts |
|---|---|
| ROI on a sim frame with a victim | returned box lies inside the marker's contour |
| Scenery-only frame (victims cleared) | raises `CalibrationError`, does not calibrate on a wall |
| Round trip | bands from `auto_suggest_hsv` still detect the marker they were derived from |
| Auto bands against scenery | `detect_red_circle` finds nothing on victim-free frames |
| Colour-cast recovery | a red disc under a warm/dim cast that `DEFAULT_CONFIG` misses is detected with auto-calibrated bands |
| All-red frame | trips `check_coverage` |
| Manual path coverage gate | an over-wide manual selection is now refused |

The colour-cast test is the one that demonstrates the feature is worth having; the others are
regressions against its failure modes.

## Risks

**A wide prior is a wider door.** `RED_PRIOR` exists only inside calibration and never reaches the
detector, but if its floors were dropped far enough that warm scenery entered red hue range, the
located "marker" could be background. The circularity gate and `check_coverage` are the two
defences; the scenery colour invariant (`B >= G >= R`) is the third and is already enforced by
existing tests.

**The destination sign calibrates identically.** In the corridor scenery the `DESTINATION` marker is
byte-identical to a victim in the camera, so auto-calibration may sample it. This is harmless —
same colour, same bands — but operator-facing text must say "marker", never "victim". The detector
must not learn to tell them apart; that distinction is the student exercise.
