import asyncio
import math
import threading
import time

import pytest
from fastapi.testclient import TestClient

from comp1.api import Drone, EmergencyStop, ScriptRun, Session, current_session
from comp1.drone.mock import MockDrone
from comp1.server import create_app
from comp1.sim.drone import SimDrone
from comp1.sim.world import Marker, World, VICTIM
from comp1.vision.config import DEFAULT_CONFIG
from comp1.vision.detector import Detection, TargetTracker

from .helpers import lost, seen
from .test_server import collect_until


def mock_session(detection=None, **kw):
    adapter = MockDrone()
    return Session(drone=adapter,
                   get_detection=lambda: detection if detection is not None else lost(),
                   **kw), adapter


def sim_session(world=None, heading=0.0, **kw):
    adapter = SimDrone(world=world or World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)]),
                       delay=0)
    adapter.heading = heading
    tracker = TargetTracker(DEFAULT_CONFIG)
    return Session(drone=adapter,
                   get_detection=lambda: tracker.update(adapter.get_frame()), **kw), adapter


# --------------------------------------------------------------- movement map

def test_every_movement_maps_to_the_right_adapter_call():
    session, adapter = mock_session()
    d = Drone(); d._s, d._d = session, adapter        # bind explicitly, no global
    d.takeoff()
    d.forward(50); d.back(60); d.left(70); d.right(80); d.up(90); d.down(100)
    d.turn_right(30); d.turn_left(45)
    d.flip("back")
    d.land()
    assert adapter.log == [
        ("takeoff",),
        ("move", "forward", 50), ("move", "back", 60), ("move", "left", 70),
        ("move", "right", 80), ("move", "up", 90), ("move", "down", 100),
        ("rotate", "cw", 30), ("rotate", "ccw", 45),
        ("flip", "back"),
        ("land",),
    ]


def test_standalone_drone_builds_its_own_adapter():
    d = Drone()                                   # no session bound -> MockDrone
    d.takeoff()
    assert ("connect",) in d._d.log and ("takeoff",) in d._d.log


def test_out_of_range_moves_are_clamped_not_fatal():
    session, adapter = mock_session()
    d = Drone(); d._s, d._d = session, adapter
    d.forward(5)                                  # under the Tello's 20 cm floor
    d.forward(9999)
    d.turn_right(400)
    assert adapter.log == [("move", "forward", 20), ("move", "forward", 500),
                           ("rotate", "cw", 360)]


def test_mark_found_signals_counts_and_emits():
    events = []
    session, adapter = mock_session(emit=events.append)
    d = Drone(); d._s, d._d = session, adapter
    d.mark_found(); d.mark_found()
    assert adapter.log == [("flip", "back"), ("flip", "back")]
    assert d.found_count == 2
    assert events == [{"type": "found_count", "count": 1},
                      {"type": "found_count", "count": 2}]


def test_battery_and_height():
    session, adapter = sim_session()
    d = Drone(); d._s, d._d = session, adapter
    assert d.battery == 100
    assert d.height == 0.0
    d.takeoff()
    assert d.height > 0


# ---------------------------------------------------------------- sensing

def test_sensing_reflects_the_detection():
    session, adapter = mock_session(detection=seen(distance_m=2.5, bearing_deg=-12.0))
    d = Drone(); d._s, d._d = session, adapter
    assert d.sees_target()
    t = d.target()
    assert t.distance_m == pytest.approx(2.5)
    assert t.distance_cm == pytest.approx(250)
    assert t.bearing_deg == pytest.approx(-12.0)
    assert t.position == "left"
    assert d.distance_cm() == pytest.approx(250)
    assert d.bearing_deg() == pytest.approx(-12.0)


def test_sensing_when_nothing_is_visible():
    session, adapter = mock_session(detection=lost())
    d = Drone(); d._s, d._d = session, adapter
    assert not d.sees_target()
    assert d.target() is None
    assert d.distance_cm() is None and d.bearing_deg() is None


def test_targets_lists_every_candidate_nearest_first():
    det = Detection.of(seen(distance_m=1.5).target,
                       [seen(distance_m=1.5).target, seen(distance_m=3.0).target])
    session, adapter = mock_session(detection=det)
    d = Drone(); d._s, d._d = session, adapter
    assert [round(t.distance_m, 1) for t in d.targets()] == [1.5, 3.0]


def test_target_uses_the_tracker_lock_not_this_frames_biggest():
    """The primary target survives another candidate becoming nearer."""
    tracker = TargetTracker(DEFAULT_CONFIG)
    near = seen(distance_m=3.0, bearing_deg=-20.0).target
    far = seen(distance_m=3.2, bearing_deg=20.0).target
    tracker._locked = far                          # already locked onto the right-hand one
    det = Detection.of(tracker._pick([near, far]), [near, far])
    session, adapter = mock_session(detection=det)
    d = Drone(); d._s, d._d = session, adapter
    assert d.target().bearing_deg == pytest.approx(20.0)
    assert d.targets()[0].bearing_deg == pytest.approx(-20.0)   # not the nearest


# ---------------------------------------------------------------- approach

async def test_approach_converges_in_the_simulator():
    session, adapter = sim_session(heading=180.0)   # start facing away
    d = Drone(); d._s, d._d = session, adapter
    d.takeoff()
    while not d.sees_target():
        d.turn_right(20)
    assert d.approach_target()
    dist = math.hypot(adapter.x - 2.0, adapter.y - 4.0)
    assert abs(dist - DEFAULT_CONFIG.approach_stop_distance_m) < 0.25


def test_approach_gives_up_when_the_target_is_never_seen():
    session, adapter = mock_session(detection=lost())
    d = Drone(); d._s, d._d = session, adapter
    assert d.approach_target() is False


def test_approach_requests_the_nearest_target_before_moving():
    selected = []
    session, adapter = mock_session(
        seen(distance_m=1.0), select_nearest_target=lambda: selected.append(True))
    d = Drone(); d._s, d._d = session, adapter

    assert d.approach_target()
    assert selected == [True]
    assert adapter.log == []


def test_approach_honours_a_custom_stop_distance():
    session, adapter = mock_session(detection=seen(distance_m=3.0, bearing_deg=0.0))
    d = Drone(); d._s, d._d = session, adapter
    d.approach_target(stop_distance_cm=200)
    assert adapter.log[0] == ("move", "forward", 100)   # clamped to the max step


# ------------------------------------------------------------ the stop flag

async def test_stop_flag_interrupts_a_runaway_loop_and_lands():
    events = []
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, events.append, settle_s=0, grace_s=1.0)

    started = threading.Event()

    def student_code():
        d = Drone()
        d.takeoff()
        started.set()
        while True:                                # never exits on its own
            d.turn_right(20)

    async def stop_when_flying():
        await asyncio.to_thread(started.wait, 2.0)
        run.request_stop()

    t0 = time.monotonic()
    _, (reason, _) = await asyncio.gather(stop_when_flying(), run.run(student_code))
    elapsed = time.monotonic() - t0

    assert reason == "stopped"
    assert elapsed < 3.0                           # promptly, not eventually
    assert ("land",) in adapter.log
    assert {"type": "script", "state": "started", "name": "script"} in events
    assert {"type": "finished", "reason": "stopped", "detail": ""} in events


async def test_students_except_exception_cannot_swallow_the_stop():
    """EmergencyStop is a BaseException precisely so this loop still ends."""
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, lambda ev: None, settle_s=0, grace_s=1.0)
    started = threading.Event()

    def student_code():
        d = Drone()
        d.takeoff()
        started.set()
        while True:
            try:
                d.turn_right(20)
            except Exception:                      # the trap
                pass

    async def stop_when_flying():
        await asyncio.to_thread(started.wait, 2.0)
        run.request_stop()

    _, (reason, _) = await asyncio.gather(stop_when_flying(), run.run(student_code))
    assert reason == "stopped"
    assert ("land",) in adapter.log


async def test_a_loop_with_no_drone_calls_is_abandoned_and_left_powerless():
    """Nothing can interrupt `while True: pass` — but it must not keep the drone.

    After the grace period the run slot is freed and the drone landed; the
    thread lives on but every API call it makes now raises.
    """
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, lambda ev: None, settle_s=0, grace_s=0.3)
    started, release = threading.Event(), threading.Event()
    after = []

    def student_code():
        d = Drone()
        d.takeoff()
        started.set()
        while not release.is_set():
            pass                                   # no API call — no stop check
        try:
            d.forward(100)
            after.append("commanded the drone")
        except EmergencyStop:
            after.append("refused")

    async def stop_when_flying():
        await asyncio.to_thread(started.wait, 2.0)
        run.request_stop()

    _, (reason, detail) = await asyncio.gather(stop_when_flying(), run.run(student_code))
    assert reason == "abandoned" and "no drone commands" in detail
    assert ("land",) in adapter.log
    assert current_session() is None               # slot freed for the next run

    release.set()
    await asyncio.to_thread(time.sleep, 0.2)
    assert after == ["refused"]
    assert ("move", "forward", 100) not in adapter.log


async def test_wait_is_interrupted_immediately():
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, lambda ev: None, settle_s=0, grace_s=1.0)
    started = threading.Event()

    def student_code():
        d = Drone()
        started.set()
        d.wait(30)                                 # a very long hover

    async def stop_when_flying():
        await asyncio.to_thread(started.wait, 2.0)
        run.request_stop()

    t0 = time.monotonic()
    _, (reason, _) = await asyncio.gather(stop_when_flying(), run.run(student_code))
    assert reason == "stopped"
    assert time.monotonic() - t0 < 2.0


async def test_a_stop_before_the_script_starts_still_wins():
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, lambda ev: None, settle_s=0)
    run.request_stop()
    reason, _ = await run.run(lambda: Drone().takeoff())
    assert reason == "stopped"
    assert ("takeoff",) not in adapter.log


# ------------------------------------------------------------ script lifecycle

async def test_script_error_is_reported_and_the_drone_lands():
    events = []
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, events.append, settle_s=0)

    def student_code():
        Drone().takeoff()
        raise ValueError("oops")

    reason, detail = await run.run(student_code)
    assert reason == "error" and "oops" in detail
    assert ("land",) in adapter.log
    assert any(e["type"] == "script" and e["state"] == "error" for e in events)


async def test_clean_script_finishes_done_without_a_forced_landing():
    adapter = MockDrone()
    run = ScriptRun(adapter, lost, lambda ev: None, settle_s=0)
    reason, _ = await run.run(lambda: (Drone().takeoff()))
    assert reason == "done"
    assert ("land",) not in adapter.log            # not landed behind the student's back


async def test_the_session_is_unbound_after_a_run():
    run = ScriptRun(MockDrone(), lost, lambda ev: None, settle_s=0)
    await run.run(lambda: None)
    assert current_session() is None


# ------------------------------------------------------- server integration

MISSION = """
from comp1.api import Drone
drone = Drone()
drone.takeoff()
drone.forward(50)
drone.land()
"""


def test_server_runs_a_script_alongside_the_live_feed(tmp_path):
    script = tmp_path / "mission.py"
    script.write_text(MISSION)
    adapter = MockDrone()
    app = create_app(adapter, script=script, script_delay=0)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        fin = collect_until(ws, "finished", limit=200)
        assert fin["reason"] == "done"
    assert adapter.log == [("connect",), ("takeoff",), ("move", "forward", 50), ("land",)]


def test_a_block_program_cannot_start_while_a_script_is_flying(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("from comp1.api import Drone\nDrone().wait(5)\n")
    app = create_app(MockDrone(), script=script, script_delay=0)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "script", limit=200)          # the script has the slot
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"}]}})
        err = collect_until(ws, "error", limit=200)
        assert "already running" in err["message"]
        ws.send_json({"type": "stop"})                  # release it for the next test run
        collect_until(ws, "finished", limit=200)


def test_estop_over_the_websocket_stops_a_running_script(tmp_path):
    script = tmp_path / "runaway.py"
    script.write_text("from comp1.api import Drone\n"
                      "d = Drone()\n"
                      "d.takeoff()\n"
                      "while True:\n"
                      "    d.turn_right(20)\n")
    adapter = MockDrone()
    app = create_app(adapter, script=script, script_delay=0)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "script", limit=200)
        ws.send_json({"type": "estop"})
        fin = collect_until(ws, "finished", limit=400)
        assert fin["reason"] == "stopped"
    assert ("emergency",) in adapter.log
