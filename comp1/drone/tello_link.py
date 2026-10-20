"""The Tello SDK command channel, spoken directly over one UDP socket.

This replaced djitellopy, and the two reasons it did are the design rules:

**Only ever wait for replies.** A Tello talks on three ports: command replies on
the socket that sent the command, telemetry ("state") pushed to the laptop's
:8890, and video pushed to :11111. To Windows Firewall the first is the answer
to a datagram we sent, so it is let through; the other two arrive unasked, and
a laptop whose firewall has no rule for this program — a university-managed
one, on the Tello's Wi-Fi, which Windows files as a *Public* network — drops
them. djitellopy refused to connect until a state packet arrived, so on those
laptops nothing flew at all. This module sends from one socket, reads its
replies, and never listens on :8890; the battery is *asked* for (``battery?``)
rather than read out of telemetry. Video is the adapter's problem, see
``comp1/drone/tello.py``.

**Send a manoeuvre exactly once.** The aircraft answers a manoeuvre when it
has *finished* it, and answers ``error Not joystick`` to anything that arrives
while it is still busy. djitellopy waited a flat 7 s and then sent the command
again — so a 360° turn was sent three times, failed with ``Not joystick``, and
its late ``ok`` was then read as the reply to the next command. A lost reply
was worse: the move was flown twice. Here a command's wait is sized to the
manoeuvre, a command is never re-sent (only the ``command`` handshake, which is
idempotent, is retried), and a datagram nobody is waiting for is dropped
rather than handed to the next command.

Pure socket code with no comp1 imports, so it can be tested against a fake
aircraft on loopback.
"""

import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

TELLO_ADDRESS = ("192.168.10.1", 8889)
#: The local port commands are sent from. It has to be the same one every time:
#: the aircraft keeps answering whichever port first sent it ``command`` until
#: it is rebooted (field test, 2026-10-12, Tello EDU SDK 3.0). A fresh port per
#: connection — tried first, to dodge a notebook kernel holding this one — got
#: no reply at all on any connection after the first, so a reconnect, or simply
#: restarting the program, needed a reboot of the drone. 8889 is the port
#: djitellopy and the SDK examples use, so it is also the port a drone that was
#: last flown from one of those is already answering.
CONTROL_PORT = 8889

#: The flat wait for a command that does not move the aircraft. A Tello answers
#: these in milliseconds; this only has to outlast a congested Wi-Fi channel.
BASE_TIMEOUT_S = 7.0
#: Takeoff climbs to about a metre and settles before it answers, and on a cold
#: or tired battery that has been seen to take well over ten seconds.
TAKEOFF_TIMEOUT_S = 20.0
#: Deliberately slow estimates of how fast the aircraft moves and turns. The
#: wait for a manoeuvre is BASE_TIMEOUT_S plus the time it would take at these
#: rates, so a long move is never given up on while it is still flying — that
#: give-up is what used to turn into a re-send and ``error Not joystick``.
MIN_SPEED_CM_S = 20.0
MIN_TURN_DEG_S = 30.0
#: The shortest wait for anything that moves the aircraft. The Tello gives up
#: on a manoeuvre it cannot finish by itself and answers ``error Run timeout``
#: — measured at 15.1 s on a 360° turn held back by a loose cage. Waiting
#: longer than that means the student hears the aircraft's own verdict rather
#: than a silence that reads as a lost link. (Measured healthy times, same
#: field test: cw 360 6.6 s, forward/back 200 3.6 s, up/down 50 2.4 s,
#: takeoff 3.9–8.9 s, land 3.8 s.)
MANOEUVRE_FLOOR_S = 17.0
#: How long the ``command`` handshake waits per try, and how many tries. The
#: only command that is ever retried: entering SDK mode twice is harmless, and
#: a Tello that has just booted can miss the first one.
HANDSHAKE_TIMEOUT_S = 2.0
HANDSHAKE_TRIES = 3
#: How long a command waits after one before it timed out. That reply may still
#: be on its way, and it must land while nobody is waiting (and be dropped)
#: rather than arrive as the answer to the next command.
OVERDUE_SETTLE_S = 0.4
#: How often the receiver thread wakes to check whether it has been closed.
_RECV_POLL_S = 0.5

_MOVES = {"forward", "back", "left", "right", "up", "down"}
_TURNS = {"cw", "ccw"}

#: What the aircraft's terse refusals mean, for a student reading the log. Keys
#: are matched case-insensitively against the reply.
_PLAIN = {
    "not joystick": "the drone was still busy finishing the previous command",
    "no valid imu": (
        "the drone's sensors are not ready — set it on a flat surface, wait a "
        "few seconds, and try again"
    ),
    "motor stop": "the drone's motors are stopped",
    "auto land": "the drone is landing by itself (low battery or overheating)",
    "run timeout": (
        "the drone could not finish the move — check nothing is blocking it and "
        "that the propellers and guard are on properly"
    ),
}


class TelloError(RuntimeError):
    """The aircraft answered, and the answer was a refusal."""


class TelloPortBusy(RuntimeError):
    """Another program on this laptop already holds the command port."""


class TelloNoReply(RuntimeError):
    """The aircraft did not answer in time. The link may be gone."""


class TelloInterrupted(RuntimeError):
    """A command was cut short by an emergency stop sent while it was flying."""


def timeout_for(command: str) -> float:
    """How long to wait for the reply to ``command``, in seconds."""
    verb, _, arg = command.partition(" ")
    if verb == "takeoff":
        return TAKEOFF_TIMEOUT_S
    try:
        amount = abs(float(arg.split()[0])) if arg else 0.0
    except ValueError:
        amount = 0.0
    if verb in _MOVES:
        return max(MANOEUVRE_FLOOR_S, BASE_TIMEOUT_S + amount / MIN_SPEED_CM_S)
    if verb in _TURNS:
        return max(MANOEUVRE_FLOOR_S, BASE_TIMEOUT_S + amount / MIN_TURN_DEG_S)
    if verb in ("flip", "land"):
        return MANOEUVRE_FLOOR_S
    return BASE_TIMEOUT_S


def plain_refusal(command: str, reply: str) -> str:
    """Turn ``error …`` into a sentence a student can act on."""
    low = reply.lower()
    for key, meaning in _PLAIN.items():
        if key in low:
            return f"the drone refused '{command}': {meaning}"
    if command.startswith("flip"):
        return (
            f"the drone refused '{command}' — a flip needs more than 50% battery "
            "and room around the drone"
        )
    return f"the drone refused '{command}' ({reply})"


class _Waiter:
    """One command's claim on the next reply."""

    def __init__(self):
        self.done = threading.Event()
        self.reply: str | None = None
        self.preempted = False


class TelloLink:
    """One SDK session's command channel: a socket, a receiver, one waiter.

    Every datagram from the aircraft goes to whichever command is currently
    waiting, and to nobody otherwise. Ordinary commands take turns
    (:meth:`send`); :meth:`interrupt` is the one call that does not wait its
    turn, because an emergency stop issued while a long move is in flight
    cannot queue behind it.
    """

    def __init__(
        self,
        address: tuple[str, int] = TELLO_ADDRESS,
        sock=None,
        local_port: int = CONTROL_PORT,
    ):
        self._address = address
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                sock.bind(("", local_port))  # fixed: see CONTROL_PORT
            except OSError as exc:
                sock.close()
                raise TelloPortBusy(
                    f"another program is already using the drone's port {local_port} "
                    "— close any Jupyter notebook, Python script or other drone app "
                    "(or a second copy of Drone Coder) and try again"
                ) from exc
        sock.settimeout(_RECV_POLL_S)
        self._sock = sock
        self._turn = threading.Lock()  # one ordinary command at a time
        self._slot = threading.Lock()  # guards _waiter
        self._waiter: _Waiter | None = None
        self._overdue = False
        self._closed = threading.Event()
        self._receiver = threading.Thread(
            target=self._receive, name="tello-replies", daemon=True
        )
        self._receiver.start()

    # --- public ------------------------------------------------------------

    def handshake(
        self, tries: int = HANDSHAKE_TRIES, timeout: float = HANDSHAKE_TIMEOUT_S
    ) -> None:
        """Enter SDK mode. Raises :class:`TelloNoReply` if nothing answers."""
        for attempt in range(tries):
            try:
                self.send("command", timeout)
                return
            except TelloNoReply:
                if attempt == tries - 1:
                    raise

    def send(self, command: str, timeout: float | None = None) -> str:
        """Send ``command`` once and return the aircraft's reply.

        Raises :class:`TelloError` on an ``error …`` reply, :class:`TelloNoReply`
        on silence, :class:`TelloInterrupted` if an emergency stop took over.
        """
        if timeout is None:
            timeout = timeout_for(command)
        with self._turn:
            if self._overdue:
                time.sleep(OVERDUE_SETTLE_S)
                self._overdue = False
            return self._exchange(command, timeout)

    def interrupt(self, command: str, timeout: float | None = None) -> str:
        """Send ``command`` now, cutting off whatever is waiting for a reply.

        For ``emergency`` only. The SDK does not number its replies, so the
        first datagram after this is taken to be this command's answer; the
        command it displaced gives up with :class:`TelloInterrupted`.
        """
        if timeout is None:
            timeout = timeout_for(command)
        return self._exchange(command, timeout)

    def send_no_reply(self, command: str) -> None:
        """Fire and forget. For teardown, where nobody will read the answer."""
        try:
            self._sock.sendto(command.encode(), self._address)
        except OSError:
            pass

    def close(self) -> None:
        """Release the socket and stop the receiver. Idempotent; never raises."""
        if self._closed.is_set():
            return
        self._closed.set()
        with self._slot:
            waiter, self._waiter = self._waiter, None
        if waiter is not None:
            waiter.done.set()  # reply stays None: reads as no reply
        try:
            self._sock.close()
        except OSError:
            pass
        if threading.current_thread() is not self._receiver:
            self._receiver.join(_RECV_POLL_S * 2)

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    # --- internals -----------------------------------------------------------

    def _exchange(self, command: str, timeout: float) -> str:
        if self._closed.is_set():
            raise TelloNoReply("the drone link is closed")
        waiter = _Waiter()
        with self._slot:
            displaced, self._waiter = self._waiter, waiter
        if displaced is not None:
            displaced.preempted = True
            displaced.done.set()
        try:
            self._sock.sendto(command.encode(), self._address)
        except OSError as exc:
            self._release(waiter)
            raise TelloNoReply(f"could not send '{command}' to the drone: {exc}") from exc
        log.debug("sent %r", command)
        if not waiter.done.wait(timeout):
            self._release(waiter)
            self._overdue = True
            raise TelloNoReply(
                f"the drone did not answer '{command}' within {timeout:g}s"
            )
        if waiter.preempted:
            raise TelloInterrupted(f"'{command}' was cut short by an emergency stop")
        if waiter.reply is None:
            raise TelloNoReply("the drone link was closed while waiting")
        reply = waiter.reply
        log.debug("reply to %r: %r", command, reply)
        if reply.lower().startswith("error"):
            raise TelloError(plain_refusal(command, reply))
        return reply

    def _release(self, waiter: _Waiter) -> None:
        with self._slot:
            if self._waiter is waiter:
                self._waiter = None

    def _receive(self) -> None:
        host = self._address[0]
        while not self._closed.is_set():
            try:
                data, source = self._sock.recvfrom(1024)
            except TimeoutError:
                continue
            except ConnectionResetError:
                # Windows reports an ICMP "port unreachable" for an earlier
                # send (no aircraft on the network yet) as a failed *receive*.
                # It says nothing about this socket, which is still fine.
                continue
            except OSError:
                return  # closed under us
            if source[0] != host:
                continue
            try:
                text = data.decode("utf-8", errors="replace").strip()
            except Exception:
                continue
            with self._slot:
                waiter, self._waiter = self._waiter, None
            if waiter is None:
                log.debug("dropped a reply nobody was waiting for: %r", text)
                continue
            waiter.reply = text
            waiter.done.set()
