import sys

import pytest

import comp1.__main__ as cli
from comp1.sim.drone import SimDrone


class FakeServer:
    """Stands in for uvicorn's Server.

    ``main`` builds the server itself rather than calling ``uvicorn.run``,
    because the app needs a handle on it: with no console and no tray icon,
    "close the program" can only come from the browser, and that means
    something has to end the serve loop from inside. These tests hold that
    seam — patching ``uvicorn.run`` would no longer stop a real port being
    bound.
    """

    latest = None

    def __init__(self, config):
        self.config = config
        self.should_exit = False
        FakeServer.latest = self

    def run(self):
        pass


@pytest.fixture(autouse=True)
def no_real_server(monkeypatch):
    FakeServer.latest = None
    monkeypatch.setattr(cli.uvicorn, "Server", FakeServer)
    # Nothing is listening in a test, but be explicit: a developer running the
    # suite with the app open must not have their launch reported as a clash.
    monkeypatch.setattr(cli, "_already_serving", lambda port: False)


def test_default_launch_uses_the_simulator(monkeypatch):
    seen = {}

    def fake_create_app(drone, **kwargs):
        seen["drone"] = drone
        return object()

    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser"])
    monkeypatch.setattr(cli, "create_app", fake_create_app)
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
    monkeypatch.setattr(cli.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(cli.time, "time_ns", lambda: 12345)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    cli.main()

    assert opened == ["http://localhost:8765/?launch=12345"]


def test_the_browser_can_close_the_program(monkeypatch):
    """The Quit button's other half: the hook has to reach the serve loop."""
    seen = {}
    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or object())

    cli.main()

    assert FakeServer.latest.should_exit is False
    seen["shutdown"]()
    assert FakeServer.latest.should_exit is True


def test_a_launched_browser_arms_the_idle_shutdown(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or object())
    monkeypatch.setattr(cli.threading, "Timer", lambda *a, **k: type("T", (), {"start": lambda self: None})())

    cli.main()

    assert seen["idle_timeout"] == cli.DEFAULT_IDLE_TIMEOUT


def test_no_browser_means_no_idle_shutdown(monkeypatch):
    """A terminal session owns its own lifetime — quitting it is Ctrl+C."""
    seen = {}
    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or object())

    cli.main()

    assert seen["idle_timeout"] is None


def test_a_second_launch_says_so_instead_of_dying_silently(monkeypatch):
    told = []
    started = []
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "_already_serving", lambda port: True)
    monkeypatch.setattr(cli, "_tell_user", told.append)
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: started.append(True))

    cli.main()

    assert started == []                      # no second server was built
    assert "already running" in told[0]
    assert "8765" in told[0]                  # where to find the one that is
    assert "Close program" in told[0]         # and how to close it


def test_idle_timeout_can_be_set_and_turned_off(monkeypatch):
    """A demonstration laptop left on a stand all day must not close itself."""
    seen = {}
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or object())

    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser", "--idle-timeout", "5"])
    cli.main()
    assert seen["idle_timeout"] == 5

    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser", "--idle-timeout", "0"])
    cli.main()
    assert seen["idle_timeout"] is None
