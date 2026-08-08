# Automatic Marker Colour Calibration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an operator calibrate marker HSV bands by pressing one button instead of dragging a box inside the marker.

**Architecture:** The victim marker is guaranteed red, so no colour-agnostic circle finder is needed. `find_targets` is run against a deliberately loose HSV config (`RED_PRIOR`) and becomes the region locator; the largest candidate's interior is handed to the existing `suggest_hsv`, which tightens the bands to that marker's real appearance. A mask-coverage gate rejects proposals that match too much of the scene. Everything downstream — preview, sliders, Apply, venue profiles, TOML download — is unchanged.

**Tech Stack:** Python 3.12+, OpenCV (`cv2`), NumPy, FastAPI + `TestClient` WebSockets, pytest (`asyncio_mode = "auto"`), vanilla ES5-style browser JS (no build step, no framework).

**Spec:** [docs/specs/2026-08-08-auto-colour-calibration.md](../specs/2026-08-08-auto-colour-calibration.md)

## Global Constraints

- **Test runner is `.conda/bin/pytest`** from the repo root. `CLAUDE.md` documents PowerShell paths (`venv\Scripts\pytest`); this checkout is Linux with a conda env. Every `Run:` line below is copy-pasteable as written.
- **All new code lives in `comp1/vision/calibration.py`, `comp1/server.py`, and `comp1/frontend/`.** Do not modify `comp1/interpreter.py`, `comp1/api.py`, `comp1/protocol.py`, `comp1/frontend/blocks.js`, or `comp1/vision/detector.py`.
- **No new block and no new sensor.** This is a setup action, not a drone capability. The four-place "adding a capability" checklist in `CLAUDE.md` does not apply.
- **`DEFAULT_CONFIG` must keep its current values.** `RED_PRIOR` is a separate object built with `dataclasses.replace`; it never becomes the detector's config.
- **Calibration stays refused while a mission runs or a drone switch is in flight.** The new message type joins the existing guarded tuple in `server.py`; do not add a separate unguarded branch.
- **Operator-facing text says "marker", never "victim".** In the corridor scenery the `DESTINATION` sign is byte-identical to a victim in the camera, so auto-calibration may legitimately sample it.
- **The frontend must work fully offline.** No external URLs in any served `.html`/`.css`/`.js`; `tests/test_offline_assets.py` enforces this.
- **The coverage gate applies to proposals only** (`vision_auto`, `vision_sample`), never to `vision_preview` or `vision_apply`.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `comp1/vision/calibration.py` | Modify | Add `RED_PRIOR`, `ROI_FILL`, `MAX_MASK_COVERAGE`, `find_marker_roi`, `check_coverage`, `auto_suggest_hsv`. Existing functions untouched. |
| `tests/test_calibration.py` | Modify | Unit coverage for the four new callables, including sim-rendered frames. |
| `comp1/server.py` | Modify | Route `vision_auto`; add the coverage gate and the `roi` response field to both proposal paths. |
| `tests/test_server.py` | Modify | WebSocket coverage for `vision_auto`, the gate, and the mission refusal. |
| `comp1/frontend/index.html` | Modify | The **Find marker for me** button, revised instruction copy, cache-busted script version. |
| `comp1/frontend/calibration.js` | Modify | Button handler; draw the returned ROI on the frozen canvas. |
| `comp1/frontend/style.css` | Modify | Lay the two figure buttons out side by side; allow `.primary` inside a figure. |
| `tests/test_offline_assets.py` | Modify | Assert the button and its handler are wired. |

Four tasks, drawn where a reviewer could reject one while accepting its neighbour: locating the region, guarding and combining, exposing over the socket, exposing in the browser.

---

### Task 1: Locate the marker region

**Files:**
- Modify: `comp1/vision/calibration.py` (add after the `HSV_KEYS` constant and after `suggest_hsv`)
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `find_targets` and `color_mask` from `comp1.vision.detector`; `DEFAULT_CONFIG` from `comp1.vision.config`; the existing `CalibrationError` in this module.
- Produces:
  - `RED_PRIOR: VisionConfig` — module-level, loose HSV bands.
  - `ROI_FILL: float` — `0.5`.
  - `find_marker_roi(frame_bgr: np.ndarray) -> list[float]` — returns `[x0, y0, x1, y1]` normalised to `0..1`, or raises `CalibrationError`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calibration.py`:

```python
def test_auto_roi_lands_inside_the_marker():
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
    # every corner of the box must be a red pixel of the disc, not the rim
    for x in (x0, x1):
        for y in (y0, y1):
            b, g, r = frame[int(y * 480), int(x * 640)]
            assert r > 150 and b < 80, f"corner ({x:.3f}, {y:.3f}) is not marker red"


def test_auto_roi_is_square_in_pixels_not_in_normalised_units():
    """radius_norm is normalised by width; the ROI's y bounds are scaled by
    height. Equal normalised half-extents would give a box a third too short."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert (x1 - x0) * 640 == pytest.approx((y1 - y0) * 480, rel=0.02)


def test_auto_roi_refuses_a_frame_with_no_red_marker():
    frame = np.full((480, 640, 3), (90, 70, 60), np.uint8)   # blue-dominant, like scenery
    with pytest.raises(CalibrationError, match="no red marker"):
        find_marker_roi(frame)


def test_auto_roi_refuses_an_empty_frame():
    with pytest.raises(CalibrationError):
        find_marker_roi(None)


def test_auto_roi_is_still_usable_for_a_distant_marker():
    """The area gate implies a radius of ~14 px, which must still clear
    suggest_hsv's 25-pixel and 0.01-normalised-extent floors."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 16, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert x1 - x0 > 0.01 and y1 - y0 > 0.01
    assert suggest_hsv(frame, [x0, y0, x1, y1])["lower1"][0] == 0
```

Extend the import at the top of the file to:

```python
from comp1.vision.calibration import (CalibrationError, config_with_hsv,
                                      draw_calibration_preview, find_marker_roi,
                                      suggest_hsv)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.conda/bin/pytest tests/test_calibration.py -v`
Expected: FAIL — `ImportError: cannot import name 'find_marker_roi'`

- [ ] **Step 3: Implement `RED_PRIOR` and `find_marker_roi`**

In `comp1/vision/calibration.py`, extend the detector import:

```python
from .detector import color_mask, detect_red_circle, draw_overlay, find_targets
```

and add `DEFAULT_CONFIG` to the config import:

```python
from .config import DEFAULT_CONFIG, VisionConfig
```

Add below `HSV_KEYS`:

```python
# A deliberately loose red band, used *only* to locate a marker for calibration.
# The victim marker is guaranteed red, so no colour-agnostic circle finder is
# needed. The saturation and value floors can be this generous because the hue
# gate is what excludes scenery: sceneries are blue-dominant in BGR by
# construction, so a lower saturation floor does not bring a wall into range.
# This never becomes the detector's config.
RED_PRIOR = replace(
    DEFAULT_CONFIG,
    lower1=(0, 60, 40), upper1=(15, 255, 255),
    lower2=(160, 60, 40), upper2=(180, 255, 255),
)

# Half-side of the sample box as a fraction of the marker radius. The binding
# constraint is the box's corners, not its sides: half-side k*R puts corners at
# k*R*sqrt(2), so the inscribed square's k = 1/sqrt(2) lands them exactly on the
# rim. 0.5 puts them at 0.71*R, clear of the anti-aliased edge.
ROI_FILL = 0.5
```

Add after `suggest_hsv`:

```python
def find_marker_roi(frame_bgr: np.ndarray) -> list[float]:
    """Locate the largest red marker and return a sample box inside it.

    This is what removes the manual drag: ``find_targets`` already gates on area
    and circularity, so running it against :data:`RED_PRIOR` turns the detector
    itself into the region locator.
    """
    if frame_bgr is None or frame_bgr.size == 0:
        raise CalibrationError("no camera frame is available")
    targets = find_targets(frame_bgr, RED_PRIOR)
    if not targets:
        raise CalibrationError(
            "no red marker in view — point the camera at one and try again")
    # nearest-first, and distance comes from apparent radius, so targets[0] is
    # also the largest blob: the most pixels to compute statistics from
    target = targets[0]
    height, width = frame_bgr.shape[:2]
    # radius_norm is normalised by width, but the ROI's y bounds are scaled by
    # height, so the y half-extent needs the aspect correction to stay square
    half_x = ROI_FILL * target.radius_norm
    half_y = half_x * width / height
    clamp = lambda v: min(max(float(v), 0.0), 1.0)
    return [clamp(target.cx - half_x), clamp(target.cy - half_y),
            clamp(target.cx + half_x), clamp(target.cy + half_y)]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.conda/bin/pytest tests/test_calibration.py -v`
Expected: PASS — all 10 tests

- [ ] **Step 5: Commit**

```bash
git add comp1/vision/calibration.py tests/test_calibration.py
git commit -m "feat(vision): locate a marker region for calibration automatically

A wide red prior turns find_targets into the region locator, so an
operator no longer has to drag a box inside the marker."
```

---

### Task 2: Guard the proposal and combine the pieces

**Files:**
- Modify: `comp1/vision/calibration.py` (add after `find_marker_roi`)
- Test: `tests/test_calibration.py`

**Interfaces:**
- Consumes: `find_marker_roi` and `RED_PRIOR` from Task 1; existing `suggest_hsv`, `config_with_hsv`, `color_mask`.
- Produces:
  - `MAX_MASK_COVERAGE: float` — `0.25`.
  - `check_coverage(frame_bgr: np.ndarray, cfg: VisionConfig) -> None` — raises `CalibrationError` if the mask is too large; returns `None` otherwise.
  - `auto_suggest_hsv(frame_bgr: np.ndarray) -> tuple[dict, list[float]]` — returns `(values, roi)` **in that order**, where `values` has the same shape as `suggest_hsv`'s return.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_calibration.py`:

```python
def test_coverage_gate_rejects_bands_that_match_most_of_the_scene():
    frame = np.full((100, 100, 3), (0, 0, 220), np.uint8)   # all red
    with pytest.raises(CalibrationError, match="too much of the scene"):
        check_coverage(frame, DEFAULT_CONFIG)


def test_coverage_gate_accepts_an_isolated_marker():
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)
    assert check_coverage(frame, DEFAULT_CONFIG) is None


def test_auto_calibration_recovers_a_marker_the_defaults_miss():
    """The point of the feature: a marker under a light level the shipped
    bands reject is recovered without anyone touching a slider."""
    frame = np.full((480, 640, 3), (90, 70, 60), np.uint8)
    # dim red: HSV value ~70, under the default floor of 80
    cv2.circle(frame, (320, 240), 60, (10, 10, 70), -1)

    assert not detect_red_circle(frame, DEFAULT_CONFIG).found

    values, roi = auto_suggest_hsv(frame)

    assert detect_red_circle(frame, config_with_hsv(DEFAULT_CONFIG, values)).found
    assert len(roi) == 4


def test_auto_calibration_on_a_simulated_victim_round_trips():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    frame = render(world, 2.0, 2.0, 1.0, 0.0)

    values, _roi = auto_suggest_hsv(frame)

    assert detect_red_circle(frame, config_with_hsv(DEFAULT_CONFIG, values)).found


def test_auto_calibration_never_calibrates_on_scenery():
    """The wide prior must not open a door the detector's own bands keep shut.
    Sweep rather than spot-check: corners and floor-facing views shade
    differently, and any one of them becoming 'a red marker' is the failure."""
    world = World(size_m=4.0, markers=[])
    for x in (0.3, 2.0, 3.7):
        for y in (0.3, 2.0, 3.7):
            for heading in range(0, 360, 60):
                frame = render(world, x, y, 1.0, heading)
                with pytest.raises(CalibrationError, match="no red marker"):
                    auto_suggest_hsv(frame)


def test_auto_calibrated_bands_do_not_flag_scenery_elsewhere_in_the_arena():
    """Bands fitted at one pose must still reject the room at every other."""
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    values, _roi = auto_suggest_hsv(render(world, 2.0, 2.0, 1.0, 0.0))
    candidate = config_with_hsv(DEFAULT_CONFIG, values)

    empty = World(size_m=4.0, markers=[])
    for x in (0.3, 2.0, 3.7):
        for y in (0.3, 2.0, 3.7):
            for heading in range(0, 360, 60):
                det = detect_red_circle(render(empty, x, y, 1.0, heading), candidate)
                assert not det.found, f"scenery detected at {x},{y} hdg {heading}"
```

Extend the imports at the top of `tests/test_calibration.py`:

```python
from comp1.vision.calibration import (CalibrationError, auto_suggest_hsv,
                                      check_coverage, config_with_hsv,
                                      draw_calibration_preview, find_marker_roi,
                                      suggest_hsv)
from comp1.sim.render import render
from comp1.sim.world import Marker, VICTIM, World
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.conda/bin/pytest tests/test_calibration.py -v`
Expected: FAIL — `ImportError: cannot import name 'auto_suggest_hsv'`

- [ ] **Step 3: Implement the gate and the combiner**

Add to `comp1/vision/calibration.py`, below `ROI_FILL`:

```python
# Largest share of the frame a proposed mask may cover. Over-wide saturation or
# value bounds look correct in their own preview -- the marker really is
# isolated in the shot being calibrated on -- and then flag a wall on the next
# frame from a different angle. This is what keeps a calibration from quietly
# invalidating the scenery false-positive tests.
MAX_MASK_COVERAGE = 0.25
```

and below `find_marker_roi`:

```python
def check_coverage(frame_bgr: np.ndarray, cfg: VisionConfig) -> None:
    """Raise if ``cfg`` accepts an implausible share of the frame."""
    mask = color_mask(frame_bgr, cfg)
    if float(np.count_nonzero(mask)) / mask.size > MAX_MASK_COVERAGE:
        raise CalibrationError(
            "these ranges match too much of the scene — try a tighter shot of the marker")


def auto_suggest_hsv(frame_bgr: np.ndarray) -> tuple[dict, list[float]]:
    """Locate a red marker and propose HSV bands fitted to it.

    Returns the bands *and* the region they came from: an auto-calibrator that
    will not show what it looked at is hard to trust at the moment it gets
    something wrong.
    """
    roi = find_marker_roi(frame_bgr)
    return suggest_hsv(frame_bgr, roi), roi
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.conda/bin/pytest tests/test_calibration.py -v`
Expected: PASS — all 16 tests

If `test_auto_calibration_never_calibrates_on_scenery` fails, the correct fix is to **tighten `RED_PRIOR`** (raise its saturation floor from 60 toward 100, or narrow the hue bands from 15/160 toward 10/170) until the sweep passes — never to relax the assertion. Then re-run Task 1's tests, which must still pass.

- [ ] **Step 5: Run the whole suite to confirm nothing regressed**

Run: `.conda/bin/pytest -q`
Expected: PASS — in particular `tests/test_sim_render.py::test_scenery_alone_is_never_a_victim`

- [ ] **Step 6: Commit**

```bash
git add comp1/vision/calibration.py tests/test_calibration.py
git commit -m "feat(vision): propose HSV bands from an auto-located marker

Adds a mask-coverage gate so a widened band cannot pass its own preview
and then flag a wall from the next pose."
```

---

### Task 3: Expose it over the WebSocket

**Files:**
- Modify: `comp1/server.py:20-21` (imports), `comp1/server.py:444-477` (the vision branch)
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: `auto_suggest_hsv` and `check_coverage` from Task 2.
- Produces: the `{"type": "vision_auto"}` request, and a `roi` field on every `vision_suggestion` response.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_server.py`:

```python
def grey_frame():
    return np.full((480, 640, 3), (90, 70, 60), np.uint8)


def all_red_frame():
    return np.full((480, 640, 3), (0, 0, 220), np.uint8)


def test_auto_calibration_suggests_bands_without_a_selection():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")          # latest raw frame is now available
        ws.send_json({"type": "vision_auto"})
        msg = collect_until(ws, "vision_suggestion")
    assert msg["config"]["lower1"][0] == 0
    assert msg["config"]["lower2"][0] >= 170
    assert len(msg["preview_jpeg"]) > 100
    x0, y0, x1, y1 = msg["roi"]
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_a_manual_selection_echoes_its_region_back():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_sample", "roi": [0.42, 0.38, 0.58, 0.62]})
        msg = collect_until(ws, "vision_suggestion")
    assert msg["roi"] == [0.42, 0.38, 0.58, 0.62]


def test_auto_calibration_reports_when_no_marker_is_in_view():
    app = create_app(MockDrone(frame_factory=grey_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_auto"})
        error = collect_until(ws, "vision_error")
    assert "no red marker" in error["message"]


def test_a_suggestion_matching_most_of_the_scene_is_refused():
    app = create_app(MockDrone(frame_factory=all_red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_sample", "roi": [0.4, 0.4, 0.6, 0.6]})
        error = collect_until(ws, "vision_error")
    assert "too much of the scene" in error["message"]


def test_auto_calibration_is_refused_during_a_mission():
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=2, delay=0.2))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}})
        collect_until(ws, "reset", limit=200)
        ws.send_json({"type": "vision_auto"})
        error = collect_until(ws, "vision_error", limit=200)
    assert "stop the mission" in error["message"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.conda/bin/pytest tests/test_server.py -k "auto_calibration or echoes_its_region or most_of_the_scene" -v`
Expected: FAIL — `AssertionError: no vision_suggestion message` (the server ignores the unknown type), and `KeyError: 'roi'` on the echo test.

- [ ] **Step 3: Wire the new message type**

In `comp1/server.py`, extend the calibration import at lines 20-21:

```python
from .vision.calibration import (CalibrationError, auto_suggest_hsv, check_coverage,
                                 config_with_hsv, draw_calibration_preview,
                                 hsv_values, suggest_hsv)
```

Change the guarded tuple at line 444 to include the new type:

```python
                elif msg["type"] in ("vision_sample", "vision_auto", "vision_preview",
                                      "vision_apply", "vision_reset"):
```

Replace the `vision_sample` arm inside the `try:` block (currently lines 456-461) with:

```python
                            if msg["type"] in ("vision_sample", "vision_auto"):
                                raw = app.state.latest_frame
                                if msg["type"] == "vision_auto":
                                    values, roi = auto_suggest_hsv(raw)
                                else:
                                    roi = msg.get("roi")
                                    values = suggest_hsv(raw, roi)
                                candidate = config_with_hsv(cfg, values)
                                # gate proposals only: an operator dragging a
                                # slider wide on purpose should get the preview
                                # they asked for, not an error
                                check_coverage(raw, candidate)
                                response = _vision_message("vision_suggestion", candidate)
                                response["preview_jpeg"] = _vision_preview(candidate)
                                response["roi"] = [float(v) for v in roi]
                                await ws.send_text(json.dumps(response))
```

Leave the `vision_preview`, `vision_apply`, and `vision_reset` arms exactly as they are.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.conda/bin/pytest tests/test_server.py -v`
Expected: PASS — including the pre-existing `test_marker_region_produces_a_previewed_hsv_suggestion`

- [ ] **Step 5: Commit**

```bash
git add comp1/server.py tests/test_server.py
git commit -m "feat(server): add the vision_auto calibration request

Replies with the existing vision_suggestion message plus the sampled
region, so the browser's accept/preview/apply flow is unchanged."
```

---

### Task 4: Expose it in the calibration dialog

**Files:**
- Modify: `comp1/frontend/index.html:114-135` and the script tag at line 195
- Modify: `comp1/frontend/calibration.js`
- Modify: `comp1/frontend/style.css:158-190`
- Test: `tests/test_offline_assets.py`

**Interfaces:**
- Consumes: the `vision_auto` request and the `roi` response field from Task 3.
- Produces: no further consumers — this is the top of the stack.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_offline_assets.py`:

```python
def test_auto_calibration_is_reachable_from_the_dialog():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "calibration.js").read_text(encoding="utf-8")
    assert 'id="vision-auto"' in html
    assert '"vision_auto"' in js
    assert "message.roi" in js          # the sampled region is drawn back
    assert 'src="calibration.js?v=' in html
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.conda/bin/pytest tests/test_offline_assets.py -v`
Expected: FAIL — `assert 'id="vision-auto"' in html`

- [ ] **Step 3: Add the button and revise the copy**

In `comp1/frontend/index.html`, replace the header paragraph at line 115:

```html
          <p>Keep the drone still, capture a frame, then press Find marker for me — or drag a
            box inside the red marker yourself.</p>
```

Replace the first figure's button (line 126) with a two-button row:

```html
          <div class="vision-figure-actions">
            <button id="vision-auto" type="button" class="primary">Find marker for me</button>
            <button id="vision-refresh" type="button">Capture current frame</button>
          </div>
```

Replace the status paragraph at line 132:

```html
      <p id="vision-status" role="status">Press Find marker for me, or drag a box tightly inside
        the coloured marker.</p>
```

Bump the script version at line 195 so browsers do not serve a cached handler:

```html
  <script src="calibration.js?v=20260808-1"></script>
```

- [ ] **Step 4: Add the button handler and draw the region back**

In `comp1/frontend/calibration.js`, update the instruction inside `captureFrame` (line 75):

```javascript
    tell("Press “Find marker for me”, or drag a box tightly inside the coloured marker.");
```

Add the handler beside the other `onclick` assignments, after the `vision-refresh` line (line 162):

```javascript
  document.getElementById("vision-auto").onclick = () => {
    // recapture first so the canvas shows roughly the frame the server will
    // sample, and so the returned region lands on the right picture
    if (!captureFrame()) return;
    tell("Looking for a red marker…");
    window.COMP1_SEND({ type: "vision_auto" });
  };
```

Replace the `vision_suggestion` branch of the bus handler (lines 207-210) with:

```javascript
    } else if (message.type === "vision_suggestion") {
      configToControls(message.config);
      if (message.roi && frozen.width) {
        selection = [message.roi[0] * frame.width, message.roi[1] * frame.height,
          message.roi[2] * frame.width, message.roi[3] * frame.height];
        redraw();
      }
      preview.src = `data:image/jpeg;base64,${message.preview_jpeg}`;
      tell("Suggested ranges are ready. Check the highlighted pixels, then Apply.", "ok");
```

- [ ] **Step 5: Lay the two buttons out**

In `comp1/frontend/style.css`, add after the `.vision-images button, ...` rule (ends line 163):

```css
.vision-figure-actions { display: flex; gap: 7px; }
```

and extend the existing `.primary` rule at line 190 so a primary button works inside a figure too:

```css
.vision-actions .primary, .vision-figure-actions .primary { border-color: #0284c7;
  background: #0284c7; color: #fff; font-weight: 700; }
```

- [ ] **Step 6: Run the frontend tests**

Run: `.conda/bin/pytest tests/test_offline_assets.py -v && node --test tests/js/blocks.test.js`
Expected: PASS both — no external URLs introduced, block serializer unaffected

- [ ] **Step 7: Verify by hand against the simulator**

Run: `.conda/bin/python -m comp1 --drone sim --seed 4`

In the browser: press **Tune colour**, then **Find marker for me** while a marker is in view. Confirm the box is drawn inside the marker on the left canvas, the mask preview isolates it on the right, and **Apply** takes effect. Then turn the drone away from every marker and press it again — confirm the status line reads "no red marker in view" rather than proposing something.

- [ ] **Step 8: Run the full suite**

Run: `.conda/bin/pytest -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add comp1/frontend/index.html comp1/frontend/calibration.js comp1/frontend/style.css tests/test_offline_assets.py
git commit -m "feat(frontend): one-button marker colour calibration

Draws the sampled region back onto the frozen frame so the operator can
see what the proposal was fitted to."
```

---

## Documentation

- [ ] **Update the spec's status line**

In `docs/specs/2026-08-08-auto-colour-calibration.md`, change `**Status:** Design` to `**Status:** Implemented`.

- [ ] **Record the new calibration entry point in CLAUDE.md**

In the "Vision pipeline" section of `CLAUDE.md`, after the paragraph describing `VisionConfig.load_file`, add:

```markdown
`comp1/vision/calibration.py` is the on-site re-tuning path (§3.1) and is **operator-triggered
only** — nothing in `interpreter.py` or `api.py` may call it. `auto_suggest_hsv` locates the marker
itself by running `find_targets` against a loose `RED_PRIOR`, then fits bands with the same
`suggest_hsv` a manual drag uses. `RED_PRIOR` exists solely to find a region and never becomes the
detector's config. `check_coverage` rejects any *proposal* whose mask covers more than
`MAX_MASK_COVERAGE` of the frame — without it a widened band passes its own preview and then flags
a wall, silently invalidating `test_scenery_alone_is_never_a_victim`.
```

- [ ] **Commit the documentation**

```bash
git add CLAUDE.md docs/specs/2026-08-08-auto-colour-calibration.md
git commit -m "docs: record the automatic calibration path"
```
