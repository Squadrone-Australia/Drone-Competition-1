"""The nine keypoints the pose model is trained to find on the ring.

Keypoints 0-7 are exactly the sector corners :class:`solution.vision.GateDetector`
fits its pose to, in the same order, so a YOLO prediction can go straight into
the same PnP: the top segment outer-left, outer-right, inner-right,
inner-left, then the bottom segment inner-left, inner-right, outer-right,
outer-left. Keypoint 8 is the ring's centre, which is not on the fabric but
gives a pointing cue when too few corners are in shot for a pose.

Left and right are *as the camera sees the ring*. From behind, the physical
corner that is outer-left from the approach side appears on the right, and it
is labelled outer-right: the colour detector cannot tell the faces apart
either, and labelling by what is seen keeps the target a function of the
image. The flip index below is what makes horizontal-flip augmentation keep
that true.

Coordinates follow the detector's object frame: x right, y down, z along the
gate normal away from the camera, metres, origin at the ring centre.
"""

from __future__ import annotations

import math

import numpy as np

from solution.vision import ORANGE_HALF_ANGLE_DEG

NUM_KPTS = 9
CORNERS = 8
CENTRE = 8
KPT_NAMES = (
    "top_outer_left",
    "top_outer_right",
    "top_inner_right",
    "top_inner_left",
    "bottom_inner_left",
    "bottom_inner_right",
    "bottom_outer_right",
    "bottom_outer_left",
    "centre",
)
# Index each keypoint becomes under a horizontal mirror.
FLIP_IDX = (1, 0, 3, 2, 5, 4, 7, 6, 8)
KPT_SHAPE = (NUM_KPTS, 3)
# YOLO keypoint visibility: 0 not labelled (out of shot or behind the camera),
# 1 labelled but hidden behind something nearer, 2 visible.
NOT_LABELLED, OCCLUDED, VISIBLE = 0, 1, 2
CLASS_NAMES = {0: "gate"}


def object_points(diameter_m: float = 0.70, opening_m: float = 0.50) -> np.ndarray:
    """The nine keypoints in the ring's own frame, (9, 3) metres."""
    r_out, r_in = diameter_m / 2, opening_m / 2
    s = math.sin(math.radians(ORANGE_HALF_ANGLE_DEG))
    c = math.cos(math.radians(ORANGE_HALF_ANGLE_DEG))
    return np.array(
        [
            [-r_out * s, -r_out * c, 0],
            [r_out * s, -r_out * c, 0],
            [r_in * s, -r_in * c, 0],
            [-r_in * s, -r_in * c, 0],
            [-r_in * s, r_in * c, 0],
            [r_in * s, r_in * c, 0],
            [r_out * s, r_out * c, 0],
            [-r_out * s, r_out * c, 0],
            [0, 0, 0],
        ],
        dtype=np.float64,
    )


def circle_points(radius_m: float, n: int = 72) -> np.ndarray:
    """``n`` points round a circle in the ring's plane, (n, 3) metres."""
    a = np.linspace(0, 2 * math.pi, n, endpoint=False)
    return np.column_stack((radius_m * np.sin(a), -radius_m * np.cos(a), np.zeros(n)))


def format_label(
    box_xyxy, keypoints: np.ndarray, width: int, height: int, cls: int = 0
) -> str:
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
    """Inverse of :func:`format_label`: (cls, box_xyxy in pixels, (9, 3) keypoints
    in pixels)."""
    values = [float(v) for v in line.split()]
    if len(values) != 5 + 3 * NUM_KPTS:
        raise ValueError(f"expected {5 + 3 * NUM_KPTS} values, got {len(values)}")
    cls = int(values[0])
    cx, cy, bw, bh = values[1:5]
    box = (
        (cx - bw / 2) * width,
        (cy - bh / 2) * height,
        (cx + bw / 2) * width,
        (cy + bh / 2) * height,
    )
    kpts = np.array(values[5:], float).reshape(NUM_KPTS, 3)
    kpts[:, 0] *= width
    kpts[:, 1] *= height
    return cls, box, kpts


def read_labels(path, width: int, height: int) -> list:
    """Every (cls, box, keypoints) in a label file; an absent file is no gates."""
    from pathlib import Path

    path = Path(path)
    if not path.exists():
        return []
    return [
        parse_label(line, width, height)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def write_labels(path, gates, width: int, height: int) -> None:
    """Write (box, keypoints) pairs; an empty list writes an empty file, which
    YOLO reads as a background image."""
    from pathlib import Path

    Path(path).write_text(
        "".join(format_label(b, k, width, height) + "\n" for b, k in gates)
    )


def box_from_keypoints(
    keypoints: np.ndarray,
    width: int,
    height: int,
    diameter_m: float = 0.70,
    opening_m: float = 0.50,
):
    """The ring's outer box from its labelled keypoints, through the plane's
    homography, clipped to the frame; None with fewer than four corners.

    For hand labels and pseudo-labels, where there is no 3D pose to project.
    """
    import cv2

    k = np.asarray(keypoints, float).reshape(NUM_KPTS, 3)
    known = k[:, 2] > 0
    if known[:CORNERS].sum() < 4:
        return None
    model = object_points(diameter_m, opening_m)[:, :2]
    h, _ = cv2.findHomography(model[known], k[known, :2], 0)
    if h is None:
        return None
    outer = circle_points(diameter_m / 2)[:, :2]
    pts = cv2.perspectiveTransform(outer.reshape(-1, 1, 2), h).reshape(-1, 2)
    if not np.isfinite(pts).all():
        return None
    x0, y0 = np.clip(pts.min(axis=0), 0, (width, height))
    x1, y1 = np.clip(pts.max(axis=0), 0, (width, height))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    return (float(x0), float(y0), float(x1), float(y1))


def centre_from_corners(corners: np.ndarray, known=None):
    """The ring centre's image point from four or more corners, through the
    plane's homography (perspective moves it off the corners' mean)."""
    import cv2

    corners = np.asarray(corners, float).reshape(-1, 2)[:CORNERS]
    known = np.ones(CORNERS, bool) if known is None else np.asarray(known, bool)
    if known.sum() < 4:
        return None
    model = object_points()[:CORNERS, :2]
    h, _ = cv2.findHomography(model[known], corners[known], 0)
    if h is None:
        return None
    return cv2.perspectiveTransform(np.zeros((1, 1, 2)), h).reshape(2)
