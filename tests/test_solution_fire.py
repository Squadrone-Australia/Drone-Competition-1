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


# --- markerless drafting (the drone-cage footage of 2026-11-11) -------------------------


def _scene(disc_bgr, sheet_bgr=(200, 200, 200), size=(H, W)):
    """A grey sheet with one filled disc on it, centre (480, 360), radius 80."""
    img = np.full((*size, 3), 90, np.uint8)
    cv2.rectangle(img, (370, 230), (590, 490), sheet_bgr, -1)
    cv2.circle(img, (480, 360), 80, disc_bgr, -1, cv2.LINE_AA)
    return img


class _FakeModel:
    """Stands in for FireDetector: one detection at ``box`` with ``score``."""

    def __init__(self, box=None, score=0.9):
        self.box, self.score = box, score

    def detect(self, frame):
        if self.box is None:
            return []
        from types import SimpleNamespace

        x0, y0, x1, y1 = self.box
        k = np.array([[(x0 + x1) / 2, (y0 + y1) / 2, VISIBLE], [(x0 + x1) / 2, y0, VISIBLE],
                      [x1, (y0 + y1) / 2, VISIBLE], [(x0 + x1) / 2, y1, VISIBLE],
                      [x0, (y0 + y1) / 2, VISIBLE]], float)
        return [SimpleNamespace(score=self.score, box=self.box,
                                keypoints_labelled=lambda: k.copy())]


def test_chroma_mask_keeps_a_backlit_red_disc_and_drops_black_print():
    from solution_fire.autolabel import chroma_mask, red_mask

    # Colours measured in the cage footage (BGR).
    backlit_red = _scene((0, 4, 28), sheet_bgr=(60, 60, 60))
    assert chroma_mask(backlit_red)[360, 480] == 255
    assert red_mask(backlit_red)[360, 480] == 0  # the old HSV mask lost it
    assert chroma_mask(_scene((0, 30, 35)))[360, 480] == 0  # the black disc decoy
    assert chroma_mask(_scene((0, 1, 9)))[360, 480] == 0  # crushed black


def test_chroma_discs_local_split_ignores_red_wood_beside_the_disc():
    from solution_fire.autolabel import chroma_discs

    img = _scene((0, 21, 153), sheet_bgr=(200, 200, 200))
    img[230:490, 594:700] = (0, 15, 70)  # varnished wood, red by ratio, just past the sheet
    roi = (380, 260, 620, 460)
    found = chroma_discs(img, roi=roi)
    assert found, "the disc should be found in the crop"
    x, y, w, h = cv2.boundingRect(found[0][0])
    assert abs(x + w / 2 - 480) < 4 and abs(w - 160) < 8


def test_chroma_discs_opening_cuts_a_pole_bridged_to_the_disc():
    from solution_fire.autolabel import chroma_discs

    img = _scene((0, 21, 153))
    cv2.line(img, (380, 150), (380, 600), (0, 30, 200), 8)  # the stand's red pole
    cv2.line(img, (380, 360), (405, 360), (0, 30, 200), 4)  # welded to the disc
    roi = (360, 250, 600, 470)
    assert not chroma_discs(img, roi=roi)  # one blob, not round
    found = chroma_discs(img, roi=roi, open_px=19)
    assert found
    x, _, w, _ = cv2.boundingRect(found[0][0])
    assert abs(x + w / 2 - 480) < 5


def test_markerless_draft_auto_when_network_and_blob_agree():
    from solution_fire.autolabel import markerless_draft

    img = _scene((0, 21, 153))
    discs, status, info = markerless_draft(img, _FakeModel((402, 282, 558, 438)), 0.8)
    assert status == "auto" and info["iou"] > 0.85
    box, k = discs[0]
    # The label is the blob's ellipse: rim points on the drawn circle.
    assert abs(k[TOP, 1] - 280) < 3 and abs(k[BOTTOM, 1] - 440) < 3
    assert abs(k[LEFT, 0] - 400) < 3 and abs(k[RIGHT, 0] - 560) < 3


def test_markerless_draft_pending_without_agreement():
    from solution_fire.autolabel import markerless_draft

    img = _scene((0, 21, 153))
    # A weak detection: drafted, but a person looks at it.
    _, status, _ = markerless_draft(img, _FakeModel((402, 282, 558, 438), score=0.3), 0.8)
    assert status == "pending"
    # No detection at all: the blob alone drafts it.
    discs, status, _ = markerless_draft(img, _FakeModel(None), 0.8)
    assert status == "pending" and len(discs) == 1
    # Nothing red: pending with no draft, for the reviewer to call empty or missed.
    discs, status, _ = markerless_draft(_scene((60, 160, 60)), _FakeModel(None), 0.8)
    assert status == "pending" and discs == []


def test_synth_black_ink_and_netting():
    from solution_fire.synth import NEGATIVES, ink, netting

    assert "black_circle" in dict(NEGATIVES)
    rng = np.random.default_rng(0)
    for _ in range(50):
        b, g, r = ink("black_circle", rng)
        assert max(b, g, r) <= 0.22 * 255 + 1
        # Never red: green keeps up with red, as the Tello renders black.
        assert r <= 1.6 * g + 8
    image = np.full((120, 160, 3), 200, np.float32)
    where = np.zeros((120, 160), np.float32)
    where[20:100, 40:120] = 1
    out = netting(image, where, rng)
    assert np.array_equal(out[where == 0], image[where == 0])
    assert out[where == 1].min() < 150  # strands drawn across the sheet
