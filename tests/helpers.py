"""Builders for synthetic detections, so control-logic tests don't need pixels."""

import math

from comp1.vision.config import DEFAULT_CONFIG
from comp1.vision.detector import Detection, Target


def fake_target(distance_m=2.0, bearing_deg=0.0, elevation_deg=0.0, cfg=DEFAULT_CONFIG):
    """A Target whose geometry is self-consistent with ``cfg``'s camera."""
    cam = cfg.intrinsics
    cx = 0.5 + cam.f_norm * math.tan(math.radians(bearing_deg))
    radius_norm = cam.radius_norm_at(distance_m, cfg.marker_radius_m)
    aspect_hw = 0.75
    if abs(cx - 0.5) < cfg.center_band / 2:
        position = "center"
    else:
        position = "left" if cx < 0.5 else "right"
    return Target(
        cx=cx,
        cy=0.5 - cam.f_norm * math.tan(math.radians(elevation_deg)) / aspect_hw,
        radius_norm=radius_norm,
        area_ratio=math.pi * radius_norm**2 / aspect_hw,
        circularity=0.95,
        bearing_deg=bearing_deg,
        elevation_deg=elevation_deg,
        distance_m=distance_m,
        position=position,
    )


def seen(distance_m=2.0, bearing_deg=0.0, **kw):
    """A Detection with one visible target."""
    return Detection.of(fake_target(distance_m, bearing_deg, **kw))


def lost():
    return Detection(found=False)
