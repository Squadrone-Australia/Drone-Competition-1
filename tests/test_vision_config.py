"""Loading a vision tuning file. Mostly the one way it must never fail."""

from pathlib import Path

from comp1.vision.config import DEFAULT_CONFIG, VisionConfig


def test_a_file_from_before_obstacles_were_removed_still_loads(tmp_path):
    # A venue's vision_config.toml was copied from the old example, which set
    # every obstacle_* and avoid_* key. Those fields are gone; the file must
    # still load, and what it says about the red bands must still apply.
    path = tmp_path / "vision_config.toml"
    path.write_text(
        "lower1 = [0, 90, 60]\n"
        "obstacle_sat_min = 80\n"
        "obstacle_val_min = 50\n"
        "obstacle_min_area_ratio = 0.004\n"
        "obstacle_max_area_ratio = 0.9\n"
        "obstacle_diameter_m = 0.25\n"
        "obstacle_clear_distance_m = 1.0\n"
        "avoid_sidestep_cm = 60\n"
        "avoid_max_steps = 6\n",
        encoding="utf-8",
    )
    cfg = VisionConfig.load_file(path)
    assert cfg.lower1 == (0, 90, 60)
    assert not hasattr(cfg, "obstacle_sat_min")


def test_the_shipped_example_loads():
    # Every key in it must still be a field: the example is what venues copy.
    example = Path(__file__).parent.parent / "vision_config.example.toml"
    assert VisionConfig.load_file(example).detector == DEFAULT_CONFIG.detector
