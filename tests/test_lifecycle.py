"""How the program stops.

The packaged build is windowed: no console to close, no icon by the clock. The
browser page is the only window there is, so closing it — deliberately with the
button, or accidentally by shutting every tab — has to be what ends the process.
Before this existed the only way out was Task Manager, and an abandoned run kept
the aircraft's video port for the life of the session.
"""

import time

from fastapi.testclient import TestClient

from comp1.drone.mock import MockDrone
from comp1.server import create_app

from .test_server import collect_until


def wait_for(predicate, timeout=3.0):
    """Give the idle task room to run. It lives on the app's own event loop,
    which TestClient drives from another thread, so the test simply waits."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_quit_stops_the_program():
    stopped = []
    app = create_app(MockDrone(), shutdown=lambda: stopped.append(True))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert collect_until(ws, "settings")["can_quit"] is True
        ws.send_json({"type": "quit"})
        collect_until(ws, "quitting")
    assert stopped == [True]


def test_without_a_shutdown_hook_the_button_is_not_offered():
    # A copy run from a terminal is closed with Ctrl+C. Offering a button that
    # cannot do anything is worse than offering none.
    app = create_app(MockDrone())
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        assert collect_until(ws, "settings")["can_quit"] is False
        ws.send_json({"type": "quit"})
        assert "Ctrl+C" in collect_until(ws, "error")["message"]


def test_quit_is_refused_while_something_is_flying():
    stopped = []
    app = create_app(MockDrone(), shutdown=lambda: stopped.append(True))
    with TestClient(app) as client, client.websocket_connect("/ws") as ws:
        collect_until(ws, "settings")

        class StillFlying:
            def request_stop(self):
                pass

        app.state.interp = StillFlying()
        ws.send_json({"type": "quit"})
        assert "stop the mission" in collect_until(ws, "error")["message"]
        app.state.interp = None
    assert stopped == []


def test_the_last_browser_window_closing_ends_the_program():
    stopped = []
    app = create_app(
        MockDrone(), shutdown=lambda: stopped.append(True), idle_timeout=0.05
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            collect_until(ws, "settings")
        # The socket is gone; the countdown starts here.
        assert wait_for(lambda: stopped)
    assert stopped == [True]


def test_nothing_closes_before_the_first_window_arrives():
    """The server comes up a second or so ahead of the browser it launched.

    Counting idle time from startup would close the program before the student
    ever saw it.
    """
    stopped = []
    app = create_app(
        MockDrone(), shutdown=lambda: stopped.append(True), idle_timeout=0.05
    )
    with TestClient(app) as client:
        client.get("/")
        assert not wait_for(lambda: stopped, timeout=0.5)
    assert stopped == []


def test_a_mission_holds_the_program_open():
    """A student watching a flight from another tab has not finished with it."""
    stopped = []
    app = create_app(
        MockDrone(), shutdown=lambda: stopped.append(True), idle_timeout=0.05
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            collect_until(ws, "settings")

        class StillFlying:
            def request_stop(self):
                pass

        app.state.interp = StillFlying()
        assert not wait_for(lambda: stopped, timeout=0.5)
        app.state.interp = None
        assert wait_for(lambda: stopped)
    assert stopped == [True]


def test_a_reconnecting_page_is_not_a_closed_one():
    """A refresh drops the socket for a moment. That is not "I am finished"."""
    stopped = []
    app = create_app(
        MockDrone(), shutdown=lambda: stopped.append(True), idle_timeout=5.0
    )
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as ws:
            collect_until(ws, "settings")
        with client.websocket_connect("/ws") as ws:  # the reload lands
            collect_until(ws, "settings")
            assert not wait_for(lambda: stopped, timeout=0.5)


def test_quitting_reaches_every_open_tab():
    stopped = []
    app = create_app(MockDrone(), shutdown=lambda: stopped.append(True))
    with TestClient(app) as client:
        with client.websocket_connect("/ws") as one:
            with client.websocket_connect("/ws") as two:
                collect_until(one, "settings")
                collect_until(two, "settings")
                one.send_json({"type": "quit"})
                # the tab that did not ask still finds out, rather than sitting
                # on "disconnected, retrying" for ever
                assert collect_until(two, "quitting")["type"] == "quitting"
    assert stopped == [True]
