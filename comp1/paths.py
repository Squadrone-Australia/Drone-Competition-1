"""Where the program lives, and where it is allowed to write.

Installed, the application directory is under ``%LOCALAPPDATA%\\Programs`` and is
replaced wholesale by the next update — anything written there is both
unreliable and temporary. Everything the *user* creates (tuned HSV bands, saved
settings, logs, downloaded installers) therefore goes in :func:`data_dir`, which
survives an upgrade and an uninstall.

Nothing here reads or writes on import.
"""

import os
import sys
from pathlib import Path

#: Folder name under %LOCALAPPDATA% (and under ~/.local/share elsewhere).
APP_NAME = "comp1"


def is_frozen() -> bool:
    """True when running from a PyInstaller bundle rather than a source tree."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The directory the program was launched from.

    Frozen: the folder holding ``comp1.exe`` (onedir, so ``_internal`` sits
    beside it). Source: the repository root.
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def data_dir() -> Path:
    """User-writable state, created on demand.

    Windows: ``%LOCALAPPDATA%\\comp1``. Elsewhere: ``$XDG_DATA_HOME/comp1`` or
    ``~/.local/share/comp1`` — the project is Windows-first but the test suite
    and the development path run everywhere.
    """
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local"
    else:
        base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    path = Path(base) / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def settings_file() -> Path:
    return data_dir() / "settings.json"


def log_file() -> Path:
    logs = data_dir() / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / "comp1.log"


def updates_dir() -> Path:
    path = data_dir() / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path
