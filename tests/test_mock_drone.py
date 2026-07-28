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


def test_tello_adapter_maps_commands(monkeypatch):
    import comp1.drone.tello as t
    calls = []

    class FakeTello:
        def connect(self): calls.append("connect")
        def streamon(self): calls.append("streamon")
        def takeoff(self): calls.append("takeoff")
        def move_forward(self, cm): calls.append(f"move_forward {cm}")
        def rotate_clockwise(self, deg): calls.append(f"cw {deg}")
        def get_battery(self): return 87
        def get_frame_read(self): raise RuntimeError("not in test")

    monkeypatch.setattr(t, "Tello", FakeTello)
    d = t.TelloDrone()
    d.connect(); d.takeoff(); d.move("forward", 40); d.rotate("cw", 90)
    assert calls == ["connect", "streamon", "takeoff", "move_forward 40", "cw 90"]
    assert d.battery() == 87
