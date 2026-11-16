"""A YOLO26-pose ring detector with the same surface as ``GateDetector``.

The network finds the ring and its eight sector corners; the pose, and so the
range, comes from exactly the PnP fit :class:`solution.vision.GateDetector`
uses, gravity prior and outlier rejection included. Only the corner finding is
learned: the geometry, and everything the controller reads, is unchanged.

    from solution_yolo.detector import YoloGateDetector
    detector = YoloGateDetector("best.pt")
    sighting = detector.detect(frame)   # GateSighting | None

``RaceController(..., detector=detector)`` and the ``--detector yolo`` flag on
the solution commands use it in place of the colour detector.

This module imports no ``comp2``: it ships with the contestant's own code.
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, replace
from pathlib import Path

import cv2
import numpy as np
from solution.vision import (
    CameraCalibration,
    DetectionTrace,
    GateDetector,
    GateSighting,
    VisionConfig,
)

from solution_yolo.keypoints import CENTRE, CORNERS, NUM_KPTS

# Learned corners are good to a pixel or two, not the sub-pixel of an edge
# fit, so the pose fit's tolerance (a share of the ring's apparent size) is
# looser than the colour detector's.
YOLO_MODEL_SLACK = 0.06


@dataclass(frozen=True)
class RingDetection:
    """One ring as the network reports it, in sensor pixels."""

    box: tuple[float, float, float, float]  # x0, y0, x1, y1
    score: float
    keypoints: np.ndarray  # (9, 3): x, y, confidence


class UltralyticsModel:
    """Adapts an Ultralytics YOLO pose model (.pt, .onnx, .engine) to a
    callable returning :class:`RingDetection` lists."""

    def __init__(
        self,
        weights: str | Path,
        *,
        imgsz: int = 640,
        conf: float = 0.25,
        device: str | int | None = None,
    ):
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise SystemExit(
                "The YOLO detector needs ultralytics: "
                "python -m pip install -r solution_yolo/requirements.txt"
            ) from exc
        if not Path(weights).exists():
            raise SystemExit(f"No YOLO weights at {weights}")
        self.model = YOLO(str(weights), task="pose")
        self.imgsz, self.conf, self.device = imgsz, conf, device

    def __call__(self, frame: np.ndarray) -> list[RingDetection]:
        result = self.model.predict(
            frame, imgsz=self.imgsz, conf=self.conf, device=self.device, verbose=False
        )[0]
        if result.boxes is None or len(result.boxes) == 0:
            return []
        boxes = result.boxes.xyxy.cpu().numpy()
        scores = result.boxes.conf.cpu().numpy()
        if result.keypoints is None:
            return []
        kpts = result.keypoints.data.cpu().numpy()
        if kpts.shape[-1] == 2:  # a model trained without visibility
            kpts = np.concatenate([kpts, np.ones(kpts.shape[:-1] + (1,))], axis=-1)
        return [
            RingDetection(tuple(map(float, b)), float(s), k.astype(np.float64))
            for b, s, k in zip(boxes, scores, kpts, strict=True)
        ]


class YoloGateDetector(GateDetector):
    """``GateDetector``'s interface, with the network finding the corners.

    ``model`` is any callable from a BGR frame to a list of
    :class:`RingDetection`; by default the Ultralytics weights at ``weights``.
    ``box_range`` lets :meth:`detect` fall back to a range from the box's size
    when fewer than four corners are in shot. That sighting has no measured
    yaw (it reports 0), so it is off by default: the controller would take it
    for a square-on gate.
    """

    def __init__(
        self,
        weights: str | Path | None = None,
        diameter_m: float = 0.70,
        opening_m: float = 0.50,
        calibration: CameraCalibration | None = None,
        config: VisionConfig | None = None,
        *,
        model=None,
        score_min: float = 0.35,
        keypoint_min: float = 0.5,
        imgsz: int = 640,
        device: str | int | None = None,
        box_range: bool = False,
    ):
        config = config or replace(VisionConfig(), model_slack=YOLO_MODEL_SLACK)
        super().__init__(diameter_m, opening_m, calibration, config)
        if model is None:
            if weights is None:
                raise ValueError("Pass YOLO weights or a model callable")
            model = UltralyticsModel(
                weights, imgsz=imgsz, conf=min(score_min, 0.25), device=device
            )
        self.model = model
        self.score_min = score_min
        self.keypoint_min = keypoint_min
        self.box_range = box_range
        self._cache_key = None
        self._cache: list[RingDetection] = []

    # --- inference ------------------------------------------------------------

    def rings(self, frame: np.ndarray) -> list[RingDetection]:
        """The network's rings above ``score_min``, best first. The last
        frame's answer is kept, so detect() then search_box() on one frame
        runs the network once."""
        key = (
            frame.shape,
            hashlib.blake2b(frame[::7, ::7].tobytes(), digest_size=16).digest(),
        )
        if key != self._cache_key:
            found = [r for r in self.model(frame) if r.score >= self.score_min]
            self._cache = sorted(found, key=lambda r: -r.score)
            self._cache_key = key
        return self._cache

    # --- GateDetector surface -----------------------------------------------

    def masks(self, frame: np.ndarray) -> list[np.ndarray]:
        """Kept for tools that show the colour detector's masks: none here."""
        return []

    def detect(
        self, frame: np.ndarray | None, trace: DetectionTrace | None = None
    ) -> GateSighting | None:
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            return None
        rows, cols = frame.shape[:2]
        if trace is not None:
            trace.frame = frame
        sightings = [s for r in self.rings(frame) if (s := self.measure(r, cols, rows))]
        sightings = self._drop_reflections(sightings)
        sightings.sort(key=lambda s: s.forward_m)
        ambiguous = (
            len(sightings) > 1 and sightings[1].forward_m < sightings[0].forward_m * 1.5
        )
        if trace is not None:
            trace.candidates = len(sightings)
            trace.ambiguous = ambiguous
        if not sightings or ambiguous:
            return None
        return sightings[0]

    def search_box(self, frame: np.ndarray | None) -> tuple[int, int, int, int] | None:
        """The best ring's box, clipped to the frame; partial rings included."""
        if frame is None or frame.ndim != 3 or frame.shape[2] != 3 or not frame.size:
            return None
        rings = self.rings(frame)
        if not rings:
            return None
        rows, cols = frame.shape[:2]
        x0, y0, x1, y1 = rings[0].box
        x0, x1 = (int(round(min(max(v, 0), cols))) for v in (x0, x1))
        y0, y1 = (int(round(min(max(v, 0), rows))) for v in (y0, y1))
        if x1 <= x0 or y1 <= y0:
            return None
        return (x0, y0, x1 - x0, y1 - y0)

    # --- geometry ---------------------------------------------------------------

    def _segments_in_shot_are_orange(self, *args) -> bool:
        # The colour detector checks a segment fitted without its corners is
        # out of shot rather than missing. Here the network has already seen
        # the ring and said those corners are not visible.
        return True

    def _rectify(self, points: np.ndarray, cols: int, rows: int) -> np.ndarray:
        if not any(self.calibration.distortion):
            return points
        matrix = self.calibration.matrix(cols, rows)
        return cv2.undistortPoints(
            points.reshape(-1, 1, 2),
            matrix,
            np.array(self.calibration.distortion),
            P=matrix,
        ).reshape(-1, 2)

    def _redistort(self, points: np.ndarray, cols: int, rows: int) -> np.ndarray:
        if not any(self.calibration.distortion):
            return points
        matrix = self.calibration.matrix(cols, rows)
        rays = cv2.undistortPoints(points.reshape(-1, 1, 2), matrix, None)
        rays = rays.reshape(-1, 2)
        out, _ = cv2.projectPoints(
            np.column_stack((rays, np.ones(len(rays)))),
            np.zeros(3),
            np.zeros(3),
            matrix,
            np.array(self.calibration.distortion),
        )
        return out.reshape(-1, 2)

    def measure(self, ring: RingDetection, cols: int, rows: int) -> GateSighting | None:
        """One ring's pose from its confident in-shot corners, or None."""
        k = np.asarray(ring.keypoints, float).reshape(NUM_KPTS, 3)
        corners = k[:CORNERS, :2]
        inside = (
            (corners[:, 0] >= 0)
            & (corners[:, 0] < cols)
            & (corners[:, 1] >= 0)
            & (corners[:, 1] < rows)
        )
        known = (k[:CORNERS, 2] >= self.keypoint_min) & inside
        if known.sum() >= 4:
            rectified = self._rectify(corners, cols, rows)
            sighting = self._pose(rectified, cols, rows, rectified=True, known=known)
            if sighting is not None:
                return replace(
                    sighting,
                    corners=self._redistort(sighting.corners, cols, rows),
                    outline=self._redistort(sighting.outline, cols, rows),
                )
        if self.box_range:
            return self.range_from_box(ring, cols, rows)
        return None

    def range_from_box(
        self, ring: RingDetection, cols: int, rows: int
    ) -> GateSighting | None:
        """Range from the ring's apparent size alone: its height, which a
        turned ring keeps, or its width when the top or bottom is cut off.
        Yaw is unmeasured and reported as 0."""
        x0, y0, x1, y1 = ring.box
        margin = 2.0
        cut_v = y0 <= margin or y1 >= rows - margin
        cut_h = x0 <= margin or x1 >= cols - margin
        matrix = self.calibration.matrix(cols, rows)
        fx, fy, cx, cy = matrix[0, 0], matrix[1, 1], matrix[0, 2], matrix[1, 2]
        if not cut_v:
            forward = fy * self.diameter_m / max(1.0, y1 - y0)
        elif not cut_h:
            forward = fx * self.diameter_m / max(1.0, x1 - x0)
        else:
            return None
        k = np.asarray(ring.keypoints, float).reshape(NUM_KPTS, 3)
        if k[CENTRE, 2] >= self.keypoint_min:
            u, v = k[CENTRE, :2]
        else:
            u, v = (x0 + x1) / 2, (y0 + y1) / 2
        if not 0.25 < forward < 3.5:
            return None
        a = np.linspace(0, 2 * math.pi, 16, endpoint=False)
        half_w, half_h = (x1 - x0) / 2, (y1 - y0) / 2
        outline = np.column_stack(
            ((x0 + x1) / 2 + half_w * np.sin(a), (y0 + y1) / 2 - half_h * np.cos(a))
        )
        return GateSighting(
            float((u - cx) / fx * forward),
            float((cy - v) / fy * forward),
            float(forward),
            0.0,
            float("nan"),
            k[:CORNERS, :2].copy(),
            outline,
        )


def load_detector(
    weights: str | Path,
    diameter_m: float = 0.70,
    opening_m: float = 0.50,
    calibration: CameraCalibration | None = None,
    **kwargs,
) -> YoloGateDetector:
    """Convenience constructor for the solution commands."""
    return YoloGateDetector(weights, diameter_m, opening_m, calibration, **kwargs)
