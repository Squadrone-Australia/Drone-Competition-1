"""Obstacle detection: everything in the frame that is *not* an accepted marker.

The arena is printed with shapes the marker detector is built to reject — red
squares, green triangles, blue circles, yellow squares. ``find_targets`` throws
all of them away by design (``circularity_min``/``solidity_min`` exist for
exactly that), which left the drone unable to know they were there at all.

So obstacle detection is defined *negatively*, and it reuses ``find_targets``
rather than re-deriving anything: find every colourful blob, then remove the ones
the marker detector accepted. What is left is an obstacle. That framing is what
makes the rule "anything other than a red circle is an obstacle" true by
construction instead of by a list of shapes somebody has to keep up to date.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np

from .config import DEFAULT_CONFIG, VisionConfig


@dataclass(frozen=True)
class Obstacle:
    """One thing in the way, with its geometry resolved into real units.

    Mirrors :class:`~comp1.vision.detector.Target` field for field so the two can
    be handled the same way downstream, plus ``shape``/``colour`` which are
    informational only — they exist for the overlay and for students reading the
    telemetry panel, and no sensor block reads them.
    """

    cx: float  # centroid, normalised by frame width  (0..1)
    cy: float  # centroid, normalised by frame height (0..1)
    radius_norm: float  # apparent radius / frame width
    area_ratio: float  # contour area / frame area
    circularity: float  # 4piA/P^2 of the convex hull
    solidity: float  # contour area / hull area
    bearing_deg: float  # + = to the right of the drone's nose
    elevation_deg: float  # + = above the camera axis
    distance_m: float
    position: str  # "left" | "center" | "right"
    shape: str  # "circle" | "square" | "triangle" | "blob" — display only
    color: str  # "red" | "green" | "blue" | "yellow" | "other" — display only


# Hue windows for naming a colour, in OpenCV's 0..179 space. Red wraps, so it is
# the two ends. Used for the label only — never to decide whether something is an
# obstacle, because an unlisted colour must still be avoided.
_HUE_NAMES = (
    (0, 12, "red"),
    (13, 34, "yellow"),
    (35, 85, "green"),
    (86, 130, "blue"),
    (131, 164, "other"),
    (165, 179, "red"),
)


def saturated_mask(
    frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG
) -> np.ndarray:
    """Every pixel colourful enough to belong to a printed shape.

    Thresholds on saturation and value, never hue: one mask has to catch red,
    green, blue and yellow, and the only thing those share is being colourful.
    What keeps the room out of the mask is the scenery palette rule (see
    ``comp1/sim/render.py``) — walls and floor are blue-dominant *and*
    low-saturation, so they fall under ``obstacle_sat_min``.

    Public for the same reason ``color_mask`` is: a tuning preview that used
    subtly different thresholds from the detector would look correct and be
    wrong.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(
        hsv,
        np.array((0, cfg.obstacle_sat_min, cfg.obstacle_val_min), np.uint8),
        np.array((179, 255, 255), np.uint8),
    )
    # Same CLOSE-then-OPEN as color_mask, for the same reason: a shadow across a
    # shape cuts a notch that OPEN alone can only widen. Same small kernel too,
    # so two shapes standing near each other do not fuse into one.
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def _classify_shape(contour, hull, circularity: float, solidity: float) -> str:
    """Name the shape for the label. Never gates anything."""
    if circularity >= 0.85 and solidity >= 0.9:
        return "circle"
    approx = cv2.approxPolyDP(hull, 0.04 * cv2.arcLength(hull, True), True)
    corners = len(approx)
    if corners == 3:
        return "triangle"
    if corners == 4:
        return "square"
    return "blob"


def _classify_color(hsv: np.ndarray, mask: np.ndarray) -> str:
    """Name the dominant hue inside one contour. Never gates anything."""
    hues = hsv[:, :, 0][mask > 0]
    if hues.size == 0:
        return "other"
    hue = float(np.median(hues))
    for lo, hi, name in _HUE_NAMES:
        if lo <= hue <= hi:
            return name
    return "other"


def find_obstacles(
    frame_bgr: np.ndarray,
    cfg: VisionConfig = DEFAULT_CONFIG,
    targets: list | None = None,
) -> list:
    """Every colourful blob that is not an accepted marker, nearest first.

    ``targets`` lets a caller that has already run ``find_targets`` on this frame
    pass the result in rather than paying for a second detection pass; omit it
    and one is run here.

    A *partially occluded* marker fails the target shape gates and therefore
    lands here as an obstacle. That is deliberate and it is the safe direction:
    the drone gives a half-seen thing a wide berth instead of flying at it.
    """
    h, w = frame_bgr.shape[:2]
    aspect_hw = h / w
    cam = cfg.intrinsics
    if targets is None:
        # Imported here, not at module scope: detector.py imports this module so
        # that a Detection can carry obstacles, and a top-level import back would
        # close the cycle.
        from .detector import find_targets

        targets = find_targets(frame_bgr, cfg)
    # An accepted marker occupies this disc in normalised-width units. A blob
    # centred inside one is that marker, and nothing else makes a blob exempt.
    claimed = [(t.cx, t.cy, t.radius_norm) for t in targets]

    mask = saturated_mask(frame_bgr, cfg)
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        area_ratio = area / (w * h)
        if area_ratio < cfg.obstacle_min_area_ratio:
            continue
        if area_ratio > cfg.obstacle_max_area_ratio:
            continue
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        perim = cv2.arcLength(hull, True)
        if perim == 0 or hull_area == 0:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = m["m10"] / m["m00"] / w
        cy = m["m01"] / m["m00"] / h
        # The one and only exemption: this blob is a marker the target detector
        # already accepted. Compared in width-normalised units, so cy has to be
        # scaled back out of its height normalisation first.
        if any(
            math.hypot(cx - tx, (cy - ty) * aspect_hw) <= tr for tx, ty, tr in claimed
        ):
            continue
        circularity = 4 * np.pi * hull_area / (perim * perim)
        solidity = area / hull_area
        (_, _), radius_px = cv2.minEnclosingCircle(c)
        radius_norm = radius_px / w
        if abs(cx - 0.5) < cfg.center_band / 2:
            pos = "center"
        else:
            pos = "left" if cx < 0.5 else "right"
        blob = np.zeros((h, w), np.uint8)
        cv2.drawContours(blob, [c], -1, 255, -1)
        out.append(
            Obstacle(
                cx=cx,
                cy=cy,
                radius_norm=radius_norm,
                area_ratio=area_ratio,
                circularity=float(circularity),
                solidity=float(solidity),
                bearing_deg=cam.bearing_deg(cx),
                elevation_deg=cam.elevation_deg(cy, aspect_hw),
                distance_m=cam.distance_m(radius_norm, cfg.obstacle_radius_m),
                position=pos,
                shape=_classify_shape(c, hull, float(circularity), float(solidity)),
                color=_classify_color(hsv, cv2.bitwise_and(blob, mask)),
            )
        )
    out.sort(key=lambda o: o.distance_m)
    return out


def is_in_the_way(obstacle, cfg: VisionConfig = DEFAULT_CONFIG) -> bool:
    """True when this obstacle is close enough and central enough to block us.

    One definition, used by the ``obstacle_ahead`` sensor and by both avoidance
    controllers, so the block a student tests with and the block that acts can
    never disagree about what "in the way" means.
    """
    return (
        obstacle is not None
        and obstacle.position == "center"
        and obstacle.distance_m <= cfg.obstacle_clear_distance_m
    )
