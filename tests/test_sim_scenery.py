import math
from pathlib import Path

import pytest

from comp1.sim import scenery
from comp1.sim.drone import SimDrone
from comp1.sim.world import FIRE, Marker


def _dist(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _free_spot(world):
    """A legal spot down the centre line of ``world``, clear of what is already there."""
    kept = [m for m in world.markers if m.kind != FIRE]
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


def test_corridor_has_the_competition_dimensions():
    w = scenery.build("corridor", seed=1)
    assert (w.width_m, w.depth_m) == (scenery.CORRIDOR_W_M, scenery.CORRIDOR_L_M)
    assert (w.width_m, w.depth_m, w.room_height_m) == (6.0, 4.0, 3.0)
    assert w.name == "corridor"


def test_corridor_starts_at_the_specified_takeoff_position():
    w = scenery.build("corridor", seed=2)
    assert w.start_xy == (3.0, 3.0)
    assert w.destination is None
    assert w.return_to_start is True


def test_competition_decoys_have_the_specified_shapes_and_colours():
    from comp1.sim.render import KIND_STYLE

    assert KIND_STYLE["green_triangle"][0] == "triangle"
    assert KIND_STYLE["green_circle"][0] == "circle"
    assert KIND_STYLE["black_square"][0] == "square"
    assert KIND_STYLE["black_triangle"][0] == "triangle"


def test_corridor_fires_stand_free_in_the_room():
    w = scenery.build("corridor", seed=3)
    assert len(w.fires) == scenery.CORRIDOR_FIRES
    assert [(m.x, m.y) for m in w.fires] == [(0.75, 3.25), (5.25, 3.25), (3.0, 0.5)]
    for m in w.fires:
        assert m.height_m == 2.0
        assert scenery.WALL_CLEARANCE_M <= m.x <= w.width_m - scenery.WALL_CLEARANCE_M
        assert scenery.WALL_CLEARANCE_M <= m.y <= w.depth_m - scenery.WALL_CLEARANCE_M


def test_corridor_has_the_four_fixed_decoys():
    w = scenery.build("corridor")
    decoys = [(m.x, m.y, m.kind, m.height_m) for m in w.markers if m.kind != FIRE]
    assert decoys == [
        (0.75, 1.50, "green_triangle", 2.0),
        (5.25, 1.50, "black_square", 2.0),
        (1.75, 0.50, "green_circle", 2.0),
        (4.25, 0.50, "black_triangle", 2.0),
    ]


def test_corridor_markers_are_far_enough_apart_to_be_told_apart():
    for seed in range(12):
        w = scenery.build("corridor", seed=seed)
        placed = [(m.x, m.y) for m in w.markers]
        for i, a in enumerate(placed):
            for b in placed[i + 1 :]:
                assert _dist(a, b) >= scenery.MIN_FIRE_SEP_M - 1e-9


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


def test_seed_does_not_change_the_fixed_competition_layout():
    a = scenery.build("corridor", seed=1)
    b = scenery.build("corridor", seed=2)
    assert a.markers == b.markers


# --- hand-editing ---------------------------------------------------------


def test_with_fires_replaces_only_the_fires():
    w = scenery.build("corridor", seed=5)
    others = [m for m in w.markers if m.kind != FIRE]
    x, y = _free_spot(w)
    edited = scenery.with_fires(w, [{"x": x, "y": y}])
    assert edited.fires == [Marker(x, y, FIRE, height_m=2.0)]
    assert [m for m in edited.markers if m.kind != FIRE] == others
    assert (edited.width_m, edited.depth_m, edited.start_xy) == (
        w.width_m,
        w.depth_m,
        w.start_xy,
    )


def test_a_fire_may_stand_in_the_middle_of_the_corridor_not_just_on_a_wall():
    base = scenery.build("corridor", seed=5)
    x, y = _free_spot(base)
    w = scenery.with_fires(base, [(x, y)])
    assert w.fires == [Marker(x, y, FIRE, height_m=2.0)]
    # nowhere near a wall — that is the whole point of the corridor scenery
    assert min(x, w.width_m - x) >= scenery.WALL_CLEARANCE_M


@pytest.mark.parametrize(
    "point",
    [
        (0.1, 2.0),  # flush against the west wall
        (5.9, 2.0),  # flush against the east wall
        (3.0, 3.9),  # through the north clearance
        (3.0, 3.0),  # on top of the start pad
    ],
)
def test_illegal_placements_are_dropped_not_clamped(point):
    w = scenery.with_fires(scenery.build("corridor", seed=5), [point])
    assert w.fires == []


def test_a_fire_cannot_be_stacked_on_a_decoy():
    base = scenery.build("corridor", seed=5)
    decoy = next(m for m in base.markers if m.kind != FIRE)
    w = scenery.with_fires(base, [(decoy.x, decoy.y)])
    assert w.fires == []


def test_clearing_leaves_the_four_competition_decoys():
    w = scenery.with_fires(scenery.build("corridor", seed=5), [])
    assert w.fires == []
    assert len(w.markers) == scenery.CORRIDOR_DISTRACTORS


# --- the adapter ----------------------------------------------------------


def test_sim_drone_starts_on_the_corridor_pad_not_mid_room():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    assert (d.x, d.y) == (3.0, 3.0)


def test_corridor_walls_stop_the_drone_at_the_far_end():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    d.takeoff()
    for _ in range(20):
        d.move("forward", 100)
    assert d.y == pytest.approx(d.world.depth_m - 0.2)


def test_scene_describes_a_rectangle_and_keeps_size_m_for_square_callers():
    s = SimDrone(scenery_name="corridor", seed=4, delay=0).scene()
    assert s["width_m"] == scenery.CORRIDOR_W_M
    assert s["depth_m"] == scenery.CORRIDOR_L_M
    assert s["size_m"] == s["width_m"]
    assert s["start"] == [3.0, 3.0]
    assert s["wall_height_m"] == 3.0
    assert s["return_to_start"] is True
    assert s["name"] == "corridor"
    assert len(s["markers"]) == 7


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


def test_randomise_keeps_the_official_layout_fixed():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    before = list(d.world.markers)
    d.load_scenery(randomise=True)
    assert d.world.markers == before


def test_editing_fires_does_not_change_scenery():
    d = SimDrone(scenery_name="corridor", seed=4, delay=0)
    x, y = _free_spot(d.world)
    d.load_scenery(fires=[{"x": x, "y": y}])
    assert d.world.name == "corridor"
    assert d.world.fires == [Marker(x, y, FIRE, height_m=2.0)]
    assert d.world.return_to_start is True


def test_mock_and_tello_have_no_arena_to_author():
    from comp1.drone.mock import MockDrone

    assert MockDrone().scenery_catalog() is None
    assert MockDrone().load_scenery("corridor") is None


# --- obstacles --------------------------------------------------------------


def test_only_the_legacy_arena_has_extra_obstacles():
    assert scenery.build("arena", seed=7).obstacles
    assert scenery.build("corridor", seed=7).obstacles == []


def test_an_obstacle_never_crowds_a_target_out_of_reach():
    for name in scenery.names():
        world = scenery.build(name, seed=3)
        for o in world.obstacles:
            for m in world.markers:
                if m is o:
                    continue
                gap = math.hypot(m.x - o.x, m.y - o.y)
                assert gap >= scenery.OBSTACLE_SEP_M - 1e-9, f"{name}: {gap:.2f} m"


def test_obstacles_are_repeatable_under_a_seed():
    a = scenery.build("corridor", seed=11).obstacles
    b = scenery.build("corridor", seed=11).obstacles
    assert [(m.x, m.y, m.kind) for m in a] == [(m.x, m.y, m.kind) for m in b]


def test_editing_the_targets_leaves_the_obstacles_alone():
    world = scenery.build("corridor", seed=5)
    before = [(m.x, m.y, m.kind) for m in world.obstacles]
    edited = scenery.with_fires(world, [{"x": 1.25, "y": 5.0}])
    assert [(m.x, m.y, m.kind) for m in edited.obstacles] == before


def test_every_obstacle_kind_has_a_look_in_both_views():
    from comp1.sim.render import KIND_STYLE
    from comp1.sim.world import OBSTACLE_KINDS

    # A kind missing here is a hard KeyError when the camera frame is drawn.
    for kind in OBSTACLE_KINDS:
        assert kind in KIND_STYLE
    colours = (Path(__file__).parent.parent / "comp1/frontend/scene3d.js").read_text(
        encoding="utf-8"
    )
    for kind in OBSTACLE_KINDS:
        assert kind in colours, f"{kind} has no colour in scene3d.js"
