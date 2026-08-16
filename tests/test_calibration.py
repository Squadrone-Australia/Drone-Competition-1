import cv2
import numpy as np
import pytest

from comp1.sim.render import render
from comp1.sim.world import FIRE, Marker, World
from comp1.vision.calibration import (
    CalibrationError,
    auto_suggest_hsv,
    check_coverage,
    config_with_hsv,
    draw_calibration_preview,
    find_marker_roi,
    suggest_hsv,
)
from comp1.vision.config import DEFAULT_CONFIG, VisionConfig
from comp1.vision.detector import detect_red_circle


def test_red_sample_suggests_two_wrapped_hue_bands():
    frame = np.full((200, 200, 3), 255, np.uint8)
    cv2.circle(frame, (100, 100), 50, (0, 0, 220), -1)

    values = suggest_hsv(frame, [0.35, 0.35, 0.65, 0.65])

    assert values["lower1"][0] == 0
    assert values["upper1"][0] <= 10
    assert values["lower2"][0] >= 170
    candidate = config_with_hsv(DEFAULT_CONFIG, values)
    assert detect_red_circle(frame, candidate).found


def test_hue_wraparound_does_not_expand_to_most_of_the_spectrum():
    hsv = np.zeros((100, 100, 3), np.uint8)
    hsv[:, :50] = (179, 220, 200)
    hsv[:, 50:] = (1, 220, 200)
    frame = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)

    values = suggest_hsv(frame, [0, 0, 1, 1])

    widths = [
        values["upper1"][0] - values["lower1"][0],
        values["upper2"][0] - values["lower2"][0],
    ]
    assert max(widths) < 15
    assert values["lower1"][0] == 0 and values["upper2"][0] == 180


def test_sample_rejects_a_colourless_region():
    white = np.full((100, 100, 3), 255, np.uint8)
    with pytest.raises(CalibrationError, match="too little colour"):
        suggest_hsv(white, [0.1, 0.1, 0.9, 0.9])


def test_config_rejects_inverted_bounds():
    values = {
        "lower1": [20, 100, 80],
        "upper1": [10, 255, 255],
        "lower2": [170, 100, 80],
        "upper2": [180, 255, 255],
    }
    with pytest.raises(CalibrationError, match="lower1"):
        config_with_hsv(DEFAULT_CONFIG, values)


def test_preview_keeps_frame_shape_and_marks_the_accepted_area():
    frame = np.full((100, 120, 3), 255, np.uint8)
    cv2.circle(frame, (60, 50), 20, (0, 0, 220), -1)
    preview = draw_calibration_preview(frame, DEFAULT_CONFIG)
    assert preview.shape == frame.shape
    assert preview[50, 60].mean() > preview[5, 5].mean()


def test_auto_roi_lands_inside_the_marker():
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0
    # every corner of the box must be a red pixel of the disc, not the rim
    for x in (x0, x1):
        for y in (y0, y1):
            b, g, r = frame[int(y * 480), int(x * 640)]
            assert r > 150 and b < 80, f"corner ({x:.3f}, {y:.3f}) is not marker red"


def test_auto_roi_is_square_in_pixels_not_in_normalised_units():
    """radius_norm is normalised by width; the ROI's y bounds are scaled by
    height. Equal normalised half-extents would give a box a third too short."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert (x1 - x0) * 640 == pytest.approx((y1 - y0) * 480, rel=0.02)


def test_auto_roi_refuses_a_frame_with_no_red_marker():
    frame = np.full(
        (480, 640, 3), (90, 70, 60), np.uint8
    )  # blue-dominant, like scenery
    with pytest.raises(CalibrationError, match="no red marker"):
        find_marker_roi(frame)


def test_auto_roi_refuses_an_empty_frame():
    with pytest.raises(CalibrationError):
        find_marker_roi(None)


def test_find_marker_roi_honours_the_operators_active_config():
    """A tightened min_area_ratio must gate the locator too, or the locator
    can lock onto a blob the operator's own detector would never accept."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)  # area_ratio ~= 0.037

    too_tight = VisionConfig(min_area_ratio=0.5)
    with pytest.raises(CalibrationError, match="no red marker"):
        find_marker_roi(frame, too_tight)

    x0, y0, x1, y1 = find_marker_roi(frame, DEFAULT_CONFIG)
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_find_marker_roi_ignores_the_operators_own_colour_bands():
    """The four colour fields are always overridden by _PRIOR_BANDS, so a
    useless (here, blue-only) operator config must still locate a red marker."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)

    blue_only = VisionConfig(
        lower1=(100, 80, 70),
        upper1=(130, 255, 255),
        lower2=(100, 80, 70),
        upper2=(130, 255, 255),
    )
    x0, y0, x1, y1 = find_marker_roi(frame, blue_only)
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_auto_roi_is_still_usable_for_a_distant_marker():
    """The area gate implies a radius of ~14 px, which must still clear
    suggest_hsv's 25-pixel and 0.01-normalised-extent floors."""
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 16, (0, 0, 220), -1)

    x0, y0, x1, y1 = find_marker_roi(frame)

    assert x1 - x0 > 0.01 and y1 - y0 > 0.01
    assert suggest_hsv(frame, [x0, y0, x1, y1])["lower1"][0] == 0


def test_coverage_gate_rejects_bands_that_match_most_of_the_scene():
    frame = np.full((100, 100, 3), (0, 0, 220), np.uint8)  # all red
    with pytest.raises(CalibrationError, match="too much of the scene"):
        check_coverage(frame, DEFAULT_CONFIG)


def test_coverage_gate_accepts_an_isolated_marker():
    frame = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(frame, (320, 240), 60, (0, 0, 220), -1)
    assert check_coverage(frame, DEFAULT_CONFIG) is None


def test_auto_calibration_recovers_a_marker_the_defaults_miss():
    """The point of the feature: a marker under a light level the shipped
    bands reject is recovered without anyone touching a slider."""
    frame = np.full((480, 640, 3), (90, 70, 60), np.uint8)
    # Dim red, deliberately just under whatever value floor the defaults ship
    # with. Derived rather than hardcoded: this test asserts "below the floor",
    # not "below 80", and tightening or loosening config.py must not silently
    # turn it into an assertion about something else.
    dim = DEFAULT_CONFIG.lower1[2] - 5
    cv2.circle(frame, (320, 240), 60, (dim // 6, dim // 6, dim), -1)

    assert not detect_red_circle(frame, DEFAULT_CONFIG).found

    values, roi = auto_suggest_hsv(frame)

    assert detect_red_circle(frame, config_with_hsv(DEFAULT_CONFIG, values)).found
    assert len(roi) == 4


def test_auto_calibration_on_a_simulated_fire_round_trips():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, FIRE)])
    frame = render(world, 2.0, 2.0, 1.0, 0.0)

    values, _roi = auto_suggest_hsv(frame)

    assert detect_red_circle(frame, config_with_hsv(DEFAULT_CONFIG, values)).found


def test_auto_calibration_never_calibrates_on_scenery():
    """The wide prior must not open a door the detector's own bands keep shut.
    Sweep rather than spot-check: corners and floor-facing views shade
    differently, and any one of them becoming 'a red marker' is the failure."""
    world = World(size_m=4.0, markers=[])
    for x in (0.3, 2.0, 3.7):
        for y in (0.3, 2.0, 3.7):
            for heading in range(0, 360, 60):
                frame = render(world, x, y, 1.0, heading)
                with pytest.raises(CalibrationError, match="no red marker"):
                    auto_suggest_hsv(frame)


def test_auto_calibrated_bands_do_not_flag_scenery_elsewhere_in_the_arena():
    """Bands fitted at one pose must still reject the room at every other."""
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, FIRE)])
    values, _roi = auto_suggest_hsv(render(world, 2.0, 2.0, 1.0, 0.0))
    candidate = config_with_hsv(DEFAULT_CONFIG, values)

    empty = World(size_m=4.0, markers=[])
    for x in (0.3, 2.0, 3.7):
        for y in (0.3, 2.0, 3.7):
            for heading in range(0, 360, 60):
                det = detect_red_circle(render(empty, x, y, 1.0, heading), candidate)
                assert not det.found, f"scenery detected at {x},{y} hdg {heading}"
