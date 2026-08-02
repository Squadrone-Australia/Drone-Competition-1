import threading
import time

import pytest
from comp1.sim.drone import SimDrone
from comp1.sim.world import Marker, World, VICTIM


def drone():
    return SimDrone(world=World(size_m=4.0, markers=[]), delay=0)


def sample_while(d, run, interval=0.005):
    """Poll the pose from a second thread while ``run()`` flies the drone.

    This is how the video loop and the browser's 3D view see the drone: mid
    command, from another thread. Animation that only existed inside the command
    call would be invisible to both.
    """
    seen = []
    stop = threading.Event()

    def poll():
        while not stop.is_set():
            seen.append((d.x, d.y, d.z, d.heading, d.roll, d.pitch))
            time.sleep(interval)

    t = threading.Thread(target=poll)
    t.start()
    try:
        run()
    finally:
        stop.set()
        t.join()
    return seen


def test_starts_landed_at_centre():
    d = drone()
    assert (d.x, d.y, d.z, d.flying) == (2.0, 2.0, 0.0, False)


def test_move_before_takeoff_raises():
    with pytest.raises(RuntimeError):
        drone().move("forward", 50)


def test_pose_math_rotate_then_move():
    d = drone(); d.takeoff()
    d.rotate("cw", 90)            # now facing +x
    d.move("forward", 100)
    assert round(d.x, 2) == 3.0 and round(d.y, 2) == 2.0
    d.rotate("ccw", 90)           # back to +y
    d.move("left", 50)
    assert round(d.x, 2) == 2.5


def test_clamped_at_walls():
    d = drone(); d.takeoff()
    d.move("forward", 500)
    assert d.y == 3.8             # 0.2 m margin


def test_takeoff_land_altitude_and_frame_shape():
    d = drone(); d.takeoff()
    assert d.z == 1.0 and d.flying
    assert d.get_frame().shape == (480, 640, 3)
    d.land()
    assert d.z == 0.0 and not d.flying


# --- animation ------------------------------------------------------------

def animated():
    return SimDrone(world=World(size_m=4.0, markers=[]), delay=0.1)


def test_a_move_is_flown_not_teleported():
    d = animated(); d.takeoff()
    seen = sample_while(d, lambda: d.move("forward", 100))
    ys = sorted({round(s[1], 2) for s in seen})
    assert len(ys) > 3, f"only saw {ys} — the drone jumped"
    assert min(ys) >= 2.0 and max(ys) <= 3.0          # never overshoots the target


def test_a_turn_is_flown_not_teleported():
    d = animated(); d.takeoff()
    seen = sample_while(d, lambda: d.rotate("cw", 90))
    assert len({round(s[3]) for s in seen}) > 3


def test_animation_still_lands_on_the_exact_commanded_pose():
    """Control-logic tests read drone.x straight after the call, so the end of an
    animation must be the arithmetic result, not wherever the last frame fell."""
    d = animated(); d.takeoff()
    d.rotate("cw", 90)
    d.move("forward", 100)
    assert (round(d.x, 6), round(d.y, 6), round(d.heading, 6)) == (3.0, 2.0, 90.0)


def test_translation_tips_the_airframe_and_levels_out_again():
    d = animated(); d.takeoff()
    seen = sample_while(d, lambda: d.move("forward", 100))
    assert max(s[5] for s in seen) > 1.0              # pitched into the move
    assert d.pitch == 0.0 and d.roll == 0.0           # level once it stops


def test_flip_spins_the_airframe_without_moving_it():
    d = animated(); d.takeoff()
    before = (d.x, d.y, d.z, d.heading)
    seen = sample_while(d, lambda: d.flip("b"))
    assert max(s[4] for s in seen) > 180              # went right round
    assert (d.x, d.y, d.z, d.heading) == before
    assert d.roll == 0.0


def test_emergency_cuts_an_animation_short():
    d = animated(); d.takeoff()
    stopper = threading.Timer(0.05, d.emergency)
    stopper.start()
    d.move("forward", 300)                            # ~1.8 s if left to finish
    stopper.join()
    assert not d.flying and d.z == 0.0
    assert d.y < 3.7, "kept flying to the wall after an emergency stop"


def test_zero_delay_keeps_tests_instant():
    d = drone(); d.takeoff()
    t0 = time.perf_counter()
    d.move("forward", 100); d.rotate("cw", 180); d.flip("b"); d.land()
    assert time.perf_counter() - t0 < 0.1


# --- display feeds --------------------------------------------------------

def test_pose_reports_what_the_third_person_view_needs():
    d = drone(); d.takeoff(); d.rotate("cw", 45)
    p = d.pose()
    assert set(p) == {"x", "y", "z", "heading", "roll", "pitch", "flying"}
    assert p["flying"] and p["heading"] == 45.0 and p["z"] == 1.0


def test_scene_describes_the_arena():
    world = World(size_m=5.0, markers=[Marker(1.0, 5.0, VICTIM)])
    s = SimDrone(world=world, delay=0).scene()
    assert s["size_m"] == 5.0 and s["wall_height_m"] > 0
    assert s["markers"] == [{"x": 1.0, "y": 5.0, "kind": VICTIM,
                             "size_m": 0.25, "height_m": 1.0}]


# --- reset ----------------------------------------------------------------

def test_reset_returns_the_drone_to_the_start_pad():
    d = drone(); d.takeoff(); d.rotate("cw", 120); d.move("forward", 90)
    d.reset()
    assert (d.x, d.y, d.z, d.heading, d.flying) == (2.0, 2.0, 0.0, 0.0, False)
    assert d.roll == 0.0 and d.pitch == 0.0


def test_reset_keeps_the_arena_so_students_iterate_on_one_problem():
    d = SimDrone(seed=5, delay=0)
    before = [(m.x, m.y, m.kind) for m in d.world.markers]
    d.takeoff(); d.reset()
    assert [(m.x, m.y, m.kind) for m in d.world.markers] == before


def test_reset_re_seeds_noise_so_a_seeded_run_repeats():
    def fly():
        d = SimDrone(world=World(size_m=4.0, markers=[]), noise=0.1, delay=0, seed=11)
        d.takeoff(); d.move("forward", 100); d.rotate("cw", 90)
        return (d.x, d.y, d.heading), d

    (first, d) = fly()
    d.reset()
    d.takeoff(); d.move("forward", 100); d.rotate("cw", 90)
    assert (d.x, d.y, d.heading) == first


def test_reset_recovers_from_an_emergency_stop():
    d = animated(); d.takeoff(); d.emergency()
    d.reset()
    d.takeoff()
    assert d.flying and d.z == 1.0      # _abort cleared, so the climb ran


def test_adapters_without_an_arena_expose_no_pose():
    """Absolute position is a simulator-only concept — see requirements §4."""
    from comp1.drone.mock import MockDrone
    assert MockDrone().pose() is None and MockDrone().scene() is None
