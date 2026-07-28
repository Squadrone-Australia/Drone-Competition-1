import numpy as np
from comp1.drone.mock import MockDrone


def test_mock_logs_commands():
    d = MockDrone()
    d.connect(); d.takeoff(); d.move("forward", 50); d.rotate("cw", 90); d.land()
    assert d.log == [("connect",), ("takeoff",), ("move", "forward", 50),
                     ("rotate", "cw", 90), ("land",)]


def test_mock_frame_factory():
    red = np.zeros((480, 640, 3), np.uint8); red[:] = (0, 0, 255)
    d = MockDrone(frame_factory=lambda: red)
    assert d.get_frame()[0, 0, 2] == 255
