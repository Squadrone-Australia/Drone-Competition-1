"""Record everything one Tello says, so two drones can be compared line by line.

Not part of the shipped program. Built for "this drone flies in DroneBlocks but
refuses commands in Drone Coder, and that one is fine in both": run it once per
drone and compare the two files it writes.

    python tools/tello_probe.py                       # asks for a label
    python tools/tello_probe.py --label bad --fly     # also a short flight

Before each run: switch the drone off and on, join the laptop to its
TELLO-xxxxxx Wi-Fi, and close Drone Coder, DroneBlocks and any notebook (they
hold the same port, and a drone answers only the first port that spoke to it
since it booted).

Unlike tools/tello_field_check.py this does not go through TelloLink. TelloLink
drops any datagram nobody is waiting for, and an extra or late reply is one of
the things being looked for: the SDK does not number its replies, so a single
stray "ok" shifts every later answer onto the wrong command. Here a receiver
thread logs every datagram that arrives, with its time and sender, and the
steps only read what it caught.

The run has three phases, and the first two keep the app's timing exactly:

1. **As the app connects** — ``command`` (2 s, up to 3 tries), ``streamon``
   straight after, video watched for a few seconds, ``battery?`` on the app's
   5 s poll. Nothing waits in between, as in ``TelloDrone.connect``.
2. **Slow and careful** — read-only queries (firmware, serial, Wi-Fi...), each
   followed by a pause that catches any second reply.
3. **Flight** (only with --fly or FLY typed) — takeoff, small moves, land, sent
   back to back as the interpreter does, with the app's timeouts.
"""

import argparse
import platform
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from comp1 import __version__  # noqa: E402
from comp1.drone.tello import VIDEO_PORT, VIDEO_SOURCE_PORTS  # noqa: E402
from comp1.drone.tello_link import (  # noqa: E402
    CONTROL_PORT,
    HANDSHAKE_TIMEOUT_S,
    HANDSHAKE_TRIES,
    TELLO_ADDRESS,
    timeout_for,
)

HOST = TELLO_ADDRESS[0]
STATE_PORT = 8890
#: The app's battery poll (server.BATTERY_INTERVAL), copied for phase 1.
BATTERY_INTERVAL_S = 5.0
VIDEO_LISTEN_S = 6.0
QUERY_TIMEOUT_S = 5.0
#: After a phase-2 reply, how long to keep listening for a second one.
LINGER_S = 0.8
#: How many datagrams per side port to keep a sample of.
SAMPLES = 3

INFO_QUERIES = [
    "sdk?", "sn?", "wifi?", "battery?", "speed?", "time?", "temp?",
    "baro?", "tof?", "height?", "attitude?", "acceleration?", "hardware?",
]
FLIGHT = ["takeoff", "up 30", "cw 90", "ccw 90", "forward 50", "back 50", "land"]


def show(data: bytes) -> str:
    """A datagram as text if it is text, otherwise as a short hex dump."""
    if all(32 <= b < 127 or b in (9, 10, 13) for b in data):
        return repr(data.decode("ascii"))
    head = data[:32].hex(" ")
    return f"<{len(data)} bytes: {head}{' ...' if len(data) > 32 else ''}>"


class Recorder:
    """The chronological log: every send and every datagram, timestamped."""

    def __init__(self):
        self.t0 = time.monotonic()
        self.lines: list[str] = []
        self._lock = threading.Lock()

    def now(self) -> float:
        return time.monotonic() - self.t0

    def note(self, kind: str, text: str) -> None:
        with self._lock:
            self.lines.append(f"{self.now():9.3f}  {kind:7} {text}")


@dataclass
class Step:
    command: str
    timeout: float
    reply: str | None = None  # None: nothing arrived in time
    source: tuple | None = None
    latency: float | None = None
    before: list = field(default_factory=list)  # arrived after the last step, unasked
    extras: list = field(default_factory=list)  # arrived within the linger, after the reply

    def line(self) -> str:
        if self.reply is None:
            out = f"{self.command:14} -> NO REPLY within {self.timeout:g}s"
        else:
            out = f"{self.command:14} -> {self.reply:28} {self.latency:6.3f}s"
            if self.source != TELLO_ADDRESS:
                out += f"  FROM {self.source[0]}:{self.source[1]}"
        for t, text in self.before:
            out += f"\n{'':17}! unasked before it, at {t:.3f}s: {text}"
        for t, text in self.extras:
            out += f"\n{'':17}! second reply, +{t:.3f}s after the first: {text}"
        return out


class Channel:
    """The command socket on the app's fixed port, with a logging receiver."""

    def __init__(self, rec: Recorder):
        self.rec = rec
        self.inbox: queue.Queue = queue.Queue()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", CONTROL_PORT))
        except OSError as exc:
            sys.exit(
                f"Could not use port {CONTROL_PORT} ({exc}). Close Drone Coder, "
                "DroneBlocks and any notebook, then try again."
            )
        self.sock.settimeout(0.3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        while not self._stop.is_set():
            try:
                data, source = self.sock.recvfrom(4096)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                return
            text = show(data)
            self.rec.note("RECV", f"{source[0]}:{source[1]}  {text}")
            self.inbox.put((self.rec.now(), source, text))

    def raw(self, command: str) -> None:
        self.rec.note("SEND", repr(command))
        try:
            self.sock.sendto(command.encode(), TELLO_ADDRESS)
        except OSError as exc:
            self.rec.note("ERROR", f"send failed: {exc}")

    def step(self, command: str, timeout: float, linger: float = 0.0) -> Step:
        """Send once, take the first datagram as the reply — as TelloLink does.

        Anything already waiting is what TelloLink would have dropped; it is
        kept on the step so it shows in the summary.
        """
        result = Step(command, timeout)
        while True:
            try:
                t, _, text = self.inbox.get_nowait()
            except queue.Empty:
                break
            result.before.append((t, text))
        sent = time.monotonic()
        self.raw(command)
        try:
            _, source, text = self.inbox.get(timeout=timeout)
        except queue.Empty:
            return result
        result.latency = time.monotonic() - sent
        result.source, result.reply = source, text
        replied = time.monotonic()
        while (left := linger - (time.monotonic() - replied)) > 0:
            try:
                _, _, extra = self.inbox.get(timeout=left)
            except queue.Empty:
                break
            result.extras.append((time.monotonic() - replied, extra))
        return result

    def close(self):
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()


class Sniffer:
    """Counts what arrives on a side port (status or video) and keeps samples."""

    def __init__(self, port: int, rec: Recorder, punch_to=()):
        self.port, self.rec, self.punch_to = port, rec, list(punch_to)
        self.count = self.bytes = 0
        self.first_at: float | None = None
        self.sources: dict = {}
        self.samples: list[str] = []
        self.error: str | None = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind(("", port))
        except OSError as exc:
            self.error = f"could not bind :{port} ({exc})"
            return
        self.sock.settimeout(0.3)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        last_punch = float("-inf")
        while not self._stop.is_set():
            # punch like FrameReader does, until something arrives
            if self.punch_to and not self.count and time.monotonic() - last_punch >= 1.0:
                for target in self.punch_to:
                    try:
                        self.sock.sendto(b"\x00", target)
                    except OSError:
                        pass
                last_punch = time.monotonic()
            try:
                data, source = self.sock.recvfrom(65536)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                return
            if self.first_at is None:
                self.first_at = self.rec.now()
                self.rec.note(f":{self.port}", f"first datagram from {source[0]}:{source[1]}")
            self.count += 1
            self.bytes += len(data)
            self.sources[source] = self.sources.get(source, 0) + 1
            if len(self.samples) < SAMPLES:
                self.samples.append(show(data))

    def report(self, label: str) -> list[str]:
        if self.error:
            return [f"{label} :{self.port}: {self.error}"]
        if not self.count:
            return [f"{label} :{self.port}: nothing received"]
        who = ", ".join(f"{h}:{p} x{n}" for (h, p), n in self.sources.items())
        out = [
            f"{label} :{self.port}: {self.count} datagrams / {self.bytes} bytes, "
            f"first at {self.first_at:.3f}s, from {who}"
        ]
        out += [f"    sample: {s}" for s in self.samples]
        return out

    def close(self):
        if self.error:
            return
        self._stop.set()
        self._thread.join(1.0)
        self.sock.close()


def environment() -> list[str]:
    out = [
        f"date:      {datetime.now().isoformat(timespec='seconds')}",
        f"comp1:     {__version__}",
        f"python:    {platform.python_version()} on {platform.platform()}",
    ]
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(TELLO_ADDRESS)  # picks a route; sends nothing
        out.append(f"laptop IP: {probe.getsockname()[0]}")
        probe.close()
    except OSError as exc:
        out.append(f"laptop IP: unknown ({exc})")
    if sys.platform == "win32":
        try:
            text = subprocess.run(
                ["netsh", "wlan", "show", "interfaces"],
                capture_output=True, text=True, timeout=5,
            ).stdout
            for key in ("SSID", "BSSID", "Radio type", "Channel", "Signal", "Receive rate"):
                match = re.search(rf"^\s*{key}\s*:\s*(.+)$", text, re.MULTILINE)
                if match:
                    out.append(f"{key + ':':10} {match.group(1).strip()}")
        except Exception as exc:
            out.append(f"wifi:      unknown ({exc})")
    return out


def phase_app_connect(ch: Channel, rec: Recorder) -> tuple[list[str], bool]:
    out = ["--- phase 1: as the app connects ---"]
    answered = False
    for attempt in range(1, HANDSHAKE_TRIES + 1):
        step = ch.step("command", HANDSHAKE_TIMEOUT_S)
        out.append(f"[try {attempt}] {step.line()}")
        if step.reply is not None:
            answered = True
            break
    if not answered:
        out.append("No reply to 'command' at all. Is the laptop on the drone's Wi-Fi, and was")
        out.append("the drone rebooted since another app talked to it?")
        return out, False
    video = Sniffer(VIDEO_PORT, rec, punch_to=[(HOST, p) for p in VIDEO_SOURCE_PORTS])
    out.append(ch.step("streamon", timeout_for("streamon")).line())
    rec.note("WAIT", f"watching video for {VIDEO_LISTEN_S:g}s")
    time.sleep(VIDEO_LISTEN_S)
    out += video.report("video ")
    video.close()
    for _ in range(2):
        out.append(ch.step("battery?", timeout_for("battery?")).line())
        time.sleep(BATTERY_INTERVAL_S)
    return out, True


def phase_queries(ch: Channel) -> list[str]:
    out = ["--- phase 2: slow and careful ---"]
    for command in INFO_QUERIES:
        out.append(ch.step(command, QUERY_TIMEOUT_S, linger=LINGER_S).line())
    # a last listen, for anything still on its way
    time.sleep(LINGER_S)
    out.append(ch.step("command", QUERY_TIMEOUT_S, linger=LINGER_S).line())
    return out


def phase_flight(ch: Channel) -> list[str]:
    out = ["--- phase 3: flight ---"]
    try:
        for command in FLIGHT:
            step = ch.step(command, timeout_for(command))
            out.append(step.line())
            print("  " + out[-1])
            if step.reply is None:
                # silence, not a refusal: the drone may be gone, or stuck mid-move
                out.append("no reply: landing and stopping")
                out.append(ch.step("land", timeout_for("land")).line())
                break
            if command == "takeoff" and step.reply.lower().startswith("error"):
                out.append("takeoff refused: skipping the moves")
                break
            # an "error" from a move means the drone answered and is hovering:
            # carry on, because which moves it refuses is the point
    except KeyboardInterrupt:
        ch.raw("emergency")
        out.append("ABORTED with Ctrl+C: emergency sent")
        return out
    if not out[-1].startswith("land"):
        out.append(ch.step("land", timeout_for("land")).line())
    time.sleep(LINGER_S)
    out.append(ch.step("battery?", QUERY_TIMEOUT_S, linger=LINGER_S).line())
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--label", help="a name for this drone, e.g. good or bad")
    parser.add_argument("--fly", action="store_true", help="also run the short flight")
    args = parser.parse_args()

    print(__doc__.split("\n\n")[0])
    print()
    print("Before you start:")
    print("  1. Switch the drone off and on, and wait for the yellow blinking light.")
    print("  2. Join this laptop to the drone's TELLO-xxxxxx Wi-Fi.")
    print("  3. Close Drone Coder, DroneBlocks and any notebook.")
    print()
    label = args.label or input("Label for this drone (e.g. good / bad): ").strip()
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", label) or "drone"
    fly = args.fly
    if not fly:
        print("\nThe flight test takes off, moves 30-50 cm, turns 90 degrees and lands.")
        print("It needs 2 m clear all round. Ctrl+C during it cuts the motors.")
        fly = input("Type FLY to include it, or press Enter to skip: ").strip() == "FLY"
    input("\nPress Enter to start (Ctrl+C to cancel)... ")

    rec = Recorder()
    env = environment()
    print("\n".join(env))
    state = Sniffer(STATE_PORT, rec)  # passive: a firewall may drop it, that is fine
    ch = Channel(rec)
    summary: list[str] = []
    try:
        print("\nPhase 1: connecting as the app does...")
        lines, answered = phase_app_connect(ch, rec)
        summary += lines
        if answered:
            print("Phase 2: read-only queries...")
            summary += phase_queries(ch)
            if fly:
                print("Phase 3: flight...")
                summary += phase_flight(ch)
    except KeyboardInterrupt:
        summary.append("ABORTED with Ctrl+C")
    finally:
        summary += ["--- status port, whole run ---"] + state.report("status")
        state.close()
        ch.close()
        path = Path.cwd() / f"tello-probe-{label}-{datetime.now():%Y%m%d-%H%M%S}.txt"
        path.write_text(
            "\n".join(
                [f"Tello probe: {label}", ""]
                + env
                + ["", "===== SUMMARY =====", *summary, "", "===== RAW LOG =====", *rec.lines]
            )
            + "\n",
            encoding="utf-8",
        )
    print("\n" + "\n".join(summary))
    print(f"\nSaved: {path}")


if __name__ == "__main__":
    main()
