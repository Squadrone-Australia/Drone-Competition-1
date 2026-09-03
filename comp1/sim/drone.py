import math
import random
import time

from ..drone.base import DroneAdapter
from . import scenery
from .render import MARKER_HEIGHT, WALL_HEIGHT_M, draw_minimap, render

ANIM_FPS = 60  # pose updates per second while a command is in flight
MAX_ALT_M = 2.5
MIN_ALT_M = 0.3
LEAN_DEG = 12.0  # how far the airframe tips into a translation
# How close the drone's centre may come to an obstacle before it has hit it.
# Roughly the Tello's half-span plus its prop guards.
DRONE_CLEARANCE_M = 0.2
FLIP_DRIFT_M = 0.3  # how far a flip throws the airframe before it recovers

# Which attitude angle a flip drives, and which way round.
#
# The signs are fixed by how scene3d.js orients the model: it has its nose at
# local -Z and its right arm at +X, and applies pitch as rotation.x and roll as
# rotation.z. So +pitch lifts the nose (a *back* flip) and +roll lifts the right
# wingtip (a *left* flip). Change either of those and change these with them.
_FLIP_SPIN = {
    "back": ("pitch", 1),
    "forward": ("pitch", -1),
    "left": ("roll", 1),
    "right": ("roll", -1),
}
# djitellopy's single-letter codes reach the simulator from tests and scripts
_FLIP_ALIAS = {"b": "back", "f": "forward", "l": "left", "r": "right"}


def _flip_key(direction: str) -> str:
    return _FLIP_ALIAS.get(direction, direction)


def _smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)


class SimDrone(DroneAdapter):
    """A drone that flies rather than teleports.

    Commands used to be ``sleep(delay)`` followed by a jump to the new pose, which
    made the camera view cut between two stills and gave the 3D view nothing to
    animate. Each command now interpolates its pose over the same wall-clock
    budget, so anything reading the pose from another thread — the video loop, the
    browser's third-person view — sees continuous motion.

    The pose fields stay public and land on exactly the value the old teleporting
    version produced, so control-logic tests can keep reading ``drone.x``
    straight after a call.
    """

    mode = "sim"
    can_reset = True  # unlike hardware, this one really can go home

    def __init__(
        self, world=None, noise=0.0, delay=0.3, seed=None, scenery_name="arena"
    ):
        self.scenery_name = world.name if world is not None else scenery_name
        self.world = (
            world if world is not None else scenery.build(scenery_name, seed=seed)
        )
        self.noise = noise
        self.delay = delay
        self._seed = seed
        self.reset()

    def reset(self):
        """Back to the start pad and landed.

        The arena itself is left alone: students iterate against the same layout,
        and re-rolling the markers under them would make each Run a different
        problem. Re-seeding the RNG means a ``--seed`` run repeats exactly, drift
        and all.
        """
        self._rng = random.Random(self._seed)
        self.x, self.y = self.world.start_xy
        self.z = 0.0
        self.heading = 0.0
        self.flying = False
        # visual-only attitude, in degrees: never read by the interpreter or the
        # detector, only by the browser's third-person view
        self.roll = 0.0
        self.pitch = 0.0
        # Set the moment the drone touches an obstacle, and never cleared except
        # by a reset: a mission that hit something did hit something, and flying
        # on afterwards does not undo it. Sim truth only -- MissionScorer reads
        # it off the pose, and no block or sensor may ever expose it (§4).
        self.crashed = False
        self._abort = False

    # --- animation ---------------------------------------------------------

    def _animate(self, apply, duration):
        """Drive ``apply(t)`` from t=0 to t=1 over ``duration`` seconds.

        Always finishes with ``apply(1.0)`` so the end pose is exact rather than
        the accumulation of however many frames happened to fit — unless
        :meth:`emergency` aborted, in which case the drone stops where it is.
        """
        if duration <= 0:
            apply(1.0)
            return
        steps = max(1, round(duration * ANIM_FPS))
        for i in range(1, steps + 1):
            time.sleep(duration / steps)
            if self._abort:
                return
            apply(_smoothstep(i / steps))
        apply(1.0)

    def _require_flying(self):
        if not self.flying:
            raise RuntimeError("not flying — use the take off block first")

    # --- commands ----------------------------------------------------------

    def connect(self):
        pass

    def takeoff(self):
        self._abort = False
        z0 = self.z
        self.flying = True  # props spin for the whole climb
        self._animate(
            lambda t: setattr(self, "z", z0 + (MARKER_HEIGHT - z0) * t),
            self.delay * 1.5,
        )

    def land(self):
        z0 = self.z
        self._animate(lambda t: setattr(self, "z", z0 * (1 - t)), self.delay * 1.5)
        self.z = 0.0
        self.flying = False
        self.roll = self.pitch = 0.0

    def emergency(self):
        self._abort = True  # cut any command mid-animation
        self.flying = False
        self.z = 0.0
        self.roll = self.pitch = 0.0

    def _body_vector(self, direction, dist):
        """World-frame ``(dx, dy)`` metres for a body-relative direction.

        ``up``/``down`` — and anything unrecognised — are (0, 0): they do not
        move the drone across the floor.
        """
        h = math.radians(self.heading)
        fx, fy = math.sin(h), math.cos(h)  # forward
        rx, ry = math.cos(h), -math.sin(h)  # right
        return {
            "forward": (fx * dist, fy * dist),
            "back": (-fx * dist, -fy * dist),
            "right": (rx * dist, ry * dist),
            "left": (-rx * dist, -ry * dist),
        }.get(direction, (0.0, 0.0))

    def move(self, direction, cm):
        self._require_flying()
        dist = cm / 100.0
        if self.noise:
            dist += self._rng.gauss(0, self.noise * dist)
        x0, y0, z0 = self.x, self.y, self.z
        dx, dy = self._body_vector(direction, dist)
        dz = 0.0
        lean_pitch = lean_roll = 0.0
        if direction == "forward":
            lean_pitch = LEAN_DEG
        elif direction == "back":
            lean_pitch = -LEAN_DEG
        elif direction == "right":
            lean_roll = LEAN_DEG
        elif direction == "left":
            lean_roll = -LEAN_DEG
        elif direction == "up":
            dz = min(MAX_ALT_M, z0 + dist) - z0
        elif direction == "down":
            dz = max(MIN_ALT_M, z0 - dist) - z0
        margin = 0.2
        x_lo, x_hi = margin, self.world.width_m - margin
        y_lo, y_hi = margin, self.world.depth_m - margin

        def at(t):
            return (
                min(max(x0 + dx * t, x_lo), x_hi),
                min(max(y0 + dy * t, y_lo), y_hi),
                z0 + dz * t,
            )

        # How far along this move the drone gets before it hits something --
        # resolved up front, over the whole swept path, not per animation frame.
        # Testing only the frames would tunnel straight through an obstacle
        # whenever the frames are far apart, and with delay=0 there is exactly
        # one frame: the end point. That is every test in the suite.
        limit_t = self._first_contact(at, math.hypot(dx, dy) + abs(dz))
        if limit_t is not None:
            # Stop dead rather than sliding around it. A drone that scrapes past
            # teaches that ignoring an obstacle mostly works, which is the
            # opposite of the lesson.
            self.crashed = True

        def step(t):
            if limit_t is not None:
                t = min(t, limit_t)
            self.x, self.y, self.z = at(t)
            # tip into the move and level out again — a quadcopter that translates
            # perfectly flat looks like a lift, not a drone
            tilt = math.sin(math.pi * t)
            self.pitch, self.roll = lean_pitch * tilt, lean_roll * tilt

        self._animate(step, self._move_duration(abs(dist)))
        self.pitch = self.roll = 0.0

    def _first_contact(self, at, length_m, resolution_m=0.02):
        """The furthest ``t`` along ``at(t)`` that is still clear, or None.

        None means the whole move is clear. Sampling rather than solving: the
        obstacle test is a cheap couple of comparisons, and a sampled sweep
        handles the vertical extent and the room-box clamp without either
        needing a closed form.
        """
        if not self.world.obstacles:
            return None  # the common case, and it costs nothing
        steps = max(1, math.ceil(length_m / resolution_m))
        for i in range(1, steps + 1):
            t = i / steps
            if self._hits_obstacle(*at(t)):
                return (i - 1) / steps  # the last sample that was still clear
        return None

    def _hits_obstacle(self, x, y, z) -> bool:
        """Would the drone be inside an obstacle at this position?

        Each obstacle is a short cylinder centred on its sign, not a column from
        floor to ceiling: climbing over one is a legitimate way past it, and
        modelling it as a column would quietly remove that option.
        """
        for m in self.world.obstacles:
            reach = m.radius_m + DRONE_CLEARANCE_M
            if abs(z - m.height_m) >= reach:
                continue
            if (m.x - x) ** 2 + (m.y - y) ** 2 < reach**2:
                return True
        return False

    def rotate(self, direction, deg):
        self._require_flying()
        if self.noise:
            deg += self._rng.gauss(0, 2)
        h0 = self.heading
        turn = deg if direction == "cw" else -deg
        self._animate(
            lambda t: setattr(self, "heading", (h0 + turn * t) % 360),
            self._turn_duration(abs(deg)),
        )
        self.heading = (h0 + turn) % 360

    def flip(self, direction):
        self._require_flying()
        # Two things move and neither changes where the drone ends up.
        #
        # The airframe tumbles a full turn about the axis the real manoeuvre
        # uses: nose-over-tail (pitch) for back/forward, wingtip-over-wingtip
        # (roll) for left/right. Every direction used to drive roll, which drew
        # a back-flip as a barrel roll and made three of the four identical.
        #
        # It also sags in the flip's own direction and comes back. A real Tello
        # translates through a flip; TelloDrone undoes that with a compensating
        # move afterwards (see FlightConfig.flip_recover_cm), and showing the sag
        # here is what makes the two views agree about a lurch the aircraft
        # genuinely performs. sin(pi*t) peaks mid-flip and returns to zero, so
        # the end pose is exactly the start pose — the flip is the *signal*
        # (requirements §2.1), never a way to travel.
        key = _flip_key(direction)
        axis, spin = _FLIP_SPIN[key]
        dx, dy = self._body_vector(key, FLIP_DRIFT_M)
        x0, y0 = self.x, self.y
        margin = 0.2
        x_lo, x_hi = margin, self.world.width_m - margin
        y_lo, y_hi = margin, self.world.depth_m - margin

        def step(t):
            setattr(self, axis, (spin * 360 * t) % 360)
            sag = math.sin(math.pi * t)
            self.x = min(max(x0 + dx * sag, x_lo), x_hi)
            self.y = min(max(y0 + dy * sag, y_lo), y_hi)

        self._animate(step, max(self.delay, 0.6) if self.delay else 0.0)
        self.x, self.y = x0, y0
        self.roll = self.pitch = 0.0

    def _move_duration(self, dist_m):
        if not self.delay:
            return 0.0
        return self.delay * min(max(dist_m / 0.5, 0.6), 3.0)

    def _turn_duration(self, deg):
        if not self.delay:
            return 0.0
        return self.delay * min(max(deg / 90.0, 0.5), 2.0)

    # --- sensing -----------------------------------------------------------

    def get_frame(self):
        return render(self.world, self.x, self.y, self.z, self.heading)

    def annotate(self, frame):
        return draw_minimap(frame, self.world, self.x, self.y, self.heading)

    def battery(self):
        return 100

    # --- display feeds -----------------------------------------------------
    # Absolute arena coordinates, for drawing the third-person view and nothing
    # else. They reach the browser's renderer over the WebSocket; they are NOT
    # reachable from the block layer or comp1.api, and must not become so —
    # requirements §4 forbids any "fly to fixed coordinate" capability.

    def pose(self):
        return {
            "x": round(self.x, 3),
            "y": round(self.y, 3),
            "z": round(self.z, 3),
            "heading": round(self.heading, 2),
            "roll": round(self.roll, 2),
            "pitch": round(self.pitch, 2),
            "flying": self.flying,
            "crashed": self.crashed,
        }

    def scene(self):
        # size_m is kept as an alias for width_m: it is what every square-arena
        # caller has always read, and dropping it would break them for nothing.
        return {
            "name": self.world.name,
            "size_m": self.world.width_m,
            "width_m": self.world.width_m,
            "depth_m": self.world.depth_m,
            "start": list(self.world.start_xy),
            "wall_height_m": self.world.room_height_m,
            "return_to_start": self.world.return_to_start,
            "markers": [
                {
                    "x": m.x,
                    "y": m.y,
                    "kind": m.kind,
                    "size_m": m.size_m,
                    "height_m": m.height_m,
                }
                for m in self.world.markers
            ],
        }

    # --- authoring feed ----------------------------------------------------
    # The mirror of the display feeds above: the browser's arena panel writes
    # marker coordinates back through here so a teacher can lay out a problem.
    # Same rule applies — this is reachable only over the WebSocket, and neither
    # comp1.interpreter nor comp1.api may ever call it (requirements §4).

    def scenery_catalog(self):
        return scenery.catalog()

    def load_scenery(self, name=None, fires=None, randomise=False):
        """Swap or edit the arena, then go back to the start pad.

        ``fires`` replaces just the targets and leaves the rest of the
        room where it was. Otherwise ``name`` picks a scenery (``None`` keeps
        the current one), built from the launch ``--seed`` so a seeded session
        stays repeatable — unless ``randomise``, which is the panel's dice
        button asking for a genuinely new layout.
        """
        if fires is not None:
            self.world = scenery.with_fires(self.world, fires)
        else:
            self.scenery_name = name or self.scenery_name
            self.world = scenery.build(
                self.scenery_name, seed=None if randomise else self._seed
            )
        self.reset()
        return self.world
