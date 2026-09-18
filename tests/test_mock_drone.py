import threading
import time

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


def test_a_flip_is_just_a_flip_by_default(fake_tello):
    """The compensating move costs altitude the flip has already taken, so
    flip_recover_cm defaults to 0 and nothing follows the flip."""
    calls, TelloDrone = fake_tello
    TelloDrone().flip("back")
    assert calls == ["flip b"]


def test_flip_recovery_flies_back_when_it_is_configured(fake_tello):
    """A Tello translates through a flip and stays displaced; an operator who
    would rather correct that than keep the altitude can still ask for it."""
    calls, TelloDrone = fake_tello
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


class FakeFrame:
    def __init__(self, seq):
        self.seq = seq

    def to_ndarray(self, format=None):
        frame = np.zeros((480, 640, 3), np.uint8)
        frame[0, 0, 0] = self.seq % 256
        return frame


class FakeContainer:
    """A video stream that behaves the way PyAV's really does.

    It hands out ``frames`` pictures and then stalls — and a stall is
    terminal: once av has raised past its read timeout the container never
    yields another frame, so the reader must reopen rather than retry. Every
    close records the thread that made it, which is what the crash regression
    below actually checks.
    """

    def __init__(self, frames=1):
        self.closed = False
        self.closed_by = None
        self._frames = frames
        self.exhausted = threading.Event()
        self.release = threading.Event()

    def decode(self, video=0):
        for i in range(self._frames):
            yield FakeFrame(i)
        self.exhausted.set()
        self.release.wait(2.0)  # a read in flight, bounded like a real timeout
        raise RuntimeError("Immediate exit requested")  # what av.error.ExitError is

    def close(self):
        self.closed = True
        self.closed_by = threading.current_thread()


class FakeStream:
    """Stands in for the ``av`` layer: records every container it opens."""

    def __init__(self, frames=1):
        self.containers = []
        self._frames = frames

    def open(self, address, open_timeout=None):
        container = FakeContainer(self._frames)
        self.containers.append(container)
        return container

    def latest(self):
        return self.containers[-1]


def _wait_for_frame(drone, timeout=2.0):
    """Block until the decoder thread has delivered its first picture."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = drone.get_frame()
        if frame is not None:
            return frame
        time.sleep(0.01)
    raise AssertionError("no frame arrived")


@pytest.fixture
def reconnectable_tello(monkeypatch):
    """Patch in a Tello that records the calls a teardown must and must not make."""
    import comp1.drone.tello as t

    instances = []
    # djitellopy keys its response queue by aircraft IP, so every Tello object
    # pointed at the same drone reads the same one
    queue = {"responses": [], "state": {}}
    stream = FakeStream()
    monkeypatch.setattr(t, "_open_container", stream.open)

    class FakeTello:
        def __init__(self):
            self.log = []
            self.is_flying = False
            self.stream_on = False
            self.background_frame_read = None
            self.address = ("192.168.10.1", 8889)
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

        def get_udp_video_address(self):
            return "udp://@0.0.0.0:11111"

        def send_command_without_return(self, command):
            self.log.append(command)

        def get_own_udp_object(self):
            return self.queue

    monkeypatch.setattr(t, "Tello", FakeTello)
    return instances, t.TelloDrone, stream


def test_closing_releases_the_video_port(reconnectable_tello):
    """A flag alone would not do it: djitellopy's decode thread only reads one
    after the *next* frame arrives, which never happens once the drone is gone,
    so the port would be held for the life of the process."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    reader = drone._reader
    stream.latest().release.set()  # let the stalled read give up

    assert drone.close() is None
    assert not reader.alive
    assert all(c.closed for c in stream.containers)
    assert "streamoff" in instances[-1].log


def test_the_container_is_only_ever_closed_by_its_own_decode_thread(
    reconnectable_tello,
):
    """The crash regression. Freeing an AVFormatContext while another thread
    sits inside av_read_frame() is a use-after-free: the process dies with
    SIGSEGV in avio_read_partial and no Python traceback at all. Only the
    decoder thread may close what it reads."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    _wait_for_frame(drone)
    assert stream.latest().exhausted.wait(2.0)  # parked in a read, as on a dead link

    drone.close()  # would have closed the container from *this* thread before

    assert stream.containers, "the reader never opened a stream"
    for container in stream.containers:
        assert container.closed
        assert container.closed_by is not threading.current_thread()


def test_a_stalled_stream_is_reopened_rather_than_retried(reconnectable_tello):
    """av poisons a container once its read timeout has fired — it never yields
    another frame — so recovery means opening a new one."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    first = stream.latest()
    assert first.exhausted.wait(2.0)
    first.release.set()  # the read gives up

    deadline = time.monotonic() + 2.0
    while len(stream.containers) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert len(stream.containers) >= 2, "the reader gave up instead of reopening"
    assert first.closed
    drone.close()


def test_closing_never_flies_the_aircraft(reconnectable_tello):
    """Letting go of an object must not be a flight command."""
    instances, TelloDrone, stream = reconnectable_tello
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
    instances, TelloDrone, stream = reconnectable_tello
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
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    assert _wait_for_frame(drone) is not None  # the first frame is genuinely new
    assert drone.get_frame() is None  # the same frame again, past the timeout
    assert drone.link_ok is False
    drone.close()


def test_a_freshly_decoded_frame_is_not_a_lost_link(reconnectable_tello):
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    _wait_for_frame(drone)
    for seq in range(3):
        # a genuinely new picture, the way the decoder thread publishes one
        drone._reader._frame = np.full((480, 640, 3), seq, np.uint8)
        assert drone.get_frame() is not None
    assert drone.link_ok
    drone.close()


def test_a_command_failure_marks_the_link_down(reconnectable_tello):
    """The interpreter turns this into a finished-with-error mission; the flag
    is what lets the server reconnect once it has stopped."""
    instances, TelloDrone, stream = reconnectable_tello
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
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()

    # a late answer from the session we are retiring, or from before a reboot
    instances[-1].queue["responses"].append(b"ok")
    drone.reconnect()

    assert instances[-1].queued_at_connect == 0
