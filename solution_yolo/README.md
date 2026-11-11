# solution_yolo: a learned ring detector

A YOLO26-pose model that finds the Squadrone ring gate and measures its
range, as a drop-in replacement for the colour detector in `solution/`.

The network learns only the hard part: where the ring and its corners are in
the image. Range, offsets and the gate's yaw still come from the same PnP fit
the colour detector uses (`GateDetector._pose`). So every circuit, controller
and test tool runs unchanged:

```powershell
venv\Scripts\python -m solution.run --circuit 1 --detector yolo --yolo-weights solution_yolo\models\gate_pose.pt --calibration solution_yolo\models\tello_camera.json
```

`--detector yolo --yolo-weights PATH` works on `solution.run`,
`single_gate_test`, `gate_search_test`, `gate_pilot` and `inspect_video`.
Without the flag, nothing changes.

## The released model

`models/` holds what the solution needs to run, and nothing else from training:

| file                       | what it is                                                                             |
| -------------------------- | -------------------------------------------------------------------------------------- |
| `gate_pose.pt`             | YOLO26s-pose, 30k simulator renders then a fine-tune on reviewed Tello footage (21 MB) |
| `tello_camera.json`        | the Tello's measured intrinsics (ChArUco, fx 940 px), for `--calibration`              |
| `tello_camera.report.json` | that calibration's fit and how well the focal length is pinned                         |

On another machine, clone, install (Setup below; CPU-only is fine) and run the
command above. Add `--yolo-device cpu` to force the CPU. For speed without a
GPU, `python -m solution_yolo.export solution_yolo\models\gate_pose.pt --format onnx` and pass the `.onnx` to `--yolo-weights`. A TensorRT `.engine` only works
on the GPU it was built on. Datasets, training runs and other weights stay
local (gitignored).

## What the model predicts

One class, `gate`. Each detection has a box and nine keypoints. Left and right
are **as the camera sees the ring**, even from behind:

| #   | keypoint                        | #   | keypoint                           |
| --- | ------------------------------- | --- | ---------------------------------- |
| 0   | top segment, outer-left corner  | 4   | bottom segment, inner-left corner  |
| 1   | top segment, outer-right corner | 5   | bottom segment, inner-right corner |
| 2   | top segment, inner-right corner | 6   | bottom segment, outer-right corner |
| 3   | top segment, inner-left corner  | 7   | bottom segment, outer-left corner  |
|     |                                 | 8   | ring centre                        |

A corner is where an orange segment's straight end meets the checker, on the
band's outer or inner edge. These are exactly the corners `solution/vision.py`
fits, at 卤40掳 from vertical. With four or more corners in shot,
`YoloGateDetector.detect()` returns the usual `GateSighting`: right, up and
forward in metres, and the gate normal's yaw. With fewer, `detect()` returns
None, but `search_box()` and `search_bearing()` still point the drone at the
ring.

`YoloGateDetector(..., box_range=True)` also measures range from the box's
size when fewer than four corners are visible. It is off by default, because
that sighting cannot measure yaw and reports 0, which the controller would
read as a square-on gate.

## Setup (once)

The detector and the trainer need PyTorch and Ultralytics on top of the
solution's own requirements. Install PyTorch from its own index first, or pip
picks a CPU-only build. An RTX 50-series card needs CUDA 12.8 or newer:

```powershell
venv\Scripts\python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu130
venv\Scripts\python -m pip install -r solution_yolo\requirements.txt
venv\Scripts\python -c "import torch; print(torch.cuda.is_available())"
```

## Workflow

```powershell
# 1. Synthetic frames with exact labels and ground-truth range. ~16 frames/s
#    on 10 processes, so 30k takes about half an hour.
venv\Scripts\python -m solution_yolo.synth --count 30000 --out solution_yolo\data\synth

# 2a. Record real footage: a Tk window that connects to the Tello, flies it
#     by keyboard and saves MP4 clips (or JPG frames) per session. Keys:
#     T take off, L land, WASD move, R/F up/down, Q/E turn, Space hover,
#     Enter record on/off, P still, Esc EMERGENCY. --drone sim to try it.
venv\Scripts\python -m solution_yolo.record --session trial1

# 2. Real footage: draft labels, then review them by hand. A folder's videos
#    are each a session, and their sidecar JSON notes are kept. Draft with the
#    model once there is one (--max-gates 1 when only one gate was filmed, so
#    clutter it mistakes for a ring stays out); the colour detector otherwise.
venv\Scripts\python -m solution_yolo.real_label solution_yolo\data\raw\session_20261106 --out solution_yolo\data\real_20261106 --every 3 --model best.pt --max-gates 1
venv\Scripts\python -m solution_yolo.real_label trial1 --out solution_yolo\data\real --vision-config vision_profile_trial1.json
venv\Scripts\python -m solution_yolo.review solution_yolo\data\real
#    Footage with no gate in it goes straight in as reviewed negatives:
venv\Scripts\python -m solution_yolo.real_label empty_room.mp4 --out solution_yolo\data\real --empty

# 3. One dataset. Real sessions stay on one side of the split; name the
#    held-out clips to balance lighting and clip type (globs on session names).
venv\Scripts\python -m solution_yolo.build_dataset --synth solution_yolo\data\synth --real solution_yolo\data\real --real solution_yolo\data\real_20261106 --out solution_yolo\data\gates --force --val-sessions *132023 *132459 *132826 --test-sessions *132307 *132613 *132902

# 4. Train. About 8 GB of GPU memory takes yolo26s at imgsz 640.
venv\Scripts\python -m solution_yolo.train --data solution_yolo\data\gates\data.yaml --name gates

# 5. Score: detection rate and range error, against the colour detector.
venv\Scripts\python -m solution_yolo.evaluate --model solution_yolo\runs\gates\weights\best.pt --data solution_yolo\data\gates --compare-colour --map
#    Recordings: per-clip measured rate and median range. A sidecar with
#    "dist" (cm) or "distance_m" also scores range error against the tape;
#    without one, clips noted closest..furthest must measure in that order.
venv\Scripts\python -m solution_yolo.evaluate --model solution_yolo\runs\gates\weights\best.pt --sessions solution_yolo\data\raw\session_20261106 --compare-colour
```

### Calibration and ground truth

```powershell
# The camera, from ChArUco footage: a CameraCalibration JSON for every
# --calibration flag. Film the board turned 30-50 degrees, not only square-on:
# the report's fx_px_resampled_10_90 shows how well the focal length is pinned.
venv\Scripts\python -m solution_yolo.calibrate solution_yolo\data\raw\calib_25_20mm_DICT_5x5_100 --squares 7 7 --square-mm 25 --marker-mm 20 --dict DICT_5X5_100 --out solution_yolo\runs\calibration\tello_camera.json

# Range, offset, height and yaw against an ArUco marker in the ring's plane,
# every frame it is seen. The ring centre's offset from the marker is
# estimated from the footage (or give --ring-offset X Y in metres).
venv\Scripts\python -m solution_yolo.evaluate --model best.pt --aruco solution_yolo\data\raw\aruco_180mm_dict_5x5_100 --marker-mm 180 --calibration solution_yolo\runs\calibration\tello_camera.json --compare-colour
```

The marker checks the keypoints, the PnP fit and the ring's size, but not the
focal length: its range scales with the focal length just as the ring's does.
Only the marker's position is used for height; its yaw is used as the ring's,
so mount it flat on the ring's own plane.

To check any labelled folder by eye, run
`python -m solution_yolo.preview <folder> --out preview`. It draws every box
and keypoint, with the true range on synthetic frames.

To make a lighter or faster model, run
`python -m solution_yolo.export best.pt --format onnx` (any CPU) or
`--format engine` (TensorRT, this GPU only).

### How the synthetic data varies

- **Venue:** every combination of the simulator's floors, netting, halls,
  crowds, external lights, camera artefacts and gate finishes.
- **Gate layout:** the three official tracks, plus layouts with the gates
  moved, raised and turned.
- **Views:**
    - approach cones, 0.35 to 3.6 m out and up to 40掳 off-axis;
    - close, wall-limited standoffs where the ring overflows the frame;
    - the ring's back face;
    - random views, including frames with no gate at all.
- **Camera:** small roll and pitch, and motion blur.
- **Stand:** a third of the frames paint the **red PVC rig** of the physical
  gates (posts through the opening, a T-bar inside) over the nearest gate,
  because a colour mask confuses it with the fabric.
- **Reflections:** floor reflections are drawn but never labelled.

Each frame's `meta/*.json` holds the true camera-frame offset and yaw of every
gate. `evaluate` scores range against it.

## What to film

Simulated frames teach the geometry, but only real frames teach the real
camera, fabric and lighting. Use the **Tello's own camera**. A phone has
different optics and colour, so use one only if there is no other way.
Recording with `gate_search_test --save-frames DIR`, or saving the raw stream
to video, both work: `real_label` takes folders and videos.

1. **Camera calibration.** This matters most for range, because PnP range
   scales directly with the focal length. Take 20 to 30 Tello frames of a
   printed checkerboard (e.g. 9脳6 inner corners, 25 mm squares, flat on a
   board). Fill the frame from many angles, including the corners of the
   image. These give a real `--calibration` JSON.
2. **Tape-measured static clips.** Stand the ring at its competition centre
   heights (1.0, 1.5 and 2.0 m). Hover or hand-hold the drone at 0.5, 0.75,
   1.0, 1.5, 2.0, 2.5 and 3.0 m from the ring plane, measured with tape. At
   each distance, film about 3 s each:
    - centred;
    - 0.4 m left and 0.4 m right;
    - turned about 30掳 each way.

    Name the files and list them in `clips.csv`
    (`file,distance_m,lateral_m,height_m,yaw_deg`). `evaluate --clips` then
    scores real range error.

3. **Flight clips.** 10 to 20 real approaches through the ring, including
   G1/G3-like approaches 1 m from a wall, where the ring overflows the frame.
4. **Hard cases:**
    - backlight and doorway glare;
    - the ring's back face;
    - the ring cut off at each frame edge;
    - fast motion blur;
    - two rings in view at once;
    - the red stand from every side;
    - a shiny floor's reflection.

5. **Negatives.** About 2 minutes of the room or venue with no ring, plus
   orange and red clutter (cones, clothing, red PVC offcuts).
6. **The venue.** If at all possible, a few minutes in the real ICONIP cage.
   Those frames are worth more than any others.

Aim for about **1,500 to 3,000 reviewed real frames** after de-duplication.
`real_label` keeps every Nth frame that differs from the last one, and drafts
most labels. `review` is then mostly pressing `a`.

## Files

| file               | what it does                                                 |
| ------------------ | ------------------------------------------------------------ |
| `keypoints.py`     | the keypoint model, flip index, YOLO label read/write        |
| `synth.py`         | simulator renders with exact labels and ground truth         |
| `real_label.py`    | real frames and video, with drafted labels and `review.json` |
| `review.py`        | OpenCV window to accept, fix, place or reject labels         |
| `preview.py`       | draws labels onto images for a visual check                  |
| `build_dataset.py` | merges sources into `data.yaml` and `data_real.yaml`         |
| `train.py`         | Ultralytics training with augmentation suited to the ring    |
| `evaluate.py`      | detection rate, range, lateral and yaw error, and mAP        |
| `export.py`        | ONNX or TensorRT export                                      |
| `calibrate.py`     | camera calibration from ChArUco footage                      |
| `marker.py`        | ring ground truth from an ArUco marker in its plane          |
| `detector.py`      | `YoloGateDetector`, the drop-in for `GateDetector`           |

`detector.py` imports no `comp2`, so it runs on the contestant side. Only
`synth.py` needs the simulator. Datasets, runs and weights are gitignored, so
share weights by file. The tests are `tests/test_solution_yolo.py`, and they
need neither torch nor a GPU.
