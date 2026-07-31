import math

import cv2
import numpy as np

from ..vision.camera import SIM_INTRINSICS
from .world import VICTIM, DEFAULT_MARKER_HEIGHT_M

# The camera model is shared with the real hardware (comp1/vision/camera.py) so
# that anything tuned in the simulator transfers to the arena. It used to be a
# local FOV_H = 83 deg, which treated DJI's *diagonal* spec figure as horizontal
# and made the sim camera ~13 deg too wide.
INTRINSICS = SIM_INTRINSICS
MARKER_HEIGHT = DEFAULT_MARKER_HEIGHT_M
MIN_DIST = 0.15
WALL_BGR = (210, 210, 205)
FLOOR_BGR = (150, 160, 170)
KIND_STYLE = {
    VICTIM:           ("circle",   (0, 0, 220)),
    "red_square":     ("square",   (0, 0, 220)),
    "blue_circle":    ("circle",   (220, 60, 0)),
    "green_triangle": ("triangle", (60, 180, 0)),
    "yellow_square":  ("square",   (0, 210, 230)),
}


def render(world, x, y, z, heading, w=640, h=480):
    """The drone's camera view — and nothing else.

    This is a *sensor* frame: it must contain only what a real camera would see.
    The minimap lives in :func:`draw_minimap`, which the server composites onto
    the display copy after detection has run.
    """
    focal = INTRINSICS.focal_px(w)
    hfov = math.radians(INTRINSICS.hfov_deg)
    img = np.empty((h, w, 3), np.uint8)
    img[:h // 2] = WALL_BGR
    img[h // 2:] = FLOOR_BGR
    hr = math.radians(heading)
    vis = []
    for m in world.markers:
        dx, dy = m.x - x, m.y - y
        d = max(math.hypot(dx, dy), MIN_DIST)
        ang = math.atan2(dx, dy)                       # cw from +y, matches heading
        rel = (ang - hr + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) > hfov / 2 + 0.35:
            continue
        vis.append((d, rel, m))
    vis.sort(key=lambda t: -t[0])                      # painter's algorithm
    for d, rel, m in vis:
        cx = int(w / 2 + focal * math.tan(rel))
        cy = int(h / 2 + focal * (z - m.height_m) / d)
        # round, not truncate: int() loses up to a full pixel off every radius,
        # which reads back as a systematic over-estimate of range
        r = max(round(focal * m.radius_m / d), 2)
        shape, color = KIND_STYLE[m.kind]
        if shape == "circle":
            cv2.circle(img, (cx, cy), r, color, -1)
        elif shape == "square":
            cv2.rectangle(img, (cx - r, cy - r), (cx + r, cy + r), color, -1)
        else:
            pts = np.array([[cx, cy - r], [cx - r, cy + r], [cx + r, cy + r]])
            cv2.fillPoly(img, [pts], color)
    return img


def draw_minimap(img, world, x, y, heading, size=140, pad=10):
    """Debug inset — display only. Never draw this on a frame the detector sees."""
    out = img.copy()
    s = size / world.size_m
    x0, y0 = out.shape[1] - size - pad, pad
    cv2.rectangle(out, (x0, y0), (x0 + size, y0 + size), (255, 255, 255), -1)
    cv2.rectangle(out, (x0, y0), (x0 + size, y0 + size), (60, 60, 60), 2)

    def to_px(wx, wy):
        return (int(x0 + wx * s), int(y0 + (world.size_m - wy) * s))

    for m in world.markers:
        color = (0, 0, 220) if m.kind == VICTIM else (140, 140, 140)
        cv2.circle(out, to_px(m.x, m.y), 4, color, -1)
    hr = math.radians(heading)
    px, py = to_px(x, y)
    tip = (int(px + 10 * math.sin(hr)), int(py - 10 * math.cos(hr)))
    cv2.circle(out, (px, py), 5, (200, 120, 0), -1)
    cv2.line(out, (px, py), tip, (200, 120, 0), 2)
    return out
