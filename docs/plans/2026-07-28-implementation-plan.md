# COMP1 Platform Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the COMP1 competition platform: a custom Blockly web frontend + single local Python service (FastAPI + djitellopy + OpenCV) that lets students block-program a Tello to search for red circular markers, with live video + detection overlay, per [docs/architecture/platform-options.md](../../Documents/GitHub/Drone-Competition-1/docs/architecture/platform-options.md).

**Architecture:** One Python process serves the static Blockly frontend and a WebSocket. The frontend serializes the block program to JSON; a backend interpreter executes it step-by-step against a drone adapter (mock or real Tello), emitting block-highlight events and honouring an emergency-stop flag. A background video loop runs OpenCV red-circle detection on every frame, draws the overlay server-side, and streams JPEGs over the same WebSocket. PyInstaller onedir packaging at the end.

**Tech Stack:** Python 3.11+, FastAPI + uvicorn, pydantic v2, djitellopy, opencv-python, numpy, pytest + fastapi TestClient; vanilla JS + vendored Blockly (no Node build step).

## Global Constraints

- UI language: **English only** (user decision 2026-07-28).
- A real Tello **is available** for testing — real-flight checklist runs right after the Tello adapter task.
- Fully offline frontend: zero CDN/external references in served assets (enforced by test in Task 10).
- No "fly to fixed coordinate" block anywhere (anti-hardcoding, requirements §4). Return-to-start (§3.4) is **out of scope** for this plan — pending organiser decision on mission pads.
- The interpreter executes validated JSON programs only — never `exec()` of generated Python.
- Python env: plain `venv` + `pip` + `pyproject.toml` (school-laptop friendly). Commands below assume Windows PowerShell.
- Commit after every task (steps include the commands).

## File Structure

```
comp1/
  __init__.py
  __main__.py            # CLI entry: parse args, start uvicorn, open browser
  protocol.py            # pydantic models: Block, Program, WS messages
  interpreter.py         # async block-program interpreter
  drone/
    __init__.py
    base.py              # DroneAdapter ABC
    mock.py              # MockDrone (command log + synthetic frames)
    tello.py             # TelloDrone wrapping djitellopy
  vision/
    __init__.py
    config.py            # VisionConfig dataclass (HSV bands, circularity, approach params)
    detector.py          # detect_red_circle(), draw_overlay()
  server.py              # create_app(): FastAPI, static mount, /ws, video loop
  frontend/
    index.html
    style.css
    blocks.js            # custom block defs + toolbox + JSON serializer
    app.js               # WS client, run/stop/e-stop, highlight, video panel
    vendor/blockly.min.js
tests/
  test_protocol.py
  test_mock_drone.py
  test_detector.py
  test_interpreter.py
  test_approach.py
  test_server.py
  test_offline_assets.py
comp1.spec               # PyInstaller
pyproject.toml
```

---

### Task 0: Project scaffold

**Files:**
- Create: `pyproject.toml`, `comp1/__init__.py`, `comp1/drone/__init__.py`, `comp1/vision/__init__.py`, `tests/__init__.py`, `.gitignore`

**Interfaces:**
- Produces: importable `comp1` package; `pytest` runs (0 tests).

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "comp1"
version = "0.1.0"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "djitellopy>=2.5",
    "opencv-python>=4.10",
    "numpy>=1.26",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-asyncio>=0.23", "httpx>=0.27", "pyinstaller>=6.10"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create package skeleton + .gitignore**

Empty `__init__.py` files as listed. `.gitignore`: `venv/`, `__pycache__/`, `build/`, `dist/`, `*.spec.bak`.

- [ ] **Step 3: Create venv, install, verify pytest runs**

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]
venv\Scripts\pytest
```
Expected: `no tests ran`.

- [ ] **Step 4: Commit** — `git add -A; git commit -m "chore: scaffold comp1 package"`

---

### Task 1: Program protocol models

**Files:**
- Create: `comp1/protocol.py`
- Test: `tests/test_protocol.py`

**Interfaces:**
- Produces: `Program.model_validate(dict)`; `Block` fields: `id: str`, `op`, and op-specific params; `Condition(sensor, value)`. Ops: `takeoff, land, move, rotate, flip, approach_marker, mark_found, end_mission, repeat_n, repeat_until, if`. Directions: move ∈ `forward/back/left/right/up/down`, rotate ∈ `cw/ccw`, flip ∈ `forward/back/left/right`. Sensors: `marker_visible`, `found_count_gte`, `marker_position_left/center/right` (per requirements §2.2 the detect capability must expose approximate position).

- [ ] **Step 1: Write failing tests**

```python
# tests/test_protocol.py
import pytest
from pydantic import ValidationError
from comp1.protocol import Program

def make(blocks):
    return {"version": 1, "blocks": blocks}

def test_valid_program_parses():
    p = Program.model_validate(make([
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "move", "dir": "forward", "cm": 50},
        {"id": "c", "op": "repeat_until",
         "cond": {"sensor": "marker_visible"},
         "body": [{"id": "d", "op": "rotate", "dir": "cw", "deg": 30}]},
        {"id": "e", "op": "land"},
    ]))
    assert p.blocks[2].body[0].op == "rotate"

def test_move_requires_valid_distance():
    with pytest.raises(ValidationError):
        Program.model_validate(make([{"id": "a", "op": "move", "dir": "forward", "cm": 5}]))

def test_unknown_op_rejected():
    with pytest.raises(ValidationError):
        Program.model_validate(make([{"id": "a", "op": "goto_xy", "x": 1, "y": 2}]))
```

- [ ] **Step 2: Run** `venv\Scripts\pytest tests/test_protocol.py -v` — expect FAIL (no module).

- [ ] **Step 3: Implement**

```python
# comp1/protocol.py
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, Field, model_validator

MOVE_DIRS = {"forward", "back", "left", "right", "up", "down"}
ROTATE_DIRS = {"cw", "ccw"}
FLIP_DIRS = {"forward", "back", "left", "right"}

class Condition(BaseModel):
    sensor: Literal["marker_visible", "found_count_gte",
                    "marker_position_left", "marker_position_center",
                    "marker_position_right"]
    value: int = 0

class Block(BaseModel):
    id: str
    op: Literal["takeoff", "land", "move", "rotate", "flip", "approach_marker",
                "mark_found", "end_mission", "repeat_n", "repeat_until", "if"]
    dir: str | None = None
    cm: int | None = Field(None, ge=20, le=500)   # Tello SDK range
    deg: int | None = Field(None, ge=1, le=360)
    n: int | None = Field(None, ge=1, le=50)
    cond: Condition | None = None
    body: list[Block] = []
    else_body: list[Block] = []

    @model_validator(mode="after")
    def check_params(self):
        need = {
            "move": self.dir in MOVE_DIRS and self.cm is not None,
            "rotate": self.dir in ROTATE_DIRS and self.deg is not None,
            "flip": self.dir in FLIP_DIRS,
            "repeat_n": self.n is not None,
            "repeat_until": self.cond is not None,
            "if": self.cond is not None,
        }
        if self.op in need and not need[self.op]:
            raise ValueError(f"invalid params for op {self.op}")
        return self

class Program(BaseModel):
    version: Literal[1]
    blocks: list[Block]
```

- [ ] **Step 4: Run tests** — expect PASS.
- [ ] **Step 5: Commit** — `git commit -m "feat: block program protocol models"`

---

### Task 2: Drone adapter ABC + MockDrone

**Files:**
- Create: `comp1/drone/base.py`, `comp1/drone/mock.py`
- Test: `tests/test_mock_drone.py`

**Interfaces:**
- Produces: `DroneAdapter` with sync methods `connect() -> None`, `takeoff()`, `land()`, `emergency()`, `move(direction: str, cm: int)`, `rotate(direction: str, deg: int)`, `flip(direction: str)`, `get_frame() -> np.ndarray | None`, `battery() -> int`. `MockDrone(frame_factory=None)` records every call as tuples in `.log` (e.g. `("move", "forward", 50)`) and returns frames from `frame_factory()` (default: 640×480 black image).

- [ ] **Step 1: Failing tests**

```python
# tests/test_mock_drone.py
import numpy as np
from comp1.drone.mock import MockDrone

def test_mock_logs_commands():
    d = MockDrone()
    d.connect(); d.takeoff(); d.move("forward", 50); d.rotate("cw", 90); d.land()
    assert d.log == [("connect",), ("takeoff",), ("move", "forward", 50),
                     ("rotate", "cw", 90), ("land",)]

def test_mock_frame_factory():
    red = np.zeros((480, 640, 3), np.uint8); red[:] = (0, 0, 255)
    d = MockDrone(frame_factory=lambda: red)
    assert d.get_frame()[0, 0, 2] == 255
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
# comp1/drone/base.py
from abc import ABC, abstractmethod
import numpy as np

class DroneAdapter(ABC):
    @abstractmethod
    def connect(self) -> None: ...
    @abstractmethod
    def takeoff(self) -> None: ...
    @abstractmethod
    def land(self) -> None: ...
    @abstractmethod
    def emergency(self) -> None: ...
    @abstractmethod
    def move(self, direction: str, cm: int) -> None: ...
    @abstractmethod
    def rotate(self, direction: str, deg: int) -> None: ...
    @abstractmethod
    def flip(self, direction: str) -> None: ...
    @abstractmethod
    def get_frame(self) -> np.ndarray | None: ...
    @abstractmethod
    def battery(self) -> int: ...
```

```python
# comp1/drone/mock.py
import numpy as np
from .base import DroneAdapter

class MockDrone(DroneAdapter):
    def __init__(self, frame_factory=None):
        self.log = []
        self._frame_factory = frame_factory or (lambda: np.zeros((480, 640, 3), np.uint8))
    def connect(self): self.log.append(("connect",))
    def takeoff(self): self.log.append(("takeoff",))
    def land(self): self.log.append(("land",))
    def emergency(self): self.log.append(("emergency",))
    def move(self, direction, cm): self.log.append(("move", direction, cm))
    def rotate(self, direction, deg): self.log.append(("rotate", direction, deg))
    def flip(self, direction): self.log.append(("flip", direction))
    def get_frame(self): return self._frame_factory()
    def battery(self): return 100
```

- [ ] **Step 4: Run — PASS.**
- [ ] **Step 5: Commit** — `git commit -m "feat: drone adapter ABC and mock drone"`

---

### Task 3: Vision — red circle detector

**Files:**
- Create: `comp1/vision/config.py`, `comp1/vision/detector.py`
- Test: `tests/test_detector.py`

**Interfaces:**
- Produces: `VisionConfig` (fields below), `Detection(found: bool, cx: float, area_ratio: float, position: str)` where `cx` ∈ 0..1 and `position` ∈ `left/center/right/none`; `detect_red_circle(frame_bgr, cfg=DEFAULT_CONFIG) -> Detection`; `draw_overlay(frame_bgr, det) -> np.ndarray`.

- [ ] **Step 1: Failing tests (synthetic frames — this is the §3.1 algorithm, unit-testable without a drone)**

```python
# tests/test_detector.py
import cv2, numpy as np
from comp1.vision.detector import detect_red_circle

def frame_with(shape="circle", color=(0, 0, 220), cx=320):
    img = np.full((480, 640, 3), 255, np.uint8)
    if shape == "circle":
        cv2.circle(img, (cx, 240), 60, color, -1)
    elif shape == "square":
        cv2.rectangle(img, (cx - 60, 180), (cx + 60, 300), color, -1)
    return img

def test_detects_red_circle_centre():
    det = detect_red_circle(frame_with())
    assert det.found and det.position == "center"

def test_position_left():
    det = detect_red_circle(frame_with(cx=80))
    assert det.found and det.position == "left"

def test_ignores_blue_circle():
    assert not detect_red_circle(frame_with(color=(220, 0, 0))).found

def test_ignores_red_square():
    assert not detect_red_circle(frame_with(shape="square")).found

def test_empty_frame():
    det = detect_red_circle(np.full((480, 640, 3), 255, np.uint8))
    assert not det.found and det.position == "none"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
# comp1/vision/config.py
from dataclasses import dataclass, field

@dataclass
class VisionConfig:
    # two HSV bands because red wraps the hue axis — re-tune on-site (§3.1)
    lower1: tuple = (0, 100, 80)
    upper1: tuple = (10, 255, 255)
    lower2: tuple = (170, 100, 80)
    upper2: tuple = (180, 255, 255)
    min_area_ratio: float = 0.002      # ignore specks
    circularity_min: float = 0.72      # 4πA/P²; squares ≈ 0.785 * corner-rounding → tune
    center_band: float = 0.2           # |cx-0.5| < band/2 → "center"
    approach_stop_area: float = 0.08   # "close enough" frame proportion (§3.2 option 2)
    approach_step_cm: int = 30
    approach_turn_deg: int = 15
    approach_max_steps: int = 40

DEFAULT_CONFIG = VisionConfig()
```

```python
# comp1/vision/detector.py
from dataclasses import dataclass
import cv2, numpy as np
from .config import VisionConfig, DEFAULT_CONFIG

@dataclass
class Detection:
    found: bool
    cx: float = 0.5
    area_ratio: float = 0.0
    position: str = "none"

def detect_red_circle(frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> Detection:
    h, w = frame_bgr.shape[:2]
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(cfg.lower1), np.array(cfg.upper1)) | \
           cv2.inRange(hsv, np.array(cfg.lower2), np.array(cfg.upper2))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for c in contours:
        area = cv2.contourArea(c)
        if area / (w * h) < cfg.min_area_ratio:
            continue
        perim = cv2.arcLength(c, True)
        if perim == 0:
            continue
        circularity = 4 * np.pi * area / (perim * perim)
        if circularity < cfg.circularity_min:
            continue
        if best is None or area > cv2.contourArea(best):
            best = c
    if best is None:
        return Detection(found=False)
    m = cv2.moments(best)
    cx = m["m10"] / m["m00"] / w
    area_ratio = cv2.contourArea(best) / (w * h)
    if abs(cx - 0.5) < cfg.center_band / 2:
        pos = "center"
    else:
        pos = "left" if cx < 0.5 else "right"
    return Detection(found=True, cx=cx, area_ratio=area_ratio, position=pos)

def draw_overlay(frame_bgr: np.ndarray, det: Detection) -> np.ndarray:
    out = frame_bgr.copy()
    label = f"VICTIM {det.position} ({det.area_ratio:.1%})" if det.found else "searching..."
    color = (0, 255, 0) if det.found else (0, 165, 255)
    cv2.putText(out, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if det.found:
        x = int(det.cx * out.shape[1])
        cv2.line(out, (x, 0), (x, out.shape[0]), color, 2)
    return out
```

- [ ] **Step 4: Run — PASS** (if the square passes circularity, raise `circularity_min` until it fails — record final value).
- [ ] **Step 5: Commit** — `git commit -m "feat: HSV+contour red circle detector with overlay"`

---

### Task 4: Interpreter — basic ops, stop flag, highlight events

**Files:**
- Create: `comp1/interpreter.py`
- Test: `tests/test_interpreter.py`

**Interfaces:**
- Consumes: `Program`/`Block` (Task 1), `DroneAdapter` (Task 2), `Detection` (Task 3).
- Produces: `Interpreter(drone, get_detection: Callable[[], Detection], on_event: Callable[[dict], None])` with `async run(program: Program) -> None` and `request_stop()`. Events: `{"type": "highlight", "blockId": id}`, `{"type": "finished", "reason": "done"|"stopped"|"error", "detail": str}`, `{"type": "found_count", "count": int}`. Drone calls go through `asyncio.to_thread` (djitellopy blocks).

- [ ] **Step 1: Failing tests**

```python
# tests/test_interpreter.py
import pytest
from comp1.protocol import Program
from comp1.drone.mock import MockDrone
from comp1.vision.detector import Detection
from comp1.interpreter import Interpreter

def prog(blocks):
    return Program.model_validate({"version": 1, "blocks": blocks})

async def run(blocks, det=Detection(found=False)):
    drone, events = MockDrone(), []
    it = Interpreter(drone, lambda: det, events.append)
    await it.run(prog(blocks))
    return drone, events, it

async def test_sequential_ops_and_highlights():
    drone, events, _ = await run([
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "move", "dir": "up", "cm": 30},
        {"id": "c", "op": "land"},
    ])
    assert drone.log == [("takeoff",), ("move", "up", 30), ("land",)]
    assert [e["blockId"] for e in events if e["type"] == "highlight"] == ["a", "b", "c"]
    assert events[-1] == {"type": "finished", "reason": "done", "detail": ""}

async def test_stop_flag_halts_and_lands():
    drone, events = MockDrone(), []
    it = Interpreter(drone, lambda: Detection(found=False), events.append)
    it.request_stop()
    await it.run(prog([{"id": "a", "op": "takeoff"}, {"id": "b", "op": "flip", "dir": "left"}]))
    assert ("flip", "left") not in drone.log
    assert ("land",) in drone.log
    assert events[-1]["reason"] == "stopped"

async def test_mark_found_signals_flip_and_counts():
    drone, events, it = await run([{"id": "a", "op": "mark_found"}])
    assert ("flip", "back") in drone.log
    assert {"type": "found_count", "count": 1} in events
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
# comp1/interpreter.py
import asyncio
from typing import Callable
from .protocol import Program, Block
from .drone.base import DroneAdapter
from .vision.detector import Detection

class _Stopped(Exception): pass
class _MissionEnd(Exception): pass

class Interpreter:
    def __init__(self, drone: DroneAdapter,
                 get_detection: Callable[[], Detection],
                 on_event: Callable[[dict], None]):
        self._drone = drone
        self._detect = get_detection
        self._emit = on_event
        self._stop = asyncio.Event()
        self.found_count = 0

    def request_stop(self):
        self._stop.set()

    async def run(self, program: Program):
        self._stop.clear() if not self._stop.is_set() else None
        reason, detail = "done", ""
        try:
            await self._run_blocks(program.blocks)
        except _Stopped:
            reason = "stopped"
        except _MissionEnd:
            pass
        except Exception as exc:                      # drone error → land, report
            reason, detail = "error", str(exc)
        if reason != "done":
            try:
                await asyncio.to_thread(self._drone.land)
            except Exception:
                pass
        self._emit({"type": "finished", "reason": reason, "detail": detail})

    async def _run_blocks(self, blocks: list[Block]):
        for b in blocks:
            if self._stop.is_set():
                raise _Stopped()
            self._emit({"type": "highlight", "blockId": b.id})
            await self._exec(b)

    def _cond(self, c) -> bool:
        if c.sensor == "marker_visible":
            return self._detect().found
        if c.sensor.startswith("marker_position_"):
            det = self._detect()
            return det.found and det.position == c.sensor.removeprefix("marker_position_")
        return self.found_count >= c.value            # found_count_gte

    async def _exec(self, b: Block):
        d = self._drone
        match b.op:
            case "takeoff":  await asyncio.to_thread(d.takeoff)
            case "land":     await asyncio.to_thread(d.land)
            case "move":     await asyncio.to_thread(d.move, b.dir, b.cm)
            case "rotate":   await asyncio.to_thread(d.rotate, b.dir, b.deg)
            case "flip":     await asyncio.to_thread(d.flip, b.dir)
            case "mark_found":
                await asyncio.to_thread(d.flip, "back")   # victory signal (§2.1)
                self.found_count += 1
                self._emit({"type": "found_count", "count": self.found_count})
            case "end_mission":
                await asyncio.to_thread(d.land)
                raise _MissionEnd()
            case "repeat_n":
                for _ in range(b.n):
                    await self._run_blocks(b.body)
            case "repeat_until":
                for _ in range(1000):                     # hard safety bound
                    if self._stop.is_set(): raise _Stopped()
                    if self._cond(b.cond): break
                    await self._run_blocks(b.body)
            case "if":
                await self._run_blocks(b.body if self._cond(b.cond) else b.else_body)
            case "approach_marker":
                await self._approach()

    async def _approach(self):
        raise NotImplementedError  # Task 5
```

Note: the stop test constructs the interpreter, calls `request_stop()`, then `run()` — `run()` must NOT clear a pre-set stop flag; fix the first line of `run()` accordingly (`if` guard as shown).

- [ ] **Step 4: Run — PASS** (all except approach, not yet tested). Also run full suite.
- [ ] **Step 5: Commit** — `git commit -m "feat: block program interpreter with stop flag and events"`

---

### Task 5: Interpreter — approach_marker (visual servoing)

**Files:**
- Modify: `comp1/interpreter.py` (`_approach`)
- Test: `tests/test_approach.py`

**Interfaces:**
- Consumes: `VisionConfig` approach params (Task 3). `Interpreter.__init__` gains optional `cfg: VisionConfig = DEFAULT_CONFIG`.
- Produces: `approach_marker` behaviour — turn toward marker while off-center, step forward while centered, stop once `area_ratio >= cfg.approach_stop_area`; abort after `approach_max_steps` steps or if marker lost for 3 consecutive checks.

- [ ] **Step 1: Failing tests (scripted detection sequence drives the loop)**

```python
# tests/test_approach.py
from comp1.protocol import Program
from comp1.drone.mock import MockDrone
from comp1.vision.detector import Detection
from comp1.interpreter import Interpreter

def prog():
    return Program.model_validate(
        {"version": 1, "blocks": [{"id": "a", "op": "approach_marker"}]})

async def run_seq(seq):
    it_seq = iter(seq)
    last = seq[-1]
    def det():
        nonlocal last
        try: last = next(it_seq)
        except StopIteration: pass
        return last
    drone, events = MockDrone(), []
    interp = Interpreter(drone, det, events.append)
    await interp.run(prog())
    return drone

async def test_turns_toward_left_marker_then_advances_and_stops():
    drone = await run_seq([
        Detection(True, cx=0.2, area_ratio=0.01, position="left"),
        Detection(True, cx=0.5, area_ratio=0.02, position="center"),
        Detection(True, cx=0.5, area_ratio=0.09, position="center"),  # >= stop area
    ])
    assert ("rotate", "ccw", 15) in drone.log
    assert ("move", "forward", 30) in drone.log
    idx = drone.log.index(("move", "forward", 30))
    assert all(x[0] != "move" for x in drone.log[idx + 1:])  # stopped after close enough

async def test_gives_up_when_marker_lost():
    drone = await run_seq([Detection(True, 0.5, 0.02, "center"),
                           Detection(False), Detection(False), Detection(False)])
    assert len(drone.log) <= 2  # one step at most, then abort — no runaway
```

- [ ] **Step 2: Run — FAIL** (`NotImplementedError`).

- [ ] **Step 3: Implement `_approach`**

```python
    async def _approach(self):
        cfg = self._cfg
        lost = 0
        for _ in range(cfg.approach_max_steps):
            if self._stop.is_set():
                raise _Stopped()
            det = self._detect()
            if not det.found:
                lost += 1
                if lost >= 3:
                    return
                await asyncio.sleep(0.3)
                continue
            lost = 0
            if det.area_ratio >= cfg.approach_stop_area:
                return                                    # close enough (§3.2)
            if det.position == "left":
                await asyncio.to_thread(self._drone.rotate, "ccw", cfg.approach_turn_deg)
            elif det.position == "right":
                await asyncio.to_thread(self._drone.rotate, "cw", cfg.approach_turn_deg)
            else:
                await asyncio.to_thread(self._drone.move, "forward", cfg.approach_step_cm)
            await asyncio.sleep(0.2)                      # let video catch up
```

Also: add `cfg: VisionConfig = DEFAULT_CONFIG` param to `__init__` storing `self._cfg`, importing `VisionConfig, DEFAULT_CONFIG` from `.vision.config`.

- [ ] **Step 4: Run — PASS** (whole suite).
- [ ] **Step 5: Commit** — `git commit -m "feat: approach_marker visual servoing loop"`

---

### Task 6: FastAPI server — WebSocket run/stop/e-stop + video loop

**Files:**
- Create: `comp1/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `create_app(drone: DroneAdapter, cfg: VisionConfig = DEFAULT_CONFIG) -> FastAPI`. WS endpoint `/ws`: client sends JSON `{"type": "run", "program": {...}}`, `{"type": "stop"}`, `{"type": "estop"}`; server sends interpreter events as JSON text messages plus video frames as **binary** JPEG messages (~10 fps, 640-wide, overlay drawn). Static frontend mounted at `/` (Task 7 adds files; mount is conditional on the dir existing). App state: `app.state.latest_detection` updated by the video loop; interpreter reads it — single shared detection source.

- [ ] **Step 1: Failing tests**

```python
# tests/test_server.py
import json
import cv2, numpy as np
from fastapi.testclient import TestClient
from comp1.drone.mock import MockDrone
from comp1.server import create_app

def red_frame():
    img = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(img, (320, 240), 60, (0, 0, 220), -1)
    return img

def collect_until(ws, want_type, limit=50):
    for _ in range(limit):
        msg = ws.receive()
        if "bytes" in msg and msg["bytes"]:
            if want_type == "frame":
                return msg["bytes"]
            continue
        data = json.loads(msg["text"])
        if data["type"] == want_type:
            return data
    raise AssertionError(f"no {want_type} message")

def test_run_program_executes_and_reports():
    drone = MockDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}]}})
        fin = collect_until(ws, "finished")
        assert fin["reason"] == "done"
    assert ("takeoff",) in drone.log and ("land",) in drone.log

def test_video_frames_are_jpeg():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        frame = collect_until(ws, "frame")
        assert frame[:2] == b"\xff\xd8"          # JPEG magic

def test_invalid_program_rejected():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "goto_xy"}]}})
        err = collect_until(ws, "error")
        assert "invalid" in err["message"].lower()

def test_estop_calls_emergency():
    drone = MockDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "estop"})
        collect_until(ws, "estopped")
    assert ("emergency",) in drone.log
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
# comp1/server.py
import asyncio, json
from contextlib import asynccontextmanager
from pathlib import Path
import cv2
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from .drone.base import DroneAdapter
from .interpreter import Interpreter
from .protocol import Program
from .vision.config import VisionConfig, DEFAULT_CONFIG
from .vision.detector import Detection, detect_red_circle, draw_overlay

FRONTEND_DIR = Path(__file__).parent / "frontend"
FRAME_INTERVAL = 0.1  # ~10 fps

def create_app(drone: DroneAdapter, cfg: VisionConfig = DEFAULT_CONFIG) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        drone.connect()
        app.state.latest_detection = Detection(found=False)
        app.state.clients = set()
        app.state.interp = None
        app.state.video_task = asyncio.create_task(_video_loop(app))
        yield
        app.state.video_task.cancel()

    app = FastAPI(lifespan=lifespan)

    async def _video_loop(app: FastAPI):
        while True:
            frame = await asyncio.to_thread(drone.get_frame)
            if frame is not None:
                det = detect_red_circle(frame, cfg)
                app.state.latest_detection = det
                small = cv2.resize(frame, (640, 480)) if frame.shape[1] != 640 else frame
                ok, jpeg = cv2.imencode(".jpg", draw_overlay(small, det),
                                        [cv2.IMWRITE_JPEG_QUALITY, 70])
                if ok:
                    await _broadcast_bytes(app, jpeg.tobytes())
            await asyncio.sleep(FRAME_INTERVAL)

    async def _broadcast_bytes(app, data: bytes):
        for ws in list(app.state.clients):
            try: await ws.send_bytes(data)
            except Exception: app.state.clients.discard(ws)

    async def _broadcast_json(app, data: dict):
        for ws in list(app.state.clients):
            try: await ws.send_text(json.dumps(data))
            except Exception: app.state.clients.discard(ws)

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        app.state.clients.add(ws)
        loop = asyncio.get_running_loop()
        emit = lambda ev: loop.create_task(_broadcast_json(app, ev))
        try:
            while True:
                msg = json.loads(await ws.receive_text())
                if msg["type"] == "run":
                    if app.state.interp is not None:
                        await _broadcast_json(app, {"type": "error", "message": "already running"})
                        continue
                    try:
                        program = Program.model_validate(msg["program"])
                    except ValidationError as e:
                        await _broadcast_json(app, {"type": "error",
                                                    "message": f"invalid program: {e}"})
                        continue
                    interp = Interpreter(drone, lambda: app.state.latest_detection, emit, cfg=cfg)
                    app.state.interp = interp
                    async def _run():
                        try: await interp.run(program)
                        finally: app.state.interp = None
                    asyncio.create_task(_run())
                elif msg["type"] == "stop":
                    if app.state.interp: app.state.interp.request_stop()
                elif msg["type"] == "estop":
                    if app.state.interp: app.state.interp.request_stop()
                    await asyncio.to_thread(drone.emergency)
                    await _broadcast_json(app, {"type": "estopped"})
        except WebSocketDisconnect:
            pass
        finally:
            app.state.clients.discard(ws)

    if FRONTEND_DIR.exists():
        app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
    return app
```

- [ ] **Step 4: Run — PASS** (whole suite).
- [ ] **Step 5: Commit** — `git commit -m "feat: FastAPI websocket server with video loop"`

---

### Task 7: Frontend — vendored Blockly, custom blocks, toolbox, serializer

**Files:**
- Create: `comp1/frontend/vendor/blockly.min.js` (vendored), `comp1/frontend/index.html`, `comp1/frontend/style.css`, `comp1/frontend/blocks.js`

**Interfaces:**
- Consumes: program JSON schema from Task 1 (op names, params, `body`/`else_body`, `cond`).
- Produces: `COMP1.serializeProgram(workspace) -> {version: 1, blocks: [...]}` (global from blocks.js); block `id`s are the Blockly block ids (so backend highlight events map directly via `workspace.highlightBlock(id)`).

- [ ] **Step 1: Vendor Blockly (one-time, needs internet)**

```powershell
curl.exe -L -o comp1/frontend/vendor/blockly.min.js https://unpkg.com/blockly@12/blockly.min.js
```
(blockly.min.js bundles core + built-in blocks + English messages.)

- [ ] **Step 2: index.html + style.css**

```html
<!-- comp1/frontend/index.html -->
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>COMP1 Drone Coder</title>
  <link rel="stylesheet" href="style.css">
  <script src="vendor/blockly.min.js"></script>
</head>
<body>
  <header>
    <span id="status" class="status">connecting…</span>
    <span id="found">Victims found: 0</span>
    <button id="run">▶ Run</button>
    <button id="stop">⏹ Stop</button>
    <button id="estop" class="estop">EMERGENCY STOP</button>
  </header>
  <main>
    <div id="blockly"></div>
    <aside>
      <img id="video" alt="drone camera">
      <pre id="console"></pre>
    </aside>
  </main>
  <script src="blocks.js"></script>
  <script src="app.js"></script>
</body>
</html>
```

```css
/* comp1/frontend/style.css */
* { box-sizing: border-box; margin: 0; }
body { font-family: system-ui, sans-serif; height: 100vh; display: flex; flex-direction: column; }
header { display: flex; gap: 12px; align-items: center; padding: 8px 12px; background: #1e293b; color: #fff; }
header button { font-size: 16px; padding: 6px 16px; border-radius: 6px; border: 0; cursor: pointer; }
#run { background: #22c55e; } #stop { background: #eab308; }
.estop { background: #dc2626; color: #fff; font-weight: 700; margin-left: auto; }
main { flex: 1; display: flex; min-height: 0; }
#blockly { flex: 2; }
aside { flex: 1; display: flex; flex-direction: column; background: #0f172a; }
#video { width: 100%; aspect-ratio: 4/3; object-fit: contain; background: #000; }
#console { flex: 1; color: #94a3b8; padding: 8px; overflow-y: auto; font-size: 13px; }
.status.ok { color: #4ade80; } .status.bad { color: #f87171; }
```

- [ ] **Step 3: blocks.js — define the competition block set + serializer**

Pattern (repeat for each block; full list below):

```javascript
// comp1/frontend/blocks.js
const C = { hue_flight: 210, hue_vision: 0, hue_flow: 120, hue_mission: 280 };

Blockly.defineBlocksWithJsonArray([
  { type: "takeoff", message0: "take off", colour: C.hue_flight,
    previousStatement: null, nextStatement: null },
  { type: "land", message0: "land", colour: C.hue_flight,
    previousStatement: null, nextStatement: null },
  { type: "move", message0: "fly %1 %2 cm", colour: C.hue_flight,
    args0: [
      { type: "field_dropdown", name: "DIR", options: [
        ["forward","forward"],["back","back"],["left","left"],
        ["right","right"],["up","up"],["down","down"]] },
      { type: "field_number", name: "CM", value: 50, min: 20, max: 500 }],
    previousStatement: null, nextStatement: null },
  { type: "rotate", message0: "turn %1 %2 °", colour: C.hue_flight,
    args0: [
      { type: "field_dropdown", name: "DIR",
        options: [["↻ clockwise","cw"],["↺ counter-clockwise","ccw"]] },
      { type: "field_number", name: "DEG", value: 90, min: 1, max: 360 }],
    previousStatement: null, nextStatement: null },
  { type: "flip", message0: "flip %1", colour: C.hue_flight,
    args0: [{ type: "field_dropdown", name: "DIR", options: [
      ["forward","forward"],["back","back"],["left","left"],["right","right"]] }],
    previousStatement: null, nextStatement: null },
  { type: "marker_visible", message0: "victim marker seen?", colour: C.hue_vision,
    output: "Boolean" },
  { type: "marker_position_is", message0: "victim marker is %1", colour: C.hue_vision,
    args0: [{ type: "field_dropdown", name: "POS", options: [
      ["on the left","left"],["in the centre","center"],["on the right","right"]] }],
    output: "Boolean" },
  { type: "approach_marker", message0: "approach victim and stop", colour: C.hue_vision,
    previousStatement: null, nextStatement: null },
  { type: "mark_found", message0: "signal victim found 🎉", colour: C.hue_vision,
    previousStatement: null, nextStatement: null },
  { type: "found_count_gte", message0: "victims found ≥ %1", colour: C.hue_vision,
    args0: [{ type: "field_number", name: "N", value: 3, min: 1, max: 20 }],
    output: "Boolean" },
  { type: "repeat_n", message0: "repeat %1 times %2", colour: C.hue_flow,
    args0: [{ type: "field_number", name: "N", value: 4, min: 1, max: 50 },
            { type: "input_statement", name: "BODY" }],
    previousStatement: null, nextStatement: null },
  { type: "repeat_until", message0: "keep doing until %1 %2", colour: C.hue_flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" }],
    previousStatement: null, nextStatement: null },
  { type: "if_block", message0: "if %1 then %2 else %3", colour: C.hue_flow,
    args0: [{ type: "input_value", name: "COND", check: "Boolean" },
            { type: "input_statement", name: "BODY" },
            { type: "input_statement", name: "ELSE" }],
    previousStatement: null, nextStatement: null },
  { type: "end_mission", message0: "end mission and land 🏁", colour: C.hue_mission,
    previousStatement: null },
  { type: "start", message0: "🚁 when mission starts", colour: C.hue_mission,
    nextStatement: null, deletable: false },
]);

const TOOLBOX = { kind: "flyoutToolbox", contents: [
  "takeoff","land","move","rotate","flip",
  "marker_visible","marker_position_is","approach_marker","mark_found","found_count_gte",
  "repeat_n","repeat_until","if_block","end_mission",
].map(t => ({ kind: "block", type: t })) };

function condJson(block, name) {
  const target = block.getInputTargetBlock(name);
  if (!target) return { sensor: "marker_visible", value: 0 };
  if (target.type === "found_count_gte")
    return { sensor: "found_count_gte", value: Number(target.getFieldValue("N")) };
  if (target.type === "marker_position_is")
    return { sensor: "marker_position_" + target.getFieldValue("POS"), value: 0 };
  return { sensor: "marker_visible", value: 0 };
}

function blockJson(b) {
  const base = { id: b.id };
  switch (b.type) {
    case "takeoff": case "land": case "approach_marker":
    case "mark_found": case "end_mission":
      return { ...base, op: b.type };
    case "move":
      return { ...base, op: "move", dir: b.getFieldValue("DIR"),
               cm: Number(b.getFieldValue("CM")) };
    case "rotate":
      return { ...base, op: "rotate", dir: b.getFieldValue("DIR"),
               deg: Number(b.getFieldValue("DEG")) };
    case "flip":
      return { ...base, op: "flip", dir: b.getFieldValue("DIR") };
    case "repeat_n":
      return { ...base, op: "repeat_n", n: Number(b.getFieldValue("N")),
               body: chainJson(b.getInputTargetBlock("BODY")) };
    case "repeat_until":
      return { ...base, op: "repeat_until", cond: condJson(b, "COND"),
               body: chainJson(b.getInputTargetBlock("BODY")) };
    case "if_block":
      return { ...base, op: "if", cond: condJson(b, "COND"),
               body: chainJson(b.getInputTargetBlock("BODY")),
               else_body: chainJson(b.getInputTargetBlock("ELSE")) };
    default:
      return null; // value blocks are consumed by condJson
  }
}

function chainJson(block) {
  const out = [];
  for (let b = block; b; b = b.getNextBlock()) {
    const j = blockJson(b);
    if (j) out.push(j);
  }
  return out;
}

window.COMP1 = {
  toolbox: TOOLBOX,
  serializeProgram(workspace) {
    const start = workspace.getBlocksByType("start", false)[0];
    return { version: 1, blocks: start ? chainJson(start.getNextBlock()) : [] };
  },
};
```

- [ ] **Step 4: Commit** — `git commit -m "feat: Blockly frontend blocks, toolbox, program serializer"`

---

### Task 8: Frontend — app.js (WS client, run/stop, highlight, video)

**Files:**
- Create: `comp1/frontend/app.js`

**Interfaces:**
- Consumes: WS protocol (Task 6), `COMP1.serializeProgram` + `COMP1.toolbox` (Task 7).

- [ ] **Step 1: Implement**

```javascript
// comp1/frontend/app.js
const workspace = Blockly.inject("blockly", {
  toolbox: COMP1.toolbox, trashcan: true, zoom: { controls: true },
});
const start = workspace.newBlock("start");
start.initSvg(); start.render(); start.moveBy(30, 30);

const statusEl = document.getElementById("status");
const consoleEl = document.getElementById("console");
const videoEl = document.getElementById("video");
const foundEl = document.getElementById("found");
let ws, lastUrl;

function log(msg) {
  consoleEl.textContent += msg + "\n";
  consoleEl.scrollTop = consoleEl.scrollHeight;
}

function connect() {
  ws = new WebSocket(`ws://${location.host}/ws`);
  ws.binaryType = "blob";
  ws.onopen = () => { statusEl.textContent = "connected"; statusEl.className = "status ok"; };
  ws.onclose = () => {
    statusEl.textContent = "disconnected — retrying"; statusEl.className = "status bad";
    setTimeout(connect, 1000);
  };
  ws.onmessage = (ev) => {
    if (ev.data instanceof Blob) {
      const url = URL.createObjectURL(ev.data);
      videoEl.src = url;
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = url;
      return;
    }
    const msg = JSON.parse(ev.data);
    if (msg.type === "highlight") workspace.highlightBlock(msg.blockId);
    else if (msg.type === "found_count") foundEl.textContent = `Victims found: ${msg.count}`;
    else if (msg.type === "finished") {
      workspace.highlightBlock(null);
      log(`mission ${msg.reason}${msg.detail ? ": " + msg.detail : ""}`);
    }
    else if (msg.type === "error") log("⚠ " + msg.message);
    else if (msg.type === "estopped") log("⛔ EMERGENCY STOP");
  };
}
connect();

document.getElementById("run").onclick = () =>
  ws.send(JSON.stringify({ type: "run", program: COMP1.serializeProgram(workspace) }));
document.getElementById("stop").onclick = () => ws.send(JSON.stringify({ type: "stop" }));
document.getElementById("estop").onclick = () => ws.send(JSON.stringify({ type: "estop" }));
```

- [ ] **Step 2: Manual verification against the mock drone** (entry point arrives in Task 9; for now run inline)

```powershell
venv\Scripts\python -c "import uvicorn; from comp1.server import create_app; from comp1.drone.mock import MockDrone; uvicorn.run(create_app(MockDrone()), port=8765)"
```
Open http://localhost:8765 — verify: blocks drag from toolbox; a `take off → repeat 4 [fly forward 50 / turn cw 90] → land` program runs with visible block highlighting; console shows "mission done"; video panel shows the (black) mock feed; Stop and EMERGENCY STOP both log.

- [ ] **Step 3: Commit** — `git commit -m "feat: frontend app with websocket client, video panel, highlighting"`

---

### Task 9: Tello adapter + CLI entry point

**Files:**
- Create: `comp1/drone/tello.py`, `comp1/__main__.py`
- Test: extend `tests/test_mock_drone.py` with a monkeypatched Tello test

**Interfaces:**
- Produces: `TelloDrone()` implementing `DroneAdapter` over djitellopy; CLI `python -m comp1 [--drone mock|tello] [--port 8765] [--no-browser]` (default: `mock`, so no one crashes a real drone by accident).

- [ ] **Step 1: Failing test (monkeypatch djitellopy — no hardware in CI)**

```python
# append to tests/test_mock_drone.py
def test_tello_adapter_maps_commands(monkeypatch):
    import comp1.drone.tello as t
    calls = []
    class FakeTello:
        def connect(self): calls.append("connect")
        def streamon(self): calls.append("streamon")
        def takeoff(self): calls.append("takeoff")
        def move_forward(self, cm): calls.append(f"move_forward {cm}")
        def rotate_clockwise(self, deg): calls.append(f"cw {deg}")
        def get_battery(self): return 87
        def get_frame_read(self): raise RuntimeError("not in test")
    monkeypatch.setattr(t, "Tello", FakeTello)
    d = t.TelloDrone()
    d.connect(); d.takeoff(); d.move("forward", 40); d.rotate("cw", 90)
    assert calls == ["connect", "streamon", "takeoff", "move_forward 40", "cw 90"]
    assert d.battery() == 87
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement**

```python
# comp1/drone/tello.py
import numpy as np
from djitellopy import Tello
from .base import DroneAdapter

class TelloDrone(DroneAdapter):
    def __init__(self):
        self._t = Tello()
        self._reader = None

    def connect(self):
        self._t.connect()
        self._t.streamon()

    def takeoff(self): self._t.takeoff()
    def land(self): self._t.land()
    def emergency(self): self._t.emergency()

    def move(self, direction, cm):
        {"forward": self._t.move_forward, "back": self._t.move_back,
         "left": self._t.move_left, "right": self._t.move_right,
         "up": self._t.move_up, "down": self._t.move_down}[direction](cm)

    def rotate(self, direction, deg):
        (self._t.rotate_clockwise if direction == "cw"
         else self._t.rotate_counter_clockwise)(deg)

    def flip(self, direction):
        self._t.flip({"forward": "f", "back": "b", "left": "l", "right": "r"}[direction])

    def get_frame(self) -> np.ndarray | None:
        if self._reader is None:
            self._reader = self._t.get_frame_read()
        frame = self._reader.frame
        return None if frame is None else frame[:, :, ::-1].copy()  # RGB→BGR

    def battery(self) -> int:
        return self._t.get_battery()
```

```python
# comp1/__main__.py
import argparse, threading, webbrowser
import uvicorn
from .server import create_app

def main():
    ap = argparse.ArgumentParser("comp1")
    ap.add_argument("--drone", choices=["mock", "tello"], default="mock")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.drone == "tello":
        from .drone.tello import TelloDrone
        drone = TelloDrone()
    else:
        from .drone.mock import MockDrone
        drone = MockDrone()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    uvicorn.run(create_app(drone), host="127.0.0.1", port=args.port)

if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests — PASS.** Also smoke: `venv\Scripts\python -m comp1 --no-browser` starts and serves the UI.

- [ ] **Step 5: REAL-FLIGHT CHECKLIST (Tello on hand — do in a clear indoor space, propeller guards on):**
  1. Charge battery; join the laptop to the Tello WiFi (`TELLO-xxxx`).
  2. `venv\Scripts\python -m comp1 --drone tello` — verify live camera appears in the UI.
  3. Hold a printed red circle (A4) in view — verify overlay shows `VICTIM` and position buckets change as you move it left/right.
  4. Run program: `take off → fly up 30 → land`. Hand on the E-STOP button.
  5. Run: `take off → repeat until [victim marker seen?] [turn cw 30] → approach victim and stop → signal victim found → land`. Verify the approach stops at a sensible distance (tune `approach_stop_area` if it gets too close/far — record the value).
  6. Test Stop mid-flight (should land) and EMERGENCY STOP (motors cut — catch or let it drop onto something soft).

- [ ] **Step 6: Commit** — `git commit -m "feat: Tello adapter and CLI entry point"`

---

### Task 10: Offline-asset guard test

**Files:**
- Test: `tests/test_offline_assets.py`

- [ ] **Step 1: Write the test (fails if any served asset references the network)**

```python
# tests/test_offline_assets.py
import re
from pathlib import Path

FRONTEND = Path(__file__).parent.parent / "comp1" / "frontend"

def test_no_external_urls_in_frontend():
    pattern = re.compile(rb"https?://(?!localhost)")
    offenders = []
    for f in FRONTEND.rglob("*"):
        if f.suffix in {".html", ".css", ".js"} and "vendor" not in f.parts:
            if pattern.search(f.read_bytes()):
                offenders.append(str(f))
    assert not offenders, f"external URLs found (breaks offline use): {offenders}"

def test_blockly_is_vendored():
    assert (FRONTEND / "vendor" / "blockly.min.js").stat().st_size > 500_000
```

- [ ] **Step 2: Run — PASS** (fix any offenders it catches).
- [ ] **Step 3: Commit** — `git commit -m "test: guard against non-offline frontend assets"`

---

### Task 11: PyInstaller onedir packaging

**Files:**
- Create: `comp1.spec`, `comp1/launcher.py`

- [ ] **Step 1: Launcher (PyInstaller needs a plain script entry)**

```python
# comp1/launcher.py
from comp1.__main__ import main
if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Spec file**

```python
# comp1.spec
a = Analysis(
    ["comp1/launcher.py"],
    datas=[("comp1/frontend", "comp1/frontend")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto",
                   "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                   "uvicorn.lifespan.on"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name="comp1", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="comp1")
```

- [ ] **Step 3: Build and smoke-test**

```powershell
venv\Scripts\pyinstaller comp1.spec --noconfirm
dist\comp1\comp1.exe --no-browser
```
Expected: server starts, http://localhost:8765 serves the full UI with the mock drone, video panel updates. If `FRONTEND_DIR` resolves wrong under PyInstaller, adjust `server.py` to use `sys._MEIPASS`-aware lookup:

```python
import sys
FRONTEND_DIR = (Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent))
                / "comp1" / "frontend") if getattr(sys, "frozen", False) \
               else Path(__file__).parent / "frontend"
```

- [ ] **Step 4: Commit** — `git commit -m "build: PyInstaller onedir packaging"`

---

### Task 12: Docs

**Files:**
- Modify: `README.md` (quick start: install, `python -m comp1`, `--drone tello`, build command)
- Create: `docs/plans/2026-07-28-implementation-plan.md` (copy of this plan — user keeps plans in-repo)

- [ ] **Step 1: Update README quick-start section; save plan copy.**
- [ ] **Step 2: Full suite green:** `venv\Scripts\pytest -v`
- [ ] **Step 3: Commit** — `git commit -m "docs: quick start and implementation plan"`

---

## Verification (end-to-end)

1. `venv\Scripts\pytest -v` — all tests pass.
2. `python -m comp1` (mock): browser opens, build the search program from Task 9 step 5, run → highlights walk the blocks, console reports "mission done".
3. `python -m comp1 --drone tello` + real-flight checklist (Task 9 Step 5) passes.
4. `dist\comp1\comp1.exe` works with WiFi disabled (offline proof).

## Out of scope (tracked for later plans)

- Return-to-start / mission pads (requirements §3.4 — pending organiser decision).
- HSV tuning debug panel (slider UI) — follow-up feature; `VisionConfig` already centralizes the parameters.
- macOS build + Chromebook hub deployment (architecture doc §4 — pending OS confirmation).
- Save/load student programs to file.
