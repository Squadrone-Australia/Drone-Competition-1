"""What the program does when the aircraft, the student, or the wire misbehaves.

These are regressions for faults found by injecting hardware failures in
software: an aircraft that accepts a command and never answers, a plan the
server refuses, a loop made entirely of arithmetic, and malformed traffic on the
websocket. Each one was reachable from the block editor or a browser tab, and
each one used to leave the program in a state a student could not get out of.
"""

import asyncio
import threading
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient

from comp1.drone.base import DroneAdapter
from comp1.drone.mock import MockDrone
from comp1.interpreter import DroneTimeout, Interpreter
from comp1.protocol import Program
from comp1.server import create_app
from comp1.sim.drone import SimDrone
from comp1.vision.config import DEFAULT_CONFIG

from .test_server import collect_until


class SilentDrone(MockDrone):
    """Accepts a command and never answers — a Tello that lost power mid-flight.

    ``command_timeout_s`` is tiny so the test does not have to sit through the
    real budget; the behaviour under test is what happens when it expires.
    """

    command_timeout_s = 0.3

    def __init__(self):
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def takeoff(self):
        self.log.append(("takeoff",))
        self.entered.set()
        self.release.wait(30)


def _interp(drone, events):
    return Interpreter(
        drone,
        lambda: None,
        events.append,
        cfg=DEFAULT_CONFIG,
        select_nearest_target=lambda: None,
    )


def _program(*blocks):
    return Program.model_validate({"version": 2, "blocks": list(blocks)})


# --- an aircraft that stops answering ---------------------------------------


async def test_a_command_that_never_answers_ends_the_mission():
    """Without a bound on the call, `to_thread` parks the mission for good:
    nothing finishes, the run slot never clears, and neither Stop nor EMERGENCY
    STOP can end it, because the stop flag is only read between blocks."""
    drone = SilentDrone()
    events = []
    interp = _interp(drone, events)

    await asyncio.wait_for(
        interp.run(_program({"id": "t", "op": "takeoff"})), timeout=10
    )

    finished = [e for e in events if e["type"] == "finished"]
    assert finished, "the mission must end even when the drone does not answer"
    assert finished[0]["reason"] == "error"
    assert "did not answer" in finished[0]["detail"]
    drone.release.set()


async def test_the_timeout_names_the_command_that_hung():
    drone = SilentDrone()
    interp = _interp(drone, [])
    with pytest.raises(DroneTimeout, match="takeoff"):
        await asyncio.wait_for(interp._call_drone("takeoff"), timeout=10)
    drone.release.set()


# --- emergency stop ----------------------------------------------------------


async def test_emergency_stop_does_not_also_land():
    """`land` after `emergency` is a flight command sent to an aircraft whose
    motors were just cut, and which of the two arrives first was a race."""
    drone = MockDrone()
    interp = _interp(drone, [])
    interp.request_stop(emergency=True)

    await asyncio.wait_for(
        interp.run(_program({"id": "t", "op": "takeoff"})), timeout=10
    )

    assert not any(c[0] == "land" for c in drone.log), drone.log


async def test_an_ordinary_stop_still_lands():
    drone = MockDrone()
    interp = _interp(drone, [])
    interp.request_stop()

    await asyncio.wait_for(
        interp.run(_program({"id": "t", "op": "takeoff"})), timeout=10
    )

    assert any(c[0] == "land" for c in drone.log), drone.log


def test_a_failed_emergency_is_not_reported_as_a_stop():
    """The old code broadcast `estopped` unconditionally, so the last and
    loudest line a student saw said EMERGENCY STOP even when the motors were
    still turning."""

    class Unreachable(MockDrone):
        def emergency(self):
            raise RuntimeError("no answer")

    app = create_app(Unreachable())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "estop"})
        msg = collect_until(ws, "estopped", limit=200)
        assert msg["ok"] is False
        assert "could not reach" in msg["message"]


def test_a_successful_emergency_says_so():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "estop"})
        assert collect_until(ws, "estopped", limit=200)["ok"] is True


# --- a loop made only of arithmetic ------------------------------------------


async def test_a_compute_only_loop_lets_the_event_loop_run():
    """A loop whose body never touches the drone used to run as one
    uninterrupted block of synchronous Python — during which video stopped and
    the websocket message carrying EMERGENCY STOP could not even be read."""
    ticks = 0

    async def watcher():
        nonlocal ticks
        while True:
            await asyncio.sleep(0)
            ticks += 1

    interp = _interp(MockDrone(), [])
    program = _program(
        {
            "id": "r",
            "op": "repeat_n",
            "n": {"kind": "number", "value": 50},
            "body": [
                {
                    "id": "w",
                    "op": "while",
                    "cond": {
                        "kind": "binop",
                        "op": ">",
                        "left": {"kind": "number", "value": 5},
                        "right": {"kind": "number", "value": 1},
                    },
                    "body": [
                        {
                            "id": "s",
                            "op": "set_var",
                            "name": "x",
                            "value": {"kind": "number", "value": 1},
                        }
                    ],
                }
            ],
        }
    )
    task = asyncio.create_task(watcher())
    try:
        await asyncio.wait_for(interp.run(program), timeout=60)
    finally:
        task.cancel()

    assert ticks > 1000, f"event loop only got {ticks} turns during the whole run"


async def test_a_compute_only_loop_can_be_stopped():
    interp = _interp(MockDrone(), [])
    program = _program(
        {
            "id": "r",
            "op": "repeat_n",
            "n": {"kind": "number", "value": 50},
            "body": [
                {
                    "id": "w",
                    "op": "while",
                    "cond": {
                        "kind": "binop",
                        "op": ">",
                        "left": {"kind": "number", "value": 5},
                        "right": {"kind": "number", "value": 1},
                    },
                    "body": [
                        {
                            "id": "s",
                            "op": "set_var",
                            "name": "x",
                            "value": {"kind": "number", "value": 1},
                        }
                    ],
                }
            ],
        }
    )
    run = asyncio.create_task(interp.run(program))
    await asyncio.sleep(0.2)
    interp.request_stop()
    await asyncio.wait_for(run, timeout=10)
    assert interp._stop.is_set()


# --- malformed traffic -------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        "not json at all",
        "42",
        '"hello"',
        "null",
        "{}",
        '{"type":"run"}',
        '{"type":"nonsense"}',
        '{"type":"scenery","name":"bogus"}',
        '{"type":"layout","fires":[{}]}',
        '{"type":"layout","fires":[["a","b"]]}',
        '{"type":"layout","fires":[5]}',
    ],
)
def test_a_malformed_message_does_not_kill_the_socket(payload):
    """The browser reconnects a second after a dropped socket, which is what
    made this invisible: the student just saw their controls stop working."""
    app = create_app(SimDrone(seed=1, delay=0))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_text(payload)
        collect_until(ws, "error", limit=400)
        # still usable afterwards
        ws.send_json({"type": "reset"})
        collect_until(ws, "reset", limit=400)


def test_an_unknown_message_type_is_answered_not_ignored():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "does_not_exist"})
        assert "does not understand" in collect_until(ws, "error", limit=200)["message"]


def test_a_rejected_plan_explains_itself_without_pydantic_noise():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {"version": 2, "blocks": [{"id": "b", "op": "break"}]},
            }
        )
        message = collect_until(ws, "error", limit=200)["message"]
        assert "inside a loop" in message
        assert "pydantic" not in message.lower()
        assert "input_value" not in message.lower()


# --- the websocket is not open to any page the student visits ----------------


def test_a_foreign_origin_cannot_open_the_socket():
    """WebSockets are not covered by the same-origin policy, so without a check
    any page open in the same browser could fly the drone."""
    from starlette.websockets import WebSocketDisconnect

    app = create_app(MockDrone())
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws", headers={"origin": "http://evil.example"}
            ) as ws:
                ws.receive_json()


def test_the_apps_own_page_can_open_the_socket():
    app = create_app(MockDrone())
    with TestClient(app) as client:
        with client.websocket_connect(
            "/ws", headers={"origin": "http://testserver"}
        ) as ws:
            assert ws.receive_json()["type"]


# --- the simulator tells the truth about walls -------------------------------


def test_flying_into_a_wall_is_a_crash():
    """The room box clamps the path, so without this a plan that puts a real
    Tello into the brickwork reports a clean success in the simulator."""
    drone = SimDrone(seed=42, delay=0)
    drone.takeoff()
    for _ in range(6):
        drone.move("forward", 300)  # 18 m across a 4 m arena
    assert drone.pose()["crashed"] is True


def test_an_ordinary_move_is_not_a_crash():
    drone = SimDrone(seed=42, delay=0)
    drone.takeoff()
    drone.move("forward", 20)
    assert drone.pose()["crashed"] is False
