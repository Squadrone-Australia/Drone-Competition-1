from comp1.protocol import Program
from comp1.drone.mock import MockDrone
from comp1.interpreter import Interpreter
from comp1.vision.config import DEFAULT_CONFIG

from .helpers import lost, seen


def prog():
    return Program.model_validate(
        {"version": 1, "blocks": [{"id": "a", "op": "approach_marker"}]})


async def run_seq(seq):
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
    interp = Interpreter(drone, det, events.append)
    await interp.run(prog())
    return drone


async def test_turns_toward_left_marker_then_advances_and_stops():
    drone = await run_seq([
        seen(distance_m=3.0, bearing_deg=-30.0),   # well off to the left
        seen(distance_m=3.0, bearing_deg=0.0),     # lined up, 2 m to close
        seen(distance_m=1.0, bearing_deg=0.0),     # at the stop distance
    ])
    assert ("rotate", "ccw", 30) in drone.log      # turn sized from the bearing
    assert ("move", "forward", 100) in drone.log   # step clamped to the 100 cm max
    idx = drone.log.index(("move", "forward", 100))
    assert all(x[0] != "move" for x in drone.log[idx + 1:])


async def test_turn_is_proportional_to_bearing_error():
    drone = await run_seq([seen(distance_m=3.0, bearing_deg=15.0),
                           seen(distance_m=1.0, bearing_deg=0.0)])
    assert ("rotate", "cw", 15) in drone.log       # not a fixed 15° regardless of error


async def test_large_bearing_error_is_clamped_to_max_turn():
    drone = await run_seq([seen(distance_m=3.0, bearing_deg=-80.0),
                           seen(distance_m=1.0, bearing_deg=0.0)])
    assert ("rotate", "ccw", DEFAULT_CONFIG.approach_max_turn_deg) in drone.log


async def test_step_is_proportional_to_remaining_distance():
    drone = await run_seq([seen(distance_m=1.5, bearing_deg=0.0),
                           seen(distance_m=1.0, bearing_deg=0.0)])
    assert ("move", "forward", 50) in drone.log    # (1.5 - 1.0) m of remaining range


async def test_small_bearing_error_inside_deadband_is_ignored():
    # 5° is inside the deadband and below the Tello's usable rotation floor
    drone = await run_seq([seen(distance_m=1.05, bearing_deg=5.0)])
    assert all(x[0] != "rotate" for x in drone.log)


async def test_stops_without_overshooting_below_the_minimum_step():
    # 15 cm of range left is less than the 20 cm the Tello will fly — stop, don't lurch
    drone = await run_seq([seen(distance_m=1.15, bearing_deg=0.0)])
    assert all(x[0] != "move" for x in drone.log)


async def test_gives_up_when_marker_lost():
    drone = await run_seq([seen(distance_m=3.0), lost(), lost(), lost()])
    assert len(drone.log) <= 2  # one step at most, then abort — no runaway


async def test_reacquires_the_nearest_target_when_approach_starts():
    readings = []
    selected = 0

    def select_nearest():
        nonlocal selected
        selected += 1
        readings.extend([
            seen(distance_m=3.0, bearing_deg=30.0),
            seen(distance_m=1.0, bearing_deg=0.0),
        ])

    def det():
        return readings.pop(0)

    drone, events = MockDrone(), []
    interp = Interpreter(drone, det, events.append,
                         select_nearest_target=select_nearest)
    await interp.run(prog())

    assert selected == 1
    assert drone.log == [("rotate", "cw", 30)]
