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


def test_estop_calls_emergency():
    drone = MockDrone()
    app = create_app(drone)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "estop"})
        collect_until(ws, "estopped")
    assert ("emergency",) in drone.log
