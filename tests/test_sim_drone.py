import pytest
from comp1.sim.drone import SimDrone
from comp1.sim.world import World


def drone():
    return SimDrone(world=World(size_m=4.0, markers=[]), delay=0)


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
