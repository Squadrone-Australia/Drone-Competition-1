import numpy as np
from djitellopy import Tello

from .base import DroneAdapter


class TelloDrone(DroneAdapter):
    def __init__(self):
        self._t = Tello()
        self._reader = None

    def connect(self):
        self._t.connect()
        self._t.streamon()

    def takeoff(self):
        self._t.takeoff()

    def land(self):
        self._t.land()

    def emergency(self):
        self._t.emergency()

    def move(self, direction, cm):
        getattr(self._t, f"move_{direction}")(cm)

    def rotate(self, direction, deg):
        (self._t.rotate_clockwise if direction == "cw"
         else self._t.rotate_counter_clockwise)(deg)

    def flip(self, direction):
        self._t.flip({"forward": "f", "back": "b", "left": "l", "right": "r"}[direction])

    def get_frame(self) -> np.ndarray | None:
        if self._reader is None:
            self._reader = self._t.get_frame_read()
        frame = self._reader.frame
        return None if frame is None else frame[:, :, ::-1].copy()  # RGB→BGR

    def battery(self) -> int:
        return self._t.get_battery()
