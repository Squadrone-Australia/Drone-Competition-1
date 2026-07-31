from dataclasses import dataclass, field

from .camera import CameraIntrinsics, TELLO_INTRINSICS


@dataclass
class VisionConfig:
    # two HSV bands because red wraps the hue axis — re-tune on-site (requirements §3.1)
    lower1: tuple = (0, 100, 80)
    upper1: tuple = (10, 255, 255)
    lower2: tuple = (170, 100, 80)
    upper2: tuple = (180, 255, 255)
    min_area_ratio: float = 0.002      # ignore specks — also caps detection range, see below
    circularity_min: float = 0.85      # 4πA/P²: square ≈ 0.785, circle ≈ 0.95
    center_band: float = 0.2           # |cx-0.5| < band/2 → "center"

    # --- physical geometry: what turns pixels into metres ---
    intrinsics: CameraIntrinsics = field(default=TELLO_INTRINSICS)
    marker_diameter_m: float = 0.25    # printed victim marker, A4-ish

    # --- approach control, in real units (requirements §3.2) ---
    approach_stop_distance_m: float = 1.0
    approach_bearing_deadband_deg: float = 8.0
    # the Tello ignores rotations below ~10° and refuses translations below 20 cm,
    # so these floors are hardware limits, not tuning preferences
    approach_min_turn_deg: int = 10
    approach_max_turn_deg: int = 45
    approach_min_step_cm: int = 20
    approach_max_step_cm: int = 100
    approach_max_steps: int = 40
    approach_lost_limit: int = 3

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


DEFAULT_CONFIG = VisionConfig()
