from abc import ABC, abstractmethod

import numpy as np


class DroneAdapter(ABC):
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
        """Static arena description for rendering: size, wall height, markers."""
        return None
