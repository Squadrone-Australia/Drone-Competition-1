import cv2
import numpy as np
from comp1.vision.detector import detect_red_circle


def frame_with(shape="circle", color=(0, 0, 220), cx=320):
    img = np.full((480, 640, 3), 255, np.uint8)
    if shape == "circle":
        cv2.circle(img, (cx, 240), 60, color, -1)
    elif shape == "square":
        cv2.rectangle(img, (cx - 60, 180), (cx + 60, 300), color, -1)
    return img


def test_detects_red_circle_centre():
    det = detect_red_circle(frame_with())
    assert det.found and det.position == "center"


def test_position_left():
    det = detect_red_circle(frame_with(cx=80))
    assert det.found and det.position == "left"


def test_ignores_blue_circle():
    assert not detect_red_circle(frame_with(color=(220, 0, 0))).found


def test_ignores_red_square():
    assert not detect_red_circle(frame_with(shape="square")).found


def test_empty_frame():
    det = detect_red_circle(np.full((480, 640, 3), 255, np.uint8))
    assert not det.found and det.position == "none"
