import math

import pytest

from comp1.vision.camera import CameraIntrinsics, TELLO_INTRINSICS
from comp1.vision.config import DEFAULT_CONFIG


def test_hfov_round_trips():
    cam = CameraIntrinsics.from_hfov(70.0)
    assert cam.hfov_deg == pytest.approx(70.0)


def test_diagonal_fov_is_not_the_horizontal_fov():
    # the bug this module exists to prevent: DJI's 82.6 deg is diagonal, and
    # reading it as horizontal makes the camera far too wide
    cam = CameraIntrinsics.from_dfov(82.6, 4, 3)
    assert cam.hfov_deg == pytest.approx(70.1, abs=0.5)
    assert cam.vfov_deg(0.75) == pytest.approx(55.5, abs=0.5)


def test_focal_px_matches_the_tello_stream():
    assert TELLO_INTRINSICS.focal_px(960) == pytest.approx(684, abs=2)


def test_bearing_is_zero_at_centre_and_half_the_fov_at_the_edge():
    cam = TELLO_INTRINSICS
    assert cam.bearing_deg(0.5) == pytest.approx(0.0)
    assert cam.bearing_deg(1.0) == pytest.approx(cam.hfov_deg / 2)
    assert cam.bearing_deg(0.0) == pytest.approx(-cam.hfov_deg / 2)


def test_elevation_is_positive_above_centre():
    cam = TELLO_INTRINSICS
    assert cam.elevation_deg(0.2) > 0      # upper part of the image
    assert cam.elevation_deg(0.8) < 0
    assert cam.elevation_deg(0.5) == pytest.approx(0.0)


@pytest.mark.parametrize("d", [0.5, 1.0, 2.0, 3.5, 6.0])
def test_distance_round_trips_through_apparent_radius(d):
    cam, R = TELLO_INTRINSICS, 0.125
    assert cam.distance_m(cam.radius_norm_at(d, R), R) == pytest.approx(d)


def test_distance_is_resolution_independent():
    cam, R, d = TELLO_INTRINSICS, 0.125, 2.5
    # same physical scene, two stream sizes: the pixel radii differ, the range doesn't
    r_norm = cam.radius_norm_at(d, R)
    for width in (640, 960, 1280):
        radius_px = r_norm * width
        assert cam.distance_m(radius_px / width, R) == pytest.approx(d)


def test_max_range_exposes_the_area_gate_as_a_range_limit():
    # the default gate quietly caps a 0.25 m marker at about 4 m
    assert DEFAULT_CONFIG.max_detect_range_m == pytest.approx(4.1, abs=0.2)


def test_area_gate_can_be_derived_from_a_wanted_range():
    cam, R = TELLO_INTRINSICS, 0.175      # a larger, A3-ish marker
    gate = cam.min_area_ratio_for_range(R, 6.0)
    assert cam.max_range_m(R, gate) == pytest.approx(6.0)
