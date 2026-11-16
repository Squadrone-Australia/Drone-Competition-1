# Switchable marker detector: colour (OpenCV HSV) or AI model (YOLO fire_pose)

## Context

`solution_fire/` has a trained YOLO26-pose red-circle model (`solution_fire/models/fire_pose.pt`,
nano; 110/110 real discs found, 0/151 empty frames alarmed, 37 ms/frame CPU). The app still only
uses the HSV detector in `comp1/vision/detector.py`. The user wants the app to use the model for
recognition, with a switch between pure OpenCV and YOLO. HSV-only controls (Tune colour) must be
disabled while YOLO is picked.

Decisions (confirmed):
- **Runtime: ONNX + onnxruntime.** Export `fire_pose.pt` once and ship the `.onnx` inside `comp1`.
  Add `onnxruntime` as a dependency (~15 MB). No torch or ultralytics in the shipped app.
  `solution_fire` stays a dev/training package that `comp1` never imports.
- **Switch: a live toggle that is remembered.** A "Colour / AI model" selector on the camera
  window, next to Tune colour. It takes effect immediately, is refused mid-mission, is saved in
  settings, and can be set with a `--detector` CLI flag.

## Design

The key move is to make **`VisionConfig.detector` (`"hsv"` | `"yolo"`) pick the finder inside
`TargetTracker`**. Every existing `TargetTracker(cfg)` call site then follows the switch with no
changes: the server's five call sites, `api._standalone_session`, and the interpreter through
`latest_detection`. Obstacles (`find_obstacles`) stay on the HSV saturation mask in both modes.
They exclude targets by `(cx, cy, radius_norm)`, so YOLO targets slot straight in.

### 1. Model asset
- Export: `.venv\Scripts\python -m solution_fire.export solution_fire/models/fire_pose.pt --format onnx`
  (installs `onnx` into `.venv` if needed). Copy the result to `comp1/vision/models/fire_pose.onnx`.
- Before writing the decoder, inspect the output tensor's shape with onnxruntime.
  YOLO26 is NMS-free, so the expected shape is `(1, 300, 6 + 5*3)`: `x1 y1 x2 y2 score cls` plus
  5 keypoints `(x, y, conf)` in letterboxed 640 px. The decoder is written against what is
  actually there.
- Add `comp1/vision/models/README.md` with the source weights, export command, input size, the
  keypoint layout (0 centre, 1 top, 2 right, 3 bottom, 4 left) and the score threshold (≥ 0.5,
  per `solution_fire/README.md`).
- `.gitignore` excludes `*.onnx` only under `solution_fire/`, so this file gets committed.

### 2. `comp1/vision/yolo.py` (new)
- `MODEL_PATH`: resolved relative to the module, so it works both frozen and from source.
- `available() -> (bool, reason | None)`: checks that onnxruntime imports and the model file
  exists. It never raises.
- `YoloFinder`: creates the `onnxruntime.InferenceSession` lazily on first use (CPU provider),
  as a module-level cached singleton. Its `find_targets(frame_bgr, cfg) -> list[Target]`:
  - Preprocess: letterbox to 640, BGR→RGB, /255, NCHW float32.
  - Decode: drop rows below `cfg.yolo_score_min`, then undo the letterbox scale and padding.
  - Map each detection to the existing `Target`, using **comp1's own `cfg.intrinsics`**. That
    keeps the YOLO and HSV paths, and the simulator, on one camera model.
    - `cx`, `cy`: the centre keypoint if its confidence is ≥ 0.5, otherwise the box centre.
    - `radius_norm`: half the top–bottom keypoint span (a board turned about its vertical axis
      does not shrink it). Falls back to left–right, then the box height. Divided by frame width.
    - `distance_m`: `cam.distance_m(radius_norm, cfg.marker_radius_m)`, the same formula as HSV.
    - `bearing_deg`, `elevation_deg` and `position` come from the existing intrinsics helpers and
      `center_band`.
    - `area_ratio`: the ellipse area of the box divided by the frame area. `circularity` and
      `solidity` are 1.0 (not measured).
  - Sort nearest first, like `detector.find_targets`.
- Add `score: float = 1.0` as the last field of `Target`. It is frozen with a default, so existing
  constructors are untouched. YOLO fills it in.

### 3. `comp1/vision/config.py` and `detector.py`
- `VisionConfig`: add `detector: str = "hsv"` and `yolo_score_min: float = 0.5`, with a short
  comment block in the file's style.
- `detector.py`: add `_finder(cfg)`. It returns `find_targets` for `"hsv"` and
  `yolo.find_targets` for `"yolo"`; the yolo module is imported lazily so the HSV path never loads
  onnxruntime. `TargetTracker.update` and `detect_red_circle` call it instead of `find_targets`
  directly. `calibration.py` keeps calling HSV `find_targets` and `color_mask` directly, which is
  correct because it only tunes HSV.
- `draw_overlay`: prefix the label with `AI` when `det` came from YOLO, so a screenshot shows
  which detector is live. Pass the detector name in as an optional argument.

### 4. Server (`comp1/server.py`)
- **Run detection off the event loop.** In `_video_loop`, change it to
  `det = await asyncio.to_thread(app.state.tracker.update, frame)`. YOLO takes about 40 ms per
  frame, and running it inline would stall the WebSocket loop, including e-stop handling. This is
  also a small win for HSV.
- New `vision_detector` socket message `{detector}`:
  - While `drone_switching` → `error` "wait for the drone connection".
  - While `interp` is set → `error` "stop the mission before changing the detector".
  - For `yolo` when `available()` is false → `error` "the AI model is not available: <reason>".
  - Otherwise: set `cfg.detector`, `app.state.tracker = TargetTracker(cfg)`, reset
    `latest_detection`, `_remember({"detector": ...})`, and broadcast `_detector_message()`.
- `_detector_message()` = `{"type": "vision_detector", "detector", "yolo_available", "yolo_reason"}`.
  It is sent on connect next to `vision_config`.
- The existing `vision_sample/auto/preview/apply/reset` branch: when `cfg.detector == "yolo"`,
  reply `vision_error` "colour tuning only applies to the Colour detector". The server enforces
  this as well as the UI.

### 5. Settings and CLI
- `settings.py`: add `DETECTORS = ("hsv", "yolo")` and `detector: str = "hsv"` on `Settings`, and
  validate it in `_clean` like `drone`/`scenery`.
- `__main__.py`: add `--detector {hsv,yolo}` with `default=None`. Then
  `detector = resolve(args.detector, saved.detector)`.
  - If the result is `yolo` and `yolo.available()` is false, log a warning and fall back to
    `hsv` rather than refusing to start (warn, don't raise).
  - Apply it with `cfg = replace(cfg, detector=detector)`.

### 6. Frontend
- `index.html`: next to `#vision-calibrate` in the camera figcaption, add
  `<select id="vision-detector">` with the options **Colour** (`hsv`) and **AI model** (`yolo`).
  Bump the `calibration.js?v=` query.
- `calibration.js`, which already owns the Tune colour button and the `running` state:
  - On `change`, send `{type: "vision_detector", detector}`. The select only changes when the
    server's `vision_detector` broadcast comes back, so a refused switch snaps back.
  - On a `vision_detector` message: set the select. Disable the AI option, with
    `yolo_reason` as its title, when the model is unavailable. Set
    `openButton.disabled = running || detector === "yolo"` with the title
    "Colour tuning is only for the Colour detector". If the calibration dialog is open, close it.
  - On `running`: disable the select during a mission, using the same rule for the button.
- `styles.css`: a compact style for the select, matching the figcaption buttons.

### 7. Hints and packaging
- `hints.py`: add a row for "AI model is not available" → "Reinstall Drone Coder, or switch the
  detector back to Colour." Feed the real message in `tests/test_hints.py`.
- `pyproject.toml`: add `onnxruntime` to `dependencies`, and add package-data for
  `comp1/vision/models/*.onnx`.
- `comp1.spec`: add `("comp1/vision/models", "comp1/vision/models")` to `datas`. onnxruntime's
  own hook in pyinstaller-hooks-contrib collects its DLLs.
- `build.ps1`: next to the existing post-build checks, verify that
  `dist/comp1/_internal/comp1/vision/models/fire_pose.onnx` exists.
- `requirements.txt` is a UTF-16 freeze and is not used by `start.ps1` (`pip install -e .[dev]`),
  so leave it alone.

### 8. Docs
- Save this plan as `docs/plans/2026-11-08-detector-switch-yolo.md`, per the standing preference.
- `CLAUDE.md` layout: add one line for `vision/yolo.py`, and `vision_detector` to the message list.

## Tests
- `tests/test_vision_yolo.py`. These need no onnxruntime: a fake session is injected into
  `YoloFinder`.
  - Letterbox un-mapping, score filtering and nearest-first ordering.
  - The keypoint → `radius_norm` → `distance_m` path matches `cam.distance_m` for a disc of
    known span. The fallback from vertical to horizontal to box.
  - The centre keypoint vs the box centre.
  - `TargetTracker` uses the YOLO finder when `cfg.detector == "yolo"`.
  - `available()` reports, and does not raise, when the model is missing.
  - A smoke test, `skipif` when onnxruntime or the model is absent: the real model loads, runs on
    a 960×720 frame, and returns a list.
- `tests/test_server*.py` (follow the existing WebSocket test pattern):
  - Connect sends `vision_detector`.
  - Switching broadcasts and persists through `settings_path`.
  - Switching is refused mid-run and while switching.
  - `yolo` is refused when unavailable (monkeypatch `available`).
  - `vision_auto` is refused under yolo.
- `tests/test_settings.py`: `_clean` keeps a valid `detector` and drops a bogus one.
- `tests/js/calibration.test.js`: the select reflects `vision_detector`, Tune colour is disabled
  under yolo and while running, and the AI option is disabled when unavailable.

## Verification
1. `python -m pytest -q`, `python -m ruff check .` and `node --test 'tests/js/**/*.test.js'`.
2. `python -m comp1`, then in the UI:
   - switch to AI model: Tune colour greys out and the video overlay shows `AI`;
   - switch back to Colour: Tune colour works again;
   - restart: the choice is remembered;
   - start a mission: the selector is locked.
3. Check that the model fires on the simulator's rendered discs. It was trained on real footage,
   so this is not guaranteed; if it misses, report it rather than hide it.
4. If the Tello is available, fly an approach in AI model mode on the real board.
5. `.\build.ps1`: the model is in the dist folder, and the packaged exe can switch to AI model.
