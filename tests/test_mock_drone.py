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


class FakeLink:
    """Stands in for :class:`comp1.drone.tello_link.TelloLink`.

    Records every datagram the adapter would put on the wire, in order, so a
    test reads exactly what the aircraft would have been told.
    """

    def __init__(self, log=None):
        self.log = [] if log is None else log
        self.closed = False
        self.replies = {"battery?": "87"}

    def handshake(self):
        self.log.append("command")

    def send(self, command):
        self.log.append(command)
        return self.replies.get(command, "ok")

    def interrupt(self, command):
        self.log.append(command)
        return "ok"

    def send_no_reply(self, command):
        self.log.append(command)

    def close(self):
        self.closed = True


@pytest.fixture
def fake_tello(monkeypatch):
    """Patch the command link out and hand back (calls, TelloDrone factory).

    Commands sent before ``connect`` need a link too, so flight tests that
    never connect get one installed directly.
    """
    import comp1.drone.tello as t

    calls = []
    monkeypatch.setattr(t, "TelloLink", lambda: FakeLink(calls))
    monkeypatch.setattr(t.FrameReader, "open", classmethod(_no_video))

    def factory(*args, **kwargs):
        drone = t.TelloDrone(*args, **kwargs)
        drone._link = FakeLink(calls)
        return drone

    return calls, factory


def _no_video(cls, *args, **kwargs):
    raise RuntimeError("not in test")


def test_tello_adapter_maps_commands(fake_tello):
    calls, TelloDrone = fake_tello
    d = TelloDrone()
    d.connect()
    d.takeoff()
    d.move("forward", 40)
    d.rotate("cw", 90)
    d.rotate("ccw", 45)
    assert calls == [
        "command",
        "streamon",
        "takeoff",
        "forward 40",
        "cw 90",
        "ccw 45",
    ]
    assert d.battery() == 87
    assert calls[-1] == "battery?"  # asked for, never read from telemetry


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
    assert calls == ["flip l", "right 45"]


def test_flip_recovery_is_skipped_below_the_tello_move_floor(fake_tello):
    """The aircraft refuses translations under 20 cm — skip rather than error."""
    calls, TelloDrone = fake_tello
    TelloDrone(FlightConfig(flip_recover_cm=0)).flip("back")
    TelloDrone(FlightConfig(flip_recover_cm=15)).flip("back")
    assert calls == ["flip b", "flip b"]


def test_emergency_does_not_wait_its_turn(fake_tello):
    """An emergency stop issued during a long move cannot queue behind it."""
    calls, TelloDrone = fake_tello
    d = TelloDrone()
    d.emergency()
    assert calls == ["emergency"]


def test_a_refusal_is_not_a_lost_link(fake_tello):
    """The aircraft answered, so the link is up; reconnecting would only throw
    away a working session."""
    from comp1.drone.tello_link import TelloError

    calls, TelloDrone = fake_tello
    d = TelloDrone()
    d.link_ok = True

    def refuse(command):
        raise TelloError("the drone refused 'flip b'")

    d._link.send = refuse
    with pytest.raises(TelloError):
        d.flip("back")
    assert d.link_ok is True


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
    stream = FakeStream()
    monkeypatch.setattr(t, "_open_container", stream.open)

    def new_link():
        link = FakeLink()
        instances.append(link)
        return link

    monkeypatch.setattr(t, "TelloLink", new_link)
    return instances, t.TelloDrone, stream


def test_closing_releases_the_video_port(reconnectable_tello):
    """A flag alone would not do it: a decode thread that only reads one after
    the *next* frame arrives never reads it once the drone is gone, so the port
    would be held for the life of the process."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    reader = drone._reader
    stream.latest().release.set()  # let the stalled read give up

    assert drone.close() is None
    assert not reader.alive
    assert all(c.closed for c in stream.containers)
    assert instances[-1].closed


def test_neither_close_nor_reconnect_ever_turns_the_video_off(reconnectable_tello):
    """After 'streamoff', a Tello answers the next 'streamon' with 'ok' and then
    sends no video until it is rebooted (field test, 2026-10-12). So a
    reconnect that turned the stream off would lose the camera for good, and
    that would be reported as a firewall problem."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    drone.reconnect()
    drone.close()
    for link in instances:
        assert "streamoff" not in link.log
    assert instances[-1].log[:2] == ["command", "streamon"]


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
    assert instances[-1].closed


def test_reconnect_starts_a_whole_new_session(reconnectable_tello):
    """The old session died with the reboot. A fresh link is a fresh socket on
    a fresh port, so anything the old session still had to say lands where
    nobody is listening instead of being read as the answer to a new command."""
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    first = instances[-1]

    drone.reconnect()
    assert instances[-1] is not first
    assert first.closed
    assert instances[-1].log[:2] == ["command", "streamon"]
    assert drone.link_ok


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

    def boom(command):
        raise RuntimeError("no response after 7 seconds")

    instances[-1].send = boom
    with pytest.raises(RuntimeError):
        drone.takeoff()
    assert drone.link_ok is False


def test_a_camera_that_never_starts_is_blocked_video_not_a_lost_link(
    reconnectable_tello,
):
    """The aircraft answered the handshake but not one frame has arrived: that
    is a firewall dropping :11111, which no reconnect can cure. Calling the
    link dead would have the watchdog reconnect forever."""
    instances, TelloDrone, stream = reconnectable_tello
    stream._frames = 0  # the stream opens and never delivers a picture
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    assert drone.get_frame() is None
    assert drone.video_blocked is True
    assert drone.link_ok is True
    for container in stream.containers:
        container.release.set()  # let the parked read give up, so close is quick
    drone.close()


def test_video_blocked_clears_once_a_frame_arrives(reconnectable_tello):
    instances, TelloDrone, stream = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=60))
    drone.connect()
    drone.video_blocked = True  # as if reported earlier this session

    _wait_for_frame(drone)
    assert drone.video_blocked is False
    drone.close()
