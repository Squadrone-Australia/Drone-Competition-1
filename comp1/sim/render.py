"""The simulated drone's camera view.

Everything drawn here is projected through :mod:`comp1.sim.scene` using the same
camera model the detector inverts, so the arena is geometrically honest: a wall
seam two metres away really is two metres away, and a marker's pixel radius still
inverts to its true range.

**Palette rule.** Every scenery colour is blue-dominant in BGR (``B >= G >= R``)
and low-saturation. The detector hunts red, and scenery is by far the largest
area in the frame — a warm grey wall is a false-positive waiting to happen. If
you re-tune the palette, keep it on the cool side of neutral.
"""

import math

import cv2
import numpy as np

from ..vision.camera import SIM_INTRINSICS
from .scene import Camera, draw_line, fill_quad, shade
from .world import DEFAULT_MARKER_HEIGHT_M, DESTINATION, FIRE

# The camera model is shared with the real hardware (comp1/vision/camera.py) so
# that anything tuned in the simulator transfers to the arena. It used to be a
# local FOV_H = 83 deg, which treated DJI's *diagonal* spec figure as horizontal
# and made the sim camera ~13 deg too wide.
INTRINSICS = SIM_INTRINSICS
MARKER_HEIGHT = DEFAULT_MARKER_HEIGHT_M
MIN_DIST = 0.15

# --- arena dressing -------------------------------------------------------
WALL_HEIGHT_M = 2.8
GRID_STEP_M = 0.5
POST_WIDTH_M = 0.035
SHADOW_RADIUS_M = 0.16

FLOOR_BGR = (168, 158, 148)
FLOOR_GRID_BGR = (140, 129, 119)
WALL_BGR = (214, 206, 198)
WALL_TRIM_BGR = (172, 162, 152)
SKIRTING_BGR = (140, 132, 124)
CEILING_BGR = (236, 231, 226)
CEILING_PANEL_BGR = (252, 250, 248)
POST_BGR = (74, 70, 66)
SHADOW_BGR = (132, 123, 114)

# Rough Lambert term per wall, keyed by the wall's inward normal. A flat fill on
# all four walls reads as a painted backdrop; a few percent of variation is what
# makes the corners legible.
_WALL_SHADE = {(1, 0): 1.00, (0, -1): 0.93, (-1, 0): 0.86, (0, 1): 0.79}

KIND_STYLE = {
    FIRE: ("circle", (0, 0, 220)),
    # Byte-identical to a target, on purpose: the destination sign is a red
    # circle and the detector cannot tell them apart. Telling them apart is the
    # student's problem, not the renderer's.
    DESTINATION: ("circle", (0, 0, 220)),
    "red_square": ("square", (0, 0, 220)),
    "blue_circle": ("circle", (220, 60, 0)),
    "green_triangle": ("triangle", (60, 180, 0)),
    "yellow_square": ("square", (0, 210, 230)),
    # Free-standing obstacles. Same shapes and same colours as the wall
    # distractors -- an obstacle is not visually a special class of thing, it is
    # just one of these standing where the drone wants to fly. A kind missing
    # from this table is a hard KeyError below, which is the intent: a new kind
    # must be given a look, not silently drawn as a triangle.
    "obstacle_red_square": ("square", (0, 0, 220)),
    "obstacle_green_triangle": ("triangle", (60, 180, 0)),
    "obstacle_blue_circle": ("circle", (220, 60, 0)),
    "obstacle_yellow_square": ("square", (0, 210, 230)),
}


def render(world, x, y, z, heading, w=640, h=480):
    """The drone's camera view — and nothing else.

    This is a *sensor* frame: it must contain only what a real camera would see.
    The minimap lives in :func:`draw_minimap`, which the server composites onto
    the display copy after detection has run.
    """
    cam = Camera.at(INTRINSICS, x, y, z, heading, w, h)
    img = np.empty((h, w, 3), np.uint8)
    img[:] = CEILING_BGR  # backdrop for any gap at the seams
    _draw_room(img, cam, world.width_m, world.depth_m)
    _draw_markers(img, cam, world, x, y, z, heading, w, h)
    return img


# --- room -----------------------------------------------------------------


def _draw_room(img, cam, width, depth):
    """The box: floor, ceiling and four walls of a ``width`` x ``depth`` room."""
    a, b, top = width, depth, WALL_HEIGHT_M
    fill_quad(img, cam, [(0, 0, 0), (a, 0, 0), (a, b, 0), (0, b, 0)], FLOOR_BGR)
    _draw_floor_grid(img, cam, a, b)
    fill_quad(
        img, cam, [(0, 0, top), (a, 0, top), (a, b, top), (0, b, top)], CEILING_BGR
    )
    _draw_ceiling_panels(img, cam, a, b)

    # Walls furthest-first: they never overlap each other from inside the room,
    # but the trim lines are drawn per wall and would otherwise cross a nearer face.
    walls = [
        ((-1, 0), [(a, 0, 0), (a, b, 0), (a, b, top), (a, 0, top)]),  # east
        ((1, 0), [(0, b, 0), (0, 0, 0), (0, 0, top), (0, b, top)]),  # west
        ((0, -1), [(0, b, 0), (a, b, 0), (a, b, top), (0, b, top)]),  # north
        ((0, 1), [(a, 0, 0), (0, 0, 0), (0, 0, top), (a, 0, top)]),  # south
    ]
    centre = {
        (-1, 0): (a, b / 2, top / 2),
        (1, 0): (0, b / 2, top / 2),
        (0, -1): (a / 2, b, top / 2),
        (0, 1): (a / 2, 0, top / 2),
    }
    for normal, corners in sorted(walls, key=lambda wl: -cam.depth_of(centre[wl[0]])):
        _draw_wall(img, cam, corners, _WALL_SHADE[normal])


def _draw_wall(img, cam, corners, lighting):
    fill_quad(img, cam, corners, shade(WALL_BGR, lighting))
    a, b = np.array(corners[0], float), np.array(corners[1], float)
    for height, color, thickness in (
        (0.10, SKIRTING_BGR, 3),
        (WALL_HEIGHT_M - 0.25, WALL_TRIM_BGR, 2),
    ):
        rise = np.array([0.0, 0.0, height])
        draw_line(img, cam, a + rise, b + rise, shade(color, lighting), thickness)
    for corner in (corners[0], corners[1]):  # vertical corner seam
        base = np.array(corner, float)
        draw_line(
            img,
            cam,
            base,
            base + [0, 0, WALL_HEIGHT_M],
            shade(WALL_TRIM_BGR, lighting),
            1,
        )


def _draw_floor_grid(img, cam, width, depth):
    nx = max(1, int(round(width / GRID_STEP_M)))
    ny = max(1, int(round(depth / GRID_STEP_M)))
    for i in range(nx + 1):
        t = i * width / nx
        draw_line(img, cam, (t, 0, 0), (t, depth, 0), FLOOR_GRID_BGR, 1)
    for i in range(ny + 1):
        t = i * depth / ny
        draw_line(img, cam, (0, t, 0), (width, t, 0), FLOOR_GRID_BGR, 1)


PANEL_PITCH_M = 3.0  # how often a strip light repeats down a long room


def _draw_ceiling_panels(img, cam, width, depth):
    """Strip lights. Pure parallax furniture — without them the ceiling is a flat
    colour and forward motion is invisible when no marker is in view.

    They tile along the room's length rather than spanning it: one 9 m smear
    down a corridor gives no parallax at all, which is the case that needs it
    most.
    """
    top = WALL_HEIGHT_M - 0.01
    rows = max(1, int(round(depth / PANEL_PITCH_M)))
    for cx in (width / 3, 2 * width / 3):
        for j in range(rows):
            y0 = depth * (j + 0.2) / rows
            y1 = depth * (j + 0.8) / rows
            fill_quad(
                img,
                cam,
                [
                    (cx - 0.12, y0, top),
                    (cx + 0.12, y0, top),
                    (cx + 0.12, y1, top),
                    (cx - 0.12, y1, top),
                ],
                CEILING_PANEL_BGR,
            )


# --- markers --------------------------------------------------------------


def _draw_markers(img, cam, world, x, y, z, heading, w, h):
    focal = INTRINSICS.focal_px(w)
    hfov = math.radians(INTRINSICS.hfov_deg)
    hr = math.radians(heading)
    vis = []
    for m in world.markers:
        dx, dy = m.x - x, m.y - y
        d = max(math.hypot(dx, dy), MIN_DIST)
        ang = math.atan2(dx, dy)  # cw from +y, matches heading
        rel = (ang - hr + math.pi) % (2 * math.pi) - math.pi
        if abs(rel) > hfov / 2 + 0.35:
            continue
        vis.append((d, rel, m))
    vis.sort(key=lambda t: -t[0])  # painter's algorithm
    for d, rel, m in vis:
        _draw_stand(img, cam, m)
        # The marker itself keeps the flat billboard projection it has always
        # had: apparent radius = f * R / d, exactly what CameraIntrinsics.distance_m
        # inverts. Do not "improve" this into a tilted quad — the range estimate
        # every approach test depends on comes straight out of this line.
        cx = int(w / 2 + focal * math.tan(rel))
        cy = int(h / 2 + focal * (z - m.height_m) / d)
        # round, not truncate: int() loses up to a full pixel off every radius,
        # which reads back as a systematic over-estimate of range
        r = max(round(focal * m.radius_m / d), 2)
        shape, color = KIND_STYLE[m.kind]
        if shape == "circle":
            cv2.circle(img, (cx, cy), r, color, -1, cv2.LINE_AA)
        elif shape == "square":
            cv2.rectangle(img, (cx - r, cy - r), (cx + r, cy + r), color, -1)
        else:
            pts = np.array([[cx, cy - r], [cx - r, cy + r], [cx + r, cy + r]])
            cv2.fillPoly(img, [pts], color)


def _draw_stand(img, cam, m):
    """Floor shadow plus the tripod post the marker is mounted on.

    Both are drawn in true 3D, so a marker's contact point with the floor tracks
    the grid — that pairing is what makes the range readable by eye.
    """
    ring = [
        (m.x + SHADOW_RADIUS_M * math.cos(a), m.y + SHADOW_RADIUS_M * math.sin(a), 0.0)
        for a in np.linspace(0, 2 * math.pi, 12, endpoint=False)
    ]
    fill_quad(img, cam, ring, SHADOW_BGR)
    # billboard the post so it keeps its width whatever angle it is seen from
    to_cam = np.array([cam.x - m.x, cam.y - m.y])
    norm = np.hypot(*to_cam)
    side = (
        (
            np.array([-to_cam[1], to_cam[0]]) / norm
            if norm > 1e-6
            else np.array([1.0, 0.0])
        )
        * POST_WIDTH_M
        / 2
    )
    fill_quad(
        img,
        cam,
        [
            (m.x - side[0], m.y - side[1], 0.0),
            (m.x + side[0], m.y + side[1], 0.0),
            (m.x + side[0], m.y + side[1], m.height_m),
            (m.x - side[0], m.y - side[1], m.height_m),
        ],
        POST_BGR,
    )


# --- display-only overlay -------------------------------------------------


def draw_minimap(img, world, x, y, heading, size=140, pad=10):
    """Debug inset — display only. Never draw this on a frame the detector sees.

    ``size`` is the longest side: the inset takes the room's own aspect, so a
    corridor reads as a corridor rather than being squashed into a square.
    """
    out = img.copy()
    s = size / max(world.width_m, world.depth_m)
    mw, mh = int(world.width_m * s), int(world.depth_m * s)
    x0, y0 = out.shape[1] - mw - pad, pad
    cv2.rectangle(out, (x0, y0), (x0 + mw, y0 + mh), (255, 255, 255), -1)
    cv2.rectangle(out, (x0, y0), (x0 + mw, y0 + mh), (60, 60, 60), 2)

    def to_px(wx, wy):
        return (int(x0 + wx * s), int(y0 + (world.depth_m - wy) * s))

    sx, sy = world.start_xy
    cv2.drawMarker(out, to_px(sx, sy), (120, 120, 120), cv2.MARKER_TILTED_CROSS, 8, 1)
    for m in world.markers:
        if m.kind == DESTINATION:
            px, py = to_px(m.x, m.y)
            cv2.rectangle(out, (px - 4, py - 4), (px + 4, py + 4), (0, 140, 0), -1)
            continue
        if m.is_obstacle:
            # Amber ring, not a filled dot: on the plan a teacher needs to see at
            # a glance which markers are solid and which are just decoration.
            cv2.circle(out, to_px(m.x, m.y), 5, (0, 165, 255), 2)
            continue
        color = (0, 0, 220) if m.kind == FIRE else (140, 140, 140)
        cv2.circle(out, to_px(m.x, m.y), 4, color, -1)
    hr = math.radians(heading)
    px, py = to_px(x, y)
    tip = (int(px + 10 * math.sin(hr)), int(py - 10 * math.cos(hr)))
    cv2.circle(out, (px, py), 5, (200, 120, 0), -1)
    cv2.line(out, (px, py), tip, (200, 120, 0), 2)
    return out
