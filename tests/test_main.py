import sys
import threading
import types

import pytest

import comp1.__main__ as cli
from comp1.sim.drone import SimDrone


class FakeServer:
    """Stands in for uvicorn's Server.

    ``main`` builds the server itself rather than calling ``uvicorn.run``,
    because the app needs a handle on it: with no console and no tray icon,
    "close the program" can only come from the window in front of it, and
    that means something has to end the serve loop from inside. These tests
    hold that seam — patching ``uvicorn.run`` would no longer stop a real
    port being bound.
    """

    latest = None
    #: Window tests flip this: behind a window, run() blocks on a worker thread
    #: exactly as uvicorn's does, and the point of the exercise is what happens
    #: while it is still running.
    block = False

    def __init__(self, config):
        self.config = config
        self.started = False
        self._should_exit = False
        self._exit = threading.Event()
        FakeServer.latest = self

    @property
    def should_exit(self):
        return self._should_exit

    @should_exit.setter
    def should_exit(self, value):
        self._should_exit = value
        if value:
            self._exit.set()

    def run(self):
        self.started = True
        if FakeServer.block:
            self._exit.wait(5)


@pytest.fixture(autouse=True)
def no_real_server(monkeypatch):
    FakeServer.latest = None
    FakeServer.block = False
    monkeypatch.setattr(cli.uvicorn, "Server", FakeServer)
    # Nothing is listening in a test, but be explicit: a developer running the
    # suite with the app open must not have their launch reported as a clash.
    monkeypatch.setattr(cli, "_already_serving", lambda port: False)
    # The window is the default front door now, and whether the machine running
    # pytest happens to be able to open one is not something these tests may
    # depend on. The ones that want a window say so.
    monkeypatch.setattr(cli, "_window_wanted", lambda args: False)


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
    monkeypatch.setattr(
        cli.threading, "Timer", lambda *a, **k: type("T", (), {"start": lambda self: None})()
    )

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


class FakeNative:
    """Stands in for comp1.window.NativeWindow, which CI cannot construct."""

    latest = None

    def __init__(self, url, *, on_close):
        self.url = url
        self.on_close = on_close
        self.opened = 0
        self.closed = 0
        FakeNative.latest = self

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1


def fake_app():
    """An app object with only the state the close policy actually reads."""
    return types.SimpleNamespace(
        state=types.SimpleNamespace(
            installing=False, interp=None, quitting=False, request_quit=None
        )
    )


@pytest.fixture
def window(monkeypatch):
    """Run main() as though this machine can open a window of its own."""
    FakeNative.latest = None
    FakeServer.block = True
    monkeypatch.setattr(cli, "_window_wanted", lambda args: True)
    monkeypatch.setattr(cli.native_window, "NativeWindow", FakeNative)
    return FakeNative


def test_the_window_is_the_front_door_when_the_machine_can_show_one(
    monkeypatch, window
):
    """No browser tab, and the server ran behind the window rather than in it."""
    opened = []
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: fake_app())
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    cli.main()

    assert opened == []
    assert window.latest.opened == 1
    assert window.latest.url == "http://localhost:8765/"
    assert FakeServer.latest.started is True


def test_closing_the_window_stops_the_server(monkeypatch, window):
    """The window going is the end of the program, however it went."""
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: fake_app())

    cli.main()

    assert FakeServer.latest.should_exit is True


def test_the_program_closing_itself_also_closes_the_window(monkeypatch, window):
    """Otherwise the server stops behind a window still showing the program."""
    seen = {}
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or fake_app())

    cli.main()

    seen["shutdown"]()
    assert window.latest.closed >= 1


def test_no_window_falls_back_to_the_browser(monkeypatch):
    """The Chromebook hub, and anyone who simply prefers a tab."""
    opened = []

    class ImmediateTimer:
        def __init__(self, _delay, callback):
            self.callback = callback

        def start(self):
            self.callback()

    made = []
    monkeypatch.setattr(sys, "argv", ["comp1", "--no-window"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: object())
    monkeypatch.setattr(cli.threading, "Timer", ImmediateTimer)
    monkeypatch.setattr(cli.time, "time_ns", lambda: 12345)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    monkeypatch.setattr(cli.native_window, "usable", lambda: made.append(True) or True)

    cli.main()

    assert opened == ["http://localhost:8765/?launch=12345"]
    assert made == []  # not even asked: the flag settles it before the probe


def test_no_browser_never_opens_a_window(monkeypatch):
    """--no-browser has always meant "open nothing", and still does."""
    made = []
    monkeypatch.setattr(sys, "argv", ["comp1", "--no-browser"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: object())
    monkeypatch.setattr(cli.native_window, "usable", lambda: made.append(True) or True)
    monkeypatch.setattr(cli.native_window, "NativeWindow", FakeNative)
    FakeNative.latest = None

    cli.main()

    assert made == []
    assert FakeNative.latest is None


def test_a_window_launch_arms_the_same_idle_shutdown(monkeypatch, window):
    """The countdown's remaining job is a renderer that died behind the frame."""
    seen = {}
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: seen.update(kw) or fake_app())

    cli.main()

    assert seen["idle_timeout"] == cli.DEFAULT_IDLE_TIMEOUT


def test_a_server_that_never_starts_says_so_instead_of_a_blank_window(
    monkeypatch, window
):
    """WebView2 shows its own error page for a refused port, and never retries."""
    told = []
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: fake_app())
    monkeypatch.setattr(FakeServer, "run", lambda self: None)  # dies without binding
    monkeypatch.setattr(cli, "_tell_user", told.append)

    cli.main()

    assert window.latest.opened == 0
    assert "could not start" in told[0]
    assert "8765" in told[0]


def closing(monkeypatch, window, app):
    """main() up to the point where the window exists, then ask it to close."""
    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: app)
    cli.main()
    return window.latest.on_close


def test_the_window_closing_asks_the_program_to_land_and_quit(monkeypatch, window):
    asked = []
    app = fake_app()
    app.state.request_quit = lambda: asked.append(True)

    answer = closing(monkeypatch, window, app)(lambda *a: True)

    assert asked == [True]
    # The window stays up while the program winds itself down, and the program
    # is what closes it — one way out, however the close was asked for.
    assert answer == cli.native_window.CLOSE_WAIT


def test_closing_over_a_flying_mission_asks_first(monkeypatch, window):
    """A brushed X must not land a real Tello without anybody saying so."""
    asked = []
    app = fake_app()
    app.state.interp = object()
    app.state.request_quit = lambda: asked.append(True)

    seen = []

    def refuse(title, message):
        seen.append(message)
        return False

    answer = closing(monkeypatch, window, app)(refuse)

    assert answer == cli.native_window.CLOSE_STAY
    assert asked == []
    assert "flying" in seen[0]


def test_a_confirmed_close_over_a_flying_mission_goes_ahead(monkeypatch, window):
    asked = []
    app = fake_app()
    app.state.interp = object()
    app.state.request_quit = lambda: asked.append(True)

    answer = closing(monkeypatch, window, app)(lambda *a: True)

    assert answer == cli.native_window.CLOSE_WAIT
    assert asked == [True]


def test_an_installing_update_is_never_stood_in_front_of(monkeypatch, window):
    """Inno is already closing this process; blocking it only earns a kill."""
    asked = []
    app = fake_app()
    app.state.installing = True
    app.state.interp = object()  # an update cannot start mid-flight, but be sure
    app.state.request_quit = lambda: asked.append(True)

    answer = closing(monkeypatch, window, app)(lambda *a: False)

    assert answer == cli.native_window.CLOSE_NOW
    assert asked == []


def test_a_close_with_no_event_loop_left_still_closes(monkeypatch, window):
    """Nothing about a half-dead server may produce a window that will not shut."""
    app = fake_app()  # request_quit is None: lifespan never got as far as it

    answer = closing(monkeypatch, window, app)(lambda *a: True)

    assert answer == cli.native_window.CLOSE_NOW
    assert FakeServer.latest.should_exit is True


def test_a_window_that_will_not_open_falls_back_to_the_browser(monkeypatch, window):
    """usable() can still be wrong, and being wrong must not end the session."""
    opened = []

    class Broken(FakeNative):
        def open(self):
            raise RuntimeError("WebView2 gave up")

    monkeypatch.setattr(sys, "argv", ["comp1"])
    monkeypatch.setattr(cli, "create_app", lambda drone, **kw: fake_app())
    monkeypatch.setattr(cli.native_window, "NativeWindow", Broken)
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)
    # The fallback waits on the serve loop, which is the idle shutdown's job to
    # end; here the server simply returns.
    FakeServer.block = False

    cli.main()

    assert opened == ["http://localhost:8765/"]
