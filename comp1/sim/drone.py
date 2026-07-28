import math
import random
import time

from ..drone.base import DroneAdapter
from .render import MARKER_HEIGHT, render
from .world import World


class SimDrone(DroneAdapter):
    def __init__(self, world=None, noise=0.0, delay=0.3, seed=None):
        self.world = world if world is not None else World.random(seed=seed)
        self.noise = noise
        self.delay = delay
        self._rng = random.Random(seed)
        self.x = self.world.size_m / 2
        self.y = self.world.size_m / 2
        self.z = 0.0
        self.heading = 0.0
        self.flying = False

    def _wait(self):
        if self.delay:
            time.sleep(self.delay)

    def _require_flying(self):
        if not self.flying:
            raise RuntimeError("not flying — use the take off block first")

    def connect(self):
        pass

    def takeoff(self):
        self._wait()
        self.flying = True
        self.z = MARKER_HEIGHT

    def land(self):
        self._wait()
        self.flying = False
        self.z = 0.0

    def emergency(self):
        self.flying = False
        self.z = 0.0

    def move(self, direction, cm):
        self._require_flying()
        self._wait()
        dist = cm / 100.0
        if self.noise:
            dist += self._rng.gauss(0, self.noise * dist)
        h = math.radians(self.heading)
        fx, fy = math.sin(h), math.cos(h)      # forward
        rx, ry = math.cos(h), -math.sin(h)     # right
        if direction == "forward":
            self.x += fx * dist; self.y += fy * dist
        elif direction == "back":
            self.x -= fx * dist; self.y -= fy * dist
        elif direction == "right":
            self.x += rx * dist; self.y += ry * dist
        elif direction == "left":
            self.x -= rx * dist; self.y -= ry * dist
        elif direction == "up":
            self.z = min(2.5, self.z + dist)
        elif direction == "down":
            self.z = max(0.3, self.z - dist)
        margin = 0.2
        self.x = min(max(self.x, margin), self.world.size_m - margin)
        self.y = min(max(self.y, margin), self.world.size_m - margin)

    def rotate(self, direction, deg):
        self._require_flying()
        self._wait()
        if self.noise:
            deg += self._rng.gauss(0, 2)
        self.heading = (self.heading + (deg if direction == "cw" else -deg)) % 360

    def flip(self, direction):
        self._require_flying()
        self._wait()                            # signal only — no pose change

    def get_frame(self):
        return render(self.world, self.x, self.y, self.z, self.heading)

    def battery(self):
        return 100
