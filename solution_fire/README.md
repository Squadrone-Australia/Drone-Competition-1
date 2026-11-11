# solution_fire: a learned red-circle detector

A YOLO26-pose model that finds the ICONIP 2026 Search & Find **fire targets**
(red circles), stays silent on the decoys (green circle, green triangle,
black square, black triangle), and says where the target is:

```python
from solution_fire.detector import FireDetector

fire = FireDetector("solution_fire/models/fire_pose.pt", score_min=0.6)
for s in fire.detect(frame_bgr):             # highest score first
    print(s.score, s.range_m, s.bearing_deg, s.elevation_deg, s.yaw_deg)
```

The network only finds the disc and five keypoints. Range, bearing and the
board's turn come from the camera model in `detector.py`, so a better
calibration improves them without retraining. `detector.py` does not import
comp1: wiring it into the block interface is separate work.

## What the model predicts

One class, `red_circle`. Five keypoints, with left and right **as the camera
sees them**:

| #   | keypoint | #   | keypoint |
| --- | -------- | --- | -------- |
| 0   | centre   | 3   | bottom   |
| 1   | top      | 4   | left     |
| 2   | right    |     |          |

Range comes from the top–bottom span, which turning the board does not
shrink. If either point is out of shot it uses left–right, then the box.
The disc diameter defaults to 0.20 m (the printed test board). Pass
`diameter_m=` if the competition prints differ.

## Results (2026-11-08)

Two models trained for 30 epochs on 9,690 images (8,000 synthetic, the rest
real frames repeated 3×). Scored on held-out **sessions** never used for
training or as synthetic backgrounds, at score ≥ 0.5:

| | `fire_pose.pt` (yolo26n) | `fire_pose_s.pt` (yolo26s) |
| --- | --- | --- |
| real test: discs found | 110/110 | 110/110 |
| real test: empty frames alarmed (ring, stand, room) | 0/151 | 0/151 |
| real val: discs found / empty frames alarmed | 81/81 / 0/136 | 81/81 / 0/136 |
| synthetic test: discs found | 244/244 | 243/244 |
| synthetic decoy-only frames alarmed | 0/49 | 0/49 |
| range error, median / p90 (real test) | 2.5% / 4.5% | 2.2% / 4.0% |
| pose mAP50-95, real test | 0.995 | 0.995 |
| CPU ms per frame (this laptop) | **37** | 128 |

`fire_pose.pt` (nano) is the default: the same answers at 3.5× the speed.
At score 0.25 the nano model raised 2 false alarms in one room frame, so
keep `score_min` at 0.5 or above.

What these numbers do **not** show:

- The real footage is one board in one room. The four competition decoys
  have only ever been seen synthetically; film the printed decoys and run
  them through `autolabel --empty` and `evaluate` before trusting the 0/49.
- The hollow red ring is treated as *not* a target. If the competition's
  fire targets turn out to be outlines, this model will miss them.
- Range assumes a 0.20 m disc. Set `diameter_m` to the competition print.

## Workflow

```powershell
# 1. Label the footage. The marker under the disc labels every positive frame;
#    the per-clip estimate of the disc's size and offset is printed for a sanity
#    check against the ruler (0.20 m, 0.33 m above the marker).
.venv\Scripts\python -m solution_fire.autolabel solution_yolo\data\raw\redcircle --out solution_fire\data\real --calibration solution_yolo\runs\calibration\tello_camera.json
.venv\Scripts\python -m solution_fire.autolabel solution_yolo\data\raw\notredcircle solution_yolo\data\raw\empetystand solution_yolo\data\raw\randomenvironment --out solution_fire\data\real --empty --calibration solution_yolo\runs\calibration\tello_camera.json

# 2. Look, then review what could not be labelled automatically.
.venv\Scripts\python -m solution_fire.preview solution_fire\data\real --status pending --grid --count 64 --out solution_fire\runs\prev
.venv\Scripts\python -m solution_fire.review solution_fire\data\real          # GUI
.venv\Scripts\python -m solution_fire.review solution_fire\data\real --set rejected STEM ...   # or batch

# 3. Synthetic decoys and discs pasted into the reviewed footage. Exclude
#    the held-out sessions so none leak in as backgrounds.
$held = "redcircle_*_s2","redcircle_*_s4","notredcircle_*_s1","empetystand_*_s2","empetystand_*_s3","randomenvironment_*_s2","randomenvironment_*_s5"
.venv\Scripts\python -m solution_fire.synth --real solution_fire\data\real --out solution_fire\data\synth --count 8000 --calibration solution_yolo\runs\calibration\tello_camera.json --exclude-sessions $held

# 4. One dataset, split by session.
.venv\Scripts\python -m solution_fire.build_dataset --synth solution_fire\data\synth --real solution_fire\data\real --out solution_fire\data\fire --force --val-sessions "redcircle_*_s2" "empetystand_*_s2" "randomenvironment_*_s2" --test-sessions "redcircle_*_s4" "notredcircle_*_s1" "empetystand_*_s3" "randomenvironment_*_s5"

# 5. Train (an RTX 5060 needs the cu130 PyTorch build, see below).
.venv\Scripts\python -m solution_fire.train --data solution_fire\data\fire\data.yaml --model yolo26s-pose.pt --name fire_s

# 6. Score: recall, false alarms per decoy kind, alarm runs, range error, CPU speed.
.venv\Scripts\python -m solution_fire.evaluate --model solution_fire\runs\fire_s\weights\best.pt --data solution_fire\data\fire --map --speed --out solution_fire\runs\fire_s\eval.json
```

GPU training needs CUDA PyTorch in `.venv`. Pip treats the CPU build as
already satisfying the requirement, so force the reinstall:

```powershell
.venv\Scripts\python -m pip install --force-reinstall --no-deps torch torchvision --index-url https://download.pytorch.org/whl/cu130
```

## How the labels are made

- **Positives.** The test board has a 180 mm DICT_5X5_100 marker
  (id 0) under the disc. Its pose places the disc exactly, so the five
  keypoints and the box are projected, not guessed. A red-blob ellipse fit
  cross-checks each frame. Agreement (IoU ≥ 0.8) makes it `auto`; anything
  else goes to `pending` for review.
- **The marker is painted out** of every saved frame, because the
  competition boards have none. Where ArUco cannot see it (blur, the frame
  edge, a mirror), its position is predicted from the disc and painted
  anyway.
- **Reflections.** A wardrobe mirror in clip `005112` shows a second copy of
  the board. It looks exactly like a red disc, so it is labelled as one
  (`twin_discs`).
- **Negatives.** Frames go in as reviewed negatives:
  - the hollow red ring on the same board;
  - the empty stand;
  - the room.
- **Synthetic sheets** are printed shapes warped into the reviewed frames by
  a plane homography, with exact keypoints for the red discs:
  - the four competition decoys, filled or outlined;
  - red rings, squares and triangles;
  - blank sheets;
  - red discs at 0.45–5 m and up to 60° off-axis.
- **Sessions.** Each clip is cut into 8 s sessions with 0.5 s gaps at the
  cuts. Validation and test are whole sessions, so no held-out frame has a
  near-twin in training.

## Files

| file               | what it does                                                     | from `solution_yolo/` |
| ------------------ | ---------------------------------------------------------------- | --------------------- |
| `geometry.py`      | keypoints, flip index, camera model, disc projection, label I/O  | `keypoints.py`        |
| `marker.py`        | ArUco pose, disc ground truth, painting the marker out           | `marker.py`           |
| `autolabel.py`     | footage → labelled frames, `review.json`, sessions               | `real_label.py`       |
| `review.py`        | OpenCV review GUI, and batch verdicts                            | `review.py`           |
| `preview.py`       | labels drawn onto images, contact sheets                         | `preview.py`          |
| `synth.py`         | printed decoys and discs pasted into real frames                 | new                   |
| `build_dataset.py` | session-split YOLO dataset, `data.yaml` / `data_real.yaml`       | `build_dataset.py`    |
| `train.py`         | Ultralytics training, augmentation for red against green         | `train.py`            |
| `evaluate.py`      | recall, false alarms by decoy, alarm runs, range error, CPU ms   | new                   |
| `detector.py`      | `FireDetector`: detections in metres and degrees                 | new                   |
| `export.py`        | ONNX or TensorRT export                                          | `export.py`           |

None of these import `solution_yolo`, which needs packages from the earlier
competition. The tests are `tests/test_solution_fire.py`, and they need
neither torch nor a GPU.
