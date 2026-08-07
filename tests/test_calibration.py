import cv2
import numpy as np
import pytest

from comp1.vision.calibration import (CalibrationError, config_with_hsv,
                                      draw_calibration_preview, suggest_hsv)
from comp1.vision.config import DEFAULT_CONFIG
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

    widths = [values["upper1"][0] - values["lower1"][0],
              values["upper2"][0] - values["lower2"][0]]
    assert max(widths) < 15
    assert values["lower1"][0] == 0 and values["upper2"][0] == 180


def test_sample_rejects_a_colourless_region():
    white = np.full((100, 100, 3), 255, np.uint8)
    with pytest.raises(CalibrationError, match="too little colour"):
        suggest_hsv(white, [0.1, 0.1, 0.9, 0.9])


def test_config_rejects_inverted_bounds():
    values = {
        "lower1": [20, 100, 80], "upper1": [10, 255, 255],
        "lower2": [170, 100, 80], "upper2": [180, 255, 255],
    }
    with pytest.raises(CalibrationError, match="lower1"):
        config_with_hsv(DEFAULT_CONFIG, values)


def test_preview_keeps_frame_shape_and_marks_the_accepted_area():
    frame = np.full((100, 120, 3), 255, np.uint8)
    cv2.circle(frame, (60, 50), 20, (0, 0, 220), -1)
    preview = draw_calibration_preview(frame, DEFAULT_CONFIG)
    assert preview.shape == frame.shape
    assert preview[50, 60].mean() > preview[5, 5].mean()
