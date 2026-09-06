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
    #: can fly this distance back the other way afterwards and resume the
    #: mission where it signalled. **Off by default (0):** the compensating
    #: move costs altitude the aircraft has already lost through the flip, and
    #: on the arena that mattered more than the displacement it corrects. Set
    #: it (measured on the floor) to turn the recovery back on.
    flip_recover_cm: int = 0

    #: How long the video stream may go without a new decoded frame before the
    #: adapter calls the link dead, in seconds. A rebooted or out-of-range Tello
    #: stops sending frames but the decoder keeps handing back the last one it
    #: got, so silence — not an error — is what a lost aircraft looks like.
    #: Long enough to cover stream start-up and a normal decoder hiccup; the
    #: server's watchdog reconnects once this expires.
    link_timeout_s: float = 6.0

    #: Charge below which the "signal target found" flip is downgraded to a
    #: 360-degree spin, in percent. A real Tello refuses ``flip`` under about
    #: 50% and answers ``error`` — which reads to a student as a signal that
    #: simply did not happen, on the one action the judges are watching for
    #: (requirements §2.1). The margin above 50 covers the charge dropping
    #: between the reading and the manoeuvre. Raise it if the airframe still
    #: refuses; 0 disables the fallback and always flips.
    flip_min_battery_pct: int = 55

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


#: The find signal's flip direction and spin angle. The flip is a *signal*, not
#: a way to travel (see ``flip_recover_cm``), and a full turn likewise ends on
#: the heading it started from, so neither displaces the mission.
SIGNAL_FLIP_DIR = "back"
SIGNAL_SPIN_DEG = 360


def choose_signal(
    kind: str, battery: int | None, flight: FlightConfig | None = None
) -> tuple[str, str | None]:
    """Pick the find signal to actually perform, and why if it was downgraded.

    Returns ``(kind, warning)`` where *kind* is one of :data:`comp1.protocol.SIGNAL_KINDS` and
    *warning* is a message for the student, or ``None`` when the requested
    signal is what happens.

    A flip on a low battery is the case this exists for: the aircraft refuses
    the command and the mission carries on as though the find *was* signalled,
    so the one action the judges look for is the one that silently vanishes. An
    unknown charge (``battery is None`` — a failed reading, or an adapter that
    cannot answer) is treated the same way, because the safe direction is the
    signal that always works rather than the one that might not.
    """
    flight = flight or DEFAULT_FLIGHT_CONFIG
    if kind != "flip":
        return kind, None
    floor = flight.flip_min_battery_pct
    if floor <= 0:
        return "flip", None
    if battery is None:
        return "spin", (
            "battery unknown, so a flip might be refused — "
            "signalling the find with a 360° spin instead"
        )
    if battery < floor:
        return "spin", (
            f"battery {battery}% is below {floor}%, where the drone refuses to "
            "flip — signalling the find with a 360° spin instead"
        )
    return "flip", None
