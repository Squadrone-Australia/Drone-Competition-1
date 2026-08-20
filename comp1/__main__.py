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

from . import __version__, settings as settings_store, update as updater
from .drone.config import DEFAULT_FLIGHT_CONFIG, FlightConfig
from .paths import is_frozen, log_file, settings_file
from .server import DEFAULT_IDLE_TIMEOUT, create_app
from .vision.calibration import CalibrationError, config_with_hsv
from .vision.config import DEFAULT_CONFIG, VisionConfig


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
            f"Open it at http://localhost:{args.port}\n\n"
            "To close it, click Close program at the top of that page. If you "
            "have already closed every Drone Coder tab, it shuts itself down "
            "about half a minute later."
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
    if not args.no_browser:
        # A unique query makes an already-open competition tab load the current
        # frontend instead of merely coming to the foreground with stale HTML.
        launch_url = f"http://localhost:{args.port}/?launch={time.time_ns()}"
        threading.Timer(1.0, lambda: webbrowser.open(launch_url)).start()
    # Closing itself when the last window goes is for the launched-browser case:
    # that browser page is the only window the program has. `--no-browser` is a
    # terminal session (tests, CI, a developer) that owns its own lifetime, so
    # it stays up until Ctrl+C. `--idle-timeout` overrides either way, and 0
    # turns it off — a demonstration laptop meant to sit on a stand all day.
    idle_timeout = args.idle_timeout
    if idle_timeout is None:
        idle_timeout = None if args.no_browser else DEFAULT_IDLE_TIMEOUT
    elif idle_timeout <= 0:
        idle_timeout = None

    # The server is built rather than run through uvicorn.run() because the app
    # needs a handle on it: with no console and no tray icon, "close the
    # program" can only come from the browser page, and that means something
    # has to be able to end this loop from inside.
    server = None

    def request_shutdown():
        if server is not None:
            server.should_exit = True

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
        # A browser that never answers its close frame must not keep a program
        # the student has just closed alive on the taskbar-less desktop.
        timeout_graceful_shutdown=5,
    )
    server = uvicorn.Server(config)
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
        raise SystemExit(1)


if __name__ == "__main__":
    run()
