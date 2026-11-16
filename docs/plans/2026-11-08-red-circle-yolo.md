# Red-circle ("fire") YOLO pose model for the ICONIP Search & Find competition

## Context

The ICONIP 2026 Search & Find task: a Tello must find 3 red circular markers, approach each one and flip.
It must ignore 4 decoys: a green triangle, a black square, a green circle and a black triangle.
All markers have their centre at 2.0 m.
Each correct find scores +2 and each decoy flip costs −2, so false positives matter as much as recall.

We want a learned detector for the red circle.
`solution_yolo/` holds the YOLO26-pose ring-gate pipeline from an earlier competition.
It is untracked here and **cannot import in this repo**:
- it depends on `solution.vision` (CameraCalibration, GateDetector, ORANGE_HALF_ANGLE_DEG);
- `synth.py` and `record.py` also depend on `comp2`.

Neither package exists in this repo.

What exists now:
- **Footage:** Tello clips (960×720) in `solution_yolo/data/raw/`:

  | folder | frames | content | role |
  |---|---|---|---|
  | `redcircle/` (2 clips) | 1,159 + 1,151 | solid red disc on the board, marker below, on the red PVC stand | positives |
  | `notredcircle/` | 323 | the same board and marker, but a **hollow red ring outline** | hard negative |
  | `empetystand/` | 800 | the red PVC stand with no board; some frames have H.264 smearing | negative |
  | `randomenvironment/` | 1,184 | the room: floor, shelves, a dark window, blur | negative |

  The four competition decoys (green triangle, black square, green circle, black triangle) are **not filmed yet**. They stay synthetic.
- **Marker:** a 180 mm DICT_5X5_100 marker (id 0) sits on the same board.
  The disc is about 200 mm across, and its centre is about 330 mm above the marker's centre.
- **Camera calibration:** `solution_yolo/runs/calibration/tello_camera.json`.
- **Base weights:** `yolo26n-pose.pt` and `yolo26s-pose.pt` at the repo root.
- **Environment:** `.venv` has ultralytics 8.4.204, but torch 2.15 is a **CPU build**. The GPU is an RTX 5060 with 8 GB.

A probe of the footage showed that auto-labelling is viable:
- the ArUco marker was found in 221/232 and 219/231 sampled frames (side 33–249 px);
- an HSV mask found a round red blob in every sampled frame.

Decisions:
- **Model:** a pose model, one class `red_circle`, 5 keypoints.
- **Decoys:** synthetic decoys are pasted in as unlabelled negatives.
- **Measured geometry:** 200 mm diameter, centre 330 mm above the marker. These are refined from the footage.

## Approach

### Package layout

Create a new package, `solution_fire/`. Leave `solution_yolo/` untouched as the gate reference.
Copy and adapt the generic parts of `solution_yolo/`, because none of them import here.

| new file | reused from | change |
|---|---|---|
| `solution_fire/geometry.py` | `solution_yolo/keypoints.py` (write_labels/read_labels, visibility constants) | Holds the keypoint layout and a local `CameraCalibration` (the `tello_camera.json` fields, `matrix(w,h)`). Keypoints: 0 centre, 1 top, 2 right, 3 bottom, 4 left (right/left as the camera sees them). `FLIP_IDX=[0,1,4,3,2]`, `KPT_SHAPE=(5,3)`. Also `board_points()`: the 3D disc centre and rim points in marker coordinates. |
| `solution_fire/marker.py` | `solution_yolo/marker.py` | Near verbatim (`MarkerTruth`, `aruco_dictionary`, `ray_onto_marker_plane`, `ring()`), importing the local calibration. |
| `solution_fire/autolabel.py` | `solution_yolo/real_label.py` (`iter_frames`, `_thumb` de-dup, `review.json` manifest) | Labels from the marker; see below. |
| `solution_fire/review.py` | `solution_yolo/review.py` | Re-pointed at the 5-keypoint layout. |
| `solution_fire/preview.py` | `solution_yolo/preview.py` | Re-pointed at the 5-keypoint layout. |
| `solution_fire/synth.py` | new (the old synth needs the comp2 simulator) | 2D compositing; see below. |
| `solution_fire/build_dataset.py` | `solution_yolo/build_dataset.py` | Imports from `geometry`. Otherwise unchanged: session split, real repeat, `data.yaml` and `data_real.yaml`. |
| `solution_fire/train.py` | `solution_yolo/train.py` | Different defaults; see below. |
| `solution_fire/evaluate.py` | ideas from `solution_yolo/evaluate.py` | Smaller rewrite. |
| `solution_fire/detector.py` | new | `FireDetector` wrapper. |
| `solution_fire/export.py` | `solution_yolo/export.py` | Copied. |

### Auto-labelling (`autolabel.py`)

1. Keep every 3rd frame, then drop near-duplicates with `_thumb` from `real_label.py`.
2. Find the marker's pose with `MarkerTruth.pose`, using IPPE_SQUARE and the calibration JSON.
3. **Refine the board offset.** For each clip, take the median over frames of the HSV disc centroid cast onto the marker plane (`ray_onto_marker_plane`, as `estimate_offset` does). Do the same for the radius, using the contour's extremes.
   - Report the result against the 200 mm / 330 mm measurements.
   - Fall back to the measured values if the two disagree by more than 15%.
4. **Project the labels.** Project the disc's centre and its 4 rim points (top, right, bottom, left) through the marker pose.
   - The box is the projected ellipse (sampled at 64 points), clipped to the frame.
   - A keypoint outside the frame gets v=0.
5. **Cross-check against HSV.** Fit an ellipse to the HSV contour nearest the projected centre, and compute its IoU with the projected ellipse.
   - IoU ≥ 0.8: status `auto`.
   - Otherwise, or with no marker: status `pending`, drafted from the HSV ellipse when one exists.
   - The red PVC stand, the bottle and the checker ring in the background are rejected because they sit far from the projected centre.
6. **Hide the marker.** Paint the ArUco quad out of every saved training image: dilate it about 15%, fill it with the median paper colour around it, add mild noise and a blur.
   - This stops the model learning "red disc above a marker", which the competition boards will not have.
   - Raw frames are kept, so evaluation can still recover ground truth from the marker.
7. **Negative clips.** Run with `--empty`, which reuses `real_label`'s idea: the frames go straight in as reviewed negatives with empty label files, and the frames the drafter would have flagged are counted.
   - The probe found only 2–3 HSV "round red blobs" per negative clip, so this is a real false-positive check.
   - `notredcircle` frames get the same marker painting as the positives. The marker then appears in neither class, so it cannot become a cue.
   - Corrupted (smeared) frames in `empetystand` are kept, because flight produces them too.
8. **Split the clips into sessions.** Each clip becomes about 6 sessions of 8 s each (`<clip>_s0..s5`).
   - Frames within 1 s of a chunk boundary are dropped, so `build_dataset` can hold out val/test chunks from both clips without near-duplicate leakage.
   - Two clips alone would otherwise mean training on only one of them.

### Synthetic composites (`synth.py`)

Render a printed sheet: white paper with one shape on it. Warp it onto backgrounds by a random homography.
- **Viewing range:** 0.4–5 m, ±60° yaw, small pitch and roll, using the Tello intrinsics.
- **Positives:** red discs with hue, saturation and brightness jittered around the measured print colour. Their 5 keypoints and box come exactly from the warp.
- **Unlabelled negatives:**
  - the four decoys: green circle, green triangle, black square, black triangle;
  - hard negatives: red square, red triangle, orange and pink discs, and red ring outlines (the real hollow ring is filmed; synthetic ones add varied sizes and line widths).
- **Backgrounds:** mostly frames from `randomenvironment` and `empetystand`, which contain no target, so composites never hide an unlabelled disc. Some composites go onto `redcircle` frames, with the real disc kept and labelled from `autolabel`.
- **Mix:** about 8,000 images, roughly 40% containing a positive. About 10% are decoy-only test images with a `meta` JSON, used to measure the false-positive rate.
- **Image effects:** motion blur, JPEG re-encoding and auto-exposure-like gain.

### Training (`train.py`)

1. **Install CUDA PyTorch** into `.venv`: `pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130` (Blackwell needs cu128 or newer). Verify that `torch.cuda.is_available()` is true.
2. **Train** `yolo26s-pose.pt` with `--model`; the head re-initialises for 5 keypoints. Settings: imgsz 640, 120 epochs, patience 30, `hsv_h=0.005`.
   - Keep hue jitter tight, because red versus green is the decision.
   - Keep `fliplr=0.5` with `FLIP_IDX` and `flipud=0`.
   - Keep `degrees=5` and `scale=0.5`.
3. **Train** `yolo26n-pose.pt` with the same settings. It runs better on a laptop CPU during the competition.
4. **Pick** between the two on the real-validation metrics below.
5. **Round two.** Draft the `pending` frames with the stage-1 model (`autolabel --model best.pt`), review them, rebuild the dataset and fine-tune at `--lr0 0.002`.

### Detector (`detector.py`)

`FireDetector(weights, calibration, score_min)`. Its `detect(frame)` returns sightings sorted by score. Each sighting has:
- the centre in pixels;
- `bearing_deg` and `elevation_deg`;
- `range_m`, from the top–bottom keypoint span against 0.20 m and fy (vertical span is unaffected by yaw), falling back to the box height;
- an unsigned yaw estimate, from the width/height ratio;
- the score.

It does not import comp1. Wiring it into `comp1/vision` or the block interface is a follow-up, not part of this plan.

### Repo housekeeping

- Add `solution_fire/data/`, `solution_fire/runs/` and `solution_fire/**/*.pt|onnx|engine` to `.gitignore`.
- Save this plan as `docs/plans/2026-11-08-red-circle-yolo.md`.
- Write a `solution_fire/README.md` with the workflow commands.

## Workflow (commands once built)

```bash
.venv/Scripts/python -m solution_fire.autolabel solution_yolo/data/raw/redcircle --out solution_fire/data/real --calibration solution_yolo/runs/calibration/tello_camera.json
.venv/Scripts/python -m solution_fire.autolabel solution_yolo/data/raw/notredcircle solution_yolo/data/raw/empetystand solution_yolo/data/raw/randomenvironment --out solution_fire/data/real --empty --calibration solution_yolo/runs/calibration/tello_camera.json
.venv/Scripts/python -m solution_fire.preview solution_fire/data/real --out solution_fire/runs/preview_real   # eyeball
.venv/Scripts/python -m solution_fire.review solution_fire/data/real                                          # pending only
.venv/Scripts/python -m solution_fire.synth --count 8000 --backgrounds solution_yolo/data/raw/redcircle --out solution_fire/data/synth
.venv/Scripts/python -m solution_fire.build_dataset --synth solution_fire/data/synth --real solution_fire/data/real --out solution_fire/data/fire --force --val-sessions "*_s2" --test-sessions "*_s4"
.venv/Scripts/python -m solution_fire.train --data solution_fire/data/fire/data.yaml --model yolo26s-pose.pt --name fire_s
.venv/Scripts/python -m solution_fire.evaluate --model solution_fire/runs/fire_s/weights/best.pt --data solution_fire/data/fire --clips solution_yolo/data/raw/redcircle
```

## Verification

- **Unit tests:** `tests/test_solution_fire.py`, which need no torch or GPU, following `tests/test_solution_yolo.py`'s pattern. They cover:
  - a label write/read round trip;
  - `FLIP_IDX` mapping left↔right;
  - board projection: a synthetic marker pose gives the expected centre and rim pixels;
  - marker painting removes ArUco detection;
  - `FireDetector` range maths on a projected disc at a known distance;
  - synth homography keypoints landing on the warped disc.

  Run them with `python -m pytest tests/test_solution_fire.py -q`, then `python -m ruff check .`.
- **Label quality:** check the auto-label rate (expected ≥ 90% of kept frames) and the per-clip offset/radius estimate against 200 mm / 330 mm, then look at the preview images.
- **Model metrics,** from `evaluate.py`:
  - pose and box mAP50-95 on `val_real` and `test_real`;
  - per-clip detection rate (target ≥ 98% where the disc is fully in shot);
  - range error against marker-derived truth (median target < 10%);
  - the **false-positive rate on the held-out real negative chunks**: hollow ring, empty stand and room. Target: 0 frames above the chosen score threshold. Also count the run of consecutive frames that would trigger a flip, since one stray frame is harmless if the mission needs N in a row.
  - the false-positive rate on the decoy-only synthetic images;
  - CPU inference fps for n and s.
- **Report back:** the chosen model and its metrics, and a recommendation to film the printed competition decoys (green triangle, black square, green circle, black triangle). The model has never seen those decoys for real, so that false-positive number is only a synthetic estimate.
