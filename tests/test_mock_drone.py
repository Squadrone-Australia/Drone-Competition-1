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
    """A Tello translates through a flip and stays displaced, so a fire signal
    would leave the drone short of the fire it just found."""
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


# --- surviving a Tello that goes away -------------------------------------
#
# A rebooted aircraft is a new SDK session: nothing announces it, the old
# object answers nothing, and its video decoder keeps the UDP port. These
# tests hold the three halves of the cure — notice, release, reconnect.


class FakeContainer:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


class FakeReader:
    def __init__(self):
        self.container = FakeContainer()
        self.stopped = False
        self.frame = np.zeros((480, 640, 3), np.uint8)

    def stop(self):
        self.stopped = True


@pytest.fixture
def reconnectable_tello(monkeypatch):
    """Patch in a Tello that records the calls a teardown must and must not make."""
    import comp1.drone.tello as t

    instances = []
    # djitellopy keys its response queue by aircraft IP, so every Tello object
    # pointed at the same drone reads the same one
    queue = {"responses": [], "state": {}}

    class FakeTello:
        def __init__(self):
            self.log = []
            self.is_flying = False
            self.stream_on = False
            self.background_frame_read = None
            self.address = ("192.168.10.1", 8889)
            self.reader = FakeReader()
            self.queue = queue
            self.queued_at_connect = None
            instances.append(self)

        def connect(self):
            self.queued_at_connect = len(self.queue["responses"])
            self.log.append("connect")

        def streamon(self):
            self.log.append("streamon")
            self.stream_on = True

        def takeoff(self):
            self.log.append("takeoff")
            self.is_flying = True

        def land(self):
            self.log.append("land")

        def get_frame_read(self):
            return self.reader

        def send_command_without_return(self, command):
            self.log.append(command)

        def get_own_udp_object(self):
            return self.queue

    monkeypatch.setattr(t, "Tello", FakeTello)
    return instances, t.TelloDrone


def test_closing_releases_the_video_port(reconnectable_tello):
    """stop() alone only sets a flag the decode thread checks after the *next*
    frame — which never arrives from a drone that is gone, so the port would be
    held for the life of the process and no later stream could open."""
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    reader = instances[-1].reader

    drone.close()
    assert reader.stopped and reader.container.closed
    assert "streamoff" in instances[-1].log


def test_closing_never_flies_the_aircraft(reconnectable_tello):
    """Letting go of an object must not be a flight command."""
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    drone.takeoff()

    drone.close()
    assert "land" not in instances[-1].log
    # ...and djitellopy's own __del__ must not do it later either
    assert instances[-1].is_flying is False


def test_reconnect_starts_a_whole_new_session(reconnectable_tello):
    """The old session died with the reboot; its queued responses are answers
    to commands from before it."""
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    first = instances[-1]

    drone.reconnect()
    assert instances[-1] is not first
    assert instances[-1].log[:2] == ["connect", "streamon"]
    assert drone.link_ok
    # the retired object can no longer delete the live one's entry in
    # djitellopy's global `drones` dict when it is garbage collected
    assert first.address[0] != "192.168.10.1"


def test_a_silent_camera_is_a_lost_link(reconnectable_tello):
    """A dead stream is silence, not an error: the decoder hands back the last
    frame it managed to decode, forever."""
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    assert drone.get_frame() is not None  # the first frame is genuinely new
    assert drone.get_frame() is None  # the same frame again, past the timeout
    assert drone.link_ok is False


def test_a_freshly_decoded_frame_is_not_a_lost_link(reconnectable_tello):
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    for _ in range(3):
        instances[-1].reader.frame = np.zeros((480, 640, 3), np.uint8)
        assert drone.get_frame() is not None
    assert drone.link_ok


def test_a_command_failure_marks_the_link_down(reconnectable_tello):
    """The interpreter turns this into a finished-with-error mission; the flag
    is what lets the server reconnect once it has stopped."""
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone()
    drone.connect()

    def boom():
        raise RuntimeError("no response after 7 seconds")

    instances[-1].takeoff = boom
    with pytest.raises(RuntimeError):
        drone.takeoff()
    assert drone.link_ok is False


def test_a_reconnect_starts_with_an_empty_response_queue(
    reconnectable_tello, monkeypatch
):
    """djitellopy pairs replies with commands *positionally* — a command takes
    whatever datagram is at the head of the queue. One stray "ok" left over from
    a dead session therefore offsets every reply from then on, and a command
    reads the answer to the one before it: which is how the aircraft's "error
    Not joystick" ends up reported against an innocent command."""
    import comp1.drone.tello as tello_module

    monkeypatch.setattr(tello_module, "_STALE_RESPONSE_SETTLE_S", 0)
    instances, TelloDrone = reconnectable_tello
    drone = TelloDrone()
    drone.connect()

    # a late answer from the session we are retiring, or from before a reboot
    instances[-1].queue["responses"].append(b"ok")
    drone.reconnect()

    assert instances[-1].queued_at_connect == 0
