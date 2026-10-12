import threading
import time

import av
import numpy as np

from .base import DroneAdapter
from .config import DEFAULT_FLIGHT_CONFIG, FlightConfig
from .tello_link import TelloError, TelloInterrupted, TelloLink

TELLO_MIN_MOVE_CM = 20  # the aircraft refuses anything shorter
_FLIP_CODE = {"forward": "f", "back": "b", "left": "l", "right": "r"}
_OPPOSITE = {"forward": "back", "back": "forward", "left": "right", "right": "left"}
#: Where the aircraft sends its H.264 stream once told ``streamon``. Fixed by
#: the SDK on a standard Tello — only the EDU can be asked to use another port.
VIDEO_ADDRESS = "udp://@0.0.0.0:11111"
#: How long to wait after a failed video-stream open before trying again. The
#: video loop asks for a frame ten times a second and re-opening the stream
#: blocks for seconds, so without this a dead stream would stall the loop.
_REOPEN_INTERVAL_S = 2.0


#: How long a stalled video read blocks before PyAV abandons it. This is the
#: whole reason a teardown can be safe: opened with *no* read timeout (as
#: djitellopy did), once the aircraft stops sending, its decode thread parks
#: inside ``av_read_frame()`` for good and the only way to get the UDP port
#: back is to free the container out from under it — which segfaults the
#: process. A finite timeout means the thread always comes back to check
#: whether it has been asked to stop.
_VIDEO_READ_TIMEOUT_S = 1.0
#: How long the *first* open may take. A stream that needs a moment to come up
#: is not a failure, so this is generous — it is only ever paid on connect,
#: where the caller is already waiting.
_VIDEO_OPEN_TIMEOUT_S = 5.0
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
    traceback at all. A reader whose ``stop()`` only sets a flag invites
    exactly that (djitellopy's ``BackgroundFrameRead``, which this replaced):
    the worker reads the flag *after the next frame arrives*, which never
    happens once the aircraft is gone, so releasing the port means closing the
    container behind the worker's back.

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
    def open(cls, address: str = VIDEO_ADDRESS, **kwargs) -> FrameReader:
        """Start a reader on the aircraft's video port."""
        reader = cls(address, **kwargs)
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
    """Adapter for a real DJI Tello, built to survive the aircraft going away
    and a laptop firewall that will not let it talk back.

    Commands go over :class:`~comp1.drone.tello_link.TelloLink`, which needs no
    firewall rule because it only ever reads replies to what it sent — see that
    module for why djitellopy was replaced. Every connection is a *fresh* link
    on a fresh socket and every disconnection is explicit: a rebooted Tello is a
    new SDK session, and anything the old one still had to say lands on a port
    nobody is listening on any more.

    Video is the one channel a firewall can still take away (it is pushed to
    :11111 unasked). The stream is read through :class:`FrameReader` — see that
    class for why closing it is otherwise a segfault — and a stream that never
    delivers a single frame is reported as ``video_blocked`` rather than as a
    lost drone, because the aircraft is still answering and still flies.
    """

    mode = "tello"

    def __init__(self, flight: FlightConfig = DEFAULT_FLIGHT_CONFIG):
        self._link: TelloLink | None = None
        self._reader = None
        self.flight = flight
        self.command_timeout_s = flight.command_timeout_s
        self.link_ok = False
        #: True when the aircraft answers commands but no video frame has ever
        #: arrived this session: what a firewall dropping :11111 looks like.
        self.video_blocked = False
        self._seen_frame = False
        self._last_frame = None  # identity of the last *decoded* frame
        self._frame_at = 0.0
        self._reopen_at = 0.0

    # --- connection lifecycle --------------------------------------------

    def connect(self):
        """Enter SDK mode on a fresh link, then start the video stream.

        Tears down whatever came before, so this doubles as the reconnect path
        (see :meth:`DroneAdapter.reconnect`) and can be called any number of
        times. Waits for no telemetry: the handshake's own reply is the proof
        that the aircraft is there, and telemetry is exactly what a firewall
        drops.
        """
        self.close()
        link = TelloLink()
        try:
            link.handshake()
            link.send("streamon")
        except Exception:
            link.close()
            raise
        self._link = link
        self.link_ok = True
        self.video_blocked = False
        self._seen_frame = False
        self._last_frame = None
        self._frame_at = time.monotonic()
        self._reopen_at = 0.0
        # Best effort: the control link is what "connected" means, and a stream
        # that needs another second is not a failed connection. get_frame()
        # retries, and reports video_blocked if it never comes up at all.
        self._open_reader()

    def close(self):
        """Stop reading video and drop the command link. Never flies.

        Deliberately sends no ``streamoff``. Once the aircraft has been told
        ``streamoff``, a later ``streamon`` answers ``ok`` but no video comes
        until the drone is rebooted (field test, 2026-10-12, Tello EDU: three
        out of three runs). So a reconnect, or a round trip through the
        simulator, would have lost the camera, and it would have been
        reported as a firewall problem. Leaving the stream running costs nothing:
        with nobody reading :11111 the datagrams are simply dropped, and the
        next connect's ``streamon`` is harmless.
        """
        self._release_reader()
        link, self._link = self._link, None
        self.link_ok = False
        if link is not None:
            link.close()

    def _open_reader(self):
        """Start the background decoder, or arm a retry. Never raises."""
        if self._link is None:
            return None
        try:
            self._reader = FrameReader.open()
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

    def _send(self, command: str) -> str:
        """Run one aircraft command, noting a link loss before re-raising.

        A *refusal* is not a link loss: the aircraft answered, so the link is
        demonstrably up and reconnecting would only throw away a working
        session. Silence and transport errors are. The interpreter turns either
        into a finished-with-error mission; the cleared flag is what lets the
        server reconnect once it stops.
        """
        link = self._link
        if link is None:
            raise RuntimeError("the Tello is not connected")
        try:
            return link.send(command)
        except (TelloError, TelloInterrupted):
            raise
        except Exception:
            self.link_ok = False
            raise

    # --- flight ----------------------------------------------------------

    def takeoff(self):
        self._send("takeoff")

    def land(self):
        self._send("land")

    def emergency(self):
        """Cut the motors *now*, even while a long move is still waiting.

        Ordinary commands take turns on the link; this one cannot queue behind
        a 30-second move, so it goes through :meth:`TelloLink.interrupt`.
        """
        link = self._link
        if link is None:
            raise RuntimeError("the Tello is not connected")
        try:
            link.interrupt("emergency")
        except TelloError:
            raise
        except Exception:
            self.link_ok = False
            raise

    def move(self, direction, cm):
        self._send(f"{direction} {int(cm)}")

    def rotate(self, direction, deg):
        self._send(f"{'cw' if direction == 'cw' else 'ccw'} {int(deg)}")

    def flip(self, direction):
        self._send(f"flip {_FLIP_CODE[direction]}")
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
            self._seen_frame = True
            self.video_blocked = False
        elif self._note_silence(now):
            # a frozen picture reads as a working camera pointed at nothing;
            # showing no picture at all is the honest report
            return None
        return frame[:, :, ::-1].copy()  # RGB→BGR

    def _note_silence(self, now: float) -> bool:
        """Decide what a quiet stream means, once it has been quiet too long.

        A stream that delivered frames and then stopped is a lost aircraft:
        clear ``link_ok`` and let the watchdog reconnect. A stream that has
        *never* delivered a frame this session, from an aircraft that answered
        the handshake, is a firewall dropping :11111 — reconnecting cannot fix
        that and would loop forever, so it is reported as ``video_blocked`` and
        the link stays up. If the aircraft really is gone as well, the next
        command or battery poll goes unanswered and clears ``link_ok`` anyway.
        """
        if now - self._frame_at < self.flight.link_timeout_s:
            return False
        if self._seen_frame:
            self.link_ok = False
        else:
            self.video_blocked = True
        return True

    def battery(self) -> int:
        # Asked for, not read from telemetry: telemetry is pushed to :8890
        # unasked, and that is exactly what a managed laptop's firewall drops.
        reply = self._send("battery?")
        try:
            return int(reply)
        except ValueError:
            raise TelloError(
                f"the drone sent an unreadable battery level: {reply!r}"
            ) from None
