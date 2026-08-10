"""The catalogue of arenas the simulator can fly in.

A scenery is just a recipe for a :class:`~comp1.sim.world.World`. Two exist:

``arena``
    The original 4 m square room, markers on the four walls, drone starting in
    the middle.

``corridor``
    A long hall. The drone starts at one end, a fixed **destination sign** sits
    at the other, and the victims are sprinkled free-standing down the middle —
    a point A to point B navigation exercise (requirements §3.4) on top of the
    search task. The destination is an ordinary red circle, so the detector
    reports it exactly like a victim; working out which is which is the job.

**These are absolute arena coordinates.** Like :meth:`DroneAdapter.scene`, they
reach the browser so the arena can be drawn and edited, and they must never
become reachable from ``comp1.interpreter`` or ``comp1.api`` — requirements §4
forbids any "fly to a fixed coordinate" capability.
"""

import random

from .world import DESTINATION, DISTRACTOR_KINDS, VICTIM, Marker, World

CORRIDOR_W_M = 2.5
CORRIDOR_L_M = 10.0
CORRIDOR_VICTIMS = 3
CORRIDOR_DISTRACTORS = 2

# Placement rules for free-standing markers. A victim on a wall is a different
# exercise (you can sweep the perimeter); these stand in the open, so they need
# real clearance to stay individually detectable.
WALL_CLEARANCE_M = 0.5  # never flush against a wall
MIN_VICTIM_SEP_M = 1.2  # two closer than this read as one blob to the detector
PAD_CLEARANCE_M = 1.2  # keep the start pad and its approach clear

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
            "start pad to the destination sign, finding victims on the way.",
        },
    ]


def names() -> list[str]:
    return [s["id"] for s in catalog()]


def build(name: str = "arena", seed=None) -> World:
    """A fresh world for ``name``. ``seed`` makes the layout repeatable."""
    if name == "corridor":
        return _corridor(seed)
    if name == "arena":
        return World.random(seed=seed)
    raise ValueError(f"unknown scenery: {name}")


def _corridor(seed=None) -> World:
    rng = random.Random(seed)
    w, length = CORRIDOR_W_M, CORRIDOR_L_M
    start = (w / 2, 0.6)
    # Fixed, and deliberately so: the destination is the one thing in the room a
    # student can count on being in the same place every run.
    markers = [Marker(w / 2, length - 0.6, DESTINATION)]
    kinds = [VICTIM] * CORRIDOR_VICTIMS + [
        rng.choice(DISTRACTOR_KINDS) for _ in range(CORRIDOR_DISTRACTORS)
    ]
    rng.shuffle(kinds)
    for kind in kinds:
        spot = _find_spot(rng, w, length, markers, start)
        if spot is not None:
            markers.append(Marker(spot[0], spot[1], kind))
    return World(
        size_m=w, markers=markers, length_m=length, start=start, name="corridor"
    )


def _find_spot(rng, w, d, markers, start_xy):
    """A legal free-standing position, or ``None`` if the room is too crowded."""
    for _ in range(_PLACE_ATTEMPTS):
        x = rng.uniform(WALL_CLEARANCE_M, w - WALL_CLEARANCE_M)
        y = rng.uniform(WALL_CLEARANCE_M, d - WALL_CLEARANCE_M)
        if is_free(x, y, w, d, markers, start_xy):
            return x, y
    return None


def is_free(x, y, w, d, markers, start_xy) -> bool:
    """Is ``(x, y)`` a legal spot for a free-standing marker in a ``w`` x ``d`` room?"""
    if not (WALL_CLEARANCE_M <= x <= w - WALL_CLEARANCE_M):
        return False
    if not (WALL_CLEARANCE_M <= y <= d - WALL_CLEARANCE_M):
        return False
    if (x - start_xy[0]) ** 2 + (y - start_xy[1]) ** 2 < PAD_CLEARANCE_M**2:
        return False
    return all((m.x - x) ** 2 + (m.y - y) ** 2 >= MIN_VICTIM_SEP_M**2 for m in markers)


def with_victims(world: World, points) -> World:
    """``world`` with its victims replaced by ``points``.

    Everything else — the destination, the distractors, the start pad — is kept
    exactly where it was, so editing the victims never shuffles the rest of the
    room under the user. Points that break the placement rules are dropped;
    the caller finds out by reading the world it gets back.
    """
    kept = [m for m in world.markers if m.kind != VICTIM]
    w, d, start = world.width_m, world.depth_m, world.start_xy
    for p in points:
        x, y = (p["x"], p["y"]) if isinstance(p, dict) else (p[0], p[1])
        if is_free(float(x), float(y), w, d, kept, start):
            kept.append(Marker(float(x), float(y), VICTIM))
    return World(
        size_m=world.size_m,
        markers=kept,
        length_m=world.length_m,
        start=world.start,
        name=world.name,
    )
