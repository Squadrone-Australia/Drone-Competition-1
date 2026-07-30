from comp1.sim.world import World, Marker, VICTIM
from comp1.sim.render import render
from comp1.vision.detector import detect_red_circle


def world_with(kind, x=2.0, y=4.0):
    return World(size_m=4.0, markers=[Marker(x, y, kind)])


def see(kind, x=2.0, y=4.0, heading=0.0):
    return detect_red_circle(render(world_with(kind, x, y), 2.0, 2.0, 1.0, heading))


def test_victim_ahead_is_detected_centre():
    det = see(VICTIM)
    assert det.found and det.position == "center"


def test_victim_to_left_reads_left():
    assert see(VICTIM, x=0.5).position == "left"


def test_distractors_not_detected():
    for kind in ("red_square", "blue_circle", "green_triangle", "yellow_square"):
        assert not see(kind).found, kind


def test_marker_behind_not_detected_and_minimap_is_not_a_false_positive():
    # victim behind the drone: only the minimap's red dot is in frame
    assert not see(VICTIM, y=0.0, heading=0.0).found


def test_apparent_size_grows_as_drone_nears():
    far = detect_red_circle(render(world_with(VICTIM), 2.0, 1.0, 1.0, 0.0))
    near = detect_red_circle(render(world_with(VICTIM), 2.0, 3.4, 1.0, 0.0))
    assert near.area_ratio > far.area_ratio > 0
