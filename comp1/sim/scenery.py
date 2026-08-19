"""The catalogue of arenas the simulator can fly in.

A scenery is just a recipe for a :class:`~comp1.sim.world.World`. Two exist:

``arena``
    The original 4 m square room, markers on the four walls, drone starting in
    the middle.

``corridor``
    A long hall. The drone starts at one end, a fixed **destination sign** sits
    at the other, and the targets are sprinkled free-standing down the middle —
    a point A to point B navigation exercise (requirements §3.4) on top of the
    search task. The destination is an ordinary red circle, so the detector
    reports it exactly like a target; working out which is which is the job.

**These are absolute arena coordinates.** Like :meth:`DroneAdapter.scene`, they
reach the browser so the arena can be drawn and edited, and they must never
become reachable from ``comp1.interpreter`` or ``comp1.api`` — requirements §4
forbids any "fly to a fixed coordinate" capability.
"""

import random

from .world import (
    DEFAULT_OBSTACLE_DIAMETER_M,
    DESTINATION,
    DISTRACTOR_KINDS,
    FIRE,
    OBSTACLE_KINDS,
    Marker,
    World,
)

CORRIDOR_W_M = 2.5
CORRIDOR_L_M = 10.0
CORRIDOR_FIRES = 3
CORRIDOR_DISTRACTORS = 2
CORRIDOR_OBSTACLES = 2
ARENA_OBSTACLES = 2

# Placement rules for free-standing markers. A target on a wall is a different
# exercise (you can sweep the perimeter); these stand in the open, so they need
# real clearance to stay individually detectable.
WALL_CLEARANCE_M = 0.5  # never flush against a wall
MIN_FIRE_SEP_M = 1.2  # two closer than this read as one blob to the detector
PAD_CLEARANCE_M = 1.2  # keep the start pad and its approach clear
# Obstacles are solid, so they need more room around them than a marker does: an
# obstacle placed one marker-width from a target makes that target unreachable,
# which is an unfair arena rather than a hard one.
OBSTACLE_SEP_M = 1.6

_PLACE_ATTEMPTS = 2000


def catalog() -> list[dict]:
    """What the frontend's scenery picker offers."""
    return [
        {
            "id": "arena",
            "name": "Square arena",
            "description": "4 m room, markers around the walls.",
        },
        {
            "id": "corridor",
            "name": "Corridor",
            "description": f"{CORRIDOR_W_M:g} x {CORRIDOR_L_M:g} m hall — fly from the "
            "start pad to the destination sign, finding targets on the way.",
        },
    ]


def names() -> list[str]:
    return [s["id"] for s in catalog()]


def build(name: str = "arena", seed=None) -> World:
    """A fresh world for ``name``. ``seed`` makes the layout repeatable."""
    if name == "corridor":
        return _corridor(seed)
    if name == "arena":
        return _arena(seed)
    raise ValueError(f"unknown scenery: {name}")


def _arena(seed=None) -> World:
    """The square room, plus a couple of solid obstacles standing in the open.

    Built on top of ``World.random`` rather than inside it: the wall-mounted
    layout is what every existing caller of ``World.random`` expects, and the
    obstacles are a property of *this scenery*, not of a random world.
    """
    world = World.random(seed=seed)
    rng = random.Random(seed)
    markers = list(world.markers)
    w, d, start = world.width_m, world.depth_m, world.start_xy
    for _ in range(ARENA_OBSTACLES):
        spot = _find_spot(rng, w, d, markers, start, sep_m=OBSTACLE_SEP_M)
        if spot is not None:
            markers.append(_obstacle(rng, spot))
    return World(
        size_m=world.size_m,
        markers=markers,
        length_m=world.length_m,
        start=world.start,
        name=world.name,
    )


def _obstacle(rng, spot) -> Marker:
    return Marker(
        spot[0], spot[1], rng.choice(OBSTACLE_KINDS), size_m=DEFAULT_OBSTACLE_DIAMETER_M
    )


def _corridor(seed=None) -> World:
    rng = random.Random(seed)
    w, length = CORRIDOR_W_M, CORRIDOR_L_M
    start = (w / 2, 0.6)
    # Fixed, and deliberately so: the destination is the one thing in the room a
    # student can count on being in the same place every run.
    markers = [Marker(w / 2, length - 0.6, DESTINATION)]
    kinds = [FIRE] * CORRIDOR_FIRES + [
        rng.choice(DISTRACTOR_KINDS) for _ in range(CORRIDOR_DISTRACTORS)
    ]
    rng.shuffle(kinds)
    for kind in kinds:
        spot = _find_spot(rng, w, length, markers, start)
        if spot is not None:
            markers.append(Marker(spot[0], spot[1], kind))
    # Obstacles last, so they fit around the targets rather than crowding them
    # out — a run that cannot reach a target is a broken arena, not a hard one.
    for _ in range(CORRIDOR_OBSTACLES):
        spot = _find_spot(rng, w, length, markers, start, sep_m=OBSTACLE_SEP_M)
        if spot is not None:
            markers.append(_obstacle(rng, spot))
    return World(
        size_m=w, markers=markers, length_m=length, start=start, name="corridor"
    )


def _find_spot(rng, w, d, markers, start_xy, sep_m=MIN_FIRE_SEP_M):
    """A legal free-standing position, or ``None`` if the room is too crowded."""
    for _ in range(_PLACE_ATTEMPTS):
        x = rng.uniform(WALL_CLEARANCE_M, w - WALL_CLEARANCE_M)
        y = rng.uniform(WALL_CLEARANCE_M, d - WALL_CLEARANCE_M)
        if is_free(x, y, w, d, markers, start_xy, sep_m=sep_m):
            return x, y
    return None


def is_free(x, y, w, d, markers, start_xy, sep_m=MIN_FIRE_SEP_M) -> bool:
    """Is ``(x, y)`` a legal spot for a free-standing marker in a ``w`` x ``d`` room?

    ``sep_m`` is how far it has to sit from everything already placed. It
    defaults to the marker separation; obstacles pass the wider
    ``OBSTACLE_SEP_M`` because a solid thing next to a target blocks it.
    """
    if not (WALL_CLEARANCE_M <= x <= w - WALL_CLEARANCE_M):
        return False
    if not (WALL_CLEARANCE_M <= y <= d - WALL_CLEARANCE_M):
        return False
    if (x - start_xy[0]) ** 2 + (y - start_xy[1]) ** 2 < PAD_CLEARANCE_M**2:
        return False
    # An obstacle already placed keeps its own wider bubble, whoever is asking.
    return all(
        (m.x - x) ** 2 + (m.y - y) ** 2
        >= (max(sep_m, OBSTACLE_SEP_M) if m.is_obstacle else sep_m) ** 2
        for m in markers
    )


def with_fires(world: World, points) -> World:
    """``world`` with its targets replaced by ``points``.

    Everything else — the destination, the distractors, the start pad — is kept
    exactly where it was, so editing the targets never shuffles the rest of the
    room under the user. Points that break the placement rules are dropped;
    the caller finds out by reading the world it gets back.
    """
    kept = [m for m in world.markers if m.kind != FIRE]
    w, d, start = world.width_m, world.depth_m, world.start_xy
    for p in points:
        x, y = (p["x"], p["y"]) if isinstance(p, dict) else (p[0], p[1])
        if is_free(float(x), float(y), w, d, kept, start):
            kept.append(Marker(float(x), float(y), FIRE))
    return World(
        size_m=world.size_m,
        markers=kept,
        length_m=world.length_m,
        start=world.start,
        name=world.name,
    )
