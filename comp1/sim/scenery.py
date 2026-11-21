"""The catalogue of arenas the simulator can fly in.

A scenery is just a recipe for a :class:`~comp1.sim.world.World`. Two exist:

``corridor`` (the default)
    The fixed ICONIP 2026 Search and Find competition layout: a 6 m by 4 m
    flight area with three red-circle fires and four specified decoys.

``arena``
    The original 4 m square room, markers on the four walls, drone starting in
    the middle.

**These are absolute arena coordinates.** Like :meth:`DroneAdapter.scene`, they
reach the browser so the arena can be drawn and edited, and they must never
become reachable from ``comp1.interpreter`` or ``comp1.api`` — requirements §4
forbids any "fly to a fixed coordinate" capability.
"""

from .world import FIRE, Marker, World

CORRIDOR_W_M = 6.0
CORRIDOR_L_M = 4.0
CORRIDOR_HEIGHT_M = 3.0
CORRIDOR_MARKER_HEIGHT_M = 2.0
CORRIDOR_START = (3.0, 3.0)
CORRIDOR_FIRES = 3
CORRIDOR_DISTRACTORS = 4

# Fixed marker centres from the competition specification. Coordinates use x
# along the 6 m length and y along the 4 m width.
CORRIDOR_MARKERS = (
    (0.75, 3.25, FIRE),
    (5.25, 3.25, FIRE),
    (3.00, 0.50, FIRE),
    (0.75, 1.50, "green_triangle"),
    (5.25, 1.50, "black_square"),
    (1.75, 0.50, "green_circle"),
    (4.25, 0.50, "black_triangle"),
)

# Placement rules for free-standing markers. A target on a wall is a different
# exercise (you can sweep the perimeter); these stand in the open, so they need
# real clearance to stay individually detectable.
WALL_CLEARANCE_M = 0.5  # never flush against a wall
MIN_FIRE_SEP_M = 1.2  # two closer than this read as one blob to the detector
PAD_CLEARANCE_M = 1.2  # keep the start pad and its approach clear
def catalog() -> list[dict]:
    """What the frontend's scenery picker offers."""
    # The competition arena first: it is the default, and the picker lists
    # these in this order.
    return [
        {
            "id": "corridor",
            "name": "Competition arena",
            "description": f"Fixed {CORRIDOR_W_M:g} x {CORRIDOR_L_M:g} x "
            f"{CORRIDOR_HEIGHT_M:g} m Search and Find layout.",
        },
        {
            "id": "arena",
            "name": "Square arena",
            "description": "4 m room, markers around the walls.",
        },
    ]


def names() -> list[str]:
    return [s["id"] for s in catalog()]


def build(name: str = "corridor", seed=None) -> World:
    """A fresh world for ``name``. ``seed`` makes the layout repeatable."""
    if name == "corridor":
        return _corridor(seed)
    if name == "arena":
        return _arena(seed)
    raise ValueError(f"unknown scenery: {name}")


def _arena(seed=None) -> World:
    """The square room, markers on its four walls."""
    return World.random(seed=seed)


def _corridor(seed=None) -> World:
    # ``seed`` is intentionally ignored. The official layout is fixed so every
    # attempt presents the same classification task.
    markers = [
        Marker(x, y, kind, height_m=CORRIDOR_MARKER_HEIGHT_M)
        for x, y, kind in CORRIDOR_MARKERS
    ]
    return World(
        size_m=CORRIDOR_W_M,
        markers=markers,
        length_m=CORRIDOR_L_M,
        start=CORRIDOR_START,
        name="corridor",
        room_height_m=CORRIDOR_HEIGHT_M,
        return_to_start=True,
    )


def is_free(x, y, w, d, markers, start_xy) -> bool:
    """Is ``(x, y)`` a legal spot for a free-standing marker in a ``w`` x ``d`` room?"""
    if not (WALL_CLEARANCE_M <= x <= w - WALL_CLEARANCE_M):
        return False
    if not (WALL_CLEARANCE_M <= y <= d - WALL_CLEARANCE_M):
        return False
    if (x - start_xy[0]) ** 2 + (y - start_xy[1]) ** 2 < PAD_CLEARANCE_M**2:
        return False
    return all(
        (m.x - x) ** 2 + (m.y - y) ** 2 >= MIN_FIRE_SEP_M**2 for m in markers
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
            height = (
                CORRIDOR_MARKER_HEIGHT_M
                if world.name == "corridor"
                else Marker(float(x), float(y), FIRE).height_m
            )
            kept.append(Marker(float(x), float(y), FIRE, height_m=height))
    return World(
        size_m=world.size_m,
        markers=kept,
        length_m=world.length_m,
        start=world.start,
        name=world.name,
        room_height_m=world.room_height_m,
        return_to_start=world.return_to_start,
    )
