from dataclasses import dataclass


@dataclass
class VisionConfig:
    # two HSV bands because red wraps the hue axis — re-tune on-site (requirements §3.1)
    lower1: tuple = (0, 100, 80)
    upper1: tuple = (10, 255, 255)
    lower2: tuple = (170, 100, 80)
    upper2: tuple = (180, 255, 255)
    min_area_ratio: float = 0.002      # ignore specks
    circularity_min: float = 0.85      # 4πA/P²: square ≈ 0.785, circle ≈ 0.95
    center_band: float = 0.2           # |cx-0.5| < band/2 → "center"
    approach_stop_area: float = 0.08   # "close enough" frame proportion (§3.2)
    approach_step_cm: int = 30
    approach_turn_deg: int = 15
    approach_max_steps: int = 40


DEFAULT_CONFIG = VisionConfig()
