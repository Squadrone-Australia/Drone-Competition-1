import socket
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
# link answers nothing, and the video reader keeps the UDP port. These tests
# hold the three halves of the cure — notice, release, reconnect. The reader
# itself is faked here; its own tests (real sockets) are further down.


class FakeReader:
    """Stands in for FrameReader: the test decides what has been decoded."""

    def __init__(self):
        self._frame = None
        self.closed = False

    @property
    def frame(self):
        return self._frame

    @property
    def alive(self):
        return not self.closed

    def close(self):
        self.closed = True
        return True

    def show(self, seq):
        """Publish a genuinely new picture, the way the worker thread does."""
        self._frame = np.full((480, 640, 3), seq, np.uint8)


def _wait_for_frame(drone, timeout=2.0):
    """Block until the reader has delivered a picture."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = drone.get_frame()
        if frame is not None:
            return frame
        time.sleep(0.01)
    raise AssertionError("no frame arrived")


@pytest.fixture
def reconnectable_tello(monkeypatch):
    """Patch in a link and a reader that record what a teardown must and must
    not do. Returns (links, TelloDrone, readers)."""
    import comp1.drone.tello as t

    links, readers = [], []

    def new_link():
        link = FakeLink()
        links.append(link)
        return link

    def open_reader(cls, **kwargs):
        reader = FakeReader()
        readers.append(reader)
        return reader

    monkeypatch.setattr(t, "TelloLink", new_link)
    monkeypatch.setattr(t.FrameReader, "open", classmethod(open_reader))
    return links, t.TelloDrone, readers


def test_closing_releases_the_video_port_and_the_link(reconnectable_tello):
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()

    assert drone.close() is None
    assert readers[-1].closed
    assert links[-1].closed


def test_neither_close_nor_reconnect_ever_turns_the_video_off(reconnectable_tello):
    """After 'streamoff', a Tello answers the next 'streamon' with 'ok' and then
    sends no video until it is rebooted (field test, 2026-10-12). So a
    reconnect that turned the stream off would lose the camera for good, and
    that would be reported as a firewall problem."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    drone.reconnect()
    drone.close()
    for link in links:
        assert "streamoff" not in link.log
    assert links[-1].log[:2] == ["command", "streamon"]


def test_a_reader_that_stopped_is_replaced(reconnectable_tello, monkeypatch):
    import comp1.drone.tello as t

    monkeypatch.setattr(t, "_REOPEN_INTERVAL_S", 0)
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    first = readers[-1]
    first.closed = True  # its worker died

    drone.get_frame()
    drone.get_frame()
    assert len(readers) == 2
    drone.close()


def test_closing_never_flies_the_aircraft(reconnectable_tello):
    """Letting go of an object must not be a flight command."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    drone.takeoff()

    drone.close()
    assert "land" not in links[-1].log
    assert links[-1].closed


def test_reconnect_starts_a_whole_new_session(reconnectable_tello):
    """The old session died with the reboot; the new one starts from the
    handshake, on a link of its own."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()
    first = links[-1]

    drone.reconnect()
    assert links[-1] is not first
    assert first.closed
    assert links[-1].log[:2] == ["command", "streamon"]
    assert drone.link_ok


def test_a_silent_camera_is_a_lost_link(reconnectable_tello):
    """A dead stream is silence, not an error: the reader hands back the last
    frame it managed to decode, forever."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()
    readers[-1].show(0)

    assert drone.get_frame() is not None  # the first frame is genuinely new
    assert drone.get_frame() is None  # the same frame again, past the timeout
    assert drone.link_ok is False
    drone.close()


def test_a_freshly_decoded_frame_is_not_a_lost_link(reconnectable_tello):
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    for seq in range(3):
        readers[-1].show(seq)
        assert drone.get_frame() is not None
    assert drone.link_ok
    drone.close()


def test_a_command_failure_marks_the_link_down(reconnectable_tello):
    """The interpreter turns this into a finished-with-error mission; the flag
    is what lets the server reconnect once it has stopped."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone()
    drone.connect()

    def boom(command):
        raise RuntimeError("no response after 7 seconds")

    links[-1].send = boom
    with pytest.raises(RuntimeError):
        drone.takeoff()
    assert drone.link_ok is False


def test_a_camera_that_never_starts_is_blocked_video_not_a_lost_link(
    reconnectable_tello,
):
    """The aircraft answered the handshake but not one frame has arrived: that
    is a firewall the punch did not get through, or a drone some other program
    told 'streamoff'. No reconnect cures either, so calling the link dead would
    have the watchdog reconnect forever."""
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=0))
    drone.connect()

    assert drone.get_frame() is None
    assert drone.video_blocked is True
    assert drone.link_ok is True
    drone.close()


def test_video_blocked_clears_once_a_frame_arrives(reconnectable_tello):
    links, TelloDrone, readers = reconnectable_tello
    drone = TelloDrone(FlightConfig(link_timeout_s=60))
    drone.connect()
    drone.video_blocked = True  # as if reported earlier this session

    readers[-1].show(1)
    _wait_for_frame(drone)
    assert drone.video_blocked is False
    drone.close()


# --- the video reader, over real sockets ------------------------------------
#
# A fake aircraft on loopback: one socket standing in for the Tello's video
# sender (the port the reader punches to) that streams datagrams at the
# reader's port.


class Aircraft:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(2.0)
        self.port = self.sock.getsockname()[1]

    def stream(self, reader, data, chunk=1460):
        for i in range(0, len(data), chunk):
            self.sock.sendto(data[i:i + chunk], ("127.0.0.1", reader.port))

    def close(self):
        self.sock.close()


@pytest.fixture
def aircraft():
    fake = Aircraft()
    yield fake
    fake.close()


def _reader(aircraft, **kwargs):
    from comp1.drone.tello import FrameReader

    return FrameReader.open(
        port=0, host="127.0.0.1", source_ports=[aircraft.port], **kwargs
    )


def _h264(frames=10, size=(240, 320)):
    """A real H.264 stream, the way the Tello sends it: Annex B, no container."""
    av = pytest.importorskip("av")
    if "libx264" not in av.codecs_available:
        pytest.skip("this PyAV build has no H.264 encoder")
    enc = av.CodecContext.create("libx264", "w")
    enc.height, enc.width = size
    enc.pix_fmt = "yuv420p"
    enc.framerate = 30
    enc.options = {"tune": "zerolatency"}
    data = b""
    for i in range(frames):
        image = np.full((*size, 3), i * 20, np.uint8)
        frame = av.VideoFrame.from_ndarray(image, format="rgb24")
        for packet in enc.encode(frame):
            data += bytes(packet)
    for packet in enc.encode(None):
        data += bytes(packet)
    return data


def test_the_reader_punches_toward_the_aircraft_before_any_video(aircraft):
    """The hole-punch: a datagram *from the video socket* to the port the
    aircraft streams from, so a firewall files the stream as a reply. Field
    test (Linux, ufw on): without it nothing arrived, with it everything did."""
    reader = _reader(aircraft)
    try:
        data, source = aircraft.sock.recvfrom(64)
        assert source[1] == reader.port  # from the very socket video lands on
    finally:
        reader.close()


def test_the_reader_decodes_a_stream_cut_into_datagrams(aircraft):
    reader = _reader(aircraft)
    try:
        aircraft.stream(reader, _h264())
        deadline = time.monotonic() + 3.0
        while reader.frame is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert reader.frame is not None
        assert reader.frame.shape == (240, 320, 3)
    finally:
        reader.close()


def test_garbage_on_the_wire_does_not_stop_the_reader(aircraft):
    """A lost datagram leaves the decoder something it cannot use; the reader
    starts a clean decoder and picks the stream up at the next keyframe."""
    reader = _reader(aircraft)
    try:
        aircraft.stream(reader, b"\x00\x00\x00\x01\x65" + b"\xff" * 4000)
        time.sleep(0.2)
        assert reader.alive
        aircraft.stream(reader, _h264())
        deadline = time.monotonic() + 3.0
        while reader.frame is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert reader.frame is not None
    finally:
        reader.close()


def test_closing_gives_the_port_back_promptly(aircraft):
    """Without this a reconnect — or a round trip through the simulator —
    finds the video port still held, and the camera never comes back."""
    reader = _reader(aircraft)
    port = reader.port
    started = time.monotonic()
    assert reader.close() is True
    assert time.monotonic() - started < 2.0
    assert not reader.alive
    again = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        again.bind(("", port))  # would fail if the reader still held it
    finally:
        again.close()


def test_a_held_video_port_is_an_error_the_caller_sees(aircraft):
    """Raised from the constructor, not lost in the worker, so the adapter can
    arm its retry."""
    from comp1.drone.tello import FrameReader

    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("", 0))
    try:
        with pytest.raises(OSError):
            FrameReader(port=holder.getsockname()[1], host="127.0.0.1")
    finally:
        holder.close()


def test_only_the_worker_touches_the_decoder(aircraft):
    """The rule that made the old reader's teardown crash-free, kept: nothing
    outside the worker thread uses what the worker reads with."""
    used_by = set()

    class SpyDecoder:
        def parse(self, data):
            used_by.add(threading.current_thread())
            return []

    reader = _reader(aircraft, decoder_factory=SpyDecoder)
    try:
        aircraft.stream(reader, b"\x00" * 3000)
        time.sleep(0.2)
    finally:
        reader.close()
    assert used_by and threading.current_thread() not in used_by
