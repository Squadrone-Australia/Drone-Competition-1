"""Search-and-rescue: spin until a victim appears, fly to it, mark it.

Run it:  python -m comp1 --script examples/02_search_and_mark.py --drone sim

This is the whole competition in miniature. Try changing SEARCH_STEP_DEG or
adding a `drone.forward(...)` to the search loop so the drone patrols instead
of spinning on the spot.
"""

from comp1.api import Drone

SEARCH_STEP_DEG = 20      # how far to turn between looks
MAX_LOOKS = 30            # give up rather than spin forever

drone = Drone()
drone.takeoff()

# --- search -------------------------------------------------------------
looks = 0
while not drone.sees_target() and looks < MAX_LOOKS:
    drone.turn_right(SEARCH_STEP_DEG)
    looks += 1

if not drone.sees_target():
    print("no victims found — landing")
    drone.land()
    raise SystemExit           # ends the mission cleanly

# --- approach and mark --------------------------------------------------
print("found one:", drone.target())

if drone.approach_target():    # True once it is holding at a safe distance
    print(f"in position, {drone.distance_cm():.0f} cm away")
    drone.mark_found()         # the victory signal the judges look for
    print("victims marked:", drone.found_count)
else:
    print("lost sight of the victim on the way in")

drone.land()
