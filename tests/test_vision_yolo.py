"""The AI marker detector (comp1/vision/yolo.py) and the live switch to it.

The decoding tests drive ``YoloFinder`` with a fake session that returns a
hand-built head tensor, so they need neither onnxruntime nor the model file.
One smoke test runs the real shipped model, and skips where it cannot.
"""

import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from comp1 import settings as settings_store
from comp1.drone.mock import MockDrone
from comp1.hints import fix_for
from comp1.server import create_app
from comp1.vision import yolo
from comp1.vision.config import VisionConfig
from comp1.vision.detector import TargetTracker

W, H = 960, 720  # the Tello's stream
SIZE = 640
SCALE = SIZE / W  # 960x720 letterboxes to 640x480 ...
PAD_Y = (SIZE - H * SCALE) / 2  # ... with 80 px of grey above and below


def disc_row(cx, cy, r, score, kconf=1.0):
    """One anchor's 20 values for a disc at frame pixel (cx, cy), radius r."""
    lx, ly, lr = cx * SCALE, cy * SCALE + PAD_Y, r * SCALE
    kpts = [(lx, ly), (lx, ly - lr), (lx + lr, ly), (lx, ly + lr), (lx - lr, ly)]
    row = [lx, ly, 2 * lr, 2 * lr, score]
    for x, y in kpts:
        row += [x, y, kconf]
    return row


def head(*rows, anchors=50):
    raw = np.zeros((1, 20, anchors), np.float32)
    for i, row in enumerate(rows):
        raw[0, :, i] = row
    return raw


class FakeSession:
    def __init__(self, raw):
        self.raw = raw
        self.fed = None

    def get_inputs(self):
        return [SimpleNamespace(name="images", shape=[1, 3, SIZE, SIZE])]

    def run(self, _outputs, feed):
        self.fed = feed
        return [self.raw]


def finder(*rows):
    return yolo.YoloFinder(session=FakeSession(head(*rows)))


FRAME = np.zeros((H, W, 3), np.uint8)
CFG = VisionConfig(detector="yolo")


def test_letterbox_matches_the_training_layout():
    image, scale, pad_x, pad_y = yolo.letterbox(FRAME, SIZE)
    assert image.shape == (SIZE, SIZE, 3)
    assert scale == pytest.approx(SCALE)
    assert (pad_x, pad_y) == (0.0, 80.0)
    assert (image[0, 0] == yolo.PAD_VALUE).all()  # grey border, not black


def test_the_input_tensor_is_rgb_nchw_in_0_to_1():
    f = finder()
    blue = np.zeros((H, W, 3), np.uint8)
    blue[..., 0] = 255  # BGR blue
    f.find_targets(blue, CFG)
    tensor = f._session.fed["images"]
    assert tensor.shape == (1, 3, SIZE, SIZE) and tensor.dtype == np.float32
    middle = tensor[0, :, SIZE // 2, SIZE // 2]
    assert middle.tolist() == [0.0, 0.0, 1.0]  # blue is the third channel in RGB


def test_a_disc_comes_back_in_frame_pixels_and_metres():
    (t,) = finder(disc_row(480, 360, 60, 0.9)).find_targets(FRAME, CFG)
    assert (t.cx, t.cy) == pytest.approx((0.5, 0.5), abs=1e-4)
    assert t.radius_norm == pytest.approx(60 / W, rel=1e-3)
    # The colour detector's formula, so switching never changes what "1 m" means.
    assert t.distance_m == pytest.approx(
        CFG.intrinsics.distance_m(60 / W, CFG.marker_radius_m), rel=1e-3
    )
    assert t.bearing_deg == pytest.approx(0, abs=0.01)
    assert t.position == "center"
    assert t.score == pytest.approx(0.9)
    assert isinstance(t.distance_m, float)  # numpy scalars break the telemetry JSON
    json.dumps(t.__dict__)


def test_low_scores_are_dropped_and_duplicates_suppressed():
    rows = [
        disc_row(480, 360, 60, 0.9),
        disc_row(482, 361, 60, 0.8),  # the same disc from the next anchor
        disc_row(200, 360, 30, 0.3),  # below yolo_score_min
    ]
    targets = finder(*rows).find_targets(FRAME, CFG)
    assert len(targets) == 1 and targets[0].score == pytest.approx(0.9)


def test_targets_are_nearest_first_like_the_colour_detector():
    rows = [disc_row(200, 360, 30, 0.99), disc_row(700, 360, 90, 0.6)]
    near, far = finder(*rows).find_targets(FRAME, CFG)
    assert near.distance_m < far.distance_m
    assert near.position == "right" and far.position == "left"


def test_range_falls_back_from_vertical_to_horizontal_to_the_box():
    k = np.array(
        [[480, 360, 1], [480, 300, 1], [540, 360, 1], [480, 420, 1], [420, 360, 1]], float
    )
    box = (400, 280, 560, 440)  # 80 px radius, deliberately unlike the keypoints
    assert yolo._radius_px(box, k, W, H) == pytest.approx(60)
    k[yolo.TOP, 2] = 0.1  # top keypoint unsure: use left-right
    assert yolo._radius_px(box, k, W, H) == pytest.approx(60)
    k[yolo.LEFT, 2] = 0.1  # both pairs gone: the box
    assert yolo._radius_px(box, k, W, H) == pytest.approx(80)


def test_an_unsure_centre_keypoint_falls_back_to_the_box_centre():
    row = disc_row(480, 360, 60, 0.9)
    row[5:8] = [100 * SCALE, 100 * SCALE + PAD_Y, 0.1]  # a wild, unconfident centre
    (t,) = finder(row).find_targets(FRAME, CFG)
    assert (t.cx, t.cy) == pytest.approx((0.5, 0.5), abs=1e-4)


def test_the_tracker_uses_the_finder_the_config_names(monkeypatch):
    calls = []
    monkeypatch.setattr(yolo, "find_targets", lambda f, c: calls.append(c) or [])
    TargetTracker(VisionConfig(detector="yolo")).update(FRAME)
    assert len(calls) == 1
    TargetTracker(VisionConfig()).update(FRAME)  # colour: the AI one is not touched
    assert len(calls) == 1


def test_available_says_why_instead_of_raising(tmp_path):
    ok, reason = yolo.available(tmp_path / "nothing.onnx")
    assert not ok and "missing" in reason


def test_the_unavailable_message_has_a_hint():
    assert "Colour" in fix_for("the AI model is not available: onnxruntime is not installed")


@pytest.mark.skipif(not yolo.available()[0], reason="onnxruntime or the model is absent")
def test_the_shipped_model_runs_and_finds_a_painted_disc():
    frame = np.full((H, W, 3), 235, np.uint8)
    cv2.circle(frame, (480, 360), 70, (30, 30, 210), -1)
    targets = yolo.YoloFinder().find_targets(frame, CFG)
    assert targets, "the model found nothing on a plain red disc"
    assert (targets[0].cx, targets[0].cy) == pytest.approx((0.5, 0.5), abs=0.03)
    # ...and nothing on an empty sheet.
    assert yolo.YoloFinder().find_targets(np.full((H, W, 3), 235, np.uint8), CFG) == []


# --- the live switch ---------------------------------------------------------


def collect_until(ws, want_type, limit=60):
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
            continue
        data = json.loads(msg["text"])
        if data["type"] == want_type:
            return data
    raise AssertionError(f"no {want_type} message")


@pytest.fixture
def model_present(monkeypatch):
    monkeypatch.setattr(yolo, "available", lambda *_: (True, None))
    # No real inference behind a switch in these tests.
    monkeypatch.setattr(yolo, "find_targets", lambda f, c: [])


def test_the_detector_is_announced_on_connect(model_present):
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = collect_until(ws, "vision_detector")
    assert msg == {
        "type": "vision_detector",
        "detector": "hsv",
        "yolo_available": True,
        "yolo_reason": None,
    }


def test_switching_is_live_broadcast_and_remembered(model_present, tmp_path):
    path = tmp_path / "settings.json"
    app = create_app(MockDrone(), settings_path=path)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "vision_detector")
        ws.send_json({"type": "vision_detector", "detector": "yolo"})
        assert collect_until(ws, "vision_detector")["detector"] == "yolo"
        assert app.state.tracker._find is yolo.find_targets
        # Colour tuning is refused while the AI model is live...
        ws.send_json({"type": "vision_auto"})
        assert "Colour detector" in collect_until(ws, "vision_error")["message"]
        ws.send_json({"type": "vision_detector", "detector": "hsv"})
        assert collect_until(ws, "vision_detector")["detector"] == "hsv"
        ws.send_json({"type": "vision_detector", "detector": "yolo"})
        collect_until(ws, "vision_detector")
    assert settings_store.load(path).detector == "yolo"


def test_an_unavailable_model_is_refused_with_the_reason(monkeypatch):
    monkeypatch.setattr(yolo, "available", lambda *_: (False, "onnxruntime is not installed"))
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        hello = collect_until(ws, "vision_detector")
        assert hello["yolo_available"] is False
        ws.send_json({"type": "vision_detector", "detector": "yolo"})
        error = collect_until(ws, "error")
        resync = collect_until(ws, "vision_detector")
    assert "not available: onnxruntime" in error["message"] and error["hint"]
    assert resync["detector"] == "hsv"


def test_switching_is_refused_during_a_mission(model_present):
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
        ws.send_json({"type": "vision_detector", "detector": "yolo"})
        error = collect_until(ws, "error", limit=200)
    assert "stop the mission" in error["message"]
    assert app.state.tracker._cfg.detector == "hsv"


def test_a_bogus_detector_name_is_refused(model_present):
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "vision_detector", "detector": "magic"})
        assert "unknown detector" in collect_until(ws, "error")["message"]
