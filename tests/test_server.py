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


def test_run_reports_the_validated_program_for_the_debug_panel():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "move", "dir": "forward", "cm": 50}]}})
        debug = collect_until(ws, "debug_program")
    assert debug["program"]["version"] == 2
    assert debug["program"]["blocks"][0]["op"] == "move"
    assert debug["program"]["blocks"][0]["cm"] == {"kind": "number", "value": 50.0}


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


def test_browser_can_switch_from_simulator_to_a_connected_tello():
    from comp1.sim.drone import SimDrone
    simulator = SimDrone(seed=2, delay=0)
    tello = MockDrone()
    tello.mode = "tello"
    app = create_app(simulator, tello_factory=lambda: tello)

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        initial = collect_until(ws, "drone_mode")
        assert initial == {"type": "drone_mode", "mode": "sim", "switching": False}

        ws.send_json({"type": "switch_drone", "mode": "tello"})
        assert collect_until(ws, "drone_mode")["switching"] is True
        connected = collect_until(ws, "drone_mode")
        assert connected == {"type": "drone_mode", "mode": "tello", "switching": False}

        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}]}})
        assert collect_until(ws, "finished")["reason"] == "done"

    assert ("connect",) in tello.log
    assert ("takeoff",) in tello.log and ("land",) in tello.log


def test_failed_tello_connection_keeps_the_simulator_active():
    from comp1.sim.drone import SimDrone

    class UnavailableTello(MockDrone):
        mode = "tello"

        def connect(self):
            raise RuntimeError("not reachable")

    simulator = SimDrone(seed=2, delay=0)
    app = create_app(simulator, tello_factory=UnavailableTello)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        assert collect_until(ws, "drone_mode")["switching"] is True
        mode = collect_until(ws, "drone_mode")
        error = collect_until(ws, "error")
        assert mode == {"type": "drone_mode", "mode": "sim", "switching": False}
        assert "could not connect" in error["message"]
        assert app.state.drone is simulator


# --- choosing and editing the arena ---------------------------------------

def collect_where(ws, want_type, pred, limit=400):
    """Like ``collect_until``, but skips messages of the right type that are not
    yet the state we are waiting for — mission state arrives repeatedly."""
    for _ in range(limit):
        msg = ws.receive()
        if not msg.get("text"):
            continue
        data = json.loads(msg["text"])
        if data["type"] == want_type and pred(data):
            return data
    raise AssertionError(f"no matching {want_type} message")


def test_the_scenery_list_is_offered_on_connect():
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=1, delay=0))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = collect_until(ws, "sceneries")
        assert [s["id"] for s in msg["sceneries"]] == ["arena", "corridor"]
        assert msg["current"] == "arena"


def test_a_drone_with_no_arena_offers_no_sceneries():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = collect_until(ws, "sceneries")
        assert msg["sceneries"] is None and msg["current"] is None


def test_switching_scenery_rebuilds_the_arena_and_re_sends_it():
    """`scene` used to be a connect-only message. It is not any more — a picker
    that changes the room without telling the browser leaves both views drawing
    the old one."""
    from comp1.sim.drone import SimDrone
    drone = SimDrone(seed=1, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "sceneries")
        ws.send_json({"type": "scenery", "name": "corridor"})
        scene = collect_where(ws, "scene", lambda m: m["scene"]["name"] == "corridor")
        assert scene["scene"]["depth_m"] > scene["scene"]["width_m"] * 3
    assert drone.world.name == "corridor"
    assert (drone.x, drone.y) == drone.world.start_xy


def test_editing_the_layout_re_sends_the_arena():
    from comp1.sim.drone import SimDrone
    from comp1.sim import scenery
    drone = SimDrone(scenery_name="corridor", seed=1, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "sceneries")
        ws.send_json({"type": "layout", "victims": [{"x": 1.25, "y": 5.0}]})
        scene = collect_where(
            ws, "scene",
            lambda m: [k["kind"] for k in m["scene"]["markers"]].count("victim") <= 1)
        victims = [k for k in scene["scene"]["markers"] if k["kind"] == "victim"]
        assert len(victims) <= 1
        # whatever survived validation, the destination is untouched
        assert any(k["kind"] == "destination" for k in scene["scene"]["markers"])
    assert scenery.MIN_VICTIM_SEP_M > 0


def test_clearing_the_layout_leaves_the_destination_alone():
    from comp1.sim.drone import SimDrone
    drone = SimDrone(scenery_name="corridor", seed=1, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "sceneries")
        ws.send_json({"type": "layout", "victims": []})
        collect_where(ws, "scene", lambda m: not [
            k for k in m["scene"]["markers"] if k["kind"] == "victim"])
    assert drone.world.victims == []
    assert drone.world.destination is not None


def test_arena_edits_are_refused_while_a_mission_is_running():
    """Moving the markers out from under a flying mission is the same mistake as
    resetting mid-flight, and gets the same answer."""
    from comp1.sim.drone import SimDrone
    app = create_app(SimDrone(seed=2, delay=0.2))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100}]}})
        collect_until(ws, "reset", limit=200)        # the pre-run reset
        ws.send_json({"type": "scenery", "name": "corridor"})
        err = collect_until(ws, "error", limit=200)
        assert "stop the mission" in err["message"]


def test_a_drone_with_no_arena_says_so_rather_than_failing_quietly():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "scenery", "name": "corridor"})
        assert "no arena" in collect_until(ws, "error", limit=200)["message"]


# --- mission success ------------------------------------------------------

def _corridor_drone():
    from comp1.sim.drone import SimDrone
    from comp1.sim.world import DESTINATION, VICTIM, Marker, World
    world = World(size_m=2.5, length_m=10.0, start=(1.25, 0.6), name="corridor",
                  markers=[Marker(1.25, 9.4, DESTINATION), Marker(1.25, 3.0, VICTIM)])
    return SimDrone(world=world, delay=0)


# start pad -> victim -> destination, then land
FLY_THE_CORRIDOR = {"version": 2, "blocks": [
    {"id": "a", "op": "takeoff"},
    {"id": "b", "op": "move", "dir": "forward", "cm": 240},
    {"id": "c", "op": "mark_found"},
    {"id": "d", "op": "move", "dir": "forward", "cm": 500},
    {"id": "e", "op": "move", "dir": "forward", "cm": 140},
    {"id": "f", "op": "land"},
]}


def test_flying_the_corridor_and_landing_at_the_sign_is_a_mission_success():
    app = create_app(_corridor_drone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": FLY_THE_CORRIDOR})
        done = collect_where(ws, "mission", lambda m: m["state"] == "success")
        assert done["found"] == done["total"] == 1
        assert done["at_destination"] is True


def test_signalling_on_the_start_pad_credits_nothing():
    """`mark found` is a bare counter in the interpreter — a program that just
    flips three times must not be able to win."""
    app = create_app(_corridor_drone())
    program = {"version": 2, "blocks": [
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "mark_found"},
        {"id": "c", "op": "land"},
    ]}
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": program})
        msg = collect_where(ws, "mission", lambda m: m["signal"] is not None)
        assert msg["signal"] == "no victim nearby"
        assert msg["found"] == 0 and msg["state"] == "flying"


def test_a_drone_with_no_arena_reports_no_mission_state():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": {"version": 1, "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "mark_found"},
            {"id": "c", "op": "land"}]}})
        seen = []
        for _ in range(200):
            msg = ws.receive()
            if not msg.get("text"):
                continue
            data = json.loads(msg["text"])
            seen.append(data["type"])
            if data["type"] == "finished":
                break
        assert "mission" not in seen
        assert "found_count" in seen      # the raw counter still works
