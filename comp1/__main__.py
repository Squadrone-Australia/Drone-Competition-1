import argparse
import logging
import socket
import sys
import threading
import time
import traceback
import webbrowser
from functools import partial
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn

from . import __version__
from . import settings as settings_store
from . import update as updater
from . import window as native_window
from .drone.config import DEFAULT_FLIGHT_CONFIG, FlightConfig
from .paths import is_frozen, log_file, settings_file
from .server import DEFAULT_IDLE_TIMEOUT, create_app
from .vision.calibration import CalibrationError, config_with_hsv
from .vision.config import DEFAULT_CONFIG, VisionConfig

#: How long the window waits for uvicorn to bind before it gives up and says so.
#: Generous, because lifespan startup connects to the drone and a Tello that is
#: not on the network takes several seconds to fail. The browser path pokes at a
#: flat one-second timer and simply loses that race when it happens; a window
#: cannot, because WebView2 shows its own error page for a refused connection
#: and never retries.
SERVER_START_TIMEOUT = 30.0
#: How long a stopped server gets to finish. ``timeout_graceful_shutdown``
#: already bounds the sockets; this bounds the lifespan shutdown behind them,
#: which is what releases the aircraft and its video port.
SERVER_JOIN_TIMEOUT = 10.0


def _setup_logging() -> None:
    """Installed, there is no console to print into — so print into a file.

    The packaged executable is windowed (that is the point: no black box, no
    Python), which also means a traceback has nowhere to go. Everything the
    server would have said goes to ``%LOCALAPPDATA%\\comp1\\logs`` instead, so a
    problem at a venue is still diagnosable afterwards.
    """
    if not is_frozen():
        return
    # Rotating, because this file is never cleaned up by anything else: a
    # laptop that lives in a cupboard between competitions should not slowly
    # fill up with a year of access logs.
    handler = RotatingFileHandler(
        log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    logging.basicConfig(level=logging.INFO, handlers=[handler])


def _tell_user(message: str) -> None:
    """Say something to whoever launched us, wherever they can see it.

    Installed there is no console, so the only place a person will ever read
    this is a message box. Run from a terminal, the terminal is the right place.
    """
    if is_frozen() and sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, message, "Squadrone Drone Coder", 0x40  # MB_ICONINFORMATION
            )
            return
        except Exception:
            pass
    print(message, file=sys.stderr)


def _already_serving(port: int) -> bool:
    """Is a copy of the program already holding our port?

    Bound to 127.0.0.1 only, so anything answering there is either us or
    something that has taken the port and would make us fail anyway. Checking
    beforehand is what turns "the exe flashed and vanished" into a sentence a
    teacher can act on: uvicorn's own failure is one line in a log file nobody
    knows exists.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 0)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return True
    return False


def _launch_url(port: int) -> str:
    """Where the front door points, whichever front door it is."""
    return f"http://localhost:{port}/"


def _window_wanted(args) -> bool:
    """Whether to try for a window of our own rather than the system browser.

    The single seam the tests reach for: whether a machine can open a window is
    not something a unit test may be allowed to depend on.
    """
    if args.no_browser or args.no_window:
        return False
    return native_window.usable()


def _wait_until_serving(server, thread, timeout=SERVER_START_TIMEOUT) -> bool:
    """True once uvicorn is accepting connections; False if it died trying.

    A window pointed at a port nothing is listening on shows the renderer's own
    error page and never retries, so it is not created until the server says so.
    A bind failure is a ``SystemExit`` inside uvicorn's startup, which on a
    worker thread is a thread that quietly ends: that is what ``is_alive()``
    catches, and without it this would wait the whole timeout for a server that
    is already gone.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if server.started:
            return True
        if not thread.is_alive():
            return False
        time.sleep(0.05)
    return thread.is_alive()  # slow, not dead — show the window anyway


def _serve_behind_the_window(server, native, fallback_url) -> bool:
    """Run uvicorn on a worker thread and give the main thread to the window.

    Both want the main thread and only one can have it. The window takes it
    because pywebview refuses to start anywhere else; uvicorn merely *prefers*
    it, and its own signal capture already stands aside off the main thread, so
    the serve loop here is the one the browser path runs, unchanged. Ctrl+C
    passes to the window, which is the right owner — as far as a student is
    concerned the window is the program.

    Daemon *and* joined: the join is what lets the lifespan shutdown release the
    aircraft, and the daemon flag is what stops a wedged server from keeping a
    windowless process alive for the rest of the afternoon.

    False means the server never came up, which is the one failure this cannot
    paper over. A window that will not open is not that failure — see below.
    """
    thread = threading.Thread(target=server.run, name="comp1-server", daemon=True)
    thread.start()
    if not _wait_until_serving(server, thread):
        return False
    try:
        native.open()  # blocks until the window is gone
    except Exception:  # noqa: BLE001 — a window is never worth losing a session over
        # usable() said yes and it still would not open. The browser reaches the
        # same program at the same URL, so there is a working front door to hand
        # the student instead of an error box, and the idle shutdown is back to
        # owning the lifetime exactly as it does on the browser path.
        logging.getLogger("comp1").exception(
            "the window would not open — falling back to the browser"
        )
        webbrowser.open(fallback_url)
        thread.join()
        return True
    # Every way the window can vanish arrives here, so one that went for a
    # reason nobody anticipated still stops the server.
    server.should_exit = True
    thread.join(SERVER_JOIN_TIMEOUT)
    return True


def _report_fatal(exc: BaseException) -> None:
    """Say something visible when a windowed build dies during startup.

    Without this the exe simply vanishes on a double-click and there is nothing
    to tell a teacher — which is a worse failure than the console window this
    build exists to remove.
    """
    logging.getLogger("comp1").exception("startup failed", exc_info=exc)
    if not is_frozen() or sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(
            None,
            f"Drone Coder could not start:\n\n{exc}\n\nDetails were written to:\n"
            f"{log_file()}",
            "Squadrone Drone Coder",
            0x10,  # MB_ICONERROR
        )
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser("comp1")
    ap.add_argument("--version", action="version", version=f"comp1 {__version__}")
    # `None` on every setting that has a saved counterpart is the "not passed"
    # sentinel: an explicit flag must beat whatever the last browser session
    # saved, and argparse cannot tell a default from a deliberate repeat of it.
    ap.add_argument(
        "--drone",
        choices=["mock", "tello", "sim"],
        default=None,
        help="initial drone (default: the saved choice, else the simulator; "
        "the browser can switch between the simulator and Tello)",
    )
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    ap.add_argument(
        "--no-window",
        action="store_true",
        help="always use the system browser, never a window of our own",
    )
    ap.add_argument(
        "--script",
        type=Path,
        default=None,
        help="run a student Python mission alongside the server "
        "(video, telemetry and EMERGENCY STOP stay live)",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=None,
        help="sim only: fixed arena layout (default: random each launch)",
    )
    ap.add_argument(
        "--noise", type=float, default=None, help="sim only: movement drift, e.g. 0.05"
    )
    ap.add_argument(
        "--scenery",
        choices=["arena", "corridor"],
        default=None,
        help="sim only: which arena to start in (also switchable in the browser)",
    )
    ap.add_argument(
        "--check-updates",
        dest="check_updates",
        action="store_true",
        default=None,
        help="look for a newer release at startup (default: the saved choice)",
    )
    ap.add_argument(
        "--no-check-updates",
        dest="check_updates",
        action="store_false",
        help="never contact GitHub — for networks that block it outright",
    )
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=None,
        help="seconds to wait after the last browser window closes before the "
        "program closes itself (default: 30, or never with --no-browser). "
        "0 disables it",
    )
    ap.add_argument(
        "--vision-config",
        type=Path,
        default=None,
        help="TOML file overriding marker size, HSV thresholds, etc. "
        "(see vision_config.example.toml at the repo root)",
    )
    ap.add_argument(
        "--flight-config",
        type=Path,
        default=None,
        help="TOML file overriding real-Tello flight quirks, e.g. how far "
        "a flip throws the aircraft "
        "(see flight_config.example.toml at the repo root)",
    )
    args = ap.parse_args()
    if args.script and not args.script.is_file():
        sys.exit(f"comp1: no such script: {args.script}")
    if args.vision_config and not args.vision_config.is_file():
        sys.exit(f"comp1: no such vision config: {args.vision_config}")
    if args.flight_config and not args.flight_config.is_file():
        sys.exit(f"comp1: no such flight config: {args.flight_config}")
    if _already_serving(args.port):
        # Clicking the shortcut a second time is common — a student who closed
        # the tab has no other way to ask for it back. Before this check the
        # second copy simply died on the port bind with nothing on screen.
        _tell_user(
            "Drone Coder is already running.\n\n"
            "Look for its window, or open it at "
            f"http://localhost:{args.port}\n\n"
            "To close it, close that window, or click Close program at the top "
            "of the page. If every Drone Coder window is already shut, it "
            "closes itself about half a minute later."
        )
        return
    saved = settings_store.load()
    resolve = settings_store.resolve
    drone_mode = resolve(args.drone, saved.drone)
    scenery = resolve(args.scenery, saved.scenery)
    noise = resolve(args.noise, saved.noise)
    seed = resolve(args.seed, saved.seed)
    check_updates = resolve(args.check_updates, saved.check_updates)
    cfg = (
        VisionConfig.load_file(args.vision_config)
        if args.vision_config
        else DEFAULT_CONFIG
    )
    if not args.vision_config and saved.hsv:
        # A venue calibration applied in the browser last session. An explicit
        # --vision-config outranks it: that file is the operator's considered
        # profile, this is the last thing somebody dragged a box around.
        try:
            cfg = config_with_hsv(cfg, saved.hsv)
        except CalibrationError:
            pass  # a hand-edited settings file must not stop the program
    flight = (
        FlightConfig.load_file(args.flight_config)
        if args.flight_config
        else DEFAULT_FLIGHT_CONFIG
    )

    def new_tello():
        # Kept lazy for the same reason server._new_tello is: importing
        # djitellopy on a simulator launch touches the hardware pathway for
        # nothing. This closure exists so a mid-session switch to Tello in the
        # browser gets the same --flight-config the CLI was given.
        from .drone.tello import TelloDrone

        return TelloDrone(flight)

    if drone_mode == "tello":
        drone = new_tello()
    elif drone_mode == "sim":
        from .sim.drone import SimDrone

        drone = SimDrone(seed=seed, noise=noise, scenery_name=scenery)
    else:
        from .drone.mock import MockDrone

        drone = MockDrone()
    want_window = _window_wanted(args)
    # Closing itself when the last window goes is for the case where a window we
    # did not open is the only one the program has. `--no-browser` is a terminal
    # session (tests, CI, a developer) that owns its own lifetime, so it stays
    # up until Ctrl+C. `--idle-timeout` overrides either way, and 0 turns it
    # off — a demonstration laptop meant to sit on a stand all day. A native
    # window keeps the same countdown for a narrower job: it is the only thing
    # that would ever notice a renderer that has died behind a frame which still
    # looks like a program.
    idle_timeout = args.idle_timeout
    if idle_timeout is None:
        idle_timeout = None if args.no_browser else DEFAULT_IDLE_TIMEOUT
    elif idle_timeout <= 0:
        idle_timeout = None

    # The server is built rather than run through uvicorn.run() because the app
    # needs a handle on it: with no console and no tray icon, "close the
    # program" can only come from the window in front of it, and that means
    # something has to be able to end this loop from inside.
    server = None
    native = None

    def request_shutdown():
        """Stop the program. Safe to call from any thread."""
        if server is not None:
            server.should_exit = True
        if native is not None:
            # Marshalled onto the GUI thread by pywebview. Without it the server
            # would stop behind a window still sitting there showing a page that
            # can no longer reach it.
            native.close()

    def request_close(confirm):
        """The window's close button. Policy for it lives here, not in window.py.

        Here because this is where the app object is, and keeping it out of the
        window module is what lets that module be tested on a machine with no
        pywebview at all.
        """
        if getattr(app.state, "installing", False):
            # The installer is already on its way to closing this process.
            # Standing in front of its WM_CLOSE only earns a forced kill a few
            # seconds later, with the drone still held.
            return native_window.CLOSE_NOW
        if app.state.interp is not None and not confirm(
            native_window.WINDOW_TITLE,
            "A mission is still flying.\n\n"
            "Close Drone Coder anyway? The drone will be landed first.",
        ):
            return native_window.CLOSE_STAY
        quit_now = getattr(app.state, "request_quit", None)
        if quit_now is None or app.state.quitting:
            request_shutdown()
            return native_window.CLOSE_NOW
        try:
            quit_now()
        except RuntimeError:  # the event loop has already gone
            request_shutdown()
            return native_window.CLOSE_NOW
        return native_window.CLOSE_WAIT

    app = create_app(
        drone,
        cfg=cfg,
        script=args.script,
        tello_factory=new_tello,
        settings_path=settings_file(),
        update_check=(partial(updater.check, __version__) if check_updates else None),
        shutdown=request_shutdown,
        idle_timeout=idle_timeout,
    )
    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        # A windowed build has no stdout, and uvicorn's default log config
        # installs a StreamHandler on it — every log line would then raise.
        # Disabling the config hands logging to the file handler set up above.
        log_config=None if is_frozen() else uvicorn.config.LOGGING_CONFIG,
        # A page that never answers its close frame must not keep a program
        # the student has just closed alive on the taskbar-less desktop.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)

    if want_window:
        native = native_window.NativeWindow(
            _launch_url(args.port), on_close=request_close
        )
        if _serve_behind_the_window(server, native, _launch_url(args.port)):
            return
        # The window was never shown, so there is nowhere on screen for this to
        # appear except a message box — and a student staring at nothing at all
        # is the failure this whole file exists to prevent.
        native = None
        _tell_user(
            "Drone Coder could not start.\n\n"
            f"Nothing is answering on port {args.port}. Details were written to:\n"
            f"{log_file()}"
        )
        return

    if not args.no_browser:
        # A unique query makes an already-open competition tab load the current
        # frontend instead of merely coming to the foreground with stale HTML.
        launch_url = f"{_launch_url(args.port)}?launch={time.time_ns()}"
        threading.Timer(1.0, lambda: webbrowser.open(launch_url)).start()
    server.run()


def run():
    """Entry point for the packaged executable — see comp1/launcher.py."""
    _setup_logging()
    try:
        main()
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — last chance to say anything
        _report_fatal(exc)
        if not is_frozen():
            traceback.print_exc()
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
