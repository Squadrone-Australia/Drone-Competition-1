"""examples/04_pilot.py, everything but the window.

The OpenCV window needs a display, which CI has not got, so these drive the
parts behind it: the key map, the recorder, and the flying thread against the
real simulator. The example is loaded from its file, as a student runs it.
"""

import importlib.util
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from comp1.api import Drone
from comp1.sim.drone import SimDrone

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "04_pilot.py"


@pytest.fixture(scope="module")
def pilot():
    spec = importlib.util.spec_from_file_location("pilot_example", EXAMPLE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # not __main__, so no window opens
    return module


def wait_idle(p, timeout=10.0):
    deadline = time.monotonic() + timeout
    while p.busy.is_set():
        assert time.monotonic() < deadline, "the flying thread never finished"
        time.sleep(0.01)


@pytest.fixture
def flying(pilot):
    drone = Drone(adapter=SimDrone(seed=1, delay=0))
    p = pilot.Pilot(drone)
    p._flyer.start()
    yield p, drone
    p.stop.set()
    p._flyer.join(timeout=5)


def test_keys_decode_letters_arrows_and_nothing(pilot):
    assert pilot.key_name(-1) is None
    assert pilot.key_name(ord("W")) == "w"  # caps lock on still flies
    assert pilot.key_name(27) == pilot.ESC
    assert pilot.key_name(2490368) == "r"  # Windows Up
    assert pilot.key_name(65361) == "q"  # Linux Left
    assert pilot.key_name(10_000_000) is None


def test_every_move_key_names_a_real_drone_method(pilot):
    for method, unit in pilot.MOVES.values():
        assert callable(getattr(Drone, method))
        assert unit in ("cm", "deg")
    for cm, deg in pilot.STEPS:
        assert cm >= 20 and deg >= 10  # never a step the drone would refuse


def test_recorder_paces_the_video_to_the_clock(pilot, tmp_path):
    rec = pilot.Recorder(tmp_path)
    path = rec.start()
    frame = np.zeros((48, 64, 3), np.uint8)
    rec.write(frame, 0.0)
    rec.write(frame, 0.0)  # the same frame twice adds nothing
    rec.write(frame, 1.0)  # a one-second stall is one second of video
    assert rec.stop() == path
    video = cv2.VideoCapture(str(path))
    count = int(video.get(cv2.CAP_PROP_FRAME_COUNT))
    video.release()
    assert count == int(pilot.RECORD_FPS) + 1


def test_recorder_with_no_frames_saves_nothing(pilot, tmp_path):
    rec = pilot.Recorder(tmp_path)
    rec.start()
    assert rec.stop() is None


def test_takeoff_needs_two_presses(flying):
    p, _ = flying
    p._key("t")
    assert not p.busy.is_set() and not p.airborne
    p._key("t")
    wait_idle(p)
    assert p.airborne


def test_moves_fly_the_sim_and_wait_for_takeoff(pilot, flying):
    p, drone = flying
    p._key("w")
    assert not p.busy.is_set()  # refused on the ground, with a message
    assert "Take off" in p.status
    p._key("t")
    p._key("t")
    wait_idle(p)
    sim = drone._d
    start = (sim.x, sim.y)
    p._key("w")
    wait_idle(p)
    cm, _ = pilot.STEPS[p.step]
    moved_m = ((sim.x - start[0]) ** 2 + (sim.y - start[1]) ** 2) ** 0.5
    assert moved_m == pytest.approx(cm / 100, abs=0.05)
    assert p.status.endswith("done")


def test_a_held_key_does_not_queue_up_moves(flying):
    """Autorepeat sends far more presses than the drone can fly; the extras
    are dropped, so letting go stops the drone after the step in flight."""
    p, _ = flying
    p.airborne = True
    gate = threading.Event()
    calls = []
    p._submit("slow", lambda d: (calls.append(1), gate.wait(5)))
    for _ in range(10):
        p._submit("again", lambda d: calls.append(2))
    gate.set()
    wait_idle(p)
    assert calls == [1]


def test_land_waits_its_turn_instead_of_being_dropped(flying):
    p, _ = flying
    p._key("t")
    p._key("t")
    wait_idle(p)
    gate = threading.Event()
    p._submit("slow", lambda d: gate.wait(5))
    p._key("l")
    gate.set()
    wait_idle(p)
    assert not p.airborne


def test_step_size_stays_in_range(pilot, flying):
    p, _ = flying
    for _ in range(10):
        p._key("+")
    assert p.step == len(pilot.STEPS) - 1
    for _ in range(10):
        p._key("-")
    assert p.step == 0
