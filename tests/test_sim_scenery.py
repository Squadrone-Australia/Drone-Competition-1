import math

import pytest

from comp1.sim import scenery
from comp1.sim.drone import SimDrone
from comp1.sim.world import DESTINATION, VICTIM, Marker


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _free_spot(world):
    """A legal spot down the centre line of ``world``, clear of what is already there."""
    kept = [m for m in world.markers if m.kind != VICTIM]
    x = world.width_m / 2
    for i in range(5, int(world.depth_m * 10)):
        y = i / 10
        if scenery.is_free(x, y, world.width_m, world.depth_m, kept, world.start_xy):
            return x, y
    raise AssertionError("nowhere legal left in this corridor")


def test_catalog_offers_both_arenas():
    ids = [s["id"] for s in scenery.catalog()]
    assert ids == ["arena", "corridor"]
    assert all(s["name"] and s["description"] for s in scenery.catalog())


def test_unknown_scenery_is_an_error_not_a_silent_square_room():
    with pytest.raises(ValueError):
        scenery.build("hangar")


def test_corridor_is_long_and_narrow():
    w = scenery.build("corridor", seed=1)
    assert (w.width_m, w.depth_m) == (scenery.CORRIDOR_W_M, scenery.CORRIDOR_L_M)
    assert w.depth_m > w.width_m * 3
    assert w.name == "corridor"


def test_corridor_starts_at_one_end_and_finishes_at_the_other():
    w = scenery.build("corridor", seed=2)
    sx, sy = w.start_xy
    dest = w.destination
    assert dest is not None
    # both on the centre line, at opposite ends
    assert sx == pytest.approx(w.width_m / 2) and dest.x == pytest.approx(w.width_m / 2)
    assert sy < w.depth_m * 0.2 < w.depth_m * 0.8 < dest.y


def test_destination_looks_exactly_like_a_victim_to_the_camera():
    """§3.1: it is a red circle. The detector cannot tell them apart, on purpose."""
    from comp1.sim.render import KIND_STYLE

    assert KIND_STYLE[DESTINATION] == KIND_STYLE[VICTIM]


def test_corridor_victims_stand_free_in_the_room():
    w = scenery.build("corridor", seed=3)
    assert len(w.victims) == scenery.CORRIDOR_VICTIMS
    for m in w.victims:
        assert scenery.WALL_CLEARANCE_M <= m.x <= w.width_m - scenery.WALL_CLEARANCE_M
        assert scenery.WALL_CLEARANCE_M <= m.y <= w.depth_m - scenery.WALL_CLEARANCE_M


def test_corridor_markers_are_far_enough_apart_to_be_told_apart():
    for seed in range(12):
        w = scenery.build("corridor", seed=seed)
        placed = [(m.x, m.y) for m in w.markers]
        for i, a in enumerate(placed):
            for b in placed[i + 1 :]:
                assert _dist(a, b) >= scenery.MIN_VICTIM_SEP_M - 1e-9


def test_corridor_keeps_the_start_pad_clear():
    for seed in range(12):
        w = scenery.build("corridor", seed=seed)
        for m in w.markers:
            assert _dist((m.x, m.y), w.start_xy) >= scenery.PAD_CLEARANCE_M - 1e-9


def test_same_seed_is_the_same_corridor():
    assert (
        scenery.build("corridor", seed=9).markers
        == scenery.build("corridor", seed=9).markers
    )


def test_randomising_moves_the_victims_but_never_the_destination():
    a = scenery.build("corridor", seed=1)
    b = scenery.build("corridor", seed=2)
    assert a.victims != b.victims
    assert a.destination == b.destination


# --- hand-editing ---------------------------------------------------------


def test_with_victims_replaces_only_the_victims():
    w = scenery.build("corridor", seed=5)
    others = [m for m in w.markers if m.kind != VICTIM]
    x, y = _free_spot(w)
    edited = scenery.with_victims(w, [{"x": x, "y": y}])
    assert edited.victims == [Marker(x, y, VICTIM)]
    assert [m for m in edited.markers if m.kind != VICTIM] == others
    assert (edited.width_m, edited.depth_m, edited.start_xy) == (
        w.width_m,
        w.depth_m,
        w.start_xy,
    )


def test_a_victim_may_stand_in_the_middle_of_the_corridor_not_just_on_a_wall():
    base = scenery.build("corridor", seed=5)
    x, y = _free_spot(base)
    w = scenery.with_victims(base, [(x, y)])
    assert w.victims == [Marker(x, y, VICTIM)]
    # nowhere near a wall — that is the whole point of the corridor scenery
    assert min(x, w.width_m - x) >= scenery.WALL_CLEARANCE_M


@pytest.mark.parametrize(
    "point",
    [
        (0.1, 5.0),  # flush against the west wall
        (2.4, 5.0),  # flush against the east wall
        (1.25, 9.9),  # through the far wall
        (1.25, 0.6),  # on top of the start pad
    ],
)
def test_illegal_placements_are_dropped_not_clamped(point):
    w = scenery.with_victims(scenery.build("corridor", seed=5), [point])
    assert w.victims == []


def test_a_victim_cannot_be_stacked_on_the_destination():
    base = scenery.build("corridor", seed=5)
    dest = base.destination
    w = scenery.with_victims(base, [(dest.x, dest.y)])
    assert w.victims == []


def test_clearing_leaves_a_corridor_with_only_its_destination():
    w = scenery.with_victims(scenery.build("corridor", seed=5), [])
    assert w.victims == [] and w.destination is not None


# --- the adapter ----------------------------------------------------------


def test_sim_drone_starts_on_the_corridor_pad_not_mid_room():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    assert (d.x, d.y) == d.world.start_xy
    assert d.y < d.world.depth_m / 4


def test_corridor_walls_stop_the_drone_at_the_far_end():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    d.takeoff()
    for _ in range(20):
        d.move("forward", 100)
    assert d.y <= d.world.depth_m
    assert d.y > d.world.width_m  # it really did fly down the long axis


def test_scene_describes_a_rectangle_and_keeps_size_m_for_square_callers():
    s = SimDrone(scenery_name="corridor", seed=4, delay=0).scene()
    assert s["width_m"] == scenery.CORRIDOR_W_M
    assert s["depth_m"] == scenery.CORRIDOR_L_M
    assert s["size_m"] == s["width_m"]
    assert s["start"] == [scenery.CORRIDOR_W_M / 2, 0.6]
    assert s["name"] == "corridor"
    assert any(m["kind"] == DESTINATION for m in s["markers"])


def test_load_scenery_swaps_the_room_and_moves_the_drone_to_its_pad():
    d = SimDrone(scenery_name="arena", seed=4, delay=0)
    d.load_scenery("corridor")
    assert d.world.name == "corridor"
    assert (d.x, d.y) == d.world.start_xy


def test_load_scenery_keeps_a_seeded_session_repeatable():
    a = SimDrone(scenery_name="arena", seed=4, delay=0)
    a.load_scenery("corridor")
    b = SimDrone(scenery_name="corridor", seed=4, delay=0)
    assert a.world.markers == b.world.markers


def test_randomise_gives_a_new_layout_even_with_a_fixed_seed():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    before = list(d.world.victims)
    for _ in range(5):
        d.load_scenery(randomise=True)
        if d.world.victims != before:
            return
    raise AssertionError("randomise never produced a different layout")


def test_editing_victims_does_not_change_scenery():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    x, y = _free_spot(d.world)
    d.load_scenery(victims=[{"x": x, "y": y}])
    assert d.world.name == "corridor"
    assert d.world.victims == [Marker(x, y, VICTIM)]
    assert d.world.destination is not None


def test_mock_and_tello_have_no_arena_to_author():
    from comp1.drone.mock import MockDrone

    assert MockDrone().scenery_catalog() is None
    assert MockDrone().load_scenery("corridor") is None
