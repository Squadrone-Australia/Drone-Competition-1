# The AI marker detector's model

`fire_pose.onnx` is the red-circle ("fire") pose model trained in
`solution_fire/` (see `solution_fire/README.md` for the data, the training
and its evaluation). `comp1/vision/yolo.py` runs it with onnxruntime when the
detector is set to **AI model**.

| | |
| --- | --- |
| source weights | `solution_fire/models/fire_pose.pt` (YOLO26n-pose, 2026-11-12: retrained with the backlit drone-cage footage and the black-circle decoy) |
| input | `images`, 1×3×640×640, RGB, 0..1, letterboxed with grey (114) padding |
| output | `output0`, 1×20×8400: box `cx cy w h`, the `red_circle` score, then 5 keypoints × (x, y, conf), all in letterboxed pixels. Not end-to-end, so `yolo.py` runs NMS. |
| keypoints | 0 centre, 1 top, 2 right, 3 bottom, 4 left (left/right as the camera sees them) |
| score threshold | `VisionConfig.yolo_score_min`, 0.5. At 0.25 the model raised false alarms in held-out room footage. |
| CPU cost | about 40 ms per 960×720 frame on the development laptop |

## Re-exporting after retraining

```powershell
.venv\Scripts\python -m pip install onnx onnxslim   # once
.venv\Scripts\python -m solution_fire.export solution_fire\models\fire_pose.pt --format onnx
copy solution_fire\models\fire_pose.onnx comp1\vision\models\fire_pose.onnx
python -m pytest tests/test_vision_yolo.py -q
```

The export writes its `.onnx` next to the `.pt`. If a future export changes the
output layout (an end-to-end head, a different keypoint count), `decode()` in
`yolo.py` has to change with it. The smoke test in `tests/test_vision_yolo.py`
runs the real file and catches a shape it cannot read.

## Licence

Ultralytics YOLO weights and their derivatives are AGPL-3.0 unless covered by an
Ultralytics Enterprise licence. Check that before distributing the installer
outside the team.
