"""Version reporting, saved preferences, and the update banner — server side.

``create_app`` takes both the settings path and the update check as arguments
specifically so this file can exercise them without writing to the developer's
own profile or opening a socket.
"""

import json

import pytest
from fastapi.testclient import TestClient

from comp1 import __version__, server, settings, update
from comp1.drone.mock import MockDrone
from comp1.server import create_app
from comp1.vision.config import VisionConfig

from .test_server import collect_until, red_frame

RELEASE = update.Release(
    version="9.9.9",
    notes="Everything is better now.",
    url="https://example.invalid/comp1-Setup-9.9.9.exe",
    sha256="0" * 64,
    filename="comp1-Setup-9.9.9.exe",
)

BLUE = {
    "lower1": [100, 80, 70],
    "upper1": [130, 255, 255],
    "lower2": [100, 80, 70],
    "upper2": [130, 255, 255],
}


# ------------------------------------------------------------------- settings


def test_connect_reports_the_version_and_saved_settings(tmp_path):
    path = tmp_path / "settings.json"
    settings.save(settings.Settings(drone="tello", scenery="corridor"), path)
    app = create_app(MockDrone(), settings_path=path)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        msg = collect_until(ws, "settings")
    assert msg["version"] == __version__
    assert msg["persisted"] is True
    assert msg["settings"]["drone"] == "tello"
    assert msg["settings"]["scenery"] == "corridor"


def test_save_settings_persists_and_echoes(tmp_path):
    path = tmp_path / "settings.json"
    app = create_app(MockDrone(), settings_path=path)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")
        ws.send_json({"type": "save_settings", "settings": {"scenery": "corridor"}})
        echoed = collect_until(ws, "settings")
    assert echoed["settings"]["scenery"] == "corridor"
    assert settings.load(path).scenery == "corridor"


def test_without_a_settings_path_nothing_is_written(tmp_path):
    # The default for tests and for a developer run: the server must not leave
    # preferences in the user's profile just because someone clicked something.
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert collect_until(ws, "settings")["persisted"] is False
        ws.send_json({"type": "save_settings", "settings": {"scenery": "corridor"}})
        assert collect_until(ws, "settings")["settings"]["scenery"] == "arena"
    assert list(tmp_path.iterdir()) == []


def test_save_settings_survives_hostile_field_names(tmp_path):
    """The payload is untrusted JSON, not a call signature.

    A field named after one of ``settings.update``'s own parameters used to
    collide with it and take the socket down with a TypeError.
    """
    path = tmp_path / "settings.json"
    app = create_app(MockDrone(), settings_path=path)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")
        ws.send_json(
            {
                "type": "save_settings",
                "settings": {"path": "C:/somewhere/else", "drone": "tello"},
            }
        )
        echoed = collect_until(ws, "settings")
        # still alive, and the field nobody asked for was simply ignored
        assert echoed["settings"]["drone"] == "tello"
        ws.send_json({"type": "save_settings", "settings": "not an object at all"})
        assert collect_until(ws, "settings")["settings"]["drone"] == "tello"
    assert settings.load(path).drone == "tello"


def test_applied_hsv_survives_the_session(tmp_path):
    path = tmp_path / "settings.json"
    app = create_app(
        MockDrone(frame_factory=red_frame),
        cfg=VisionConfig(lower1=(1, 90, 70)),
        settings_path=path,
    )
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "vision_config")
        ws.send_json({"type": "vision_apply", "config": BLUE})
        collect_until(ws, "vision_config")
    assert settings.load(path).hsv == BLUE
    # ...and "restore startup settings" forgets it again, rather than leaving a
    # calibration nobody can see in the UI still governing the next launch.
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "vision_config")
        ws.send_json({"type": "vision_reset"})
        collect_until(ws, "vision_config")
    assert settings.load(path).hsv == {}


# --------------------------------------------------------------------- update


def test_update_available_is_broadcast_and_replayed_to_late_clients(monkeypatch):
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)
    app = create_app(MockDrone(), update_check=lambda: RELEASE)
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            msg = collect_until(ws, "update_available", limit=200)
            assert msg["version"] == "9.9.9"
            assert msg["current"] == __version__
        # A browser opened after the check finished must still see the banner.
        with client.websocket_connect("/ws") as ws:
            assert collect_until(ws, "update_available")["version"] == "9.9.9"


def test_no_check_means_no_banner():
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")
        ws.send_json({"type": "install_update"})
        assert "no update" in collect_until(ws, "error")["message"]


def test_a_check_that_finds_nothing_says_nothing(monkeypatch):
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)
    app = create_app(MockDrone(), update_check=lambda: None)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")
        for _ in range(20):
            received = ws.receive()
            if received.get("text"):
                assert json.loads(received["text"])["type"] != "update_available"


def test_a_failing_check_never_reaches_the_browser(monkeypatch):
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)

    def explode():
        raise RuntimeError("proxy said no")

    app = create_app(MockDrone(), update_check=explode)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")
        for _ in range(20):
            received = ws.receive()
            if received.get("text"):
                assert json.loads(received["text"])["type"] not in (
                    "update_available",
                    "error",
                )


def test_installing_downloads_verifies_and_launches(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)
    installer = tmp_path / RELEASE.filename
    installer.write_bytes(b"MZ")
    launched = []
    monkeypatch.setattr(update, "download", lambda release: installer)
    monkeypatch.setattr(update, "launch_installer", lambda path: launched.append(path))
    app = create_app(MockDrone(), update_check=lambda: RELEASE)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "update_available", limit=200)
        ws.send_json({"type": "install_update"})
        assert collect_until(ws, "update_progress")["state"] == "downloading"
        assert collect_until(ws, "update_progress")["state"] == "installing"
    assert launched == [installer]


def test_a_failed_download_is_reported_not_swallowed(monkeypatch):
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)

    def refuse(release):
        raise update.UpdateError("the downloaded installer did not match its checksum")

    monkeypatch.setattr(update, "download", refuse)
    app = create_app(MockDrone(), update_check=lambda: RELEASE)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "update_available", limit=200)
        ws.send_json({"type": "install_update"})
        failure = collect_until(ws, "update_progress", limit=100)
        while failure["state"] != "failed":
            failure = collect_until(ws, "update_progress", limit=100)
    assert "checksum" in failure["message"]


def test_updating_is_refused_while_something_is_flying(monkeypatch):
    """The installer's first act is to close this process.

    Doing that to a drone in the air leaves it hovering with nothing flying it,
    which is why this guard matters more than the calibration one it copies.
    """
    monkeypatch.setattr(server, "UPDATE_CHECK_DELAY", 0.0)
    monkeypatch.setattr(
        update,
        "launch_installer",
        lambda path: pytest.fail("the installer must not run mid-mission"),
    )
    app = create_app(MockDrone(), update_check=lambda: RELEASE)
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "update_available", limit=200)

        class StillFlying:
            def request_stop(self):
                pass

        app.state.interp = StillFlying()
        ws.send_json({"type": "install_update"})
        assert "stop the mission" in collect_until(ws, "error")["message"]
        app.state.interp = None
