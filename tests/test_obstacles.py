"""The obstacle detector: everything that is not an accepted red circle.

Frames are hand-built with OpenCV rather than rendered, exactly as in
``test_detector.py`` — the simulator draws flawless flat shapes, so it cannot
exercise the interesting cases on its own.
"""

import cv2
import numpy as np

from comp1.vision.config import DEFAULT_CONFIG, VisionConfig
from comp1.vision.detector import detect_red_circle
from comp1.vision.obstacles import find_obstacles, is_in_the_way

RED = (0, 0, 220)
GREEN = (60, 180, 0)
BLUE = (220, 60, 0)
YELLOW = (0, 210, 230)


def canvas():
    return np.full((480, 640, 3), 255, np.uint8)


def circle(color=RED, cx=320, r=60):
    img = canvas()
    cv2.circle(img, (cx, 240), r, color, -1)
    return img


def square(color=RED, cx=320, half=60):
    img = canvas()
    cv2.rectangle(img, (cx - half, 240 - half), (cx + half, 240 + half), color, -1)
    return img


def triangle(color=GREEN, cx=320, r=70):
    img = canvas()
    pts = np.array([[cx, 240 - r], [cx - r, 240 + r], [cx + r, 240 + r]])
    cv2.fillPoly(img, [pts], color)
    return img


# --- the core rule: not a red circle => obstacle ---------------------------


def test_red_square_is_an_obstacle():
    obstacles = find_obstacles(square(RED))
    assert len(obstacles) == 1
    assert obstacles[0].color == "red"
    assert obstacles[0].shape == "square"


def test_green_triangle_is_an_obstacle():
    obstacles = find_obstacles(triangle(GREEN))
    assert len(obstacles) == 1
    assert obstacles[0].color == "green"
    assert obstacles[0].shape == "triangle"


def test_blue_circle_is_an_obstacle():
    # Right shape, wrong colour — the marker detector rejects it, so it is
    # something in the way like anything else the drone cannot identify.
    obstacles = find_obstacles(circle(BLUE))
    assert len(obstacles) == 1
    assert obstacles[0].color == "blue"


def test_yellow_square_is_an_obstacle():
    obstacles = find_obstacles(square(YELLOW))
    assert len(obstacles) == 1
    assert obstacles[0].color == "yellow"


def test_a_red_circle_is_a_target_and_never_an_obstacle():
    det = detect_red_circle(circle(RED))
    assert det.found
    assert det.obstacles == []


def test_a_target_and_an_obstacle_are_told_apart_in_one_frame():
    img = canvas()
    cv2.circle(img, (180, 240), 55, RED, -1)  # the marker
    cv2.rectangle(img, (420, 180), (540, 300), GREEN, -1)  # something else
    det = detect_red_circle(img)
    assert det.found and det.count == 1
    assert det.obstacle_count == 1
    assert det.obstacle.color == "green"


# --- what must NOT be reported ---------------------------------------------


def test_an_empty_frame_has_no_obstacles():
    assert find_obstacles(canvas()) == []


def test_low_saturation_scenery_is_not_an_obstacle():
    # The whole approach rests on this: walls and floor are blue-dominant and
    # desaturated (see comp1/sim/render.py), so they fall under obstacle_sat_min.
    # If this ever fails, the palette moved and the detector will flag the room.
    for bgr in [(120, 105, 100), (150, 140, 132), (90, 80, 74)]:
        wall = np.full((480, 640, 3), bgr, np.uint8)
        assert find_obstacles(wall) == [], bgr


def test_a_speck_is_not_an_obstacle():
    assert find_obstacles(circle(GREEN, r=6)) == []


# --- geometry ---------------------------------------------------------------


def test_position_and_bearing_follow_the_camera():
    left = find_obstacles(square(GREEN, cx=140))[0]
    right = find_obstacles(square(GREEN, cx=500))[0]
    assert left.position == "left" and left.bearing_deg < 0
    assert right.position == "right" and right.bearing_deg > 0
    assert find_obstacles(square(GREEN, cx=320))[0].position == "center"


def test_range_uses_the_configured_obstacle_size():
    frame = square(GREEN)
    near = find_obstacles(frame)[0]
    # Twice the assumed printed size reads as twice as far away.
    big = VisionConfig(obstacle_diameter_m=DEFAULT_CONFIG.obstacle_diameter_m * 2)
    far = find_obstacles(frame, big)[0]
    assert far.distance_m == 2 * near.distance_m


def test_obstacles_come_back_nearest_first():
    img = canvas()
    cv2.rectangle(img, (60, 200), (120, 260), GREEN, -1)  # small => far
    cv2.rectangle(img, (400, 140), (560, 300), BLUE, -1)  # large => near
    obstacles = find_obstacles(img)
    assert [o.color for o in obstacles] == ["blue", "green"]


# --- the documented fail-safe ----------------------------------------------


def test_a_half_seen_marker_is_treated_as_an_obstacle():
    """A marker cut off by the frame edge fails the target shape gates.

    It then lands here, and that is the safe direction: the drone gives a
    half-seen thing a wide berth rather than flying at it. Deliberate — if this
    flips, something has quietly loosened the target gates.
    """
    img = canvas()
    cv2.circle(img, (320, 240), 70, RED, -1)
    cv2.rectangle(img, (320, 0), (640, 480), (255, 255, 255), -1)  # slice it in half
    det = detect_red_circle(img)
    assert not det.found
    assert det.obstacle_count == 1


# --- "in the way" -----------------------------------------------------------


def test_in_the_way_needs_both_close_and_central():
    cfg = DEFAULT_CONFIG
    close_centre = find_obstacles(square(GREEN, cx=320, half=90))[0]
    assert close_centre.distance_m <= cfg.obstacle_clear_distance_m
    assert is_in_the_way(close_centre, cfg)

    # Same size, off to one side: seen, but not blocking.
    off_to_the_side = find_obstacles(square(GREEN, cx=80, half=70))[0]
    assert not is_in_the_way(off_to_the_side, cfg)

    # Dead centre but far away: seen, but not blocking either.
    far_ahead = find_obstacles(square(GREEN, cx=320, half=18))[0]
    assert far_ahead.distance_m > cfg.obstacle_clear_distance_m
    assert not is_in_the_way(far_ahead, cfg)


def test_nothing_is_never_in_the_way():
    assert not is_in_the_way(None)
