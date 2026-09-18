import threading
import time

import av
import numpy as np
from djitellopy import Tello

from .base import DroneAdapter
from .config import DEFAULT_FLIGHT_CONFIG, FlightConfig

TELLO_MIN_MOVE_CM = 20  # the aircraft refuses anything shorter
_FLIP_CODE = {"forward": "f", "back": "b", "left": "l", "right": "r"}
_OPPOSITE = {"forward": "back", "back": "forward", "left": "right", "right": "left"}
#: How long to wait after a failed video-stream open before trying again. The
#: video loop asks for a frame ten times a second and re-opening the stream
#: blocks for seconds, so without this a dead stream would stall the loop.
_REOPEN_INTERVAL_S = 2.0
#: How long a reconnect pauses so the retiring session's last replies land in
#: the queue that is about to be thrown away rather than the new one. Every
#: command's own timeout is measured in seconds, so this is cheap insurance.
_STALE_RESPONSE_SETTLE_S = 0.4


def _drain_responses(t) -> None:
    """Throw away datagrams queued for this aircraft. Never raises.

    They can only be answers to commands from a session that no longer exists —
    a reboot, or the adapter we just retired — and djitellopy would hand the
    first of them to the next command as if it were its own reply.
    """
    try:
        t.get_own_udp_object()["responses"].clear()
    except Exception:
        pass


#: How long a stalled video read blocks before PyAV abandons it. This is the
#: whole reason a teardown can be safe: djitellopy opens the stream with *no*
#: read timeout, so once the aircraft stops sending, its decode thread parks
#: inside ``av_read_frame()`` for good and the only way to get the UDP port
#: back is to free the container out from under it — which segfaults the
#: process. A finite timeout means the thread always comes back to check
#: whether it has been asked to stop.
_VIDEO_READ_TIMEOUT_S = 1.0
#: How long the *first* open may take. A stream that needs a moment to come up
#: is not a failure, so this is generous — it is only ever paid on connect,
#: where the caller is already waiting.
_VIDEO_OPEN_TIMEOUT_S = float(Tello.FRAME_GRAB_TIMEOUT)
#: How long a *reopen* on the decoder thread may take. Much shorter than the
#: first open, because this one is paid where nobody is waiting and a teardown
#: cannot interrupt an open in flight — it is the longest a close() can block.
#: Giving up here is cheap: the worker exits, ``alive`` goes False and the
#: adapter opens a fresh reader on its own retry interval.
_VIDEO_REOPEN_TIMEOUT_S = 1.5
#: How long a teardown waits for the decoder thread to notice. Longer than the
#: worst single blocking call the worker can be inside — a read or a reopen —
#: so the normal path always joins.
_VIDEO_JOIN_TIMEOUT_S = 4.0


def _open_container(address: str, open_timeout: float = _VIDEO_OPEN_TIMEOUT_S):
    """Open the video stream with both timeouts set. Raises like ``av.open``."""
    return av.open(address, timeout=(open_timeout, _VIDEO_READ_TIMEOUT_S))


class FrameReader:
    """Decodes the Tello's H.264 stream on a thread that *owns* its container.

    The ownership rule is the point, and it is not decoration: an
    ``AVFormatContext`` freed by one thread while another sits inside
    ``av_read_frame()`` is a use-after-free, and it lands as
    ``SIGSEGV`` in ``avio_read_partial`` — the process dies with no Python
    traceback at all. djitellopy's ``BackgroundFrameRead`` invites exactly
    that: ``stop()`` only sets a flag its worker reads *after the next frame
    arrives*, which never happens once the aircraft is gone, so releasing the
    port means closing the container behind the worker's back.

    Here only :meth:`_run` ever touches the container, and it always returns
    within ``_VIDEO_READ_TIMEOUT_S``. A teardown sets an event and joins; no
    other thread closes anything.
    """

    def __init__(self, address: str, opener=None):
        self._address = address
        # resolved here rather than as a default argument so that tests (and a
        # future transport) can substitute the av layer wholesale
        self._opener = opener or _open_container
        self._lock = threading.Lock()
        self._frame = None
        self._stop = threading.Event()
        # Opened here rather than on the thread so that a stream which never
        # comes up is reported to the caller instead of dying in the worker.
        # Once :meth:`start` runs, this reference is stale by design — the
        # worker reopens as needed and nobody else may close it.
        self._container = self._opener(address)
        self._worker = threading.Thread(target=self._run, name="tello-video",
                                        daemon=True)
        self._started = False

    @classmethod
    def open(cls, tello, **kwargs) -> FrameReader:
        """Start a reader on ``tello``'s video port."""
        reader = cls(tello.get_udp_video_address(), **kwargs)
        reader.start()
        return reader

    def start(self) -> None:
        self._started = True
        self._worker.start()

    @property
    def frame(self):
        """Most recently decoded frame as RGB uint8, or None before the first."""
        with self._lock:
            return self._frame

    @property
    def alive(self) -> bool:
        """False once the worker has given up — the stream needs reopening."""
        return self._started and self._worker.is_alive()

    def close(self, timeout: float = _VIDEO_JOIN_TIMEOUT_S) -> bool:
        """Stop decoding and give the UDP port back. Idempotent.

        Returns whether the worker actually finished. It normally does; if it
        somehow did not, the container is deliberately *leaked* rather than
        closed, because leaking a port is a bug and closing it is a crash.
        """
        self._stop.set()
        if not self._started:
            self._discard(self._container)
            return True
        self._worker.join(timeout)
        return not self._worker.is_alive()

    # --- worker thread only ------------------------------------------------

    def _run(self) -> None:
        container = self._container
        try:
            while container is not None and not self._stop.is_set():
                try:
                    for frame in container.decode(video=0):
                        with self._lock:
                            self._frame = frame.to_ndarray(format="rgb24")
                        if self._stop.is_set():
                            break
                except Exception:
                    # A read timeout, a decoder hiccup and an aircraft that
                    # went away all arrive here as av.error.ExitError, and a
                    # container that has raised once never yields another
                    # frame — reopening is the only cure, not retrying.
                    pass
                if self._stop.is_set():
                    break
                container = self._reopen(container)
        finally:
            self._discard(container)

    def _reopen(self, old):
        self._discard(old)
        try:
            return self._opener(self._address, _VIDEO_REOPEN_TIMEOUT_S)
        except Exception:
            return None  # `alive` goes False; the adapter retries from scratch

    @staticmethod
    def _discard(container) -> None:
        if container is None:
            return
        try:
            container.close()
        except Exception:
            pass

class TelloDrone(DroneAdapter):
    """Adapter for a real DJI Tello, built to survive the aircraft going away.

    Every connection is a *fresh* ``djitellopy.Tello`` and every disconnection
    is explicit. Both halves matter on competition day:

    * A rebooted Tello is a new SDK session with an empty response buffer. The
      old object's queued responses would be answers to commands from before the
      reboot, so it is retired rather than reused.
    * The video decoder holds a UDP port until its container is closed, so the
      stream is read through :class:`FrameReader` rather than djitellopy's
      ``BackgroundFrameRead`` — see that class for why closing it is otherwise
      a segfault. Releasing the port here is what makes a later reconnect (or
      a round trip through the simulator) possible without restarting.
    """

    mode = "tello"

    def __init__(self, flight: FlightConfig = DEFAULT_FLIGHT_CONFIG):
        self._t = Tello()
        self._reader = None
        self.flight = flight
        self.command_timeout_s = flight.command_timeout_s
        self.link_ok = False
        #: whether a session has actually been opened on the aircraft — a
        #: teardown only has replies to outrun if there was one
        self._session_live = False
        self._last_frame = None  # identity of the last *decoded* frame
        self._frame_at = 0.0
        self._reopen_at = 0.0

    # --- connection lifecycle --------------------------------------------

    def connect(self):
        """Enter SDK mode on a clean object, then start the video stream.

        Tears down whatever came before, so this doubles as the reconnect path
        (see :meth:`DroneAdapter.reconnect`) and can be called any number of
        times.
        """
        had_session = self._session_live
        self.close()
        if had_session:
            # Let the dying session's last answers arrive before the new one
            # starts listening. djitellopy pairs replies with commands
            # *positionally* — a command takes whatever datagram is at the head
            # of the queue — so one stray "ok" left over from the old session
            # offsets every reply from then on, and a command reads the answer
            # to the one before it. That mis-pairing is how an innocent command
            # ends up reporting the aircraft's "error Not joystick".
            time.sleep(_STALE_RESPONSE_SETTLE_S)
        self._t = Tello()  # installs a fresh, empty response queue
        _drain_responses(self._t)  # ...and anything that beat us to it
        self._t.connect()
        self._t.streamon()
        self._session_live = True
        self.link_ok = True
        self._last_frame = None
        self._frame_at = time.monotonic()
        self._reopen_at = 0.0
        # Best effort: the control link is what "connected" means, and a stream
        # that needs another second is not a failed connection. get_frame()
        # retries, and the watchdog reconnects if it never comes up.
        self._open_reader()

    def close(self):
        """Drop the video stream and retire the aircraft object. Never flies."""
        self._release_reader()
        old, self._t = self._t, None
        self._session_live = False
        self.link_ok = False
        if old is None:
            return
        try:
            # fire-and-forget: a Tello that has gone away would make the normal
            # retrying command wait tens of seconds for answers that never come
            old.send_command_without_return("streamoff")
        except Exception:
            pass
        # Neutralise the retired object. djitellopy's __del__ calls end(), which
        # lands a drone it believes is flying and deletes the global `drones`
        # entry by host — and that entry now belongs to the *new* instance, so a
        # late garbage collection would break the live connection.
        for attr, value in (
            ("is_flying", False),
            ("stream_on", False),
            ("background_frame_read", None),
        ):
            try:
                setattr(old, attr, value)
            except Exception:
                pass
        try:
            old.address = (f"retired-{id(old)}", 0)
        except Exception:
            pass

    def _open_reader(self):
        """Start the background decoder, or arm a retry. Never raises."""
        if self._t is None:
            return None
        try:
            self._reader = FrameReader.open(self._t)
        except Exception:
            self._reader = None
            self._reopen_at = time.monotonic() + _REOPEN_INTERVAL_S
        return self._reader

    def _release_reader(self):
        reader, self._reader = self._reader, None
        if reader is None:
            return
        try:
            reader.close()
        except Exception:
            pass

    def _cmd(self, name: str, *args):
        """Run an aircraft command, noting a link loss before re-raising.

        Looked up by name rather than taken as a bound method so that a command
        issued between a close and the next connect reports what is actually
        wrong instead of an AttributeError on None. The interpreter turns the
        exception into a finished-with-error mission; the cleared flag is what
        lets the server reconnect once it stops.
        """
        aircraft = self._t
        if aircraft is None:
            raise RuntimeError("the Tello is not connected")
        try:
            return getattr(aircraft, name)(*args)
        except Exception:
            self.link_ok = False
            raise

    # --- flight ----------------------------------------------------------

    def takeoff(self):
        self._cmd("takeoff")

    def land(self):
        self._cmd("land")

    def emergency(self):
        self._cmd("emergency")

    def move(self, direction, cm):
        self._cmd(f"move_{direction}", cm)

    def rotate(self, direction, deg):
        self._cmd(
            "rotate_clockwise" if direction == "cw" else "rotate_counter_clockwise", deg
        )

    def flip(self, direction):
        self._cmd("flip", _FLIP_CODE[direction])
        # The aircraft throws itself along the flip direction and stays there.
        # An opposite move undoes that, but it also costs altitude the flip has
        # already taken, so flip_recover_cm defaults to 0 and the recovery is
        # opt-in. Below the 20 cm floor the Tello would refuse the move, so
        # skip it instead of erroring — which is also how 0 disables it.
        if self.flight.flip_recover_cm >= TELLO_MIN_MOVE_CM:
            self.move(_OPPOSITE[direction], self.flight.flip_recover_cm)

    # --- sensing ---------------------------------------------------------

    def get_frame(self) -> np.ndarray | None:
        """Latest camera frame, or None while the stream is not delivering.

        Silence is what a lost aircraft looks like: the decoder keeps handing
        back the last frame it managed to decode, forever. So a repeat of the
        same frame object is tolerated briefly (decoding is slower than this
        loop) and treated as a dead link past ``link_timeout_s``.
        """
        now = time.monotonic()
        reader = self._reader
        if reader is not None and not reader.alive:
            # the decoder thread gave up: the stream stalled and would not
            # reopen. Let go of it and come back through the retry path.
            self._release_reader()
            reader = None
            self._reopen_at = now + _REOPEN_INTERVAL_S
        if reader is None:
            if now < self._reopen_at:
                return None
            reader = self._open_reader()
            if reader is None:
                self._note_silence(now)
                return None
        try:
            frame = reader.frame
        except Exception:
            self._release_reader()
            self._reopen_at = now + _REOPEN_INTERVAL_S
            self._note_silence(now)
            return None
        if frame is None:
            self._note_silence(now)
            return None
        if frame is not self._last_frame:
            self._last_frame = frame
            self._frame_at = now
        elif self._note_silence(now):
            # a frozen picture reads as a working camera pointed at nothing;
            # showing no picture at all is the honest report
            return None
        return frame[:, :, ::-1].copy()  # RGB→BGR

    def _note_silence(self, now: float) -> bool:
        """Clear ``link_ok`` once the stream has been quiet for too long."""
        if now - self._frame_at < self.flight.link_timeout_s:
            return False
        self.link_ok = False
        return True

    def battery(self) -> int:
        return self._cmd("get_battery")
