"""Pinhole projection for the simulator's 3D scenery.

The simulator used to paint the camera view as two flat bands — wall colour on
top, floor colour underneath — with markers pasted on. That is enough to test a
detector but it gives a student no sense of *where the drone is*, and no visual
feedback that a `move forward` block did anything at all until a marker happens
to grow.

This module is the small amount of 3D maths needed to draw the arena properly:
world-space quads and line segments projected through the **same** camera model
the detector inverts (:mod:`comp1.vision.camera`), so the scenery is
geometrically consistent with the marker sizes rather than decorative.

Coordinate convention, matching :mod:`comp1.sim.world` and ``SimDrone``:

* ``x`` east, ``y`` north, ``z`` up, all in metres
* ``heading`` in degrees, clockwise from +y (so heading 0 looks along +y)

Camera space is ``(right, up, depth)``. Nothing here knows about markers or
arenas — :mod:`comp1.sim.render` builds the geometry, this projects it.
"""

import math
from dataclasses import dataclass

import cv2
import numpy as np

NEAR = 0.05          # near plane, metres — geometry closer than this is clipped away
_SCREEN_MARGIN = 2.0  # clip polygons to this many frame-widths beyond the viewport


@dataclass(frozen=True)
class Camera:
    """A pinhole camera at a pose, sized for one frame."""

    x: float
    y: float
    z: float
    heading: float
    w: int
    h: int
    focal: float

    @classmethod
    def at(cls, intrinsics, x, y, z, heading, w, h) -> "Camera":
        return cls(x, y, z, heading, w, h, intrinsics.focal_px(w))

    @property
    def eye(self) -> np.ndarray:
        return np.array([self.x, self.y, self.z], float)

    @property
    def basis(self) -> tuple:
        """(right, up, forward) unit vectors in world space."""
        hr = math.radians(self.heading)
        forward = np.array([math.sin(hr), math.cos(hr), 0.0])
        right = np.array([math.cos(hr), -math.sin(hr), 0.0])
        up = np.array([0.0, 0.0, 1.0])
        return right, up, forward

    def to_camera(self, points) -> np.ndarray:
        """World points (N,3) -> camera space (N,3) as (right, up, depth)."""
        pts = np.asarray(points, float).reshape(-1, 3) - self.eye
        right, up, forward = self.basis
        return np.stack([pts @ right, pts @ up, pts @ forward], axis=1)

    def project(self, cam_pts) -> np.ndarray:
        """Camera-space points (N,3) -> pixel coordinates (N,2).

        Callers must clip to ``depth >= NEAR`` first; this does not divide safely.
        """
        cam = np.asarray(cam_pts, float).reshape(-1, 3)
        depth = cam[:, 2]
        sx = self.w / 2 + self.focal * cam[:, 0] / depth
        sy = self.h / 2 - self.focal * cam[:, 1] / depth
        return np.stack([sx, sy], axis=1)

    def depth_of(self, point) -> float:
        """Distance along the optical axis — the painter's-algorithm sort key."""
        return float(self.to_camera(point)[0, 2])


def _clip_near(cam_pts: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman clip of a camera-space polygon against ``depth >= NEAR``."""
    out = []
    n = len(cam_pts)
    for i in range(n):
        a, b = cam_pts[i], cam_pts[(i + 1) % n]
        a_in, b_in = a[2] >= NEAR, b[2] >= NEAR
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out) if out else np.empty((0, 3))


def _clip_rect(poly: np.ndarray, x0, y0, x1, y1) -> np.ndarray:
    """Clip a 2D polygon to a rectangle.

    Near-plane clipping alone leaves vertices millions of pixels off-screen when
    a wall runs almost parallel to the view direction; OpenCV will happily fill
    that but slowly, and the int32 conversion is a genuine overflow risk. Clipping
    to a rectangle a couple of frames wide is exact for the convex quads we draw.
    """
    for inside, intersect in (
        (lambda p: p[0] >= x0, lambda a, b: (x0 - a[0]) / (b[0] - a[0])),
        (lambda p: p[0] <= x1, lambda a, b: (x1 - a[0]) / (b[0] - a[0])),
        (lambda p: p[1] >= y0, lambda a, b: (y0 - a[1]) / (b[1] - a[1])),
        (lambda p: p[1] <= y1, lambda a, b: (y1 - a[1]) / (b[1] - a[1])),
    ):
        if len(poly) == 0:
            return poly
        out = []
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            a_in, b_in = inside(a), inside(b)
            if a_in:
                out.append(a)
            if a_in != b_in:
                out.append(a + intersect(a, b) * (b - a))
        poly = np.array(out) if out else np.empty((0, 2))
    return poly


def fill_quad(img, cam: Camera, corners, color) -> None:
    """Fill a convex world-space polygon. Silently skips fully clipped geometry."""
    poly = _clip_near(cam.to_camera(corners))
    if len(poly) < 3:
        return
    m = _SCREEN_MARGIN * cam.w
    screen = _clip_rect(cam.project(poly), -m, -m, cam.w + m, cam.h + m)
    if len(screen) < 3:
        return
    cv2.fillPoly(img, [np.round(screen).astype(np.int32)], color, cv2.LINE_AA)


def draw_line(img, cam: Camera, p0, p1, color, thickness=1) -> None:
    """Draw a world-space segment, clipped to the near plane and the frame."""
    seg = _clip_near_segment(cam.to_camera([p0, p1]))
    if seg is None:
        return
    screen = cam.project(seg)
    clipped = _clip_segment_2d(screen[0], screen[1], -cam.w, -cam.h, 2 * cam.w, 2 * cam.h)
    if clipped is None:
        return
    a, b = clipped
    cv2.line(img, (round(a[0]), round(a[1])), (round(b[0]), round(b[1])),
             color, thickness, cv2.LINE_AA)


def _clip_near_segment(cam_pts: np.ndarray):
    a, b = cam_pts[0], cam_pts[1]
    a_in, b_in = a[2] >= NEAR, b[2] >= NEAR
    if not a_in and not b_in:
        return None
    if a_in and b_in:
        return np.array([a, b])
    t = (NEAR - a[2]) / (b[2] - a[2])
    cut = a + t * (b - a)
    return np.array([a, cut]) if a_in else np.array([cut, b])


def _clip_segment_2d(a, b, x0, y0, x1, y1):
    """Liang-Barsky. Returns the clipped endpoints, or None if fully outside."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, a[0] - x0), (dx, x1 - a[0]), (-dy, a[1] - y0), (dy, y1 - a[1])):
        if p == 0:
            if q < 0:
                return None
            continue
        t = q / p
        if p < 0:
            t0 = max(t0, t)
        else:
            t1 = min(t1, t)
        if t0 > t1:
            return None
    return (np.array([a[0] + t0 * dx, a[1] + t0 * dy]),
            np.array([a[0] + t1 * dx, a[1] + t1 * dy]))


def shade(color, factor: float):
    """Scale a BGR colour, for cheap directional lighting on wall faces."""
    return tuple(int(min(255, max(0, c * factor))) for c in color)
