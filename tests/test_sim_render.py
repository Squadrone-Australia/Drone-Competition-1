import numpy as np
import pytest

from comp1.sim.render import render
from comp1.sim.world import VICTIM, Marker, World
from comp1.vision.detector import detect_red_circle


def world_with(kind, x=2.0, y=4.0):
    return World(size_m=4.0, markers=[Marker(x, y, kind)])


def see(kind, x=2.0, y=4.0, heading=0.0):
    return detect_red_circle(render(world_with(kind, x, y), 2.0, 2.0, 1.0, heading))


def test_victim_ahead_is_detected_centre():
    det = see(VICTIM)
    assert det.found and det.position == "center"


def test_victim_to_left_reads_left():
    # ~27 deg off the nose: inside the real Tello's ~70 deg horizontal FOV
    assert see(VICTIM, x=1.0).position == "left"


def test_marker_outside_the_real_camera_fov_is_not_seen():
    # ~37 deg off the nose — visible under the old 83 deg sim camera, but past
    # the edge of the actual hardware's frame
    assert not see(VICTIM, x=0.5).found


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


# --- 3D scenery -----------------------------------------------------------


def empty_room(size_m=4.0):
    return World(size_m=size_m, markers=[])


def test_scenery_alone_is_never_a_victim():
    """The room fills nearly every pixel, so a warm-toned wall would be a
    false positive on every frame. Sweep the arena rather than spot-check one
    pose — corners, floor-facing and ceiling-facing views all shade differently.
    """
    world = empty_room()
    for x in (0.3, 2.0, 3.7):
        for y in (0.3, 2.0, 3.7):
            for heading in range(0, 360, 30):
                for z in (0.3, 1.0, 2.4):
                    det = detect_red_circle(render(world, x, y, z, heading))
                    assert not det.found, (
                        f"scenery detected at {x},{y},{z} hdg {heading}"
                    )


def empty_corridor():
    from comp1.sim import scenery

    return World(
        size_m=scenery.CORRIDOR_W_M,
        markers=[],
        length_m=scenery.CORRIDOR_L_M,
        start=(1.25, 0.6),
        name="corridor",
    )


def test_corridor_scenery_alone_is_never_a_victim():
    """The same sweep for the rectangular room.

    A corridor is not the square arena with different numbers: the walls are at
    steeper angles, the far one is 10 m away rather than 4, and the ceiling
    strip lights repeat down its length. All of it is new area for the red
    detector to trip over.
    """
    world = empty_corridor()
    for x in (0.3, 1.25, 2.2):
        for y in (0.3, 2.5, 5.0, 7.5, 9.7):
            for heading in range(0, 360, 30):
                for z in (0.3, 1.0, 2.4):
                    det = detect_red_circle(render(world, x, y, z, heading))
                    assert not det.found, (
                        f"scenery detected at {x},{y},{z} hdg {heading}"
                    )


def test_a_victim_down_the_corridor_measures_true():
    """The whole exercise is flying a long way to something small, so the range
    estimate has to survive being made at range."""
    world = empty_corridor()
    world.markers = [Marker(1.25, 4.0, VICTIM)]
    det = detect_red_circle(render(world, 1.25, 1.0, 1.0, 0.0))
    assert det.found and det.distance_m == pytest.approx(3.0, rel=0.1)


def test_the_destination_sign_reads_exactly_like_a_victim():
    """§3.1 and the corridor's whole premise: the detector cannot tell them apart."""
    from comp1.sim.world import DESTINATION

    victim = detect_red_circle(render(world_with(VICTIM), 2.0, 2.0, 1.0, 0.0))
    dest = detect_red_circle(render(world_with(DESTINATION), 2.0, 2.0, 1.0, 0.0))
    assert victim.found and dest.found
    assert dest.distance_m == pytest.approx(victim.distance_m)
    assert dest.bearing_deg == pytest.approx(victim.bearing_deg)


def test_the_room_is_actually_drawn():
    """Guards against the projection silently clipping everything away, which
    would look like the old flat two-band background and pass every other test."""
    img = render(empty_room(), 2.0, 2.0, 1.0, 20.0)
    assert len(np.unique(img.reshape(-1, 3), axis=0)) > 4


def test_the_view_changes_as_the_drone_moves_with_no_marker_in_sight():
    """Perspective, not paint: with an empty room the old renderer produced the
    identical frame from every pose, so `fly forward` looked like a no-op."""
    world = empty_room()
    near_wall = render(world, 2.0, 3.5, 1.0, 0.0)
    mid_room = render(world, 2.0, 1.0, 1.0, 0.0)
    assert not np.array_equal(near_wall, mid_room)


def test_a_marker_is_still_measurable_against_the_new_background():
    det = detect_red_circle(render(world_with(VICTIM), 2.0, 2.0, 1.0, 0.0))
    assert det.found and det.distance_m == pytest.approx(2.0, rel=0.1)


def test_markers_are_drawn_over_the_scenery_not_behind_it():
    plain = detect_red_circle(render(empty_room(), 2.0, 2.0, 1.0, 0.0))
    with_marker = detect_red_circle(render(world_with(VICTIM), 2.0, 2.0, 1.0, 0.0))
    assert not plain.found and with_marker.found
