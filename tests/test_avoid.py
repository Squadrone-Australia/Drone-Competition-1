"""The `step around obstacle` block and its Python-pathway twin."""

from dataclasses import replace

from comp1.drone.mock import MockDrone
from comp1.interpreter import Interpreter
from comp1.protocol import Program
from comp1.vision.config import DEFAULT_CONFIG

from .helpers import blocked, clear, seen


def prog():
    return Program.model_validate(
        {"version": 2, "blocks": [{"id": "a", "op": "avoid_obstacle"}]}
    )


async def run_flight(seq, cfg=DEFAULT_CONFIG):
    """Run one avoid against a scripted detection sequence.

    The last entry repeats forever once the sequence runs out, so a test that
    wants the controller to give up simply never supplies a clear frame.
    """
    it_seq = iter(seq)
    last = seq[-1]

    def det():
        nonlocal last
        try:
            last = next(it_seq)
        except StopIteration:
            pass
        return last

    drone, events = MockDrone(), []
    interp = Interpreter(drone, det, events.append, cfg=cfg)
    await interp.run(prog())
    return drone, events


def moves(drone):
    return [x for x in drone.log if x[0] == "move"]


def warnings(events):
    return [e for e in events if e["type"] == "warning"]


async def test_does_nothing_when_the_way_is_clear():
    drone, events = await run_flight([clear()])
    assert moves(drone) == []
    assert warnings(events) == []


async def test_does_nothing_when_only_a_target_is_in_view():
    # A target is a thing to fly *at*, and must never be stepped around.
    drone, _ = await run_flight([seen(distance_m=0.6, bearing_deg=0.0)])
    assert moves(drone) == []


async def test_an_obstacle_off_to_the_side_is_not_in_the_way():
    drone, _ = await run_flight([blocked(distance_m=0.6, bearing_deg=-30.0)])
    assert moves(drone) == []


async def test_a_far_obstacle_dead_ahead_is_not_in_the_way():
    far = DEFAULT_CONFIG.obstacle_clear_distance_m + 2.0
    drone, _ = await run_flight([blocked(distance_m=far, bearing_deg=0.0)])
    assert moves(drone) == []


async def test_steps_left_away_from_an_obstacle_on_the_right():
    drone, events = await run_flight(
        [blocked(distance_m=0.6, bearing_deg=4.0), clear()]
    )
    assert moves(drone) == [("move", "left", DEFAULT_CONFIG.avoid_sidestep_cm)]
    assert warnings(events) == []


async def test_steps_right_away_from_an_obstacle_on_the_left():
    drone, _ = await run_flight([blocked(distance_m=0.6, bearing_deg=-4.0), clear()])
    assert moves(drone) == [("move", "right", DEFAULT_CONFIG.avoid_sidestep_cm)]


async def test_a_dead_centre_obstacle_always_goes_the_same_way():
    # Arbitrary, but consistent: a student cannot reason about a run whose
    # direction flips on a fraction of a degree of noise.
    for _ in range(3):
        drone, _ = await run_flight([blocked(bearing_deg=0.0), clear()])
        assert moves(drone) == [("move", "left", DEFAULT_CONFIG.avoid_sidestep_cm)]


async def test_keeps_stepping_until_the_way_is_clear():
    drone, events = await run_flight(
        [
            blocked(distance_m=0.6, bearing_deg=2.0),
            blocked(distance_m=0.7, bearing_deg=3.0),
            clear(),
        ]
    )
    assert len(moves(drone)) == 2
    assert warnings(events) == []


async def test_warns_instead_of_raising_when_it_cannot_get_clear():
    # The obstacle never goes away. A mission that dies mid-flight is worse than
    # one that warns, so this must come back normally.
    cfg = replace(DEFAULT_CONFIG, avoid_max_steps=3)
    drone, events = await run_flight([blocked(distance_m=0.5)], cfg=cfg)
    assert len(moves(drone)) == 3
    assert len(warnings(events)) == 1
    assert "obstacle" in warnings(events)[0]["message"]
    assert events[-1]["reason"] == "done"


async def test_sidestep_is_held_to_the_flight_limits():
    # A config asking for less than the Tello's 20 cm floor must not emit a move
    # the aircraft will silently refuse.
    cfg = replace(DEFAULT_CONFIG, avoid_sidestep_cm=5)
    drone, _ = await run_flight([blocked(), clear()], cfg=cfg)
    assert moves(drone) == [("move", "left", 20)]
