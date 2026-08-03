import sys

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
