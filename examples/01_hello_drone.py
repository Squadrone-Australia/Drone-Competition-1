"""Your first flight: take off, have a look around, land.

Run it:  python -m comp1 --script examples/01_hello_drone.py --drone sim

The video feed and the big red EMERGENCY STOP button stay live the whole time.
Press STOP and this program stops too, wherever it has got to.
"""

from comp1.api import Drone

drone = Drone()

print("battery:", drone.battery, "%")

drone.takeoff()
print("height:", drone.height, "cm")

# turn all the way around in 8 steps of 45 degrees, reporting what we can see
for step in range(8):
    drone.turn_right(45)
    if drone.sees_target():
        print(f"step {step}: I can see a target! {drone.target()}")
    else:
        print(f"step {step}: nothing here")

drone.land()
print("done")
