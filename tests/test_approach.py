from comp1.protocol import Program
from comp1.drone.mock import MockDrone
from comp1.vision.detector import Detection
from comp1.interpreter import Interpreter


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
        Detection(True, cx=0.2, area_ratio=0.01, position="left"),
        Detection(True, cx=0.5, area_ratio=0.02, position="center"),
        Detection(True, cx=0.5, area_ratio=0.09, position="center"),  # >= stop area
    ])
    assert ("rotate", "ccw", 15) in drone.log
    assert ("move", "forward", 30) in drone.log
    idx = drone.log.index(("move", "forward", 30))
    assert all(x[0] != "move" for x in drone.log[idx + 1:])  # stopped after close enough


async def test_gives_up_when_marker_lost():
    drone = await run_seq([Detection(True, 0.5, 0.02, "center"),
                           Detection(False), Detection(False), Detection(False)])
    assert len(drone.log) <= 2  # one step at most, then abort — no runaway
