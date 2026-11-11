"""Ground truth for the ring's position from one ArUco marker beside it.

Stick a printed marker flat in the ring's plane (on the wall or door the ring
hangs on, upright), and every frame that sees it says where the ring is,
however the drone drifted: no tape, no hovering. ``evaluate --aruco`` compares
the detector's ``GateSighting`` with it, frame by frame.

What it can and cannot check: the marker's range scales with the camera's focal
length exactly as the ring's does, so this tests the keypoints, the PnP fit
and the ring's assumed size, not the calibration. The ChArUco calibration
(``solution_yolo.calibrate``) is what makes the metres themselves right.

The ring's centre sits at a fixed offset from the marker's centre in the shared
plane. ``estimate_offset`` finds it from the footage by casting the ray through
the ring centre the network sees onto the marker's plane, which uses only the
direction to the ring, never its size or range; measure it with a ruler to
check (x to the marker's right, y up, metres).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np

from solution.vision import CameraCalibration
from solution_yolo.keypoints import CENTRE


@dataclass(frozen=True)
class MarkerPose:
    rotation: np.ndarray  # marker -> camera, 3x3
    translation: np.ndarray  # marker centre in the camera frame, metres
    side_px: float
    error_px: float


@dataclass(frozen=True)
class RingTruth:
    right_m: float
    up_m: float
    forward_m: float
    normal_yaw_deg: float

    @property
    def distance_m(self) -> float:
        return math.sqrt(self.right_m**2 + self.up_m**2 + self.forward_m**2)


def aruco_dictionary(name: str):
    key = name.upper()
    if not key.startswith("DICT_"):
        key = "DICT_" + key
    if not hasattr(cv2.aruco, key):
        raise ValueError(f"Unknown ArUco dictionary {name!r}")
    return cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, key))


class MarkerTruth:
    def __init__(
        self,
        side_m: float,
        dictionary: str = "DICT_5X5_100",
        marker_id: int = 0,
        calibration: CameraCalibration | None = None,
        min_side_px: float = 25.0,
        max_error_px: float = 1.5,
    ):
        self.side_m = side_m
        self.marker_id = marker_id
        self.calibration = calibration or CameraCalibration()
        self.min_side_px = min_side_px
        self.max_error_px = max_error_px
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_SUBPIX
        self._detector = cv2.aruco.ArucoDetector(aruco_dictionary(dictionary), params)
        s = side_m / 2
        # OpenCV's marker order: top-left, top-right, bottom-right, bottom-left,
        # with x right, y up and z out of the marker towards the viewer.
        self._object = np.array(
            [[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], np.float64
        )

    def _intrinsics(self, frame: np.ndarray):
        h, w = frame.shape[:2]
        return self.calibration.matrix(w, h), np.array(self.calibration.distortion)

    def pose(self, frame: np.ndarray) -> MarkerPose | None:
        corners, ids, _ = self._detector.detectMarkers(frame)
        if ids is None:
            return None
        hits = [
            c for c, i in zip(corners, ids.ravel(), strict=True) if i == self.marker_id
        ]
        if len(hits) != 1:
            return None
        image = hits[0].reshape(4, 2).astype(np.float64)
        side = float(np.mean(np.linalg.norm(image - np.roll(image, 1, axis=0), axis=1)))
        if side < self.min_side_px:
            return None
        matrix, dist = self._intrinsics(frame)
        ok, rvec, tvec = cv2.solvePnP(
            self._object, image, matrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE
        )
        if not ok:
            return None
        projected, _ = cv2.projectPoints(self._object, rvec, tvec, matrix, dist)
        error = float(
            np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - image) ** 2, axis=1)))
        )
        if error > self.max_error_px:
            return None
        return MarkerPose(cv2.Rodrigues(rvec)[0], tvec.ravel(), side, error)

    def ray_onto_marker_plane(
        self, frame: np.ndarray, pose: MarkerPose, pixel
    ) -> np.ndarray | None:
        """Where the ray through ``pixel`` meets the marker's plane, in marker
        coordinates (metres)."""
        matrix, dist = self._intrinsics(frame)
        x, y = cv2.undistortPoints(
            np.array([[pixel]], np.float64), matrix, dist
        ).ravel()
        ray = np.array([x, y, 1.0])
        normal = pose.rotation[:, 2]
        denom = float(normal @ ray)
        if abs(denom) < 1e-6:
            return None
        point = ray * float(normal @ pose.translation) / denom
        return pose.rotation.T @ (point - pose.translation)

    def ring(self, pose: MarkerPose, offset_m) -> RingTruth:
        """The ring centre as right, up and forward of a level camera, which
        is the frame the detector reports in for a hovering Tello, and the yaw
        of the marker's normal (pointing away from the camera) about it.

        Only the marker's *position* is trusted for the frame, not its tilt: a
        marker a few degrees off the ring's plane, or off vertical, would
        otherwise turn into a height error of range x sin(tilt). The yaw does
        come from the marker, so it is only as good as its mounting."""
        rot = pose.rotation
        point = pose.translation + rot @ np.array([offset_m[0], offset_m[1], 0.0])
        normal = -rot[:, 2]
        yaw = math.degrees(math.atan2(normal[0], normal[2]))
        return RingTruth(float(point[0]), float(-point[1]), float(point[2]), yaw)


def estimate_offset(truth: MarkerTruth, detector, frames, keypoint_min: float = 0.5):
    """The ring centre in marker coordinates, as the median over frames where
    both the marker and a confident ring centre are seen. Returns (offset,
    frames used)."""
    points = []
    for frame in frames:
        pose = truth.pose(frame)
        if pose is None:
            continue
        rings = detector.rings(frame)
        if not rings:
            continue
        k = np.asarray(rings[0].keypoints, float)
        if k[CENTRE, 2] < keypoint_min:
            continue
        p = truth.ray_onto_marker_plane(frame, pose, k[CENTRE, :2])
        if p is not None:
            points.append(p[:2])
    if not points:
        return None, 0
    return np.median(np.array(points), axis=0), len(points)
