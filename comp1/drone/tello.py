import time

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


class TelloDrone(DroneAdapter):
    """Adapter for a real DJI Tello, built to survive the aircraft going away.

    Every connection is a *fresh* ``djitellopy.Tello`` and every disconnection
    is explicit. Both halves matter on competition day:

    * A rebooted Tello is a new SDK session with an empty response buffer. The
      old object's queued responses would be answers to commands from before the
      reboot, so it is retired rather than reused.
    * The video decoder holds a UDP port until its container is closed, and
      ``BackgroundFrameRead.stop()`` only sets a flag that the decode thread
      checks *after the next frame arrives* — which never happens once the
      aircraft is gone. Closing the container here is what makes a later
      reconnect (or a round trip through the simulator) possible without
      restarting the program.
    """

    mode = "tello"

    def __init__(self, flight: FlightConfig = DEFAULT_FLIGHT_CONFIG):
        self._t = Tello()
        self._reader = None
        self.flight = flight
        self.link_ok = False
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
        self.close()
        self._t = Tello()
        self._t.connect()
        self._t.streamon()
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
            self._reader = self._t.get_frame_read()
        except Exception:
            self._reader = None
            self._reopen_at = time.monotonic() + _REOPEN_INTERVAL_S
        return self._reader

    def _release_reader(self):
        reader, self._reader = self._reader, None
        if reader is None:
            return
        try:
            reader.stop()
        except Exception:
            pass
        try:
            # the flag alone is not enough — see the class docstring
            reader.container.close()
        except Exception:
            pass

    def _cmd(self, call, *args):
        """Run an aircraft command, noting a link loss before re-raising.

        The interpreter turns the exception into a finished-with-error mission;
        the cleared flag is what lets the server reconnect once it stops.
        """
        if self._t is None:
            raise RuntimeError("the Tello is not connected")
        try:
            return call(*args)
        except Exception:
            self.link_ok = False
            raise

    # --- flight ----------------------------------------------------------

    def takeoff(self):
        self._cmd(self._t.takeoff)

    def land(self):
        self._cmd(self._t.land)

    def emergency(self):
        self._cmd(self._t.emergency)

    def move(self, direction, cm):
        self._cmd(getattr(self._t, f"move_{direction}"), cm)

    def rotate(self, direction, deg):
        self._cmd(
            self._t.rotate_clockwise
            if direction == "cw"
            else self._t.rotate_counter_clockwise,
            deg,
        )

    def flip(self, direction):
        self._cmd(self._t.flip, _FLIP_CODE[direction])
        # The aircraft throws itself along the flip direction and stays there,
        # so without this a "signal fire found" back-flip leaves the drone
        # short of the fire it just found and every following move starts from
        # the wrong place. Tune flip_recover_cm on-site; below the 20 cm floor
        # the Tello would refuse the move, so skip it instead of erroring.
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
        return self._cmd(self._t.get_battery)
