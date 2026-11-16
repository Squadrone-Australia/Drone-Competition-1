"""The ArUco marker under the disc: ground truth while filming, never a cue.

The board the training footage was filmed with carries a 180 mm DICT_5X5_100
marker below the disc. Every frame that sees it says exactly where the disc
is, which is what lets ``autolabel`` label footage with no human, and lets
``evaluate`` score range against something better than a guess.

The competition boards will have no marker. A network that saw one under
every positive could learn "red thing above a marker" instead of "red disc",
so ``paint_out`` covers it with paper before a frame is saved for training.
The hollow-ring negatives were filmed on the same board, so the marker is in
both classes anyway, but painting both keeps the two identical in that
respect.

Adapted from ``solution_yolo/marker.py`` (the gate competition's version).
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from solution_fire.geometry import MARKER_DICT, MARKER_SIDE_M, CameraCalibration


@dataclass(frozen=True)
class MarkerPose:
    rvec: np.ndarray  # marker -> camera, Rodrigues
    tvec: np.ndarray  # marker centre in the camera frame, metres
    rotation: np.ndarray  # 3x3 of rvec
    corners: np.ndarray  # (4, 2) image corners, OpenCV order
    side_px: float
    error_px: float


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
        side_m: float = MARKER_SIDE_M,
        dictionary: str = MARKER_DICT,
        marker_id: int = 0,
        calibration: CameraCalibration | None = None,
        min_side_px: float = 25.0,
        max_error_px: float = 2.0,
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
        self._object = np.array([[-s, s, 0], [s, s, 0], [s, -s, 0], [-s, -s, 0]], np.float64)

    def detect_all(self, frame: np.ndarray) -> list[np.ndarray]:
        """Every marker's (4, 2) corners in the frame, any id: what to paint."""
        corners, ids, _ = self._detector.detectMarkers(frame)
        if ids is None:
            return []
        return [c.reshape(4, 2).astype(np.float64) for c in corners]

    def pose(self, frame: np.ndarray) -> MarkerPose | None:
        corners, ids, _ = self._detector.detectMarkers(frame)
        if ids is None:
            return None
        hits = [c for c, i in zip(corners, ids.ravel(), strict=True) if i == self.marker_id]
        if len(hits) != 1:
            return None
        image = hits[0].reshape(4, 2).astype(np.float64)
        side = float(np.mean(np.linalg.norm(image - np.roll(image, 1, axis=0), axis=1)))
        if side < self.min_side_px:
            return None
        h, w = frame.shape[:2]
        matrix, dist = self.calibration.matrix(w, h), self.calibration.dist()
        ok, rvec, tvec = cv2.solvePnP(
            self._object, image, matrix, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE
        )
        if not ok:
            return None
        projected, _ = cv2.projectPoints(self._object, rvec, tvec, matrix, dist)
        error = float(np.sqrt(np.mean(np.sum((projected.reshape(4, 2) - image) ** 2, axis=1))))
        if error > self.max_error_px:
            return None
        return MarkerPose(rvec, tvec, cv2.Rodrigues(rvec)[0], image, side, error)

    def ray_onto_plane(self, frame: np.ndarray, pose: MarkerPose, pixels) -> np.ndarray:
        """Where the rays through ``pixels`` meet the marker's plane, as (n, 2)
        marker coordinates in metres (NaN where a ray runs parallel)."""
        h, w = frame.shape[:2]
        rays = self.calibration.rays(pixels, w, h)
        normal = pose.rotation[:, 2]
        denom = rays @ normal
        with np.errstate(divide="ignore", invalid="ignore"):
            scale = float(normal @ pose.tvec.ravel()) / denom
        points = rays * scale[:, None]
        local = (pose.rotation.T @ (points - pose.tvec.ravel()).T).T
        local[np.abs(denom) < 1e-6] = np.nan
        return local[:, :2]


def disc_truth(pose: MarkerPose, offset_m) -> dict:
    """The disc centre as right, up and forward of the camera, metres, and the
    board normal's yaw (0 = square-on), for range scoring."""
    point = pose.tvec.ravel() + pose.rotation @ np.array([offset_m[0], offset_m[1], 0.0])
    normal = -pose.rotation[:, 2]
    return {
        "right_m": float(point[0]),
        "up_m": float(-point[1]),
        "forward_m": float(point[2]),
        "distance_m": float(np.linalg.norm(point)),
        "yaw_deg": float(np.degrees(np.arctan2(normal[0], normal[2]))),
    }


def predicted_corners(kpts: np.ndarray, radius_m: float, offset_m, side_m: float = MARKER_SIDE_M):
    """Where the marker must be, from a disc's keypoints, when ArUco cannot
    find it (motion blur, or cut by the frame edge): the disc's centre-to-
    bottom and centre-to-right vectors give the board's scale and orientation
    in the image. Affine, so only good for painting, never for truth. None
    without the centre and one rim point in each direction."""
    k = np.asarray(kpts, float).reshape(-1, 3)
    centre, top, right, bottom, left = k[:5]
    if centre[2] <= 0:
        return None
    if bottom[2] > 0:
        down = (bottom[:2] - centre[:2]) / radius_m
    elif top[2] > 0:
        down = (centre[:2] - top[:2]) / radius_m
    else:
        return None
    if right[2] > 0:
        across = (right[:2] - centre[:2]) / radius_m
    elif left[2] > 0:
        across = (centre[:2] - left[:2]) / radius_m
    else:
        across = np.array([-down[1], down[0]])
    # Disc centre is at offset_m from the marker centre (y up), so the marker
    # centre is offset_m back the other way.
    mc = centre[:2] - across * offset_m[0] + down * offset_m[1]
    s = side_m / 2
    return np.array([mc - across * s - down * s, mc + across * s - down * s,
                     mc + across * s + down * s, mc - across * s + down * s])


def paint_out(
    frame: np.ndarray, corners: np.ndarray, grow: float = 0.18, rng=None
) -> np.ndarray:
    """Cover a marker with plain paper, in place, and return the frame.

    The quad is grown about its centre by ``grow`` (the black border and its
    blur halo reach past the detected corners), filled with the median colour
    of a thin band just outside it (the paper margin), given sensor-like noise,
    and feathered in so it leaves no hard rectangle edge to learn either."""
    rng = rng or np.random.default_rng()
    h, w = frame.shape[:2]
    quad = np.asarray(corners, np.float64).reshape(4, 2)
    centre = quad.mean(axis=0)
    inner = centre + (quad - centre) * (1 + grow)
    outer = centre + (quad - centre) * (1 + grow + 0.12)
    mask_in = np.zeros((h, w), np.uint8)
    mask_out = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(mask_in, np.round(inner).astype(np.int32), 255)
    cv2.fillConvexPoly(mask_out, np.round(outer).astype(np.int32), 255)
    band = (mask_out > 0) & (mask_in == 0)
    if band.sum() < 10:
        band = mask_in == 0
    paper = np.median(frame[band].reshape(-1, 3), axis=0)
    # Prefer the bright part of the band: the margin is paper, but the band can
    # clip the stand or the wall behind, which are darker than the print.
    samples = frame[band].reshape(-1, 3).astype(np.float64)
    bright = samples[samples.sum(axis=1) >= np.percentile(samples.sum(axis=1), 50)]
    if len(bright):
        paper = np.median(bright, axis=0)
    side = float(np.mean(np.linalg.norm(inner - np.roll(inner, 1, axis=0), axis=1)))
    fill = np.empty_like(frame, dtype=np.float64)
    fill[:] = paper
    # A gentle brightness gradient across the patch, like real paper lighting.
    yy, xx = np.mgrid[0:h, 0:w]
    g = rng.normal(0, 0.03, 2)
    fill *= (1 + g[0] * (xx - centre[0]) / max(side, 1) + g[1] * (yy - centre[1]) / max(side, 1))[
        ..., None
    ]
    fill += rng.normal(0, 2.5, fill.shape)
    feather = max(1, int(round(side * 0.04))) * 2 + 1
    alpha = cv2.GaussianBlur(mask_in.astype(np.float64) / 255, (feather, feather), 0)[..., None]
    out = frame.astype(np.float64) * (1 - alpha) + fill * alpha
    frame[:] = np.clip(out, 0, 255).astype(np.uint8)
    return frame
