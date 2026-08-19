from abc import ABC, abstractmethod

import numpy as np


class DroneAdapter(ABC):
    #: Short identifier sent to the browser so it can make the active flight
    #: target unambiguous. Concrete adapters override this where appropriate.
    mode: str = "mock"

    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def takeoff(self) -> None: ...

    @abstractmethod
    def land(self) -> None: ...

    @abstractmethod
    def emergency(self) -> None: ...

    @abstractmethod
    def move(self, direction: str, cm: int) -> None: ...

    @abstractmethod
    def rotate(self, direction: str, deg: int) -> None: ...

    @abstractmethod
    def flip(self, direction: str) -> None: ...

    @abstractmethod
    def get_frame(self) -> np.ndarray | None: ...

    @abstractmethod
    def battery(self) -> int: ...

    #: Whether :meth:`reset` actually moves the aircraft. False on hardware, and
    #: the UI says so rather than claiming a reposition that did not happen — a
    #: student trusting "back on the start pad" while a real Tello hovers where
    #: they left it is how a drone gets flown into a wall.
    can_reset: bool = False

    #: Whether the adapter currently believes it can still reach its aircraft.
    #: Only hardware can lose a link, so the simulated adapters leave this True
    #: forever; :class:`~comp1.drone.tello.TelloDrone` clears it when the
    #: aircraft stops answering, and the server's watchdog reconnects.
    link_ok: bool = True

    def close(self) -> None:
        """Release every OS resource the adapter holds and stop its threads.

        The server calls this whenever an adapter stops being the active drone.
        It matters because the Tello video stream owns a UDP port for the life
        of the process unless it is closed explicitly: an adapter that is merely
        dropped keeps that port, and the *next* connection cannot open the
        stream — which is why switching to the simulator and back used to need a
        restart of the whole program.

        Must never fly the aircraft. Closing is a housekeeping operation, and a
        surprise landing (or worse) from letting go of an object is not.
        """

    def reconnect(self) -> None:
        """Re-establish the link to the aircraft, from scratch.

        A rebooted Tello is a brand-new SDK session — the old one cannot be
        resumed, so the default is a full close-and-connect rather than a
        keepalive. Safe to call repeatedly: it is the watchdog's retry.
        """
        self.close()
        self.connect()

    def reset(self) -> None:
        """Put a simulated drone back on its start pad.

        Called before every run so a program always starts from the same state
        instead of wherever the last attempt happened to end — students iterate
        by hitting Run repeatedly, and a drone that begins each attempt somewhere
        new makes their own changes impossible to judge.

        Real hardware cannot teleport, so this is a no-op there and ``can_reset``
        stays False. Landing the drone instead would be a surprise flight command
        from a button labelled Reset; the software-side clearing the server does
        around this call is the whole of the hardware behaviour.
        """

    def annotate(self, frame: np.ndarray) -> np.ndarray:
        """Adapter-specific debug overlay, applied to the *display* copy only.

        Never call this on a frame headed for the detector — the simulator uses
        it to draw a minimap, which would otherwise be a red blob in the sensor
        stream.
        """
        return frame

    # --- display feeds ----------------------------------------------------
    # Optional, and display-only: the browser's third-person view needs to know
    # where the drone is in the arena to draw it. An adapter that cannot know
    # (a real Tello has no arena-absolute position) returns None and the browser
    # hides the 3D stage.
    #
    # These are deliberately NOT part of the sensing surface. Nothing in
    # comp1/interpreter.py or comp1/api.py may call them, and no block may
    # expose them — absolute arena coordinates in a student's hands is exactly
    # the hardcoding requirements §4 rules out.

    def pose(self) -> dict | None:
        """Arena-absolute pose for rendering: x, y, z, heading, roll, pitch, flying."""
        return None

    def scene(self) -> dict | None:
        """Static arena description for rendering: extents, wall height, markers."""
        return None

    # --- authoring feed ---------------------------------------------------
    # The mirror of the display feeds: the browser's arena panel writes marker
    # coordinates back so a teacher can lay a problem out by hand. Same rule —
    # WebSocket only, never a block, never a sensor (requirements §4). An
    # adapter with no arena to author returns None and the panel stays hidden.

    def scenery_catalog(self) -> list | None:
        """The sceneries this adapter can fly in, or None if it has no arena."""
        return None

    def load_scenery(self, name=None, fires=None, randomise=False):
        """Swap scenery or replace the target layout. No-op without an arena."""
        return
