import argparse
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .drone.config import DEFAULT_FLIGHT_CONFIG, FlightConfig
from .server import create_app
from .vision.config import DEFAULT_CONFIG, VisionConfig


def main():
    ap = argparse.ArgumentParser("comp1")
    ap.add_argument(
        "--drone",
        choices=["mock", "tello", "sim"],
        default="sim",
        help="initial drone (default: simulator; the browser can switch between "
        "the simulator and Tello)",
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
        "--noise", type=float, default=0.0, help="sim only: movement drift, e.g. 0.05"
    )
    ap.add_argument(
        "--scenery",
        choices=["arena", "corridor"],
        default="arena",
        help="sim only: which arena to start in (also switchable in the browser)",
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
    cfg = (
        VisionConfig.load_file(args.vision_config)
        if args.vision_config
        else DEFAULT_CONFIG
    )
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

    if args.drone == "tello":
        drone = new_tello()
    elif args.drone == "sim":
        from .sim.drone import SimDrone

        drone = SimDrone(seed=args.seed, noise=args.noise, scenery_name=args.scenery)
    else:
        from .drone.mock import MockDrone

        drone = MockDrone()
    if not args.no_browser:
        # A unique query makes an already-open competition tab load the current
        # frontend instead of merely coming to the foreground with stale HTML.
        launch_url = f"http://localhost:{args.port}/?launch={time.time_ns()}"
        threading.Timer(1.0, lambda: webbrowser.open(launch_url)).start()
    uvicorn.run(
        create_app(drone, cfg=cfg, script=args.script, tello_factory=new_tello),
        host="127.0.0.1",
        port=args.port,
    )


if __name__ == "__main__":
    main()
