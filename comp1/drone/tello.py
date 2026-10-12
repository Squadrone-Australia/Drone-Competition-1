import socket
import threading
import time

import av
import numpy as np

from .base import DroneAdapter
from .config import DEFAULT_FLIGHT_CONFIG, FlightConfig
from .tello_link import TELLO_ADDRESS, TelloError, TelloInterrupted, TelloLink

TELLO_MIN_MOVE_CM = 20  # the aircraft refuses anything shorter
_FLIP_CODE = {"forward": "f", "back": "b", "left": "l", "right": "r"}
_OPPOSITE = {"forward": "back", "back": "forward", "left": "right", "right": "left"}
#: Where the aircraft sends its H.264 stream once told ``streamon``. Fixed by
#: the SDK on a standard Tello — only the EDU can be asked to use another port.
VIDEO_PORT = 11111
#: The ports the aircraft sends that stream *from*, as measured on a Tello EDU
#: (field tests, 2026-10-12): 62512 every time but once, when it was 62513.
#: Punching to a port it is not using costs one ignored datagram.
VIDEO_SOURCE_PORTS = (62512, 62513)
#: How long to wait after a failed video-stream open before trying again. The
#: video loop asks for a frame ten times a second, so without this a port that
#: cannot be bound would be retried ten times a second.
_REOPEN_INTERVAL_S = 2.0
#: How long the worker blocks in one receive before it looks at its stop flag
#: again — and so the longest a close() normally waits.
_RECV_TIMEOUT_S = 0.5
#: While no video is arriving, how often to punch again. A punch is only needed
#: until the stream flows (the stream itself keeps the firewall's entry alive),
#: but an aircraft that reboots, or a stream that stalls long enough for the
#: entry to expire, needs a fresh one.
_PUNCH_INTERVAL_S = 1.0
#: How long a teardown waits for the worker to notice. Longer than one
#: receive timeout, so the normal path always joins.
_VIDEO_JOIN_TIMEOUT_S = 2.0
#: How many packets in a row may fail to decode before the decoder is replaced.
#: About two seconds of video at the Tello's rate: one bad packet is normal on
#: Wi-Fi, and only a decoder stuck in a bad state needs starting over.
_MAX_DECODE_FAILURES = 60


def _h264_decoder():
    return av.CodecContext.create("h264", "r")


class FrameReader:
    """Receives the Tello's video on a socket it owns, and decodes it.

    **Owning the socket is what gets video through a firewall.** The stream is
    pushed to :11111 unasked, so a laptop firewall that drops unsolicited
    inbound UDP (a university-managed laptop on the Tello's *Public* Wi-Fi)
    drops all of it. But a firewall lets in *replies*: traffic from an address
    and port this socket has itself sent to. So while no video is arriving, the
    worker sends a one-byte datagram from :11111 to each port the aircraft
    streams from — a UDP hole-punch — and the stream that follows is filed as
    the reply. Field test (2026-10-12, Linux, ufw on): no punch, nothing
    arrived; punched, the full stream did. That only works if the punch leaves
    from the very socket the video arrives on, which is why this reads the
    datagrams itself instead of handing ``udp://@:11111`` to FFmpeg.

    **Only the worker thread touches the socket or the decoder.** The reader
    this replaced let FFmpeg own the socket inside a container, and freeing an
    ``AVFormatContext`` from one thread while another sat in
    ``av_read_frame()`` was a use-after-free that killed the process with no
    traceback. Here a receive always returns within ``_RECV_TIMEOUT_S``, and a
    teardown just sets an event and joins.

    The Tello sends raw H.264 (Annex B) cut into ~1460-byte datagrams with no
    framing of its own, so datagrams go straight into the decoder's parser,
    which finds the frame boundaries.
    """

    def __init__(
        self,
        port: int = VIDEO_PORT,
        host: str = TELLO_ADDRESS[0],
        source_ports=VIDEO_SOURCE_PORTS,
        decoder_factory=None,
    ):
        self._host = host
        self._punch_to = [(host, p) for p in source_ports]
        # resolved here rather than as a default argument so that tests can
        # substitute the av layer wholesale
        self._new_decoder = decoder_factory or _h264_decoder
        self._lock = threading.Lock()
        self._frame = None
        self._stop = threading.Event()
        # Bound here rather than on the thread so that a port someone else
        # holds is reported to the caller instead of dying in the worker.
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self._sock.bind(("", port))
        except OSError:
            self._sock.close()
            raise
        self._sock.settimeout(_RECV_TIMEOUT_S)
        self.port = self._sock.getsockname()[1]
        self._worker = threading.Thread(target=self._run, name="tello-video",
                                        daemon=True)
        self._started = False

    @classmethod
    def open(cls, **kwargs) -> FrameReader:
        """Start a reader on the aircraft's video port."""
        reader = cls(**kwargs)
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
        """False once the worker has stopped — the reader needs reopening."""
        return self._started and self._worker.is_alive()

    def close(self, timeout: float = _VIDEO_JOIN_TIMEOUT_S) -> bool:
        """Stop decoding and give the UDP port back. Idempotent.

        Returns whether the worker actually finished. It normally does; if it
        somehow did not, its socket is left for it to close on its way out.
        """
        self._stop.set()
        if not self._started:
            self._sock.close()
            return True
        if threading.current_thread() is not self._worker:
            self._worker.join(timeout)
        return not self._worker.is_alive()

    # --- worker thread only ------------------------------------------------

    def _punch(self) -> None:
        for target in self._punch_to:
            try:
                self._sock.sendto(b"\x00", target)
            except OSError:
                pass  # no route yet (Wi-Fi still joining): the next punch will do

    def _run(self) -> None:
        decoder = self._new_decoder()
        failures = 0
        last_data = last_punch = float("-inf")
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                if (
                    now - last_data >= _PUNCH_INTERVAL_S
                    and now - last_punch >= _PUNCH_INTERVAL_S
                ):
                    self._punch()
                    last_punch = now
                try:
                    data, source = self._sock.recvfrom(65536)
                except TimeoutError:
                    continue
                except ConnectionResetError:
                    # Windows reports an ICMP "port unreachable" from an earlier
                    # punch as a failed receive. The socket is fine.
                    continue
                except OSError:
                    return  # closed under us
                if source[0] != self._host:
                    continue
                last_data = time.monotonic()
                try:
                    packets = decoder.parse(data)
                except Exception:
                    decoder, failures = self._new_decoder(), 0
                    continue
                for packet in packets:
                    try:
                        frames = decoder.decode(packet)
                    except Exception:
                        # A lost datagram leaves a frame the decoder cannot
                        # use. Skip it: the decoder resynchronises by itself at
                        # the next keyframe. Throwing it away instead would also
                        # throw away the good stream its parser has buffered.
                        # Only a decoder that keeps failing is replaced.
                        failures += 1
                        if failures >= _MAX_DECODE_FAILURES:
                            decoder, failures = self._new_decoder(), 0
                            break
                        continue
                    failures = 0
                    for frame in frames:
                        image = frame.to_ndarray(format="rgb24")
                        with self._lock:
                            self._frame = image
        finally:
            try:
                self._sock.close()
            except OSError:
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

    Video is pushed to :11111 unasked, which a laptop firewall drops;
    :class:`FrameReader` punches a hole for it from its own socket. A stream
    that still never delivers a single frame (a firewall the punch did not get
    through, or a drone another program told ``streamoff``) is reported as
    ``video_blocked`` rather than as a lost drone, because the aircraft is still
    answering and still flies.
    """

    mode = "tello"

    def __init__(self, flight: FlightConfig = DEFAULT_FLIGHT_CONFIG):
        self._link: TelloLink | None = None
        self._reader = None
        self.flight = flight
        self.command_timeout_s = flight.command_timeout_s
        self.link_ok = False
        #: True when the aircraft answers commands but no video frame has ever
        #: arrived this session: a firewall the punch did not get through, or
        #: a drone some other program told ``streamoff``.
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
        the handshake, is a firewall or a drone whose video was switched off
        elsewhere — reconnecting cannot fix either and would loop forever, so it
        is reported as ``video_blocked`` and the link stays up. If the aircraft
        really is gone as well, the next command or battery poll goes
        unanswered and clears ``link_ok`` anyway.
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
