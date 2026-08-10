import numpy as np
import pytest

from comp1.drone.config import FlightConfig
from comp1.drone.mock import MockDrone


def test_mock_logs_commands():
    d = MockDrone()
    d.connect()
    d.takeoff()
    d.move("forward", 50)
    d.rotate("cw", 90)
    d.land()
    assert d.log == [
        ("connect",),
        ("takeoff",),
        ("move", "forward", 50),
        ("rotate", "cw", 90),
        ("land",),
    ]


def test_mock_frame_factory():
    red = np.zeros((480, 640, 3), np.uint8)
    red[:] = (0, 0, 255)
    d = MockDrone(frame_factory=lambda: red)
    assert d.get_frame()[0, 0, 2] == 255


@pytest.fixture
def fake_tello(monkeypatch):
    """Patch djitellopy out and hand back (calls, TelloDrone factory)."""
    import comp1.drone.tello as t

    calls = []

    class FakeTello:
        def connect(self):
            calls.append("connect")

        def streamon(self):
            calls.append("streamon")

        def takeoff(self):
            calls.append("takeoff")

        def move_forward(self, cm):
            calls.append(f"move_forward {cm}")

        def move_back(self, cm):
            calls.append(f"move_back {cm}")

        def move_right(self, cm):
            calls.append(f"move_right {cm}")

        def flip(self, code):
            calls.append(f"flip {code}")

        def rotate_clockwise(self, deg):
            calls.append(f"cw {deg}")

        def get_battery(self):
            return 87

        def get_frame_read(self):
            raise RuntimeError("not in test")

    monkeypatch.setattr(t, "Tello", FakeTello)
    return calls, t.TelloDrone


def test_tello_adapter_maps_commands(fake_tello):
    calls, TelloDrone = fake_tello
    d = TelloDrone()
    d.connect()
    d.takeoff()
    d.move("forward", 40)
    d.rotate("cw", 90)
    assert calls == ["connect", "streamon", "takeoff", "move_forward 40", "cw 90"]
    assert d.battery() == 87


def test_flip_flies_back_to_where_it_started(fake_tello):
    """A Tello translates through a flip and stays displaced, so a victim signal
    would leave the drone short of the victim it just found."""
    calls, TelloDrone = fake_tello
    TelloDrone().flip("back")
    assert calls == ["flip b", "move_forward 30"]

    calls.clear()
    TelloDrone(FlightConfig(flip_recover_cm=45)).flip("left")
    assert calls == ["flip l", "move_right 45"]


def test_flip_recovery_is_skipped_below_the_tello_move_floor(fake_tello):
    """The aircraft refuses translations under 20 cm — skip rather than error."""
    calls, TelloDrone = fake_tello
    TelloDrone(FlightConfig(flip_recover_cm=0)).flip("back")
    TelloDrone(FlightConfig(flip_recover_cm=15)).flip("back")
    assert calls == ["flip b", "flip b"]
