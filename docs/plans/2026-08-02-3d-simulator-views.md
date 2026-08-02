# 3D simulator: real scenery, third-person view, animated flight

**Date:** 2026-08-02 · **Status:** implemented

## Problem

The simulator was a competent detector test rig and a poor teaching tool.

* The camera view painted two flat colour bands with markers pasted on. In an empty
  corner of the arena, `fly forward 100` produced a byte-identical frame — the block
  looked like a no-op.
* There was no view of the drone at all. A student could see what the drone saw, but
  not where it was, which way it faced, or why it had lost the marker.
* Commands were `sleep(delay)` then a jump to the new pose. Nothing moved; frames cut
  between stills.

## Approach

Three changes, sharing one camera model.

### 1. The camera view became a projected room

`comp1/sim/scene.py` is the new projection layer: a `Camera` built from the *same*
`SIM_INTRINSICS` the detector inverts, plus near-plane polygon clipping, a 2D viewport
clip, and `fill_quad` / `draw_line`. `comp1/sim/render.py` uses it to draw a floor grid,
four directionally shaded walls with skirting and corner seams, a ceiling with strip
lights, and per-marker posts and contact shadows.

The marker billboard was deliberately left alone — apparent radius is still
`focal * radius_m / d`, exactly what `CameraIntrinsics.distance_m` inverts. All existing
range and approach tests pass unchanged, which is the point: the room is new furniture
around an unchanged ruler.

**The palette constraint is the load-bearing part.** Scenery is most of the frame, so every
scenery colour is blue-dominant in BGR and low-saturation. A warm-grey wall would be a red
false positive on every single frame. `test_scenery_alone_is_never_a_victim` sweeps 324
poses (3×3 positions × 12 headings × 3 altitudes) to hold the line.

Cost: ~4 ms/frame at 640×480, against a 100 ms video budget.

### 2. Commands fly instead of teleporting

`SimDrone._animate` drives each command's pose over its duration at 60 Hz, with smoothstep
easing, so any thread polling the pose — the video loop, the browser — sees continuous
motion. Duration now scales with distance and turn angle rather than being a flat `delay`.

Two invariants kept the change cheap:

* every command still ends on the exact arithmetic pose, so tests reading `drone.x`
  straight after a call are unaffected;
* `delay=0` skips animation entirely, so the suite stays instant.

Added along the way: a visual-only `roll`/`pitch` so the airframe tips into a translation
and a `flip` actually tumbles (that flip is the §2.1 find signal, and it was previously
invisible), and an `_abort` flag so `emergency()` cuts a command mid-animation instead of
letting the drone finish flying into a wall.

### 3. Third-person view in the browser

`comp1/frontend/scene3d.js` renders the arena in three.js: floor grid, glass-box walls,
markers on posts facing the room, a quadcopter with counter-rotating props, a blob shadow,
a flight trail, and a wireframe of the onboard camera's FOV — which is what visually links
the two views. `view3d.js` wires it to the socket and the toolbar; the split keeps the
renderer free of protocol and DOM knowledge.

Three camera rigs: **Third person** (chase), **Orbit** (`OrbitControls`), **Map** (top-down; frames
the whole arena, not the drone — a map that slides around under the drone is not a map). They live
in a labelled "3D view" bar rather than as bare icons floating on the canvas: the first version put
unlabelled buttons on the canvas and the third-person view went unnoticed. The bar hides itself
when there is no arena to look at. The camera panel is a draggable window floating over the stage,
with a dock toggle.

Feeds are two new messages: `scene` once on connect, `pose` at 30 Hz. Pose runs 3× the video
rate because at 10 Hz a fast yaw stutters visibly and no browser-side smoothing hides it. The
browser chases the target pose with a framerate-independent exponential, taking the short way
round the circle for heading and roll.

The geometry reused from `Squadrone_Academy/artifacts/squadrone-academy/src/components/Simulator.tsx`
is the drone mesh (body, arms, counter-rotating props, nose light) and the follow-camera idea,
ported from React Three Fiber to plain three.js. Nothing else transferred: that project's mission,
gate and coordinate model does not apply here.

three.js 0.185.1 is vendored (`three.module.min.js`, `three.core.min.js`, `OrbitControls.js`)
and loaded through an import map, because the frontend must work with no network.

### 4. Run always starts from the start pad

Iterating meant hitting Run repeatedly, and each attempt began wherever the last one had stopped —
so the drone's behaviour changed even when the program did not. The server now calls
`drone.reset()` and rebuilds the `TargetTracker` before every run (a stale marker lock would have
the new attempt reacting to something the drone is no longer looking at), and broadcasts
`{"type":"reset"}` so the browser clears the trail, the highlight and the found count. A `⟲ Reset`
button sends the same message for recovering from a stopped or e-stopped attempt without flying
another one; it is refused while a mission is running.

`SimDrone.reset()` re-seeds its RNG but deliberately leaves the arena alone: re-rolling the markers
would make every Run a different problem, and re-seeding makes a `--seed` run repeat exactly, drift
and all.

**On hardware `reset()` is a no-op**, and `DroneAdapter.can_reset` (False everywhere but `SimDrone`)
rides along in the broadcast so the UI can say which of two things happened. With a real Tello the
tracker and counters clear but the aircraft does not move, and the console reads "counters cleared —
the drone has not moved" rather than "back on the start pad". The first version claimed the latter
unconditionally, which would have told a student their drone was on the pad while it hovered where
they left it.

Landing the drone on reset was considered and rejected: a surprise flight command issued by a button
labelled Reset is a worse failure than a button that does less than its name suggests.

Note this leaves a pre-existing hardware sharp edge untouched — if a Tello is still airborne when Run
is pressed, the program's `takeoff` block runs against an already-flying aircraft. That predates this
work and is not something reset should paper over.

## §4 anti-hardcoding

`DroneAdapter.pose()`/`.scene()` put absolute arena coordinates on the wire. They are a one-way
display feed: the interpreter and `comp1/api.py` never call them, no block or sensor exposes
them, and `MockDrone`/`TelloDrone` return `None`. This is noted at all three sites (the ABC,
`SimDrone`, and `CLAUDE.md`) because it is the obvious place for the constraint to erode.

## Verification

200 tests pass (was 162). New coverage: projection maths and clipping (`test_sim_scene.py`),
the scenery false-positive sweep and "the view changes when the drone moves"
(`test_sim_render.py`), threaded sampling proving mid-command animation is visible to other
threads plus the reset invariants (`test_sim_drone.py`), and `scene` / `pose` / `reset` on the
socket including "two identical programs end in the same place" (`test_server.py`).

Beyond that the app was driven in a real browser under Playwright with software WebGL: a full
search-approach-signal mission, all three camera rigs, the docked and floating camera window,
back-to-back runs proving the reset, and the `--drone mock` placeholder state — with a clean
console throughout.
