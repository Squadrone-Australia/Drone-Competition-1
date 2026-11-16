"""Find red-circle fire targets in a frame, and say where they are.

    from solution_fire.detector import FireDetector

    fire = FireDetector("solution_fire/models/fire_pose.pt")
    for s in fire.detect(frame_bgr):           # best first
        print(s.score, s.range_m, s.bearing_deg, s.elevation_deg)

The network finds the disc and its five keypoints; everything metric comes
from the camera model here, so a better calibration improves range without
retraining.

Range uses the **vertical** diameter (top and bottom keypoints): the angle it
subtends is the disc's diameter at that range, and turning the board about
its vertical axis does not shrink it. With either keypoint missing (the disc
cut off by the frame edge), the horizontal pair is used (an overestimate when
the board is turned), then the box. ``yaw_deg`` is the board's turn from the
width-to-height ratio; its sign cannot be told from one frame.

This module imports neither comp1 nor anything torch-specific beyond
Ultralytics, and only on construction: ``range_from_keypoints`` and
``Sighting`` are plain numpy, so the tests and the mission code can use them
without a GPU.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from solution_fire.geometry import (
    BOTTOM,
    CENTRE,
    DISC_DIAMETER_M,
    LEFT,
    NUM_KPTS,
    RIGHT,
    TOP,
    VISIBLE,
    CameraCalibration,
)


@dataclass
class Sighting:
    score: float
    box: tuple  # x0, y0, x1, y1 pixels
    keypoints: np.ndarray  # (5, 3): x, y, confidence
    centre_px: tuple
    bearing_deg: float  # + right of the nose
    elevation_deg: float  # + above the camera axis
    range_m: float | None
    range_from: str  # "vertical", "horizontal", "box" or "none"
    yaw_deg: float | None  # unsigned board turn, 0 = square-on
    position_m: tuple | None = field(default=None)  # right, up, forward

    def keypoints_labelled(self, keypoint_min: float = 0.5) -> np.ndarray:
        """The keypoints as a training label: confident ones visible, the
        rest unlabelled."""
        k = np.zeros((NUM_KPTS, 3))
        good = self.keypoints[:, 2] >= keypoint_min
        k[good, :2] = self.keypoints[good, :2]
        k[good, 2] = VISIBLE
        return k


def _angle(ray_a, ray_b) -> float:
    cos = float(ray_a @ ray_b) / (np.linalg.norm(ray_a) * np.linalg.norm(ray_b))
    return math.acos(max(-1.0, min(1.0, cos)))


def range_from_keypoints(
    keypoints: np.ndarray,
    box,
    calibration: CameraCalibration,
    width: int,
    height: int,
    diameter_m: float = DISC_DIAMETER_M,
    keypoint_min: float = 0.5,
):
    """(range to the disc centre in metres, which measurement, unsigned yaw in
    degrees or None). The chord a diameter subtends at angle a is seen from
    range d where d = (D / 2) / tan(a / 2)."""
    k = np.asarray(keypoints, float).reshape(NUM_KPTS, 3)
    ok = k[:, 2] >= keypoint_min
    inside = (k[:, 0] >= 0) & (k[:, 0] < width) & (k[:, 1] >= 0) & (k[:, 1] < height)
    ok &= inside
    rays = calibration.rays(k[:, :2], width, height)

    def chord(a, b):
        angle = _angle(rays[a], rays[b])
        return None if angle <= 1e-4 else (diameter_m / 2) / math.tan(angle / 2)

    vertical = chord(TOP, BOTTOM) if ok[TOP] and ok[BOTTOM] else None
    horizontal = chord(LEFT, RIGHT) if ok[LEFT] and ok[RIGHT] else None
    yaw = None
    if vertical and horizontal:
        # The horizontal chord looks smaller (longer range) by cos(yaw).
        yaw = math.degrees(math.acos(max(0.0, min(1.0, vertical / horizontal))))
    if vertical:
        return vertical, "vertical", yaw
    if horizontal:
        return horizontal, "horizontal", yaw
    x0, y0, x1, y1 = box
    edge = 2
    if y0 > edge and y1 < height - edge:
        cx = (x0 + x1) / 2
        r = calibration.rays([(cx, y0), (cx, y1)], width, height)
        angle = _angle(r[0], r[1])
        if angle > 1e-4:
            return (diameter_m / 2) / math.tan(angle / 2), "box", None
    return None, "none", None


def describe(score, box, keypoints, calibration, width, height, diameter_m,
             keypoint_min=0.5) -> Sighting:
    """A Sighting from one network detection (pixels in, metres out)."""
    k = np.asarray(keypoints, float).reshape(NUM_KPTS, 3)
    if k[CENTRE, 2] >= keypoint_min:
        centre = (float(k[CENTRE, 0]), float(k[CENTRE, 1]))
    else:
        centre = ((box[0] + box[2]) / 2, (box[1] + box[3]) / 2)
    ray = calibration.rays([centre], width, height)[0]
    bearing = math.degrees(math.atan2(ray[0], ray[2]))
    elevation = math.degrees(math.atan2(-ray[1], math.hypot(ray[0], ray[2])))
    range_m, how, yaw = range_from_keypoints(k, box, calibration, width, height, diameter_m,
                                             keypoint_min)
    position = None
    if range_m is not None:
        unit = ray / np.linalg.norm(ray)
        position = (float(unit[0] * range_m), float(-unit[1] * range_m), float(unit[2] * range_m))
    return Sighting(float(score), tuple(float(v) for v in box), k, centre, bearing, elevation,
                    range_m, how, yaw, position)


class FireDetector:
    """The trained pose model, wrapped to answer in metres and degrees."""

    def __init__(
        self,
        weights,
        calibration: CameraCalibration | None = None,
        score_min: float = 0.5,
        diameter_m: float = DISC_DIAMETER_M,
        imgsz: int = 640,
        device: str | None = None,
        keypoint_min: float = 0.5,
    ):
        from ultralytics import YOLO

        self.model = YOLO(str(Path(weights)), task="pose")
        self.calibration = calibration or CameraCalibration()
        self.score_min = score_min
        self.diameter_m = diameter_m
        self.imgsz = imgsz
        self.device = device
        self.keypoint_min = keypoint_min

    def detect(self, frame: np.ndarray) -> list[Sighting]:
        """Every red circle above ``score_min``, highest score first."""
        h, w = frame.shape[:2]
        result = self.model.predict(frame, imgsz=self.imgsz, conf=self.score_min,
                                    device=self.device, verbose=False)[0]
        if result.boxes is None or len(result.boxes) == 0:
            return []
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        kpts = result.keypoints.data.cpu().numpy() if result.keypoints is not None else None
        out = []
        for i in np.argsort(-scores):
            k = kpts[i] if kpts is not None else np.zeros((NUM_KPTS, 3))
            if k.shape[-1] == 2:
                k = np.column_stack((k, np.ones(len(k))))
            out.append(describe(scores[i], boxes[i], k, self.calibration, w, h,
                                self.diameter_m, self.keypoint_min))
        return out
