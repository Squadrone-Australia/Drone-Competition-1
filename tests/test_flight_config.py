import pytest

from comp1.drone.config import DEFAULT_FLIGHT_CONFIG, FlightConfig


def write(tmp_path, text):
    p = tmp_path / "flight.toml"
    p.write_text(text)
    return p


def test_code_defaults_stand_on_their_own():
    """Importing the module must never read a file: the dataclass is the default
    everywhere that does not thread a config through."""
    assert FlightConfig().flip_recover_cm == 30
    assert DEFAULT_FLIGHT_CONFIG == FlightConfig()


def test_a_file_overrides_only_the_keys_it_names(tmp_path):
    cfg = FlightConfig.load_file(write(tmp_path, "flip_recover_cm = 45\n"))
    assert cfg.flip_recover_cm == 45
    assert DEFAULT_FLIGHT_CONFIG.flip_recover_cm == 30, "load_file mutated the default"


def test_an_empty_file_keeps_every_default(tmp_path):
    assert FlightConfig.load_file(write(tmp_path, "")) == FlightConfig()


def test_an_unknown_key_is_loud(tmp_path):
    """A mistyped tuning key that silently does nothing is the worst outcome on
    competition day."""
    with pytest.raises(TypeError):
        FlightConfig.load_file(write(tmp_path, "flip_recovery_cm = 45\n"))


def test_the_example_file_parses_and_matches_the_code_defaults():
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / "flight_config.example.toml"
    assert FlightConfig.load_file(example) == FlightConfig()
