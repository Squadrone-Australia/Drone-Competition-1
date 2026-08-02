"""Projection maths behind the simulator's 3D scenery."""

import math

import numpy as np
import pytest

from comp1.sim.scene import NEAR, Camera, _clip_near, _clip_segment_2d, draw_line, fill_quad
from comp1.vision.camera import SIM_INTRINSICS


def cam(x=2.0, y=2.0, z=1.0, heading=0.0, w=640, h=480):
    return Camera.at(SIM_INTRINSICS, x, y, z, heading, w, h)


def blank(w=640, h=480):
    return np.zeros((h, w, 3), np.uint8)


def test_point_straight_ahead_projects_to_frame_centre():
    c = cam()
    px = c.project(c.to_camera([(2.0, 5.0, 1.0)]))[0]
    assert px == pytest.approx([320, 240], abs=0.5)


def test_projection_agrees_with_the_detectors_camera_model():
    """A 25 cm disc 2 m away must span the radius CameraIntrinsics.distance_m inverts.

    The scenery and the marker billboard share this camera; if they drift apart,
    the room stops being a usable ruler for judging range by eye.
    """
    c = cam()
    left = c.project(c.to_camera([(2.0 - 0.125, 4.0, 1.0)]))[0]
    right = c.project(c.to_camera([(2.0 + 0.125, 4.0, 1.0)]))[0]
    radius_norm = (right[0] - left[0]) / 2 / c.w
    assert SIM_INTRINSICS.distance_m(radius_norm, 0.125) == pytest.approx(2.0, rel=0.01)


def test_heading_puts_the_target_on_the_expected_side():
    ahead = cam(heading=0.0)
    px = ahead.project(ahead.to_camera([(3.0, 4.0, 1.0)]))[0]
    assert px[0] > ahead.w / 2                     # target to the right of the nose
    turned = cam(heading=45.0)                     # now looking straight at it
    px = turned.project(turned.to_camera([(3.0, 3.0, 1.0)]))[0]
    assert px[0] == pytest.approx(turned.w / 2, abs=1.0)


def test_points_above_the_camera_project_above_the_centre_row():
    c = cam(z=1.0)
    assert c.project(c.to_camera([(2.0, 4.0, 2.0)]))[0][1] < c.h / 2


def test_depth_of_is_along_the_optical_axis_not_euclidean():
    c = cam(heading=0.0)
    assert c.depth_of((2.0, 4.0, 1.0)) == pytest.approx(2.0)
    assert c.depth_of((5.0, 2.0, 1.0)) == pytest.approx(0.0, abs=1e-9)   # abeam
    assert c.depth_of((2.0, 0.0, 1.0)) < 0                                # behind


def test_near_clip_trims_a_polygon_that_straddles_the_camera():
    poly = np.array([[-1.0, 0.0, -1.0], [1.0, 0.0, -1.0], [1.0, 0.0, 2.0], [-1.0, 0.0, 2.0]])
    clipped = _clip_near(poly)
    assert len(clipped) == 4
    assert clipped[:, 2].min() == pytest.approx(NEAR)


def test_geometry_entirely_behind_the_camera_is_dropped():
    c = cam(heading=0.0)
    img = blank()
    fill_quad(img, c, [(0, 0, 0), (4, 0, 0), (4, 0, 2), (0, 0, 2)], (255, 255, 255))
    draw_line(img, c, (0, 0, 0), (4, 0, 0), (255, 255, 255), 2)
    assert img.max() == 0


def test_a_wall_spanning_the_camera_plane_does_not_blow_up_coordinates():
    """Near-clipping alone leaves vertices millions of pixels out; the 2D clip is
    what keeps that from reaching OpenCV's int32 conversion."""
    c = cam(x=2.0, y=2.0, z=1.0, heading=90.0)
    img = blank()
    fill_quad(img, c, [(0, 1.999, 0), (4, 1.999, 0), (4, 1.999, 2.8), (0, 1.999, 2.8)],
              (255, 255, 255))
    assert img.max() == 255                        # drawn, and without overflowing


def test_segment_clip_keeps_the_visible_part():
    a, b = _clip_segment_2d(np.array([-100.0, 50.0]), np.array([100.0, 50.0]), 0, 0, 80, 60)
    assert a[0] == pytest.approx(0.0) and b[0] == pytest.approx(80.0)
    assert _clip_segment_2d(np.array([-5.0, -5.0]), np.array([-1.0, -1.0]), 0, 0, 80, 60) is None


def test_horizon_sits_at_the_centre_row_for_a_level_camera():
    """A level pinhole puts anything at infinity on the optical axis, so the
    floor/ceiling seam is the frame's mid-row. Off-by-one here shows up as a
    systematic elevation bias in every marker."""
    c = cam(z=1.2)
    far = c.project(c.to_camera([(2.0, 10_000.0, 1.2)]))[0]
    assert far[1] == pytest.approx(c.h / 2, abs=0.01)


def test_focal_length_tracks_frame_width():
    assert cam(w=960, h=720).focal == pytest.approx(SIM_INTRINSICS.focal_px(960))
    assert math.isclose(cam(w=640).focal / cam(w=320).focal, 2.0)
