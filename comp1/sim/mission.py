"""Scoring a simulated mission against the arena's ground truth.

``mark found`` is a bare counter in both pathways — the interpreter and
:mod:`comp1.api` increment it wherever the student calls it, because the drone
itself has no way to know whether it was right. The simulator does know, so the
server scores it here: a signal only counts when the drone was actually beside
an un-credited victim, and the mission succeeds when every victim has been
credited *and* the drone has landed at the destination sign.

This is built from :meth:`DroneAdapter.scene` and fed :meth:`DroneAdapter.pose`
— the same one-way display feeds the browser's 3D view runs on. It is a
*scoring* feed and stays one-way too: nothing here may become a block or a
sensor (requirements §4). The drone finds the destination the way it finds a
victim, by looking at it.
"""

import math

CREDIT_RADIUS_M = 1.5  # how close a "found" signal has to be to count
ARRIVAL_RADIUS_M = 1.5  # how close to the destination counts as arrived


class MissionScorer:
    """Ground truth for one attempt. Rebuilt on every reset, like the tracker."""

    def __init__(self, scene: dict):
        markers = scene.get("markers") or []
        self.victims = [(m["x"], m["y"]) for m in markers if m["kind"] == "victim"]
        dest = next((m for m in markers if m["kind"] == "destination"), None)
        self.destination = (dest["x"], dest["y"]) if dest else None
        self.credited: set[int] = set()

    @property
    def total(self) -> int:
        return len(self.victims)

    @property
    def found(self) -> int:
        return len(self.credited)

    def signal(self, pose) -> bool:
        """Credit the nearest un-credited victim to a ``mark found``.

        Returns whether anything was credited — a flip in an empty corridor
        scores nothing, and the student is told so rather than left to wonder.
        """
        if pose is None:
            return False
        best, best_d = None, CREDIT_RADIUS_M
        for i, (vx, vy) in enumerate(self.victims):
            if i in self.credited:
                continue
            d = math.hypot(vx - pose["x"], vy - pose["y"])
            if d <= best_d:
                best, best_d = i, d
        if best is None:
            return False
        self.credited.add(best)
        return True

    def at_destination(self, pose) -> bool:
        if pose is None or self.destination is None:
            return False
        return (
            math.hypot(self.destination[0] - pose["x"], self.destination[1] - pose["y"])
            <= ARRIVAL_RADIUS_M
        )

    def state(self, pose) -> dict:
        """The mission panel's whole view of the world.

        A scenery with no destination sign (the square arena) succeeds on the
        victims alone — otherwise the original search mission could never be
        won once scoring existed.
        """
        arrived = self.at_destination(pose)
        landed = bool(pose) and not pose["flying"]
        all_found = self.found == self.total
        if self.destination is None:
            # Nothing to fly to, so the victims are the whole mission — and an
            # arena with none of either is not a mission anyone can win.
            success = all_found and self.total > 0
        else:
            # A corridor cleared of victims is a teacher setting up a pure
            # A-to-B lesson, and getting there is the whole of it.
            success = all_found and arrived and landed
        return {
            "found": self.found,
            "total": self.total,
            "at_destination": arrived,
            "needs_destination": self.destination is not None,
            "state": "success" if success else "flying",
        }
