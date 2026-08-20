"""Preferences round-trip, and the rule that a flag always beats a saved value."""

import json

from comp1 import settings


def test_round_trip(tmp_path):
    path = tmp_path / "settings.json"
    saved = settings.Settings(drone="tello", scenery="corridor", seed=7, noise=0.05)
    assert settings.save(saved, path)
    assert settings.load(path) == saved


def test_missing_file_is_defaults(tmp_path):
    assert settings.load(tmp_path / "nothing.json") == settings.DEFAULTS


def test_corrupt_file_is_defaults_not_an_exception(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json at all", encoding="utf-8")
    assert settings.load(path) == settings.DEFAULTS


def test_nonsense_values_are_dropped_field_by_field(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "drone": "helicopter",  # not a mode we have
                "scenery": "corridor",  # good, and must survive its bad neighbours
                "seed": "seven",
                "noise": True,  # bool is not a float here
                "check_updates": "yes",
                "hsv": {"lower1": [0, 120, 70], "upper1": "nope"},
            }
        ),
        encoding="utf-8",
    )
    loaded = settings.load(path)
    assert loaded.drone == "sim"  # dropped, back to the default
    assert loaded.scenery == "corridor"  # kept
    assert loaded.seed is None and loaded.noise == 0.0
    assert loaded.check_updates is True
    assert loaded.hsv == {"lower1": [0, 120, 70]}  # only the well-formed triplet


def test_update_merges_without_clobbering_the_rest(tmp_path):
    path = tmp_path / "settings.json"
    settings.save(settings.Settings(drone="tello", seed=3), path)
    merged = settings.update(path, scenery="corridor")
    assert (merged.drone, merged.seed, merged.scenery) == ("tello", 3, "corridor")
    assert settings.load(path) == merged


def test_save_reports_failure_instead_of_raising(tmp_path):
    # A directory where the file should be: unwritable, in the same way a
    # locked-down profile is unwritable.
    target = tmp_path / "settings.json"
    target.mkdir()
    assert settings.save(settings.Settings(), target) is False


def test_an_explicit_flag_beats_the_saved_value():
    assert settings.resolve("tello", "sim") == "tello"
    assert settings.resolve(None, "sim") == "sim"
    # 0 and False are real choices, not "unset" — only None means not passed
    assert settings.resolve(0.0, 0.05) == 0.0
    assert settings.resolve(False, True) is False
