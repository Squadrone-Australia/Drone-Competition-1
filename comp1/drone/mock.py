import numpy as np

from .base import DroneAdapter


class MockDrone(DroneAdapter):
    def __init__(self, frame_factory=None):
        self.log = []
        self._frame_factory = frame_factory or (lambda: np.zeros((480, 640, 3), np.uint8))

    def connect(self):
        self.log.append(("connect",))

    def takeoff(self):
        self.log.append(("takeoff",))

    def land(self):
        self.log.append(("land",))

    def emergency(self):
        self.log.append(("emergency",))

    def move(self, direction, cm):
        self.log.append(("move", direction, cm))

    def rotate(self, direction, deg):
        self.log.append(("rotate", direction, deg))

    def flip(self, direction):
        self.log.append(("flip", direction))

    def get_frame(self):
        return self._frame_factory()

    def battery(self):
        return 100
