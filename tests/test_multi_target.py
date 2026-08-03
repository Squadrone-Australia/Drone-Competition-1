import cv2
import numpy as np

from comp1.sim.drone import SimDrone
from comp1.sim.render import draw_minimap, render
from comp1.sim.world import Marker, World, VICTIM
from comp1.vision.detector import TargetTracker, detect_red_circle, find_targets

RED = (0, 0, 220)


def frame(*circles):
    """circles: (cx_px, radius_px) pairs."""
    img = np.full((480, 640, 3), 255, np.uint8)
    for cx, r in circles:
        cv2.circle(img, (cx, 240), r, RED, -1)
    return img


def test_all_candidates_are_reported_not_just_the_largest():
    targets = find_targets(frame((200, 50), (450, 40)))
    assert len(targets) == 2


def test_candidates_are_ordered_nearest_first():
    targets = find_targets(frame((200, 30), (450, 60)))
    assert targets[0].distance_m < targets[1].distance_m
    assert targets[0].cx > 0.5          # the big one is the right-hand circle


def test_bearing_signs_follow_screen_position():
    left, right = sorted(find_targets(frame((150, 40), (500, 40))), key=lambda t: t.cx)
    assert left.bearing_deg < 0 < right.bearing_deg


def test_detection_count_is_exposed():
    assert detect_red_circle(frame((200, 50), (450, 40))).count == 2
    assert detect_red_circle(frame()).count == 0


def test_lock_survives_the_other_target_becoming_larger():
    # this is the oscillation regression: without a lock the primary target
    # flips to whichever blob happens to be biggest this frame
    tracker = TargetTracker()
    first = tracker.update(frame((200, 50), (450, 40)))
    assert first.target.cx < 0.5                     # locked on the left circle

    second = tracker.update(frame((200, 38), (450, 52)))
    assert second.target.cx < 0.5                    # still the left circle
    assert second.count == 2
    # and it is genuinely no longer the nearest candidate
    assert second.target is not second.targets[0]


def test_reacquire_nearest_replaces_an_old_farther_lock():
    tracker = TargetTracker()
    tracker.update(frame((200, 50), (450, 40)))       # initial lock: left
    changed = tracker.update(frame((200, 38), (450, 52)))
    assert changed.target.cx < 0.5
    assert changed.targets[0].cx > 0.5                # right is now closest

    reacquired = tracker.reacquire_nearest(changed)
    assert reacquired.target is reacquired.targets[0]
    assert reacquired.target.cx > 0.5

    # The following frame keeps the newly selected target locked instead of
    # immediately jumping back to the old bearing.
    following = tracker.update(frame((200, 38), (450, 52)))
    assert following.target.cx > 0.5


def test_lock_reacquires_after_the_target_stays_missing():
    tracker = TargetTracker()
    tracker.update(frame((200, 50)))
    for _ in range(tracker._cfg.lock_lost_frames):
        assert not tracker.update(frame()).found
    reacquired = tracker.update(frame((450, 40)))
    assert reacquired.found and reacquired.target.cx > 0.5


def test_tracker_handles_a_dropped_frame():
    tracker = TargetTracker()
    assert not tracker.update(None).found


def test_minimap_is_not_in_the_frame_the_detector_sees():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    drone = SimDrone(world=world, delay=0)
    sensor = drone.get_frame()
    assert np.array_equal(sensor, render(world, drone.x, drone.y, drone.z, drone.heading))
    display = drone.annotate(sensor)
    assert not np.array_equal(sensor, display)       # the overlay went on a copy
    assert np.array_equal(sensor, drone.get_frame())  # and did not mutate the sensor path


def test_minimap_dots_would_otherwise_be_detectable_clutter():
    world = World(size_m=4.0, markers=[Marker(2.0, 4.0, VICTIM)])
    drone = SimDrone(world=world, delay=0)
    sensor = drone.get_frame()
    mapped = draw_minimap(sensor, world, drone.x, drone.y, drone.heading)
    # the dots are drawn in exactly the victim's red, so keeping them out of the
    # sensor path is what makes the area gate safe to re-tune
    x0 = mapped.shape[1] - 150
    assert (mapped[10:150, x0:] != sensor[10:150, x0:]).any()
