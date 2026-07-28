import argparse
import threading
import webbrowser

import uvicorn

from .server import create_app


def main():
    ap = argparse.ArgumentParser("comp1")
    ap.add_argument("--drone", choices=["mock", "tello"], default="mock")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    if args.drone == "tello":
        from .drone.tello import TelloDrone
        drone = TelloDrone()
    else:
        from .drone.mock import MockDrone
        drone = MockDrone()
    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{args.port}")).start()
    uvicorn.run(create_app(drone), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
