import numpy as np
from djitellopy import Tello

from .base import DroneAdapter
from .config import DEFAULT_FLIGHT_CONFIG, FlightConfig

TELLO_MIN_MOVE_CM = 20  # the aircraft refuses anything shorter
_FLIP_CODE = {"forward": "f", "back": "b", "left": "l", "right": "r"}
_OPPOSITE = {"forward": "back", "back": "forward", "left": "right", "right": "left"}


class TelloDrone(DroneAdapter):
    mode = "tello"

    def __init__(self, flight: FlightConfig = DEFAULT_FLIGHT_CONFIG):
        self._t = Tello()
        self._reader = None
        self.flight = flight

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
        (
            self._t.rotate_clockwise
            if direction == "cw"
            else self._t.rotate_counter_clockwise
        )(deg)

    def flip(self, direction):
        self._t.flip(_FLIP_CODE[direction])
        # The aircraft throws itself along the flip direction and stays there,
        # so without this a "signal victim found" back-flip leaves the drone
        # short of the victim it just found and every following move starts from
        # the wrong place. Tune flip_recover_cm on-site; below the 20 cm floor
        # the Tello would refuse the move, so skip it instead of erroring.
        if self.flight.flip_recover_cm >= TELLO_MIN_MOVE_CM:
            self.move(_OPPOSITE[direction], self.flight.flip_recover_cm)

    def get_frame(self) -> np.ndarray | None:
        if self._reader is None:
            self._reader = self._t.get_frame_read()
        frame = self._reader.frame
        return None if frame is None else frame[:, :, ::-1].copy()  # RGB→BGR

    def battery(self) -> int:
        return self._t.get_battery()
