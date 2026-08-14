import pytest

from comp1.sim.render import render
from comp1.sim.world import VICTIM, Marker, World
from comp1.vision.config import DEFAULT_CONFIG, VisionConfig
from comp1.vision.detector import detect_red_circle

VICTIM_Y = 4.0


def look_from(distance_m, cfg=DEFAULT_CONFIG, marker=None):
    """Render a victim head-on at ``distance_m`` and detect it."""
    m = marker or Marker(2.0, VICTIM_Y, VICTIM)
    world = World(size_m=8.0, markers=[m])
    frame = render(world, 2.0, VICTIM_Y - distance_m, m.height_m, 0.0)
    return detect_red_circle(frame, cfg)


@pytest.mark.parametrize("truth", [1.0, 1.5, 2.0, 2.5, 3.0, 3.5])
def test_estimated_range_matches_ground_truth(truth):
    det = look_from(truth)
    assert det.found
    assert det.distance_m == pytest.approx(truth, rel=0.06)


def test_bearing_is_near_zero_for_a_marker_dead_ahead():
    assert look_from(2.0).bearing_deg == pytest.approx(0.0, abs=1.0)


def test_elevation_is_near_zero_at_marker_height():
    assert look_from(2.0).elevation_deg == pytest.approx(0.0, abs=1.0)


def test_marker_above_the_camera_reads_positive_elevation():
    world = World(size_m=8.0, markers=[Marker(2.0, VICTIM_Y, VICTIM)])
    low = detect_red_circle(render(world, 2.0, 2.0, 0.4, 0.0))  # flying below it
    assert low.found and low.elevation_deg > 0


def test_range_estimate_is_unaffected_by_bearing():
    """A marker off to one side is not reported as further away than it is."""
    world = World(size_m=8.0, markers=[Marker(2.0, VICTIM_Y, VICTIM)])
    ahead = detect_red_circle(render(world, 2.0, 2.0, 1.0, 0.0))
    # shift sideways so the same marker sits well off the optical axis
    off = detect_red_circle(render(world, 1.2, 2.0, 1.0, 0.0))
    assert ahead.found and off.found
    assert off.distance_m == pytest.approx(off.target.distance_m)
    assert off.distance_m > ahead.distance_m  # genuinely further away now


def test_detection_dies_past_the_area_gate_range():
    limit = DEFAULT_CONFIG.max_detect_range_m
    assert look_from(limit * 0.85).found
    assert not look_from(limit * 1.15).found


def test_a_larger_marker_extends_usable_range():
    """The fix for the ~4 m ceiling is physical, not algorithmic."""
    big = Marker(2.0, VICTIM_Y, VICTIM, size_m=0.5)
    cfg = VisionConfig(marker_diameter_m=0.5)
    assert cfg.max_detect_range_m > DEFAULT_CONFIG.max_detect_range_m * 1.9
    det = look_from(6.0, cfg=cfg, marker=big)
    assert det.found and det.distance_m == pytest.approx(6.0, rel=0.06)
