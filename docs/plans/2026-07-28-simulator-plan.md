# Simulator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `SimDrone` adapter with a randomly generated arena and OpenCV-rendered synthetic camera frames, so programs and the real vision pipeline can be tested with zero hardware ([spec](../specs/2026-07-28-simulator-design.md)).

**Architecture:** New `comp1/sim/` package: `world.py` (random arena of wall-mounted markers), `render.py` (pinhole billboard projection + minimap inset), `drone.py` (`DroneAdapter` implementation holding pose). Wired into the CLI as `--drone sim`; no server/frontend changes.

**Tech Stack:** Python stdlib `random`/`math`, numpy, OpenCV (already dependencies). pytest, TDD.

## Global Constraints

- Deterministic by default: exact cm-accurate moves; `noise=0.0` and `delay=0` in all tests.
- `seed=None` → different world each launch (competition "changes between rounds" rule); integer seed → reproducible.
- No new dependencies. Branch: `feat/simulator` (stacked on `feat/platform-mvp`).
- Coordinate conventions (used consistently everywhere): metres; heading in degrees, `0 = +y`, clockwise positive; forward vector `(sin h, cos h)`; markers at height 1.0 m; victim radius 0.125 m.

---

### Task 1: World generation

**Files:** Create `comp1/sim/__init__.py` (empty), `comp1/sim/world.py`. Test `tests/test_sim_world.py`.

**Interfaces — Produces:** `Marker(x: float, y: float, kind: str)` frozen dataclass; `VICTIM = "victim"`; `DISTRACTOR_KINDS`; `World(size_m, markers)` with `World.random(seed=None, n_victims=3, n_distractors=4, size_m=4.0)` and `.victims` property.

- [ ] Write failing tests:

```python
# tests/test_sim_world.py
from comp1.sim.world import World, VICTIM

def test_seeded_world_is_reproducible():
    assert World.random(seed=42).markers == World.random(seed=42).markers

def test_counts_and_wall_placement():
    w = World.random(seed=1)
    assert len(w.markers) == 7 and len(w.victims) == 3
    for m in w.markers:
        assert m.x in (0.0, 4.0) or m.y in (0.0, 4.0)   # on a wall
        assert 0.5 <= (m.y if m.x in (0.0, 4.0) else m.x) <= 3.5  # away from corners

def test_min_spacing():
    w = World.random(seed=7)
    ms = w.markers
    for i, a in enumerate(ms):
        for b in ms[i + 1:]:
            assert (a.x - b.x) ** 2 + (a.y - b.y) ** 2 >= 0.6 ** 2
```

- [ ] Run → FAIL (no module). Implement:

```python
# comp1/sim/world.py
import random
from dataclasses import dataclass

VICTIM = "victim"
DISTRACTOR_KINDS = ["red_square", "blue_circle", "green_triangle", "yellow_square"]

@dataclass(frozen=True)
class Marker:
    x: float
    y: float
    kind: str

@dataclass
class World:
    size_m: float
    markers: list

    @classmethod
    def random(cls, seed=None, n_victims=3, n_distractors=4, size_m=4.0):
        rng = random.Random(seed)
        kinds = [VICTIM] * n_victims + \
                [rng.choice(DISTRACTOR_KINDS) for _ in range(n_distractors)]
        rng.shuffle(kinds)
        markers = []
        attempts = 0
        while kinds and attempts < 1000:
            attempts += 1
            wall = rng.randrange(4)
            t = rng.uniform(0.5, size_m - 0.5)
            x, y = {0: (t, size_m), 1: (size_m, t), 2: (t, 0.0), 3: (0.0, t)}[wall]
            if all((m.x - x) ** 2 + (m.y - y) ** 2 >= 0.6 ** 2 for m in markers):
                markers.append(Marker(x, y, kinds.pop()))
        return cls(size_m=size_m, markers=markers)

    @property
    def victims(self):
        return [m for m in self.markers if m.kind == VICTIM]
```

- [ ] Run → PASS. Commit `feat: random simulator world generation`.

### Task 2: Renderer (billboard projection + minimap)

**Files:** Create `comp1/sim/render.py`. Test `tests/test_sim_render.py`.

**Interfaces — Consumes:** `World`, `Marker`, `VICTIM` (Task 1); `detect_red_circle` (existing `comp1/vision/detector.py`). **Produces:** `render(world, x, y, z, heading, w=640, h=480) -> np.ndarray` (BGR frame).

- [ ] Write failing tests (drive the renderer through the REAL detector):

```python
# tests/test_sim_render.py
from comp1.sim.world import World, Marker, VICTIM
from comp1.sim.render import render
from comp1.vision.detector import detect_red_circle

def world_with(kind, x=2.0, y=4.0):
    return World(size_m=4.0, markers=[Marker(x, y, kind)])

def see(kind, x=2.0, y=4.0, heading=0.0):
    return detect_red_circle(render(world_with(kind, x, y), 2.0, 2.0, 1.0, heading))

def test_victim_ahead_is_detected_centre():
    det = see(VICTIM)
    assert det.found and det.position == "center"

def test_victim_to_left_reads_left():
    assert see(VICTIM, x=0.5).position == "left"

def test_distractors_not_detected():
    for kind in ("red_square", "blue_circle", "green_triangle", "yellow_square"):
        assert not see(kind).found, kind

def test_marker_behind_not_detected_and_minimap_is_not_a_false_positive():
    # victim behind the drone: only the minimap's red dot is in frame
    assert not see(VICTIM, y=0.0, heading=0.0).found

def test_apparent_size_grows_as_drone_nears():
    far = detect_red_circle(render(world_with(VICTIM), 2.0, 1.0, 1.0, 0.0))
    near = detect_red_circle(render(world_with(VICTIM), 2.0, 3.4, 1.0, 0.0))
    assert near.area_ratio > far.area_ratio > 0
```

- [ ] Run → FAIL. Implement:

```python
# comp1/sim/render.py
import math
import cv2
import numpy as np
from .world import VICTIM

FOV_H = math.radians(83)                 # Tello-like horizontal field of view
VICTIM_RADIUS = 0.125                    # metres (A4-ish red circle)
MARKER_HEIGHT = 1.0
MIN_DIST = 0.15
WALL_BGR = (210, 210, 205)
FLOOR_BGR = (150, 160, 170)
KIND_STYLE = {
    VICTIM:           ("circle",   (0, 0, 220)),
    "red_square":     ("square",   (0, 0, 220)),
    "blue_circle":    ("circle",   (220, 60, 0)),
    "green_triangle": ("triangle", (60, 180, 0)),
    "yellow_square":  ("square",   (0, 210, 230)),
}

def render(world, x, y, z, heading, w=640, h=480):
    focal = (w / 2) / math.tan(FOV_H / 2)
    img = np.empty((h, w, 3), np.uint8)
    img[:h // 2] = WALL_BGR
    img[h // 2:] = FLOOR_BGR
    hr = math.radians(heading)
    vis = []
    for m in world.markers:
        dx, dy = m.x - x, m.y - y
        d = max(math.hypot(dx, dy), MIN_DIST)
        ang = math.atan2(dx, dy)                       # cw from +y, matches heading
        rel = (ang - hr + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) > FOV_H / 2 + 0.35:
            continue
        vis.append((d, rel, m))
    vis.sort(key=lambda t: -t[0])                      # painter's algorithm
    for d, rel, m in vis:
        cx = int(w / 2 + focal * math.tan(rel))
        cy = int(h / 2 + focal * (z - MARKER_HEIGHT) / d)
        r = max(int(focal * VICTIM_RADIUS / d), 2)
        shape, color = KIND_STYLE[m.kind]
        if shape == "circle":
            cv2.circle(img, (cx, cy), r, color, -1)
        elif shape == "square":
            cv2.rectangle(img, (cx - r, cy - r), (cx + r, cy + r), color, -1)
        else:
            pts = np.array([[cx, cy - r], [cx - r, cy + r], [cx + r, cy + r]])
            cv2.fillPoly(img, [pts], color)
    _draw_minimap(img, world, x, y, heading)
    return img

def _draw_minimap(img, world, x, y, heading, size=140, pad=10):
    s = size / world.size_m
    x0, y0 = img.shape[1] - size - pad, pad
    cv2.rectangle(img, (x0, y0), (x0 + size, y0 + size), (255, 255, 255), -1)
    cv2.rectangle(img, (x0, y0), (x0 + size, y0 + size), (60, 60, 60), 2)

    def to_px(wx, wy):
        return (int(x0 + wx * s), int(y0 + (world.size_m - wy) * s))

    for m in world.markers:
        color = (0, 0, 220) if m.kind == VICTIM else (140, 140, 140)
        cv2.circle(img, to_px(m.x, m.y), 4, color, -1)   # 4 px: below detector min_area
    hr = math.radians(heading)
    px, py = to_px(x, y)
    tip = (int(px + 10 * math.sin(hr)), int(py - 10 * math.cos(hr)))
    cv2.circle(img, (px, py), 5, (200, 120, 0), -1)
    cv2.line(img, (px, py), tip, (200, 120, 0), 2)
```

- [ ] Run → PASS (if the minimap dot false-positives, shrink it or recolour to dark red — it must stay under the detector's `min_area_ratio`). Commit `feat: simulator billboard renderer with minimap`.

### Task 3: SimDrone adapter

**Files:** Create `comp1/sim/drone.py`. Test `tests/test_sim_drone.py`.

**Interfaces — Consumes:** `DroneAdapter` ABC (`comp1/drone/base.py`), `World` (Task 1), `render` (Task 2). **Produces:** `SimDrone(world=None, noise=0.0, delay=0.3, seed=None)` with pose attrs `x, y, z, heading`, `flying` flag.

- [ ] Write failing tests:

```python
# tests/test_sim_drone.py
import pytest
from comp1.sim.drone import SimDrone
from comp1.sim.world import World

def drone():
    return SimDrone(world=World(size_m=4.0, markers=[]), delay=0)

def test_starts_landed_at_centre():
    d = drone()
    assert (d.x, d.y, d.z, d.flying) == (2.0, 2.0, 0.0, False)

def test_move_before_takeoff_raises():
    with pytest.raises(RuntimeError):
        drone().move("forward", 50)

def test_pose_math_rotate_then_move():
    d = drone(); d.takeoff()
    d.rotate("cw", 90)            # now facing +x
    d.move("forward", 100)
    assert round(d.x, 2) == 3.0 and round(d.y, 2) == 2.0
    d.rotate("ccw", 90)           # back to +y
    d.move("left", 50)
    assert round(d.x, 2) == 2.5

def test_clamped_at_walls():
    d = drone(); d.takeoff()
    d.move("forward", 500)
    assert d.y == 3.8             # 0.2 m margin

def test_takeoff_land_altitude_and_frame_shape():
    d = drone(); d.takeoff()
    assert d.z == 1.0 and d.flying
    assert d.get_frame().shape == (480, 640, 3)
    d.land()
    assert d.z == 0.0 and not d.flying
```

- [ ] Run → FAIL. Implement:

```python
# comp1/sim/drone.py
import math
import random
import time
from ..drone.base import DroneAdapter
from .render import MARKER_HEIGHT, render
from .world import World

class SimDrone(DroneAdapter):
    def __init__(self, world=None, noise=0.0, delay=0.3, seed=None):
        self.world = world if world is not None else World.random(seed=seed)
        self.noise = noise
        self.delay = delay
        self._rng = random.Random(seed)
        self.x = self.world.size_m / 2
        self.y = self.world.size_m / 2
        self.z = 0.0
        self.heading = 0.0
        self.flying = False

    def _wait(self):
        if self.delay:
            time.sleep(self.delay)

    def _require_flying(self):
        if not self.flying:
            raise RuntimeError("not flying — use the take off block first")

    def connect(self):
        pass

    def takeoff(self):
        self._wait()
        self.flying = True
        self.z = MARKER_HEIGHT

    def land(self):
        self._wait()
        self.flying = False
        self.z = 0.0

    def emergency(self):
        self.flying = False
        self.z = 0.0

    def move(self, direction, cm):
        self._require_flying()
        self._wait()
        dist = cm / 100.0
        if self.noise:
            dist += self._rng.gauss(0, self.noise * dist)
        h = math.radians(self.heading)
        fx, fy = math.sin(h), math.cos(h)      # forward
        rx, ry = math.cos(h), -math.sin(h)     # right
        if direction == "forward":
            self.x += fx * dist; self.y += fy * dist
        elif direction == "back":
            self.x -= fx * dist; self.y -= fy * dist
        elif direction == "right":
            self.x += rx * dist; self.y += ry * dist
        elif direction == "left":
            self.x -= rx * dist; self.y -= ry * dist
        elif direction == "up":
            self.z = min(2.5, self.z + dist)
        elif direction == "down":
            self.z = max(0.3, self.z - dist)
        margin = 0.2
        self.x = min(max(self.x, margin), self.world.size_m - margin)
        self.y = min(max(self.y, margin), self.world.size_m - margin)

    def rotate(self, direction, deg):
        self._require_flying()
        self._wait()
        if self.noise:
            deg += self._rng.gauss(0, 2)
        self.heading = (self.heading + (deg if direction == "cw" else -deg)) % 360

    def flip(self, direction):
        self._require_flying()
        self._wait()                            # signal only — no pose change

    def get_frame(self):
        return render(self.world, self.x, self.y, self.z, self.heading)

    def battery(self):
        return 100
```

- [ ] Run → PASS. Commit `feat: SimDrone adapter with pose physics`.

### Task 4: CLI wiring + end-to-end mission test + docs

**Files:** Modify `comp1/__main__.py` (add `sim` choice, `--seed`, `--noise`), `README.md` (sim usage). Test `tests/test_sim_e2e.py`.

**Interfaces — Consumes:** everything above + `Interpreter`, `Program`, `detect_red_circle`.

- [ ] Write failing E2E test (the whole point of the simulator — search, detect, approach, signal, all through the real pipeline):

```python
# tests/test_sim_e2e.py
import math
from comp1.protocol import Program
from comp1.interpreter import Interpreter
from comp1.sim.drone import SimDrone
from comp1.sim.world import Marker, World, VICTIM
from comp1.vision.detector import detect_red_circle

SEARCH_PROGRAM = {"version": 1, "blocks": [
    {"id": "a", "op": "takeoff"},
    {"id": "b", "op": "repeat_until", "cond": {"sensor": "marker_visible"},
     "body": [{"id": "c", "op": "rotate", "dir": "cw", "deg": 20}]},
    {"id": "d", "op": "approach_marker"},
    {"id": "e", "op": "mark_found"},
    {"id": "f", "op": "land"},
]}

async def test_full_mission_finds_victim_without_hardware():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    drone = SimDrone(world=world, delay=0)
    drone.heading = 180.0          # start facing AWAY from the victim
    events = []
    interp = Interpreter(drone, lambda: detect_red_circle(drone.get_frame()),
                         events.append)
    await interp.run(Program.model_validate(SEARCH_PROGRAM))
    assert events[-1] == {"type": "finished", "reason": "done", "detail": ""}
    assert {"type": "found_count", "count": 1} in events
    dist = math.hypot(drone.x - 2.0, drone.y - 4.0)
    assert dist < 0.8              # approach converged near the victim wall
    assert not drone.flying        # landed

async def test_error_when_flying_before_takeoff():
    drone = SimDrone(world=World(size_m=4.0, markers=[]), delay=0)
    events = []
    interp = Interpreter(drone, lambda: detect_red_circle(drone.get_frame()),
                         events.append)
    await interp.run(Program.model_validate(
        {"version": 1, "blocks": [{"id": "a", "op": "move", "dir": "forward", "cm": 50}]}))
    assert events[-1]["reason"] == "error"
```

- [ ] Run → FAIL (CLI not needed for test; failure = missing sleeps/pose bugs surface here). Fix until PASS. Note: the interpreter's `_approach` sleeps 0.2 s between steps — acceptable test runtime (< ~5 s); if slower, patch `comp1.interpreter.asyncio.sleep` is NOT needed — keep real.
- [ ] Wire CLI in `comp1/__main__.py`:

```python
    ap.add_argument("--drone", choices=["mock", "tello", "sim"], default="mock")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--noise", type=float, default=0.0)
    ...
    elif args.drone == "sim":
        from .sim.drone import SimDrone
        drone = SimDrone(seed=args.seed, noise=args.noise)
```

- [ ] Manual check: `venv\Scripts\python -m comp1 --drone sim --no-browser` + browser/smoke: video shows walls, markers, minimap; overlay reads VICTIM when one is ahead.
- [ ] README: add `--drone sim` line to quick start.
- [ ] Full suite green → Commit `feat: sim drone CLI wiring and end-to-end mission test`.

## Verification

`venv\Scripts\pytest -v` all green (30+ tests). `python -m comp1 --drone sim` shows a random arena; running the Task-4 search program in the UI finds a victim and the minimap tracks the drone. `--seed 42` twice gives the identical arena.
