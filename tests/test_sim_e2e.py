import math

from comp1.protocol import Program
from comp1.interpreter import Interpreter
from comp1.sim.drone import SimDrone
from comp1.sim.world import Marker, World, VICTIM
from comp1.vision.config import DEFAULT_CONFIG
from comp1.vision.detector import detect_red_circle

SEARCH_PROGRAM = {"version": 1, "blocks": [
    {"id": "a", "op": "takeoff"},
    {"id": "b", "op": "repeat_until", "cond": {"sensor": "marker_visible"},
     "body": [{"id": "c", "op": "rotate", "dir": "cw", "deg": 20}]},
    {"id": "d", "op": "approach_marker"},
    {"id": "e", "op": "mark_found"},
    {"id": "f", "op": "land"},
]}


async def test_full_mission_finds_victim_without_hardware():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    drone = SimDrone(world=world, delay=0)
    drone.heading = 180.0          # start facing AWAY from the victim
    events = []
    interp = Interpreter(drone, lambda: detect_red_circle(drone.get_frame()),
                         events.append)
    await interp.run(Program.model_validate(SEARCH_PROGRAM))
    assert events[-1] == {"type": "finished", "reason": "done", "detail": ""}
    assert {"type": "found_count", "count": 1} in events
    dist = math.hypot(drone.x - 2.0, drone.y - 4.0)
    stop = DEFAULT_CONFIG.approach_stop_distance_m
    # holds off at the configured safe distance rather than flying into the wall;
    # the band is one minimum Tello step either side
    assert abs(dist - stop) < 0.25
    assert not drone.flying        # landed


async def test_error_when_flying_before_takeoff():
    drone = SimDrone(world=World(size_m=4.0, markers=[]), delay=0)
    events = []
    interp = Interpreter(drone, lambda: detect_red_circle(drone.get_frame()),
                         events.append)
    await interp.run(Program.model_validate(
        {"version": 1, "blocks": [{"id": "a", "op": "move", "dir": "forward", "cm": 50}]}))
    assert events[-1]["reason"] == "error"
