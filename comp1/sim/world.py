import random
from dataclasses import dataclass

VICTIM = "victim"
DISTRACTOR_KINDS = ["red_square", "blue_circle", "green_triangle", "yellow_square"]
DEFAULT_MARKER_DIAMETER_M = 0.25   # printed A4-ish disc; keep in step with VisionConfig
DEFAULT_MARKER_HEIGHT_M = 1.0      # tripod-mounted


@dataclass(frozen=True)
class Marker:
    x: float
    y: float
    kind: str
    size_m: float = DEFAULT_MARKER_DIAMETER_M      # diameter
    height_m: float = DEFAULT_MARKER_HEIGHT_M

    @property
    def radius_m(self) -> float:
        return self.size_m / 2


@dataclass
class World:
    size_m: float
    markers: list

    @classmethod
    def random(cls, seed=None, n_victims=3, n_distractors=4, size_m=4.0):
        rng = random.Random(seed)
        kinds = [VICTIM] * n_victims + \
                [rng.choice(DISTRACTOR_KINDS) for _ in range(n_distractors)]
        rng.shuffle(kinds)
        markers = []
        attempts = 0
        while kinds and attempts < 1000:
            attempts += 1
            wall = rng.randrange(4)
            t = rng.uniform(0.5, size_m - 0.5)
            x, y = {0: (t, size_m), 1: (size_m, t), 2: (t, 0.0), 3: (0.0, t)}[wall]
            if all((m.x - x) ** 2 + (m.y - y) ** 2 >= 0.6 ** 2 for m in markers):
                markers.append(Marker(x, y, kinds.pop()))
        return cls(size_m=size_m, markers=markers)

    @property
    def victims(self):
        return [m for m in self.markers if m.kind == VICTIM]
