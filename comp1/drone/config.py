import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FlightConfig:
    """Hardware quirks of the real aircraft, re-tunable on-site.

    The flight-side twin of :class:`comp1.vision.config.VisionConfig`, and it
    follows the same three rules: the code defaults live in this dataclass,
    :meth:`load_file` overlays a TOML on top of them, and importing this module
    never reads a file. Vision tuning and flight tuning stay in separate files
    because they are separate jobs — one is done with a camera pointed at a
    marker, the other with a tape measure on the arena floor.
    """

    #: How far a real Tello translates through a flip, in centimetres. The flip
    #: is a *signal* (requirements §2.1), not a way to travel, so the adapter
    #: flies this distance back the other way afterwards and the drone resumes
    #: the mission where it signalled. Measure it on the floor and put it here;
    #: 0 disables the recovery move entirely.
    flip_recover_cm: int = 30

    @classmethod
    def load_file(cls, path: str | Path) -> "FlightConfig":
        """Build a config from a TOML file.

        Only the keys present in the file are overridden; anything omitted keeps
        the code default above. An unknown key raises ``TypeError`` rather than
        being ignored — a mistyped tuning key that silently does nothing is the
        worst possible outcome on competition day. See
        ``flight_config.example.toml`` at the repo root.
        """
        with open(path, "rb") as f:
            return cls(**tomllib.load(f))


DEFAULT_FLIGHT_CONFIG = FlightConfig()
