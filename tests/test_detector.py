import cv2
import numpy as np

from comp1.vision.config import DEFAULT_CONFIG
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


# --- surviving a mask the cage's lighting has damaged ----------------------
#
# The simulator renders flawless discs, so none of the cases below can arise
# from it. They are hand-built to stand in for what a real frame does to the
# mask: shadow across the marker, glare on its rim, and a ragged edge from
# compression. Each one used to fail the raw-perimeter circularity gate.


def marker(radius=60):
    img = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(img, (320, 240), radius, (0, 0, 220), -1)
    return img


def test_marker_bitten_by_glare_on_the_rim_is_still_found():
    """Highlights wash the rim past the saturation floor, taking notches out of
    the mask. Perimeter reacts to a notch far more than area does, so this is
    what a perimeter-based gate rejects while the marker is plainly visible."""
    img = marker()
    for angle in (0, 90, 180, 270):
        x = 320 + int(58 * np.cos(np.radians(angle)))
        y = 240 + int(58 * np.sin(np.radians(angle)))
        cv2.circle(img, (x, y), 11, (255, 255, 255), -1)  # blown-out highlight
    assert detect_red_circle(img).found


def test_marker_with_a_shadow_across_it_is_still_found():
    """A shadow band drops part of the disc below the value floor, splitting a
    chunk off the mask. The hull spans it; the raw contour did not."""
    img = marker()
    cv2.rectangle(img, (268, 264), (372, 318), (60, 55, 50), -1)
    assert detect_red_circle(img).found


def test_a_ragged_edge_does_not_reject_the_marker():
    """Low-bitrate H.264 leaves a chewed boundary on a small blob. Deterministic
    bites rather than random noise so a failure is reproducible."""
    img = marker()
    for i in range(24):
        angle = i * 15
        x = 320 + int(60 * np.cos(np.radians(angle)))
        y = 240 + int(60 * np.sin(np.radians(angle)))
        cv2.circle(img, (x, y), 4 if i % 2 else 6, (255, 255, 255), -1)
    assert detect_red_circle(img).found


def test_half_a_marker_is_still_rejected():
    """The repair must not become blind. Half a disc is genuinely ambiguous and
    has to stay rejected, or the gates have simply been switched off."""
    img = marker()
    cv2.rectangle(img, (320, 160), (400, 320), (255, 255, 255), -1)
    assert not detect_red_circle(img).found


# --- shapes and sizes that are not the marker ------------------------------


def test_ignores_a_red_star():
    """The convex hull's blind spot: a star hulls into a near-circular polygon
    and clears the circularity gate. Solidity is the only thing that sees it."""
    img = np.full((480, 640, 3), 255, np.uint8)
    pts = []
    for i in range(10):
        r = 90 if i % 2 == 0 else 36
        a = np.radians(i * 36 - 90)
        pts.append([320 + int(r * np.cos(a)), 240 + int(r * np.sin(a))])
    cv2.fillPoly(img, [np.array(pts, np.int32)], (0, 0, 220))
    assert not detect_red_circle(img).found


def test_ignores_a_red_blob_too_large_to_be_a_marker():
    """Apparent size is the only input to range, so an unbounded blob reads as
    very close and sorts to the front of the candidate list. This is the red
    jacket beyond the cage net."""
    assert not detect_red_circle(marker(radius=230)).found


def test_a_marker_at_the_approach_stop_distance_is_still_seen():
    """The size cap must sit outside the operating envelope. At
    approach_stop_distance_m the marker is ~13% of the frame; a cap set below
    that would blind the drone at the exact moment it arrives."""
    cfg = DEFAULT_CONFIG
    radius_norm = cfg.intrinsics.radius_norm_at(
        cfg.approach_stop_distance_m, cfg.marker_radius_m
    )
    det = detect_red_circle(marker(radius=int(radius_norm * 640)))
    assert det.found
    assert det.area_ratio < cfg.max_area_ratio


def test_two_adjacent_markers_do_not_fuse_into_one_blob():
    """MORPH_CLOSE dilates before it erodes, so an oversized kernel would merge
    neighbouring markers into a single blob that then fails the shape gates --
    losing both, not one."""
    img = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(img, (280, 240), 40, (0, 0, 220), -1)
    cv2.circle(img, (390, 240), 40, (0, 0, 220), -1)
    assert detect_red_circle(img).count == 2


def test_a_marker_in_shadow_clears_the_value_floor():
    """Uneven cage lighting, the plain case: the same sheet of paper in a dim
    corner. The shipped floors have to reach it without on-site calibration."""
    img = np.full((480, 640, 3), (90, 80, 75), np.uint8)
    cv2.circle(img, (320, 240), 60, (12, 12, 95), -1)  # HSV value ~95
    assert detect_red_circle(img).found
