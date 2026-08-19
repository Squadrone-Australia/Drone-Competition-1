import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from .camera import TELLO_INTRINSICS, CameraIntrinsics

_TUPLE_FIELDS = {"lower1", "upper1", "lower2", "upper2"}


@dataclass
class VisionConfig:
    # two HSV bands because red wraps the hue axis — re-tune on-site (requirements §3.1)
    # The saturation and value floors are set for a cage lit unevenly, not for a
    # studio: the same sheet of paper reads S~110 in a bright patch and V~60 in a
    # shadowed corner, and a floor tuned to the bright patch simply loses it in
    # the corner. Band 2 starts at 165 rather than 170 because printer red is
    # often a little magenta and lands just below the old edge.
    lower1: tuple = (0, 70, 50)
    upper1: tuple = (12, 255, 255)
    lower2: tuple = (165, 70, 50)
    upper2: tuple = (180, 255, 255)
    min_area_ratio: float = (
        0.002  # ignore specks — also caps detection range, see below
    )
    # a blob larger than this cannot be the marker at any plausible range; see
    # find_targets. 0.35 is ~0.3 m for a 0.25 m marker, comfortably inside
    # approach_stop_distance_m so arriving never blinds the detector.
    max_area_ratio: float = 0.35
    circularity_min: float = 0.82  # 4πA/P² of the hull: square ≈ 0.785, circle ≈ 0.98
    solidity_min: float = 0.85  # contour/hull area: disc ≈ 1.0, star ≈ 0.5
    center_band: float = 0.2  # |cx-0.5| < band/2 → "center"

    # --- physical geometry: what turns pixels into metres ---
    intrinsics: CameraIntrinsics = field(default=TELLO_INTRINSICS)
    marker_diameter_m: float = 0.25  # printed target, A4-ish

    # --- approach control, in real units (requirements §3.2) ---
    approach_stop_distance_m: float = 0.5
    approach_bearing_deadband_deg: float = 8.0
    # the Tello ignores rotations below ~10° and refuses translations below 20 cm,
    # so these floors are hardware limits, not tuning preferences
    approach_min_turn_deg: int = 10
    approach_max_turn_deg: int = 45
    approach_min_step_cm: int = 20
    approach_max_step_cm: int = 100
    approach_max_steps: int = 40
    # How long the marker may stay out of view before the approach is abandoned.
    # A budget in seconds, not in polls: a poll costs 0.3 s when the marker is
    # missing but several seconds when it is visible and the drone is flying a
    # blocking move, so a poll count is not a predictable amount of blindness.
    # Cluttered arenas drop frames in bursts, and a burst is not a lost target.
    approach_lost_timeout_s: float = 2.5

    # --- multi-target locking ---
    # once locked, prefer the candidate nearest the last known bearing rather than
    # whichever happens to be biggest this frame; stops the drone ping-ponging
    # between two similarly sized red circles
    lock_max_bearing_jump_deg: float = 25.0
    lock_lost_frames: int = 5

    @property
    def marker_radius_m(self) -> float:
        return self.marker_diameter_m / 2

    @property
    def max_detect_range_m(self) -> float:
        """Range at which a marker stops clearing ``min_area_ratio``."""
        return self.intrinsics.max_range_m(self.marker_radius_m, self.min_area_ratio)

    @classmethod
    def load_file(cls, path: str | Path) -> "VisionConfig":
        """Build a config from a TOML file, e.g. for re-tuning HSV on-site (§3.1).

        Only the keys present in the file are overridden; anything omitted keeps
        the code default above. ``camera_dfov_deg``, if given, replaces
        ``intrinsics`` via :meth:`CameraIntrinsics.from_dfov` instead of being
        passed straight through (``intrinsics`` itself isn't a flat TOML value).
        See ``vision_config.example.toml`` at the repo root for the full set of
        keys and what each one does.
        """
        with open(path, "rb") as f:
            data = tomllib.load(f)
        kwargs = {
            k: (tuple(v) if k in _TUPLE_FIELDS else v)
            for k, v in data.items()
            if k != "camera_dfov_deg"
        }
        if "camera_dfov_deg" in data:
            kwargs["intrinsics"] = CameraIntrinsics.from_dfov(data["camera_dfov_deg"])
        return cls(**kwargs)


DEFAULT_CONFIG = VisionConfig()
