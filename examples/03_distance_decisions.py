"""Decide what to do from the numbers the camera gives you.

Run it:  python -m comp1 --script examples/03_distance_decisions.py --drone sim

`approach_target()` does all of this for you. Here we do it by hand, so you can
see what it is made of — and so you can build something smarter.

Two readings drive everything:
  drone.bearing_deg()   which way the victim is:  + right, - left, 0 straight ahead
  drone.distance_cm()   how far away it is
"""

from comp1.api import Drone

STOP_CM = 120  # how close we are willing to get
DEADBAND_DEG = 8  # closer than this to straight-ahead counts as "lined up"

drone = Drone()
drone.takeoff()

# find something to look at
while not drone.sees_target():
    drone.turn_right(20)

for step in range(30):
    if not drone.sees_target():
        print("lost it — turning back")
        drone.turn_left(20)
        continue

    bearing = drone.bearing_deg()
    distance = drone.distance_cm()
    print(f"{distance:6.0f} cm   {bearing:+6.1f} deg")

    if abs(bearing) > DEADBAND_DEG:
        # not lined up yet: turn towards it, by however far off we are.
        # min 10 deg because the drone ignores turns smaller than that.
        turn = max(10, min(45, round(abs(bearing))))
        if bearing > 0:
            drone.turn_right(turn)
        else:
            drone.turn_left(turn)

    elif distance > STOP_CM + 20:
        # lined up and still too far: close half the remaining gap
        drone.forward(min(100, max(20, round((distance - STOP_CM) / 2))))

    else:
        print(f"close enough at {distance:.0f} cm")
        break

# different signals for different situations
if drone.sees_target() and drone.distance_cm() < 200:
    drone.mark_found()
else:
    print("never got close enough to be sure")

drone.land()
