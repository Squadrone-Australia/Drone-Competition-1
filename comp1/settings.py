"""Persisted operator preferences — the CLI flags, minus the CLI.

Installed from the Windows installer there is no command line to type flags
into, so the choices a teacher makes in the browser have to survive a restart.
This is that store: a small JSON file in :func:`comp1.paths.data_dir`.

Two rules hold the design together.

**Nothing here may raise.** A corrupt, truncated, or hand-edited file falls back
to defaults, exactly like a runtime warning in the interpreter — a program that
refuses to start because a preferences file has a stray comma is worse on
competition day than one that starts with the wrong scenery.

**An explicit CLI flag always wins.** ``--drone tello`` in a shortcut's target
must not be silently overridden by whatever the last browser session saved, so
:func:`resolve` takes ``None`` to mean "not passed" and only then consults the
file. Settings are the *default*, never the override.
"""

import json
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from .paths import settings_file

#: Everything the browser is allowed to persist. Kept flat and boring: this file
#: is written by a machine and occasionally read by a human debugging a venue.
DRONE_MODES = ("sim", "tello", "mock")
SCENERIES = ("arena", "corridor")


@dataclass
class Settings:
    drone: str = "sim"
    scenery: str = "arena"
    seed: int | None = None
    noise: float = 0.0
    #: Whether the app may look for a newer release at startup. Off is a valid
    #: choice on a school network that blocks api.github.com outright.
    check_updates: bool = True
    #: Applied HSV bands from the last successful "Apply to detector", so a
    #: venue re-tune (§3.1) outlives the session that made it. Empty means
    #: "use whatever the config says".
    hsv: dict[str, list[int]] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


DEFAULTS = Settings()


def _clean(raw: Any) -> Settings:
    """Build a Settings from untrusted JSON, dropping anything unusable."""
    if not isinstance(raw, dict):
        return replace(DEFAULTS)
    values: dict[str, Any] = {}
    drone = raw.get("drone")
    if drone in DRONE_MODES:
        values["drone"] = drone
    scenery = raw.get("scenery")
    if scenery in SCENERIES:
        values["scenery"] = scenery
    seed = raw.get("seed")
    if isinstance(seed, int) and not isinstance(seed, bool):
        values["seed"] = seed
    noise = raw.get("noise")
    if isinstance(noise, (int, float)) and not isinstance(noise, bool):
        values["noise"] = max(0.0, min(1.0, float(noise)))
    check = raw.get("check_updates")
    if isinstance(check, bool):
        values["check_updates"] = check
    hsv = raw.get("hsv")
    if isinstance(hsv, dict):
        # Shape only. The values are validated for real by
        # ``calibration.config_with_hsv`` before they ever reach the detector,
        # so a bad triplet here is dropped at load rather than trusted.
        cleaned = {
            key: [int(v) for v in value]
            for key, value in hsv.items()
            if isinstance(value, (list, tuple))
            and len(value) == 3
            and all(
                isinstance(v, (int, float)) and not isinstance(v, bool) for v in value
            )
        }
        if cleaned:
            values["hsv"] = cleaned
    return replace(DEFAULTS, **values)


def load(path: Path | None = None) -> Settings:
    """Read saved preferences. Any problem at all yields the defaults."""
    target = path or settings_file()
    try:
        return _clean(json.loads(target.read_text(encoding="utf-8")))
    except Exception:
        return replace(DEFAULTS)


def save(settings: Settings, path: Path | None = None) -> bool:
    """Write preferences. Returns False instead of raising if it cannot.

    Written to a sibling temp file and moved into place, so an interrupted
    write (a laptop lid closing at the end of a session) leaves the previous
    settings intact rather than a half-file that loads as defaults.
    """
    target = path or settings_file()
    temp = target.with_suffix(".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        temp.write_text(
            json.dumps(settings.to_json(), indent=2) + "\n", encoding="utf-8"
        )
        temp.replace(target)
        return True
    except Exception:
        try:
            temp.unlink(missing_ok=True)
        except Exception:
            pass
        return False


def update(path: Path | None = None, /, **changes: Any) -> Settings:
    """Load, apply ``changes``, save, and return the result.

    ``path`` is positional-only deliberately: ``changes`` comes straight off a
    WebSocket message, and a client sending a field called ``path`` would
    otherwise collide with this argument instead of being dropped as the
    unknown field it is.
    """
    current = load(path)
    merged = _clean({**current.to_json(), **changes})
    save(merged, path)
    return merged


def resolve(flag: Any, saved: Any) -> Any:
    """An explicitly passed flag, else the saved value.

    ``None`` is the "not passed" sentinel, which is why every affected argparse
    argument defaults to ``None`` rather than to its real default.
    """
    return saved if flag is None else flag
