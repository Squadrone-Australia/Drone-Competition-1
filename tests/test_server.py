import json
import time

import cv2
import numpy as np
from fastapi.testclient import TestClient

from comp1 import server
from comp1.drone.mock import MockDrone
from comp1.server import create_app
from comp1.vision.config import VisionConfig


def red_frame():
    img = np.full((480, 640, 3), 255, np.uint8)
    cv2.circle(img, (320, 240), 60, (0, 0, 220), -1)
    return img


def collect_until(ws, want_type, limit=50):
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
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
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [{"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}],
                },
            }
        )
        fin = collect_until(ws, "finished")
        assert fin["reason"] == "done"
    assert ("takeoff",) in drone.log and ("land",) in drone.log


def test_run_reports_the_validated_program_for_the_debug_panel():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [{"id": "a", "op": "move", "dir": "forward", "cm": 50}],
                },
            }
        )
        debug = collect_until(ws, "debug_program")
    assert debug["program"]["version"] == 2
    assert debug["program"]["blocks"][0]["op"] == "move"
    assert debug["program"]["blocks"][0]["cm"] == {"kind": "number", "value": 50.0}


def test_video_frames_are_jpeg():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        frame = collect_until(ws, "frame")
        assert frame[:2] == b"\xff\xd8"  # JPEG magic


def test_vision_config_is_sent_on_connect():
    cfg = VisionConfig(lower1=(2, 90, 70))
    app = create_app(MockDrone(frame_factory=red_frame), cfg=cfg)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = collect_until(ws, "vision_config")
    assert msg["config"]["lower1"] == [2, 90, 70]


def test_marker_region_produces_a_previewed_hsv_suggestion():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")  # latest raw frame is now available
        ws.send_json({"type": "vision_sample", "roi": [0.42, 0.38, 0.58, 0.62]})
        msg = collect_until(ws, "vision_suggestion")
    assert msg["config"]["lower1"][0] == 0
    assert msg["config"]["lower2"][0] >= 170
    assert len(msg["preview_jpeg"]) > 100


def test_vision_settings_apply_live_and_reset_to_startup_config():
    startup = VisionConfig(lower1=(1, 90, 70))
    app = create_app(MockDrone(frame_factory=red_frame), cfg=startup)
    blue = {
        "lower1": [100, 80, 70],
        "upper1": [130, 255, 255],
        "lower2": [100, 80, 70],
        "upper2": [130, 255, 255],
    }
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "vision_config")
        ws.send_json({"type": "vision_apply", "config": blue})
        assert collect_until(ws, "vision_config")["config"] == blue
        ws.send_json({"type": "vision_reset"})
        restored = collect_until(ws, "vision_config")["config"]
    assert restored["lower1"] == [1, 90, 70]


def test_vision_calibration_is_refused_during_a_mission():
    from comp1.sim.drone import SimDrone

    app = create_app(SimDrone(seed=2, delay=0.2))
    values = {
        "lower1": [0, 100, 80],
        "upper1": [10, 255, 255],
        "lower2": [170, 100, 80],
        "upper2": [180, 255, 255],
    }
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
        collect_until(ws, "reset", limit=200)
        ws.send_json({"type": "vision_apply", "config": values})
        error = collect_until(ws, "vision_error", limit=200)
    assert "stop the mission" in error["message"]


def test_invalid_program_rejected():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {"version": 1, "blocks": [{"id": "a", "op": "goto_xy"}]},
            }
        )
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


class _CountingBatteryDrone(MockDrone):
    """A drone that reports a charge and remembers how often it was asked."""

    def __init__(self):
        super().__init__()
        self.battery_polls = 0

    def battery(self):
        self.battery_polls += 1
        return 77


def test_battery_is_broadcast_to_the_app_bar(monkeypatch):
    monkeypatch.setattr(server, "BATTERY_INTERVAL", 0.01)
    app = create_app(_CountingBatteryDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        for _ in range(100):
            msg = collect_until(ws, "battery")
            if msg["percent"] is not None:
                break
        assert msg == {"type": "battery", "percent": 77}


def test_battery_is_not_polled_while_a_mission_is_running(monkeypatch):
    """A poll from the server would sit between the interpreter's own commands,
    and djitellopy pairs replies with commands positionally — every reply after
    it would belong to the command before it."""
    monkeypatch.setattr(server, "BATTERY_INTERVAL", 0.01)
    drone = _CountingBatteryDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "battery")
        app.state.interp = object()  # stands in for a flying program
        polled = drone.battery_polls
        time.sleep(0.1)  # many poll intervals
        assert drone.battery_polls == polled
        app.state.interp = None
        time.sleep(0.1)
        assert drone.battery_polls > polled  # and it resumes once the mission ends


def test_pose_is_broadcast_while_flying():
    from comp1.sim.drone import SimDrone

    drone = SimDrone(seed=1, delay=0.1)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
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
    program = {
        "version": 1,
        "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 100},
        ],
    }
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
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
        collect_until(ws, "finished", limit=200)
        assert drone.y != 2.0
        ws.send_json({"type": "reset"})
        collect_until(ws, "reset", limit=200)
    assert (drone.x, drone.y, drone.flying) == (2.0, 2.0, False)


def test_reset_on_hardware_clears_state_but_admits_it_did_not_move_the_drone():
    """A real Tello cannot teleport. Reporting `repositioned: true` there would
    tell a student the aircraft is on its pad while it hovers where they left it.
    """
    drone = MockDrone()  # like TelloDrone: no reset support
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
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
        collect_until(ws, "reset", limit=200)  # the pre-run reset
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

        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [{"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}],
                },
            }
        )
        assert collect_until(ws, "finished")["reason"] == "done"

    assert ("connect",) in tello.log
    assert ("takeoff",) in tello.log and ("land",) in tello.log


def test_browser_can_switch_from_tello_back_to_the_same_simulator():
    from comp1.sim.drone import SimDrone

    simulator = SimDrone(seed=2, delay=0)
    tello = MockDrone()
    tello.mode = "tello"
    app = create_app(simulator, tello_factory=lambda: tello)

    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        collect_until(ws, "drone_mode")  # switching
        collect_until(ws, "drone_mode")  # connected

        ws.send_json({"type": "switch_drone", "mode": "sim"})
        assert collect_until(ws, "drone_mode") == {
            "type": "drone_mode",
            "mode": "tello",
            "switching": True,
        }
        restored = collect_until(ws, "drone_mode")
        assert restored == {"type": "drone_mode", "mode": "sim", "switching": False}
        assert app.state.drone is simulator


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


# --- surviving a Tello that goes away --------------------------------------


class ClosableTello(MockDrone):
    mode = "tello"

    def close(self):
        self.log.append(("close",))


def test_switching_back_to_the_simulator_releases_the_tello():
    """The video decoder holds its UDP port until the adapter is closed, so an
    adapter that is merely dropped makes the *next* Tello connection fail —
    which is why sim -> tello -> sim used to need a restart of the program."""
    from comp1.sim.drone import SimDrone

    tello = ClosableTello()
    app = create_app(SimDrone(seed=2, delay=0), tello_factory=lambda: tello)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        collect_where(ws, "drone_mode", lambda m: m["mode"] == "tello" and not m["switching"])
        assert ("close",) not in tello.log

        ws.send_json({"type": "switch_drone", "mode": "sim"})
        collect_where(ws, "drone_mode", lambda m: m["mode"] == "sim" and not m["switching"])
    assert ("close",) in tello.log


def test_reconnect_rebuilds_the_tello_without_leaving_the_hardware():
    """A rebooted Tello answers nothing, and its old adapter cannot be revived.
    The operator gets a new one — still in hardware mode throughout."""
    from comp1.sim.drone import SimDrone

    built = []

    def factory():
        built.append(ClosableTello())
        return built[-1]

    app = create_app(SimDrone(seed=2, delay=0), tello_factory=factory)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        collect_where(ws, "drone_mode", lambda m: m["mode"] == "tello" and not m["switching"])

        ws.send_json({"type": "reconnect_drone"})
        collect_where(ws, "drone_mode", lambda m: not m["switching"])
        assert app.state.drone is built[1]
    assert ("close",) in built[0].log  # the stale one released its video port
    assert ("connect",) in built[1].log


def test_a_dropped_link_reconnects_by_itself(monkeypatch):
    """Nothing in the protocol announces a reboot, so the watchdog is the only
    thing between a student and restarting the whole program."""
    from comp1.sim.drone import SimDrone

    import comp1.server as server_module

    monkeypatch.setattr(server_module, "LINK_CHECK_INTERVAL", 0.01)

    class FlakyTello(ClosableTello):
        def reconnect(self):
            self.log.append(("reconnect",))
            self.link_ok = True

    tello = FlakyTello()
    app = create_app(SimDrone(seed=2, delay=0), tello_factory=lambda: tello)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        collect_where(ws, "drone_mode", lambda m: m["mode"] == "tello" and not m["switching"])

        tello.link_ok = False
        assert collect_where(ws, "drone_link", lambda m: not m["ok"], limit=800)
        assert collect_where(ws, "drone_link", lambda m: m["ok"], limit=800)
    assert ("reconnect",) in tello.log


def test_a_disconnected_drone_refuses_to_fly(monkeypatch):
    """Better than a mission that dies on its first unanswered command."""
    from comp1.sim.drone import SimDrone

    tello = ClosableTello()
    app = create_app(SimDrone(seed=2, delay=0), tello_factory=lambda: tello)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "drone_mode")
        ws.send_json({"type": "switch_drone", "mode": "tello"})
        collect_where(ws, "drone_mode", lambda m: m["mode"] == "tello" and not m["switching"])
        tello.link_ok = False
        ws.send_json(
            {
                "type": "run",
                "program": {"version": 1, "blocks": [{"id": "a", "op": "takeoff"}]},
            }
        )
        assert "not connected" in collect_until(ws, "error", limit=200)["message"]
    assert ("takeoff",) not in tello.log


def test_a_launch_with_the_drone_missing_still_starts_the_server():
    """Joining the Tello Wi-Fi late is an ordinary mistake, not a relaunch."""

    class UnavailableTello(MockDrone):
        mode = "tello"

        def connect(self):
            raise RuntimeError("not reachable")

    app = create_app(UnavailableTello())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        link = collect_until(ws, "drone_link")
        assert link["ok"] is False
        assert "could not connect" in link["message"]


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
    from comp1.sim import scenery
    from comp1.sim.drone import SimDrone

    drone = SimDrone(scenery_name="corridor", seed=1, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "sceneries")
        ws.send_json({"type": "layout", "fires": [{"x": 1.25, "y": 5.0}]})
        scene = collect_where(
            ws,
            "scene",
            lambda m: [k["kind"] for k in m["scene"]["markers"]].count("fire") <= 1,
        )
        fires = [k for k in scene["scene"]["markers"] if k["kind"] == "fire"]
        assert len(fires) <= 1
        # whatever survived validation, the destination is untouched
        assert any(k["kind"] == "destination" for k in scene["scene"]["markers"])
    assert scenery.MIN_FIRE_SEP_M > 0


def test_clearing_the_layout_leaves_the_destination_alone():
    from comp1.sim.drone import SimDrone

    drone = SimDrone(scenery_name="corridor", seed=1, delay=0)
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "sceneries")
        ws.send_json({"type": "layout", "fires": []})
        collect_where(
            ws,
            "scene",
            lambda m: not [k for k in m["scene"]["markers"] if k["kind"] == "fire"],
        )
    assert drone.world.fires == []
    assert drone.world.destination is not None


def test_arena_edits_are_refused_while_a_mission_is_running():
    """Moving the markers out from under a flying mission is the same mistake as
    resetting mid-flight, and gets the same answer."""
    from comp1.sim.drone import SimDrone

    app = create_app(SimDrone(seed=2, delay=0.2))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
        collect_until(ws, "reset", limit=200)  # the pre-run reset
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
    from comp1.sim.world import DESTINATION, FIRE, Marker, World

    world = World(
        size_m=2.5,
        length_m=10.0,
        start=(1.25, 0.6),
        name="corridor",
        markers=[Marker(1.25, 9.4, DESTINATION), Marker(1.25, 3.0, FIRE)],
    )
    return SimDrone(world=world, delay=0)


# start pad -> fire -> destination, then land
FLY_THE_CORRIDOR = {
    "version": 2,
    "blocks": [
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "move", "dir": "forward", "cm": 240},
        {"id": "c", "op": "mark_found"},
        {"id": "d", "op": "move", "dir": "forward", "cm": 500},
        {"id": "e", "op": "move", "dir": "forward", "cm": 140},
        {"id": "f", "op": "land"},
    ],
}


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
    program = {
        "version": 2,
        "blocks": [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "mark_found"},
            {"id": "c", "op": "land"},
        ],
    }
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "run", "program": program})
        msg = collect_where(ws, "mission", lambda m: m["signal"] is not None)
        assert msg["signal"] == "no target nearby"
        assert msg["found"] == 0 and msg["state"] == "flying"


def test_a_drone_with_no_arena_reports_no_mission_state():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "mark_found"},
                        {"id": "c", "op": "land"},
                    ],
                },
            }
        )
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
        assert "found_count" in seen  # the raw counter still works


def grey_frame():
    return np.full((480, 640, 3), (90, 70, 60), np.uint8)


def all_red_frame():
    return np.full((480, 640, 3), (0, 0, 220), np.uint8)


def test_auto_calibration_suggests_bands_without_a_selection():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")  # latest raw frame is now available
        ws.send_json({"type": "vision_auto"})
        msg = collect_until(ws, "vision_suggestion")
    assert msg["config"]["lower1"][0] == 0
    assert msg["config"]["lower2"][0] >= 170
    assert len(msg["preview_jpeg"]) > 100
    x0, y0, x1, y1 = msg["roi"]
    assert 0.0 <= x0 < x1 <= 1.0 and 0.0 <= y0 < y1 <= 1.0


def test_a_manual_selection_echoes_its_region_back():
    app = create_app(MockDrone(frame_factory=red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_sample", "roi": [0.42, 0.38, 0.58, 0.62]})
        msg = collect_until(ws, "vision_suggestion")
    assert msg["roi"] == [0.42, 0.38, 0.58, 0.62]


def test_auto_calibration_reports_when_no_marker_is_in_view():
    app = create_app(MockDrone(frame_factory=grey_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_auto"})
        error = collect_until(ws, "vision_error")
    assert "no red marker" in error["message"]


def test_auto_calibration_uses_the_servers_active_config():
    """A min_area_ratio tightened past what the red_frame marker clears must
    make vision_auto fail to locate it -- proving the server threads its
    active cfg into auto_suggest_hsv rather than a code-default prior."""
    too_tight = VisionConfig(min_area_ratio=0.5)
    app = create_app(MockDrone(frame_factory=red_frame), cfg=too_tight)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_auto"})
        error = collect_until(ws, "vision_error")
    assert "no red marker" in error["message"]


def test_a_suggestion_matching_most_of_the_scene_is_refused():
    app = create_app(MockDrone(frame_factory=all_red_frame))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "frame")
        ws.send_json({"type": "vision_sample", "roi": [0.4, 0.4, 0.6, 0.6]})
        error = collect_until(ws, "vision_error")
    assert "too much of the scene" in error["message"]


def test_auto_calibration_is_refused_during_a_mission():
    from comp1.sim.drone import SimDrone

    app = create_app(SimDrone(seed=2, delay=0.2))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json(
            {
                "type": "run",
                "program": {
                    "version": 1,
                    "blocks": [
                        {"id": "a", "op": "takeoff"},
                        {"id": "b", "op": "move", "dir": "forward", "cm": 100},
                    ],
                },
            }
        )
        collect_until(ws, "reset", limit=200)
        ws.send_json({"type": "vision_auto"})
        error = collect_until(ws, "vision_error", limit=200)
    assert "stop the mission" in error["message"]
