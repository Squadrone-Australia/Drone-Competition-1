import sys

import pytest

import comp1.__main__ as cli
from comp1.sim.drone import SimDrone


def test_default_launch_uses_the_simulator(monkeypatch):
    seen = {}

    def fake_create_app(drone, **kwargs):
        seen["drone"] = drone
        return object()

    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser"])
    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)

    cli.main()

    assert isinstance(seen["drone"], SimDrone)


def test_flight_config_reaches_a_tello_built_later_in_the_session(
    monkeypatch, tmp_path
):
    """The browser can switch to hardware mid-session, and that Tello must get
    the same --flight-config the CLI was given rather than the code defaults."""
    cfg_file = tmp_path / "flight.toml"
    cfg_file.write_text("flip_recover_cm = 45\n")
    seen = {}

    def fake_create_app(drone, **kwargs):
        seen.update(kwargs)
        return object()

    class FakeTello:
        def __init__(self):
            pass

    monkeypatch.setattr(
        sys, "argv", ["comp1", "--no-browser", "--flight-config", str(cfg_file)]
    )
    monkeypatch.setattr(cli, "create_app", fake_create_app)
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)

    cli.main()

    import comp1.drone.tello as t

    monkeypatch.setattr(t, "Tello", FakeTello)
    assert seen["tello_factory"]().flight.flip_recover_cm == 45


def test_a_missing_flight_config_is_reported_not_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(
        sys,
        "argv",
        ["comp1", "--no-browser", "--flight-config", str(tmp_path / "nope.toml")],
    )
    with pytest.raises(SystemExit):
        cli.main()


def test_browser_launch_reloads_an_existing_tab(monkeypatch):
    opened = []

    class ImmediateTimer:
        def __init__(self, _delay, callback):
            self.callback = callback

        def start(self):
            self.callback()

    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kwargs: object())
    monkeypatch.setattr(cli.uvicorn, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(cli.time, "time_ns", lambda: 12345)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    cli.main()

    assert opened == ["http://localhost:8765/?launch=12345"]
