import json

import cv2
import numpy as np
from fastapi.testclient import TestClient
from comp1.drone.mock import MockDrone
from comp1.server import create_app


def red_frame():
    img = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(img, (320, 240), 60, (0, 0, 220), -1)
    return img


def collect_until(ws, want_type, limit=50):
    for _ in range(limit):
        msg = ws.receive()
        if "bytes" in msg and msg["bytes"]:
            if want_type == "frame":
                return msg["bytes"]
            continue
        data = json.loads(msg["text"])
        if data["type"] == want_type:
            return data
    raise AssertionError(f"no {want_type} message")


def test_run_program_executes_and_reports():
    drone = MockDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}]}})
        fin = collect_until(ws, "finished")
        assert fin["reason"] == "done"
    assert ("takeoff",) in drone.log and ("land",) in drone.log


def test_video_frames_are_jpeg():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        frame = collect_until(ws, "frame")
        assert frame[:2] == b"\xff\xd8"          # JPEG magic


def test_invalid_program_rejected():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "goto_xy"}]}})
        err = collect_until(ws, "error")
        assert "invalid" in err["message"].lower()


def test_scene_is_sent_once_on_connect():
    """The 3D view is built from this one message; if it never arrives the stage
    stays empty for the whole session, with no retry."""
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=1, delay=0))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = json.loads(ws.receive()["text"])
        assert msg["type"] == "scene"
        assert msg["scene"]["size_m"] > 0 and msg["scene"]["markers"]


def test_scene_is_null_for_a_drone_with_no_arena():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert json.loads(ws.receive()["text"]) == {"type": "scene", "scene": None}


def test_pose_is_broadcast_while_flying():
    from comp1.sim.drone import SimDrone
    drone = SimDrone(seed=1, delay=0.1)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}})
        heights = set()
        for _ in range(300):
            msg = ws.receive()
            if not msg.get("text"):
                continue
            data = json.loads(msg["text"])
            if data["type"] == "pose":
                heights.add(data["z"])
            if data["type"] == "finished":
                break
        # more than start and end: the climb itself was broadcast
        assert len(heights) > 2


def test_run_starts_from_the_start_pad_every_time():
    """Two identical programs must land the drone in the same place. Without the
    reset the second run starts wherever the first ended, so a student changing
    nothing sees a different result."""
    from comp1.sim.drone import SimDrone
    drone = SimDrone(seed=2, delay=0)
    program = {"version": 1, "blocks": [
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": program})
        collect_until(ws, "finished", limit=200)
        first = (drone.x, drone.y, drone.heading)
        ws.send_json({"type": "run", "program": program})
        collect_until(ws, "finished", limit=200)
    assert (drone.x, drone.y, drone.heading) == first


def test_reset_message_returns_the_drone_and_tells_the_clients():
    from comp1.sim.drone import SimDrone
    drone = SimDrone(seed=2, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}})
        collect_until(ws, "finished", limit=200)
        assert drone.y != 2.0
        ws.send_json({"type": "reset"})
        collect_until(ws, "reset", limit=200)
    assert (drone.x, drone.y, drone.flying) == (2.0, 2.0, False)


def test_reset_on_hardware_clears_state_but_admits_it_did_not_move_the_drone():
    """A real Tello cannot teleport. Reporting `repositioned: true` there would
    tell a student the aircraft is on its pad while it hovers where they left it.
    """
    drone = MockDrone()                      # like TelloDrone: no reset support
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "reset"})
        msg = collect_until(ws, "reset", limit=200)
    assert msg["repositioned"] is False
    assert not any(call[0] == "reset" for call in drone.log)


def test_reset_on_the_simulator_reports_a_real_reposition():
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=2, delay=0))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "reset"})
        assert collect_until(ws, "reset", limit=200)["repositioned"] is True


def test_reset_is_refused_while_a_mission_is_running():
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=2, delay=0.2))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}})
        collect_until(ws, "reset", limit=200)        # the pre-run reset
        ws.send_json({"type": "reset"})
        err = collect_until(ws, "error", limit=200)
        assert "stop the mission" in err["message"]


def test_estop_calls_emergency():
    drone = MockDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "estop"})
        collect_until(ws, "estopped")
    assert ("emergency",) in drone.log
