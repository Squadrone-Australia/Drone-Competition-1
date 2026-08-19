import random
from dataclasses import dataclass

FIRE = "fire"
# The destination sign is a red circle, exactly like a target, and the detector
# cannot tell the two apart — that is the point. Distinguishing them (the
# destination is the one at the end of the corridor) is the student's problem.
DESTINATION = "destination"
DISTRACTOR_KINDS = ["red_square", "blue_circle", "green_triangle", "yellow_square"]
DEFAULT_MARKER_DIAMETER_M = 0.25  # printed A4-ish disc; keep in step with VisionConfig
DEFAULT_MARKER_HEIGHT_M = 1.0  # tripod-mounted


@dataclass(frozen=True)
class Marker:
    x: float
    y: float
    kind: str
    size_m: float = DEFAULT_MARKER_DIAMETER_M  # diameter
    height_m: float = DEFAULT_MARKER_HEIGHT_M

    @property
    def radius_m(self) -> float:
        return self.size_m / 2


@dataclass
class World:
    """A rectangular room.

    ``size_m`` is the x extent and stays the first positional field: it was the
    side of the square arena, and every caller that only ever wanted a square
    still gets one. A scenery with a different y extent sets ``length_m``.
    Read ``width_m``/``depth_m`` downstream, never ``size_m``.
    """

    size_m: float
    markers: list
    length_m: float | None = None  # y extent; None -> square
    start: tuple | None = None  # (x, y) start pad; None -> centre
    name: str = "arena"

    @classmethod
    def random(cls, seed=None, n_fires=3, n_distractors=4, size_m=4.0):
        rng = random.Random(seed)
        kinds = [FIRE] * n_fires + [
            rng.choice(DISTRACTOR_KINDS) for _ in range(n_distractors)
        ]
        rng.shuffle(kinds)
        markers = []
        attempts = 0
        while kinds and attempts < 1000:
            attempts += 1
            wall = rng.randrange(4)
            t = rng.uniform(0.5, size_m - 0.5)
            x, y = {0: (t, size_m), 1: (size_m, t), 2: (t, 0.0), 3: (0.0, t)}[wall]
            if all((m.x - x) ** 2 + (m.y - y) ** 2 >= 0.6**2 for m in markers):
                markers.append(Marker(x, y, kinds.pop()))
        return cls(size_m=size_m, markers=markers)

    # --- extents -----------------------------------------------------------

    @property
    def width_m(self) -> float:
        return self.size_m

    @property
    def depth_m(self) -> float:
        return self.size_m if self.length_m is None else self.length_m

    @property
    def start_xy(self) -> tuple:
        return (
            self.start
            if self.start is not None
            else (self.width_m / 2, self.depth_m / 2)
        )

    # --- markers -----------------------------------------------------------

    @property
    def fires(self):
        return [m for m in self.markers if m.kind == FIRE]

    @property
    def destination(self):
        return next((m for m in self.markers if m.kind == DESTINATION), None)
