from dataclasses import dataclass, field

import cv2
import numpy as np

from .config import DEFAULT_CONFIG, VisionConfig
from .obstacles import find_obstacles, is_in_the_way


@dataclass(frozen=True)
class Target:
    """One candidate marker, with its geometry resolved into real units."""

    cx: float  # centroid, normalised by frame width  (0..1)
    cy: float  # centroid, normalised by frame height (0..1)
    radius_norm: float  # apparent radius / frame width
    area_ratio: float  # contour area / frame area
    circularity: float  # 4piA/P^2 of the convex hull, not the raw contour
    solidity: float  # contour area / hull area — what the hull can't tell you
    bearing_deg: float  # + = to the right of the drone's nose
    elevation_deg: float  # + = above the camera axis
    distance_m: float
    position: str  # "left" | "center" | "right"


@dataclass
class Detection:
    found: bool = False
    targets: list = field(default_factory=list)  # all candidates, nearest first
    target: Target | None = None  # the locked/primary one
    # Everything in the frame that is not an accepted marker, nearest first.
    # `found` stays a statement about targets only — an obstacle is not a find.
    obstacles: list = field(default_factory=list)

    # --- back-compatible scalar view of the primary target ---
    @property
    def cx(self) -> float:
        return self.target.cx if self.target else 0.5

    @property
    def area_ratio(self) -> float:
        return self.target.area_ratio if self.target else 0.0

    @property
    def position(self) -> str:
        return self.target.position if self.target else "none"

    @property
    def distance_m(self) -> float | None:
        return self.target.distance_m if self.target else None

    @property
    def bearing_deg(self) -> float | None:
        return self.target.bearing_deg if self.target else None

    @property
    def elevation_deg(self) -> float | None:
        return self.target.elevation_deg if self.target else None

    @property
    def count(self) -> int:
        return len(self.targets)

    # --- obstacles ---
    @property
    def obstacle(self):
        """The nearest thing in the way, or None."""
        return self.obstacles[0] if self.obstacles else None

    @property
    def obstacle_count(self) -> int:
        return len(self.obstacles)

    @classmethod
    def of(
        cls,
        target: Target | None,
        targets: list | None = None,
        obstacles: list | None = None,
    ) -> "Detection":
        if target is None:
            return cls(found=False, targets=targets or [], obstacles=obstacles or [])
        return cls(
            found=True,
            targets=targets if targets is not None else [target],
            target=target,
            obstacles=obstacles or [],
        )


def color_mask(frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> np.ndarray:
    """Return the detector's binary HSV mask.

    This is public so the calibration UI can show exactly which pixels the
    detector accepts.  Keeping preview and detection on the same function
    prevents a tuning panel from looking correct while the real detector uses
    subtly different thresholds.
    """
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, np.array(cfg.lower1), np.array(cfg.upper1)) | cv2.inRange(
        hsv, np.array(cfg.lower2), np.array(cfg.upper2)
    )
    kernel = np.ones((5, 5), np.uint8)
    # CLOSE then OPEN, in that order. Uneven lighting in the cage is what makes
    # this necessary: a shadow falling across the marker, or a highlight on its
    # rim, cuts a notch out of the mask that OPEN alone can only ever make
    # bigger. CLOSE fills the notch first; OPEN then removes the specks. A 5x5
    # kernel is deliberately small -- CLOSE dilates before it erodes, so a large
    # one would fuse two adjacent markers into a single blob that then fails the
    # shape gates, losing both.
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)


def find_targets(frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG) -> list:
    """Every red circle in the frame that clears the area and circularity gates.

    Returned nearest-first. The old detector kept only the largest contour and
    silently dropped the rest every frame, which is what made the drone flip
    between two similar markers.
    """
    h, w = frame_bgr.shape[:2]
    aspect_hw = h / w
    cam = cfg.intrinsics
    contours, _ = cv2.findContours(
        color_mask(frame_bgr, cfg), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )
    out = []
    for c in contours:
        area = cv2.contourArea(c)
        area_ratio = area / (w * h)
        if area_ratio < cfg.min_area_ratio:
            continue
        # A blob far too large to be a marker is not a marker. Apparent size is
        # the only thing range is derived from, so an unbounded one reads as
        # very close and sorts to the front of the candidate list -- which is
        # how a red jacket beyond the cage net becomes the primary target.
        if area_ratio > cfg.max_area_ratio:
            continue
        # Shape is judged on the convex hull, not the raw contour. 4piA/P^2 is
        # dominated by its perimeter term, and perimeter is what a ragged mask
        # edge inflates -- shadow notches, glare bites, JPEG ringing on a small
        # blob. The hull spans all of them, so the gate measures the marker's
        # shape rather than the mask's edge quality.
        hull = cv2.convexHull(c)
        hull_area = cv2.contourArea(hull)
        perim = cv2.arcLength(hull, True)
        if perim == 0 or hull_area == 0:
            continue
        circularity = 4 * np.pi * hull_area / (perim * perim)
        if circularity < cfg.circularity_min:
            continue
        # The hull's blind spot: it fills concavities, so a red star or cross
        # hulls into a near-circular polygon and sails through the gate above.
        # Solidity is what sees the difference -- a disc is ~1.0, a five-point
        # star ~0.5 -- and it costs one more contourArea call.
        solidity = area / hull_area
        if solidity < cfg.solidity_min:
            continue
        m = cv2.moments(c)
        if m["m00"] == 0:
            continue
        cx = m["m10"] / m["m00"] / w
        cy = m["m01"] / m["m00"] / h
        # minEnclosingCircle is a direct linear measure of apparent size; the
        # circularity gate above keeps it from being skewed by spurs
        (_, _), radius_px = cv2.minEnclosingCircle(c)
        radius_norm = radius_px / w
        if abs(cx - 0.5) < cfg.center_band / 2:
            pos = "center"
        else:
            pos = "left" if cx < 0.5 else "right"
        out.append(
            Target(
                cx=cx,
                cy=cy,
                radius_norm=radius_norm,
                area_ratio=area_ratio,
                circularity=float(circularity),
                solidity=float(solidity),
                bearing_deg=cam.bearing_deg(cx),
                elevation_deg=cam.elevation_deg(cy, aspect_hw),
                distance_m=cam.distance_m(radius_norm, cfg.marker_radius_m),
                position=pos,
            )
        )
    out.sort(key=lambda t: t.distance_m)
    return out


def detect_red_circle(
    frame_bgr: np.ndarray, cfg: VisionConfig = DEFAULT_CONFIG
) -> Detection:
    """Single-frame detection. Primary target is the nearest candidate.

    Stateless — for a run, prefer :class:`TargetTracker`, which holds a lock.
    """
    targets = find_targets(frame_bgr, cfg)
    obstacles = find_obstacles(frame_bgr, cfg, targets)
    return Detection.of(targets[0] if targets else None, targets, obstacles)


class TargetTracker:
    """Frame-to-frame target lock.

    Without this the primary target is whichever blob is largest *this frame*,
    so two similarly sized markers swap the lead as noise moves them and the
    approach controller oscillates between them. Here the lock stays on the
    candidate closest in bearing to where it last was, and only decays after the
    target has been missing for several frames.
    """

    def __init__(self, cfg: VisionConfig = DEFAULT_CONFIG):
        self._cfg = cfg
        self._locked: Target | None = None
        self._lost = 0

    def reset(self) -> None:
        self._locked, self._lost = None, 0

    def reacquire_nearest(self, detection: Detection) -> Detection:
        """Drop the old lock and immediately make the nearest candidate primary.

        Approach controllers call this once when they start. Subsequent frames
        rebuild the normal bearing lock around that candidate, so choosing the
        closest target does not reintroduce frame-to-frame oscillation.
        """
        self.reset()
        targets = detection.targets
        if not targets:
            return Detection(found=False, obstacles=detection.obstacles)
        self._locked = targets[0]
        return Detection.of(self._locked, targets, detection.obstacles)

    def update(self, frame_bgr: np.ndarray | None) -> Detection:
        if frame_bgr is None:
            return self._miss([], [])
        targets = find_targets(frame_bgr, self._cfg)
        # Detected on every frame, including the ones with no target in them:
        # searching is exactly when the drone is flying blind into things.
        obstacles = find_obstacles(frame_bgr, self._cfg, targets)
        if not targets:
            return self._miss(targets, obstacles)
        self._lost = 0
        self._locked = self._pick(targets)
        return Detection.of(self._locked, targets, obstacles)

    def _pick(self, targets: list) -> Target:
        if self._locked is None:
            return targets[0]  # nearest
        jump = self._cfg.lock_max_bearing_jump_deg
        near = min(targets, key=lambda t: abs(t.bearing_deg - self._locked.bearing_deg))
        if abs(near.bearing_deg - self._locked.bearing_deg) <= jump:
            return near
        return targets[0]  # lock broke — re-acquire nearest

    def _miss(self, targets: list, obstacles: list) -> Detection:
        self._lost += 1
        if self._lost >= self._cfg.lock_lost_frames:
            self._locked = None
        return Detection(found=False, targets=targets, obstacles=obstacles)


def draw_overlay(frame_bgr: np.ndarray, det: Detection) -> np.ndarray:
    out = frame_bgr.copy()
    if det.found:
        t = det.target
        label = f"TARGET  {t.distance_m:.2f} m  {t.bearing_deg:+.0f} deg"
        color = (0, 255, 0)
    else:
        label, color = "searching...", (0, 165, 255)
    cv2.putText(out, label, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, color, 2)
    if det.count > 1:
        cv2.putText(
            out,
            f"{det.count} candidates",
            (10, 58),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (0, 200, 255),
            2,
        )
    h, w = out.shape[:2]
    for t in det.targets:
        primary = det.target is not None and t is det.target
        c = (0, 255, 0) if primary else (120, 120, 120)
        cv2.circle(
            out, (int(t.cx * w), int(t.cy * h)), max(int(t.radius_norm * w), 2), c, 2
        )
        if primary:
            cv2.line(out, (int(t.cx * w), 0), (int(t.cx * w), h), c, 1)
    # Obstacles get a box, not a circle, so the two families never look alike in
    # a screenshot. Amber for anything seen, red for whatever is in the way.
    for o in det.obstacles:
        blocking = is_in_the_way(o)
        c = (0, 0, 255) if blocking else (0, 180, 255)
        r = max(int(o.radius_norm * w), 2)
        px, py = int(o.cx * w), int(o.cy * h)
        cv2.rectangle(out, (px - r, py - r), (px + r, py + r), c, 2)
        cv2.putText(
            out,
            f"{o.color} {o.shape} {o.distance_m:.1f}m",
            (px - r, py - r - 6),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.4,
            c,
            1,
        )
    if det.obstacle is not None and is_in_the_way(det.obstacle):
        cv2.putText(
            out,
            "OBSTACLE AHEAD",
            (10, h - 12),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 0, 255),
            2,
        )
    return out
