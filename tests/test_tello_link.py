"""The Tello command channel against a fake aircraft on loopback.

The fake behaves the way the firmware does in the two respects that broke
djitellopy in the field: it answers a manoeuvre only when the manoeuvre has
finished, and it answers ``error Not joystick`` to anything sent while it is
still busy. It sends no telemetry at all — which is what a laptop whose
firewall drops :8890 sees — and the link must not care.

Timings are scaled down (tenths of a second) by patching the module's
constants, so the real socket code runs end to end in well under a second per
test.
"""

import socket
import threading
import time

import pytest

from comp1.drone import tello_link
from comp1.drone.tello_link import (
    TelloError,
    TelloInterrupted,
    TelloLink,
    TelloNoReply,
    TelloPortBusy,
    plain_refusal,
    timeout_for,
)


class FakeAircraft:
    """A Tello on 127.0.0.1:<ephemeral> that only speaks the command channel."""

    def __init__(self, durations=None, drop=()):
        self.durations = {"takeoff": 0.1, "cw": 0.6, "forward": 0.1, "land": 0.1}
        self.durations.update(durations or {})
        self.drop = set(drop)  # commands whose reply is "lost on the Wi-Fi"
        self.received = []
        self.busy_until = 0.0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.address = self.sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def count(self, command):
        return self.received.count(command)

    def _reply(self, text, addr):
        try:
            self.sock.sendto(text.encode(), addr)
        except OSError:
            pass

    def _serve(self):
        while not self._stop.is_set():
            try:
                data, addr = self.sock.recvfrom(1024)
            except (TimeoutError, ConnectionResetError):
                continue
            except OSError:
                return
            command = data.decode()
            self.received.append(command)
            verb = command.split()[0]
            if command in self.drop:
                continue
            if verb in ("command", "streamon", "streamoff"):
                self._reply("ok", addr)
            elif verb == "battery?":
                self._reply("87", addr)
            elif verb == "emergency":
                self.busy_until = 0.0
                self._reply("ok", addr)
            elif time.monotonic() < self.busy_until:
                self._reply("error Not joystick", addr)
            else:
                duration = self.durations.get(verb, 0.1)
                self.busy_until = time.monotonic() + duration
                timer = threading.Timer(duration, self._reply, ("ok", addr))
                timer.daemon = True
                timer.start()

    def close(self):
        self._stop.set()
        self.sock.close()
        self._thread.join(1.0)


@pytest.fixture
def fast(monkeypatch):
    """Scale the link's waits to the fake's tenths of a second."""
    monkeypatch.setattr(tello_link, "BASE_TIMEOUT_S", 0.3)
    monkeypatch.setattr(tello_link, "TAKEOFF_TIMEOUT_S", 1.0)
    monkeypatch.setattr(tello_link, "MIN_TURN_DEG_S", 360.0)  # 360° → +1 s
    monkeypatch.setattr(tello_link, "MIN_SPEED_CM_S", 500.0)
    monkeypatch.setattr(tello_link, "OVERDUE_SETTLE_S", 0.5)
    monkeypatch.setattr(tello_link, "MANOEUVRE_FLOOR_S", 0.3)


@pytest.fixture
def aircraft(fast):
    fake = FakeAircraft()
    yield fake
    fake.close()


@pytest.fixture
def link(aircraft):
    link = TelloLink(address=aircraft.address, local_port=0)
    yield link
    link.close()


def test_connects_without_any_telemetry(aircraft, link):
    """djitellopy refused to connect until a state packet arrived on :8890,
    which a managed laptop's firewall drops. The handshake's reply is proof
    enough that the aircraft is there."""
    link.handshake()
    assert link.send("streamon") == "ok"
    assert aircraft.received == ["command", "streamon"]


def test_battery_is_asked_for(aircraft, link):
    assert link.send("battery?") == "87"


def test_a_long_manoeuvre_is_waited_for_and_sent_exactly_once(aircraft, link):
    """The 360° turn outlasts the base wait (0.3 s here, 7 s on the aircraft).
    djitellopy re-sent it on timeout and got 'error Not joystick' back."""
    link.handshake()
    link.send("takeoff")
    assert link.send("cw 360") == "ok"
    assert link.send("forward 50") == "ok"
    assert aircraft.count("cw 360") == 1
    assert aircraft.count("forward 50") == 1


def test_a_timed_out_command_is_never_resent(fast, aircraft, link):
    """A lost reply must not become a second copy of the same move."""
    aircraft.drop.add("forward 100")
    link.handshake()
    with pytest.raises(TelloNoReply):
        link.send("forward 100")
    assert aircraft.count("forward 100") == 1


def test_a_late_reply_is_dropped_not_given_to_the_next_command(fast, monkeypatch):
    """The reply that arrives after its command gave up must not be read as the
    answer to the next one — that offset is how 'Not joystick' used to land on
    an innocent command."""
    fake = FakeAircraft(durations={"cw": 0.4})
    try:
        link = TelloLink(address=fake.address, local_port=0)
        link.handshake()
        with pytest.raises(TelloNoReply):
            link.send("cw 90", timeout=0.1)  # gives up; the "ok" lands at 0.4 s
        # the overdue settle (0.5 s) lets that "ok" arrive and be dropped
        assert link.send("battery?") == "87"
        link.close()
    finally:
        fake.close()


def test_a_busy_aircraft_is_reported_in_plain_language(aircraft, link):
    link.handshake()
    aircraft.busy_until = time.monotonic() + 5.0  # mid-manoeuvre
    with pytest.raises(TelloError, match="still busy"):
        link.send("land")


def test_emergency_cuts_in_on_a_command_in_flight(fast):
    fake = FakeAircraft(durations={"forward": 5.0})
    link = TelloLink(address=fake.address, local_port=0)
    try:
        link.handshake()
        outcome = {}

        def fly():
            try:
                link.send("forward 500", timeout=5.0)
            except Exception as exc:
                outcome["error"] = exc

        flight = threading.Thread(target=fly)
        flight.start()
        time.sleep(0.2)
        started = time.monotonic()
        assert link.interrupt("emergency") == "ok"
        assert time.monotonic() - started < 1.0
        flight.join(2.0)
        assert isinstance(outcome.get("error"), TelloInterrupted)
    finally:
        link.close()
        fake.close()


def test_silence_on_handshake_is_no_reply(fast):
    """Nobody at the address: the handshake retries, then gives up cleanly."""
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    try:
        link = TelloLink(address=sink.getsockname(), local_port=0)
        with pytest.raises(TelloNoReply):
            link.handshake(tries=2, timeout=0.1)
        link.close()
    finally:
        sink.close()


def test_closing_is_idempotent_and_unblocks_a_waiter(fast):
    sink = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sink.bind(("127.0.0.1", 0))
    link = TelloLink(address=sink.getsockname(), local_port=0)
    try:
        outcome = {}

        def wait():
            try:
                link.send("takeoff", timeout=5.0)
            except Exception as exc:
                outcome["error"] = exc

        waiter = threading.Thread(target=wait)
        waiter.start()
        time.sleep(0.1)
        link.close()
        link.close()
        waiter.join(1.0)
        assert isinstance(outcome.get("error"), TelloNoReply)
        with pytest.raises(TelloNoReply):
            link.send("land")
    finally:
        sink.close()


def _free_port():
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_a_reconnect_reuses_the_same_local_port(aircraft):
    """The aircraft answers whichever port first sent it 'command' until it is
    rebooted, so every link must send from the same one — a fresh port per
    connection got no reply after the first (field test, 2026-10-12)."""
    port = _free_port()
    first = TelloLink(address=aircraft.address, local_port=port)
    first.handshake()
    first.close()
    second = TelloLink(address=aircraft.address, local_port=port)
    try:
        second.handshake()
        assert second._sock.getsockname()[1] == port
    finally:
        second.close()


def test_a_port_held_by_another_program_is_named_plainly():
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("", 0))
    try:
        with pytest.raises(TelloPortBusy, match="Jupyter"):
            TelloLink(local_port=holder.getsockname()[1])
    finally:
        holder.close()


def test_waits_are_sized_to_the_manoeuvre():
    assert timeout_for("takeoff") == tello_link.TAKEOFF_TIMEOUT_S
    assert timeout_for("battery?") == tello_link.BASE_TIMEOUT_S
    assert timeout_for("cw 360") > timeout_for("cw 90") > tello_link.BASE_TIMEOUT_S
    assert timeout_for("forward 500") > timeout_for("forward 20")
    # the interpreter's backstop must still be the outer bound
    from comp1.drone.config import FlightConfig

    longest = max(timeout_for("forward 500"), timeout_for("ccw 360"))
    assert longest < FlightConfig().command_timeout_s


def test_refusals_read_as_sentences():
    assert "still busy" in plain_refusal("forward 50", "error Not joystick")
    assert "50% battery" in plain_refusal("flip b", "error")
    assert "(error Motor stop)" not in plain_refusal("takeoff", "error Motor stop")
    assert "error whatever" in plain_refusal("up 30", "error whatever")


def test_every_manoeuvre_outwaits_the_aircrafts_own_give_up():
    """The Tello answers 'error Run timeout' at about 15 s on a move it cannot
    finish. Giving up first would report silence — a lost link — instead."""
    for command in ("ccw 90", "up 20", "forward 20", "flip b", "land", "cw 360"):
        assert timeout_for(command) >= tello_link.MANOEUVRE_FLOOR_S > 15.1
    assert "could not finish the move" in plain_refusal("cw 360", "error Run timeout")
