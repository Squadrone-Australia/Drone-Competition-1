"""solution_fire: the red-circle labelling and geometry, without torch or a GPU."""

from __future__ import annotations

import math

import cv2
import numpy as np
import pytest

from solution_fire.detector import describe, range_from_keypoints
from solution_fire.geometry import (
    BOTTOM,
    CENTRE,
    FLIP_IDX,
    KPT_NAMES,
    LEFT,
    NUM_KPTS,
    RIGHT,
    TOP,
    VISIBLE,
    CameraCalibration,
    box_from_keypoints,
    project_disc,
    read_labels,
    write_labels,
)
from solution_fire.marker import MarkerTruth, aruco_dictionary, paint_out, predicted_corners
from solution_fire.synth import board_homography, rotation

W, H = 960, 720
NO_DISTORTION = CameraCalibration(distortion=[0, 0, 0, 0, 0])


def facing_pose(distance, yaw_deg=0.0, right=0.0, down=0.0):
    """rvec, tvec of a marker board square-on to the camera (turned by yaw),
    its origin ``distance`` ahead."""
    rot = rotation(yaw_deg, 0, 0)
    rvec, _ = cv2.Rodrigues(rot)
    return rvec, np.array([right, down, distance], float)


def test_label_round_trip(tmp_path):
    box = (100.0, 120.0, 300.0, 330.0)
    k = np.zeros((NUM_KPTS, 3))
    k[CENTRE] = (200, 225, VISIBLE)
    k[TOP] = (200, 120, VISIBLE)
    path = tmp_path / "a.txt"
    write_labels(path, [(box, k)], W, H)
    [(cls, box2, k2)] = read_labels(path, W, H)
    assert cls == 0
    assert np.allclose(box2, box, atol=0.01)
    assert np.allclose(k2[[CENTRE, TOP]], k[[CENTRE, TOP]], atol=0.01)
    assert (k2[[RIGHT, BOTTOM, LEFT]] == 0).all()


def test_flip_index_swaps_left_and_right_only():
    assert len(FLIP_IDX) == NUM_KPTS
    assert FLIP_IDX[RIGHT] == LEFT and FLIP_IDX[LEFT] == RIGHT
    for k in (CENTRE, TOP, BOTTOM):
        assert FLIP_IDX[k] == k
    assert KPT_NAMES[RIGHT] == "right"


def test_projected_disc_lands_where_the_pinhole_says():
    d = 2.0
    rvec, tvec = facing_pose(d)
    box, k, rim = project_disc(rvec, tvec, NO_DISTORTION, W, H, radius_m=0.1, offset_m=(0, 0.33))
    m = NO_DISTORTION.matrix(W, H)
    fx, fy, cx, cy = m[0, 0], m[1, 1], m[0, 2], m[1, 2]
    # Board y is up, image y is down: the disc sits above the marker.
    assert k[CENTRE, :2] == pytest.approx((cx, cy - fy * 0.33 / d), abs=0.5)
    assert k[TOP, 1] < k[CENTRE, 1] < k[BOTTOM, 1]
    assert k[LEFT, 0] < k[CENTRE, 0] < k[RIGHT, 0]
    assert k[RIGHT, 0] - k[LEFT, 0] == pytest.approx(fx * 0.2 / d, rel=0.01)
    assert box[2] - box[0] == pytest.approx(fx * 0.2 / d, rel=0.02)
    assert (k[:, 2] == VISIBLE).all()


def test_projected_disc_is_none_when_out_of_shot():
    rvec, tvec = facing_pose(1.0, right=3.0)
    assert project_disc(rvec, tvec, NO_DISTORTION, W, H) is None


def test_range_from_keypoints_recovers_distance_and_yaw():
    for d, yaw in ((0.6, 0.0), (1.5, 30.0), (4.0, 0.0)):
        rvec, tvec = facing_pose(d, yaw_deg=yaw, down=0.33)
        box, k, _ = project_disc(rvec, tvec, NO_DISTORTION, W, H, 0.1, (0, 0.33))
        k[:, 2] = 1.0
        rng, how, est_yaw = range_from_keypoints(k, box, NO_DISTORTION, W, H, 0.2)
        assert how == "vertical"
        assert rng == pytest.approx(d, rel=0.03)
        assert est_yaw == pytest.approx(yaw, abs=4.0)


def test_range_falls_back_to_the_box_without_rim_points():
    rvec, tvec = facing_pose(2.0, down=0.33)
    box, k, _ = project_disc(rvec, tvec, NO_DISTORTION, W, H, 0.1, (0, 0.33))
    k[:, 2] = 0.0
    rng, how, _ = range_from_keypoints(k, box, NO_DISTORTION, W, H, 0.2)
    assert how == "box"
    assert rng == pytest.approx(2.0, rel=0.05)


def test_describe_bearing_sign():
    rvec, tvec = facing_pose(2.0, right=0.5, down=0.33)
    box, k, _ = project_disc(rvec, tvec, NO_DISTORTION, W, H, 0.1, (0, 0.33))
    k[:, 2] = 1.0
    s = describe(0.9, box, k, NO_DISTORTION, W, H, 0.2)
    assert s.bearing_deg == pytest.approx(math.degrees(math.atan2(0.5, 2.0)), abs=0.5)
    assert abs(s.elevation_deg) < 0.5
    assert s.position_m[2] == pytest.approx(2.0, rel=0.03)


def test_box_from_hand_keypoints_mirrors_a_missing_rim_point():
    k = np.zeros((NUM_KPTS, 3))
    k[CENTRE] = (100, 100, VISIBLE)
    k[TOP] = (100, 60, VISIBLE)
    k[RIGHT] = (140, 100, VISIBLE)
    k[LEFT] = (60, 100, VISIBLE)
    assert box_from_keypoints(k, W, H) == pytest.approx((60, 60, 140, 140))
    k[[RIGHT, LEFT]] = 0
    assert box_from_keypoints(k, W, H) is None


def _marker_frame(side_px=180, at=(480, 400)):
    frame = np.full((H, W, 3), 235, np.uint8)
    marker = cv2.aruco.generateImageMarker(aruco_dictionary("DICT_5X5_100"), 0, side_px)
    x, y = at[0] - side_px // 2, at[1] - side_px // 2
    frame[y : y + side_px, x : x + side_px] = marker[..., None]
    return frame


def test_marker_pose_and_paint_out():
    frame = _marker_frame()
    truth = MarkerTruth(calibration=NO_DISTORTION, max_error_px=5)
    pose = truth.pose(frame)
    assert pose is not None
    fx = NO_DISTORTION.matrix(W, H)[0, 0]
    assert pose.tvec.ravel()[2] == pytest.approx(fx * 0.18 / 180, rel=0.03)
    corners = truth.detect_all(frame)
    assert len(corners) == 1
    paint_out(frame, corners[0], rng=np.random.default_rng(0))
    assert truth.detect_all(frame) == []
    # Painted with the surrounding paper, not left dark.
    assert frame[400, 480].mean() > 200


def test_predicted_corners_put_the_marker_below_the_disc():
    k = np.zeros((NUM_KPTS, 3))
    k[CENTRE] = (480, 200, VISIBLE)
    k[BOTTOM] = (480, 250, VISIBLE)  # radius 50 px for 0.1 m
    k[RIGHT] = (530, 200, VISIBLE)
    quad = predicted_corners(k, 0.1, (0.0, 0.33))
    centre = quad.mean(axis=0)
    assert centre == pytest.approx((480, 200 + 0.33 * 500))
    assert np.ptp(quad[:, 0]) == pytest.approx(0.18 * 500)


def test_synth_homography_matches_projection():
    calib = NO_DISTORTION
    m = calib.matrix(W, H)
    rot = rotation(25, 5, -3)
    t = np.array([0.2, -0.1, 1.8])
    hom = board_homography(m, rot, t)
    pts = np.array([[0.0, 0.1], [0.1, 0.0], [-0.05, -0.07]])
    via_h = cv2.perspectiveTransform(pts.reshape(-1, 1, 2), hom).reshape(-1, 2)
    rvec, _ = cv2.Rodrigues(rot)
    obj = np.column_stack((pts, np.zeros(len(pts))))
    via_p, _ = cv2.projectPoints(obj, rvec, t, m, np.zeros(5))
    assert np.allclose(via_h, via_p.reshape(-1, 2), atol=1e-6)
    # A board facing the camera has its top up the image.
    top, bottom = cv2.perspectiveTransform(
        np.array([[[0, 0.1]], [[0, -0.1]]], np.float64), board_homography(m, rotation(0, 0, 0),
                                                                          t)
    ).reshape(-1, 2)
    assert top[1] < bottom[1]
