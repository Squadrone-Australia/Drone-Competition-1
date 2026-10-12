"""Field checks for a real Tello: what reaches this laptop, and how it flies.

Not part of the shipped program. Run from the repo root with the laptop joined to
the Tello's Wi-Fi and Drone Coder *closed* (it holds the same ports):

    python tools/tello_field_check.py link        # handshake, battery, SDK version
    python tools/tello_field_check.py reconnect   # does the drone only answer one port?
    python tools/tello_field_check.py ports --between close|keep|nostreamoff
                                                  # status/video, and does video come back?
    python tools/tello_field_check.py punch       # video through a blocking firewall?
    python tools/tello_field_check.py fly         # real flight: needs 2 m clear all round

Each check ends with a SUMMARY block — paste that back. See
docs/plans/2026-10-12-tello-udp-client.md for what each one is deciding.
"""

import argparse
import socket
import sys
import threading
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comp1.drone.tello_link import (  # noqa: E402
    TELLO_ADDRESS,
    TelloError,
    TelloLink,
    TelloNoReply,
    TelloPortBusy,
    timeout_for,
)

HOST = TELLO_ADDRESS[0]
STATE_PORT = 8890
VIDEO_PORT = 11111
LISTEN_S = 6.0


def summary(lines):
    print("\n===== SUMMARY (paste this back) =====")
    for line in lines:
        print(line)
    print("=====================================")


def ask(link, command):
    try:
        return link.send(command, timeout=5.0)
    except (TelloError, TelloNoReply) as exc:
        return f"<{type(exc).__name__}: {exc}>"


class Sniffer:
    """Counts datagrams arriving on one local port, and records who sent them."""

    def __init__(self, port, punch_to=()):
        self.port = port
        self.sources = Counter()
        self.bytes = 0
        self.first_at = None
        self.error = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", port))
        except OSError as exc:
            self.error = f"could not bind :{port} ({exc}) - is Drone Coder still running?"
            return
        self.sock.settimeout(0.3)
        # The hole-punch: an outbound datagram from this exact local port to
        # each port the aircraft may send from, so the firewall files whatever
        # comes back on that pair as a reply.
        for target in punch_to:
            for _ in range(3):
                self.sock.sendto(b"\x00", target)
        self._stop = threading.Event()
        self._started = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, source = self.sock.recvfrom(65536)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                return
            if self.first_at is None:
                self.first_at = time.monotonic() - self._started
            self.sources[source] += 1
            self.bytes += len(data)

    def reset(self):
        """Start counting afresh — the socket stays bound."""
        self.sources = Counter()
        self.bytes = 0
        self.first_at = None
        self._started = time.monotonic()

    def stop(self):
        if self.error:
            return
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()

    def report(self, label):
        if self.error:
            return f"{label} :{self.port}: {self.error}"
        total = sum(self.sources.values())
        if not total:
            return f"{label} :{self.port}: NOTHING received"
        who = ", ".join(f"{h}:{p} x{n}" for (h, p), n in self.sources.most_common(3))
        return (
            f"{label} :{self.port}: {total} packets / {self.bytes} bytes, "
            f"first after {self.first_at:.1f}s, from {who}"
        )


def open_link(**kwargs):
    try:
        link = TelloLink(**kwargs)
    except TelloPortBusy as exc:
        sys.exit(str(exc))
    try:
        link.handshake()
    except TelloNoReply:
        link.close()
        sys.exit(
            "No reply to 'command'. Is the laptop on the TELLO-xxxxxx Wi-Fi and the drone on?"
        )
    return link


def check_link(_args):
    link = open_link()
    out = ["handshake: ok"]
    for command in ("battery?", "sdk?", "sn?", "wifi?", "speed?", "temp?"):
        out.append(f"{command:9} -> {ask(link, command)}")
    link.close()
    summary(out)


def try_handshake(label, **kwargs):
    try:
        link = TelloLink(**kwargs)
    except TelloPortBusy as exc:
        return f"{label}: port busy ({exc})"
    port = link._sock.getsockname()[1]
    try:
        link.handshake(tries=2)
        result = "ok"
    except TelloNoReply:
        result = "NO REPLY"
    link.close()
    return f"{label} (local port {port}): {result}"


def check_reconnect(_args):
    """Does the aircraft only answer the port that first sent it 'command'?"""
    out = [
        try_handshake("1. first link, fixed port"),
        try_handshake("2. reconnect, same fixed port"),
        try_handshake("3. stranger, random port", local_port=0),
        try_handshake("4. fixed port again, after the stranger"),
    ]
    out.append("-> expected: 1 ok, 2 ok, 3 NO REPLY. Step 4 tells us whether a")
    out.append("   stranger can steal the drone (if 4 says NO REPLY, reboot it).")
    summary(out)


def listen(link, state, video, label, streamoff=True):
    state.reset()
    video.reset()
    streamon = ask(link, "streamon")
    time.sleep(LISTEN_S)
    if streamoff:
        ask(link, "streamoff")
    return [
        f"{label}streamon -> {streamon}",
        f"{label}{state.report('status')}",
        f"{label}{video.report('video ')}",
    ]


def check_ports(args):
    """Twice in a row, to learn what brings the video back a second time.

    --between close        streamoff, close our sockets, reopen  (what a reconnect does today)
    --between keep         streamoff, but keep our sockets open
    --between nostreamoff  no streamoff; close and reopen our sockets only
    """
    link = open_link()
    out = [f"between runs: {args.between}"]
    state, video = Sniffer(STATE_PORT), Sniffer(VIDEO_PORT)
    out += listen(link, state, video, "run 1: ", streamoff=args.between != "nostreamoff")
    if args.between != "keep":
        state.stop()
        video.stop()
        time.sleep(1.0)
        state, video = Sniffer(STATE_PORT), Sniffer(VIDEO_PORT)
    else:
        time.sleep(1.0)
    # no streamoff at the end: it would leave the drone sending no video until
    # it is rebooted, and the next check would inherit that
    out += listen(link, state, video, "run 2: ", streamoff=False)
    state.stop()
    video.stop()
    link.close()
    summary(out)


def check_punch(args):
    """One punched run. Only meaningful where inbound :8890/:11111 is blocked.

    Ends without 'streamoff' (which kills video until a reboot), so the next
    check can run straight after.
    """
    link = open_link()
    out = [f"punching to video src {args.video_src}, status src {args.state_src}"]
    state = Sniffer(STATE_PORT, punch_to=[(HOST, p) for p in args.state_src])
    video = Sniffer(VIDEO_PORT, punch_to=[(HOST, p) for p in args.video_src])
    out += listen(link, state, video, "punched: ", streamoff=False)
    state.stop()
    video.stop()
    # does the aircraft mind a stray datagram on its sending ports?
    out.append(f"after punch, battery? -> {ask(link, 'battery?')}")
    link.close()
    summary(out)


FLIGHT = [
    "takeoff",
    "cw 360",
    "ccw 90",
    "forward 200",
    "back 200",
    "up 50",
    "down 50",
    "land",
]


def check_fly(args):
    print("This FLIES the drone: takeoff, a full turn, 2 m forward and back, land.")
    print("Clear 2 m all round and 2.5 m overhead. Ctrl+C at any time sends 'emergency'.")
    if not args.yes and input("Type FLY to continue: ").strip() != "FLY":
        sys.exit("cancelled")
    link = open_link()
    out = [f"battery before: {ask(link, 'battery?')}"]
    try:
        for command in FLIGHT:
            started = time.monotonic()
            try:
                reply = link.send(command)
            except (TelloError, TelloNoReply) as exc:
                reply = f"<{type(exc).__name__}: {exc}>"
            took = time.monotonic() - started
            line = (
                f"{command:12} -> {reply!s:40} took {took:5.1f}s "
                f"(allowed {timeout_for(command):.0f}s)"
            )
            print(line)
            out.append(line)
            if reply.startswith("<") and command != "land":
                out.append("stopping early: landing")
                out.append(f"land -> {ask(link, 'land')}")
                break
    except KeyboardInterrupt:
        link.send_no_reply("emergency")
        out.append("ABORTED with Ctrl+C: emergency sent")
    out.append(f"battery after: {ask(link, 'battery?')}")
    link.close()
    summary(out)


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="check", required=True)
    sub.add_parser("link").set_defaults(run=check_link)
    sub.add_parser("reconnect").set_defaults(run=check_reconnect)
    ports = sub.add_parser("ports")
    ports.add_argument(
        "--between", choices=["close", "keep", "nostreamoff"], default="close"
    )
    ports.set_defaults(run=check_ports)
    punch = sub.add_parser("punch")
    # the aircraft's video source ports, as measured on 2026-10-12
    punch.add_argument("--video-src", type=int, nargs="+", default=[62512, 62513])
    punch.add_argument("--state-src", type=int, nargs="+", default=[8889])
    punch.set_defaults(run=check_punch)
    fly = sub.add_parser("fly")
    fly.add_argument("--yes", action="store_true", help="skip the FLY prompt")
    fly.set_defaults(run=check_fly)
    args = parser.parse_args()
    args.run(args)


if __name__ == "__main__":
    main()
