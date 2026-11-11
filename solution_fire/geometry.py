"""The red-circle target: its keypoints, its board, and the camera that sees it.

The pose model finds one class, ``red_circle``, with five keypoints:

    0 centre   1 top   2 right   3 bottom   4 left

Rim points are where the disc's edge crosses its own vertical and horizontal
diameters. Right and left are *as the camera sees the disc*: a horizontal
flip of the image swaps them, which is what ``FLIP_IDX`` tells YOLO's flip
augmentation. Top and bottom never swap (no up-down flips: a Tello is level).

The top-bottom pair is the useful one for range. Turning the board about its
vertical axis (yaw) shrinks the left-right span but leaves the vertical span
alone, so ``detector.range_from_keypoints`` prefers it.

The board the footage was filmed with carries the disc above a printed ArUco
marker. Marker coordinates follow OpenCV: x right, y up, z out of the print
towards the viewer, metres, origin at the marker's centre. The disc centre
sits at ``offset`` in that plane (measured: 0.33 m straight up), so a marker
pose is a disc pose, which is how ``autolabel`` labels frames without a
human.

Nothing here imports torch, so the tests and the labelling tools run on any
laptop.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

NUM_KPTS = 5
CENTRE, TOP, RIGHT, BOTTOM, LEFT = range(NUM_KPTS)
KPT_NAMES = ("centre", "top", "right", "bottom", "left")
# Index each keypoint becomes under a horizontal mirror.
FLIP_IDX = (0, 1, 4, 3, 2)
KPT_SHAPE = (NUM_KPTS, 3)
# YOLO keypoint visibility: 0 not labelled (out of shot), 1 labelled but
# hidden, 2 visible.
NOT_LABELLED, OCCLUDED, VISIBLE = 0, 1, 2
CLASS_NAMES = {0: "red_circle"}

# The printed board, as measured with a ruler (see the plan).
DISC_DIAMETER_M = 0.20
DISC_OFFSET_M = (0.0, 0.33)  # disc centre in marker coordinates
MARKER_SIDE_M = 0.18
MARKER_DICT = "DICT_5X5_100"


@dataclass
class CameraCalibration:
    """Pinhole intrinsics as fractions of the image size, so one file serves
    any resolution the stream is resized to. The fields match the
    ``tello_camera.json`` the ChArUco calibration wrote; the defaults are that
    file's values, so a missing ``--calibration`` is still the Tello."""

    focal_per_width: float = 0.9795442295704091
    fy_per_width: float | None = 0.9801151785281438
    cx_fraction: float = 0.4766620260909961
    cy_fraction: float = 0.4599193945281663
    distortion: list = field(
        default_factory=lambda: [-0.0403154307, 0.1508617692, -0.0061224255, -0.0076861975, 0.0]
    )

    @classmethod
    def load(cls, path) -> CameraCalibration:
        data = json.loads(Path(path).read_text())
        known = {k: data[k] for k in cls.__dataclass_fields__ if k in data}
        return cls(**known)

    def matrix(self, width: int, height: int) -> np.ndarray:
        fx = self.focal_per_width * width
        fy = (self.fy_per_width or self.focal_per_width) * width
        return np.array(
            [[fx, 0, self.cx_fraction * width], [0, fy, self.cy_fraction * height], [0, 0, 1]],
            np.float64,
        )

    def dist(self) -> np.ndarray:
        return np.asarray(self.distortion, np.float64)

    def rays(self, pixels, width: int, height: int) -> np.ndarray:
        """Unit-depth camera rays (x right, y down, z forward) through pixels."""
        import cv2

        pts = np.asarray(pixels, np.float64).reshape(-1, 1, 2)
        und = cv2.undistortPoints(pts, self.matrix(width, height), self.dist()).reshape(-1, 2)
        return np.column_stack((und, np.ones(len(und))))


def disc_points(
    radius_m: float = DISC_DIAMETER_M / 2, offset_m=DISC_OFFSET_M
) -> np.ndarray:
    """The five keypoints in marker coordinates, (5, 3) metres (y up)."""
    ox, oy = offset_m
    r = radius_m
    return np.array(
        [[ox, oy, 0], [ox, oy + r, 0], [ox + r, oy, 0], [ox, oy - r, 0], [ox - r, oy, 0]],
        np.float64,
    )


def rim_points(radius_m: float = DISC_DIAMETER_M / 2, offset_m=DISC_OFFSET_M, n: int = 64):
    """``n`` points round the disc's edge in marker coordinates, (n, 3)."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack(
        (offset_m[0] + radius_m * np.cos(a), offset_m[1] + radius_m * np.sin(a), np.zeros(n))
    )


def label_from_image_points(kpts_px: np.ndarray, rim_px: np.ndarray, width: int, height: int,
                            min_inside: float = 0.25):
    """(box, keypoints) for a disc whose keypoints and rim are already in pixels.

    The box is the rim's extent clipped to the frame. A keypoint outside the
    frame is unlabelled. None when too little of the disc is in shot to call it
    a sighting: less than ``min_inside`` of its unclipped box area.
    """
    rim = np.asarray(rim_px, float).reshape(-1, 2)
    if not np.isfinite(rim).all():
        return None
    x0, y0 = rim.min(axis=0)
    x1, y1 = rim.max(axis=0)
    full = max((x1 - x0) * (y1 - y0), 1e-9)
    cx0, cy0 = max(x0, 0.0), max(y0, 0.0)
    cx1, cy1 = min(x1, float(width)), min(y1, float(height))
    if cx1 - cx0 < 3 or cy1 - cy0 < 3:
        return None
    if (cx1 - cx0) * (cy1 - cy0) / full < min_inside:
        return None
    k = np.zeros((NUM_KPTS, 3))
    pts = np.asarray(kpts_px, float).reshape(NUM_KPTS, 2)
    inside = (pts[:, 0] >= 0) & (pts[:, 0] < width) & (pts[:, 1] >= 0) & (pts[:, 1] < height)
    k[inside, :2] = pts[inside]
    k[inside, 2] = VISIBLE
    return (float(cx0), float(cy0), float(cx1), float(cy1)), k


def project_disc(rvec, tvec, calibration: CameraCalibration, width: int, height: int,
                 radius_m: float = DISC_DIAMETER_M / 2, offset_m=DISC_OFFSET_M,
                 min_inside: float = 0.25):
    """The disc's label for a marker pose: (box, keypoints, rim pixels), or
    None when the disc is mostly out of shot or behind the camera."""
    import cv2

    matrix, dist = calibration.matrix(width, height), calibration.dist()
    rot = cv2.Rodrigues(np.asarray(rvec, np.float64))[0]
    t = np.asarray(tvec, np.float64).reshape(3)
    rim3 = rim_points(radius_m, offset_m)
    if ((rot @ rim3.T).T + t)[:, 2].min() <= 0.05:
        return None
    kp, _ = cv2.projectPoints(disc_points(radius_m, offset_m), rvec, tvec, matrix, dist)
    rim, _ = cv2.projectPoints(rim3, rvec, tvec, matrix, dist)
    rim = rim.reshape(-1, 2)
    label = label_from_image_points(kp.reshape(-1, 2), rim, width, height, min_inside)
    if label is None:
        return None
    return label[0], label[1], rim


# --- YOLO pose label files ---------------------------------------------------------------


def format_label(box_xyxy, keypoints: np.ndarray, width: int, height: int, cls: int = 0) -> str:
    """One YOLO-pose label line: class, normalised box centre and size, then
    ``x y v`` per keypoint. Unlabelled keypoints are written as ``0 0 0``."""
    x0, y0, x1, y1 = box_xyxy
    parts = [
        str(cls),
        f"{(x0 + x1) / 2 / width:.6f}",
        f"{(y0 + y1) / 2 / height:.6f}",
        f"{(x1 - x0) / width:.6f}",
        f"{(y1 - y0) / height:.6f}",
    ]
    for x, y, v in np.asarray(keypoints, float).reshape(NUM_KPTS, 3):
        if v <= 0:
            parts += ["0", "0", "0"]
        else:
            parts += [f"{x / width:.6f}", f"{y / height:.6f}", str(int(v))]
    return " ".join(parts)


def parse_label(line: str, width: int, height: int):
    """Inverse of :func:`format_label`: (cls, box_xyxy, (5, 3) keypoints), pixels."""
    values = [float(v) for v in line.split()]
    if len(values) != 5 + 3 * NUM_KPTS:
        raise ValueError(f"expected {5 + 3 * NUM_KPTS} values, got {len(values)}")
    cx, cy, bw, bh = values[1:5]
    box = ((cx - bw / 2) * width, (cy - bh / 2) * height,
           (cx + bw / 2) * width, (cy + bh / 2) * height)
    kpts = np.array(values[5:], float).reshape(NUM_KPTS, 3)
    kpts[:, 0] *= width
    kpts[:, 1] *= height
    return int(values[0]), box, kpts


def read_labels(path, width: int, height: int) -> list:
    """Every (cls, box, keypoints) in a label file; an absent file is no discs."""
    path = Path(path)
    if not path.exists():
        return []
    return [parse_label(ln, width, height) for ln in path.read_text().splitlines() if ln.strip()]


def write_labels(path, discs, width: int, height: int) -> None:
    """Write (box, keypoints) pairs; an empty list writes an empty file, which
    YOLO reads as a background image."""
    Path(path).write_text("".join(format_label(b, k, width, height) + "\n" for b, k in discs))


def box_from_keypoints(keypoints: np.ndarray, width: int, height: int):
    """A disc's box from hand-placed keypoints: the ellipse through the four
    rim points (the centre fills in a missing one), clipped to the frame.
    None with fewer than three rim points."""
    k = np.asarray(keypoints, float).reshape(NUM_KPTS, 3)
    rim = [i for i in (TOP, RIGHT, BOTTOM, LEFT) if k[i, 2] > 0]
    if len(rim) < 3 and not (len(rim) == 2 and k[CENTRE, 2] > 0):
        return None
    pts = {i: k[i, :2] for i in range(NUM_KPTS) if k[i, 2] > 0}
    centre = pts.get(CENTRE)
    if centre is None:
        centre = np.mean([pts[i] for i in rim], axis=0)
    # A missing rim point mirrors its opposite through the centre.
    for a, b in ((TOP, BOTTOM), (RIGHT, LEFT)):
        if a not in pts and b in pts:
            pts[a] = 2 * centre - pts[b]
        if b not in pts and a in pts:
            pts[b] = 2 * centre - pts[a]
    if not all(i in pts for i in (TOP, RIGHT, BOTTOM, LEFT)):
        return None
    xs = [pts[i][0] for i in (TOP, RIGHT, BOTTOM, LEFT)]
    ys = [pts[i][1] for i in (TOP, RIGHT, BOTTOM, LEFT)]
    x0, x1 = max(min(xs), 0.0), min(max(xs), float(width))
    y0, y1 = max(min(ys), 0.0), min(max(ys), float(height))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (float(x0), float(y0), float(x1), float(y1))
