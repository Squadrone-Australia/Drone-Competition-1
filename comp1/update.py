"""Look for a newer release, and hand the swap to the installer.

The competition venue is offline — the laptop is joined to the aircraft's own
``TELLO-xxxx`` network, which routes nowhere. So the whole module is built
around one rule: **every failure is silence.** No internet, blocked DNS, a
school proxy, GitHub rate-limiting, malformed JSON — :func:`check` returns
``None`` and the browser shows nothing. An update banner is a nicety; a startup
that hangs or errors because a network was not there is a lost competition slot.

The swap itself is done by the Inno Setup installer, not by this process. A
running program cannot replace its own directory on Windows, which is precisely
why the installer was chosen over a portable folder: ``/CLOSEAPPLICATIONS``
lets the installer shut this process down and put the new files in its place.

No new dependency — ``urllib.request`` from the standard library is enough for
two GETs, and PyInstaller already bundles it.
"""

import hashlib
import json
import subprocess
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .paths import updates_dir

REPO = "Squadrone-Australia/Drone-Competition-1"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
CHECKSUM_ASSET = "SHA256SUMS.txt"
#: Short on purpose. This runs at startup; a laptop with no route to the
#: internet must not make the student wait for a TCP timeout.
TIMEOUT_S = 3.0
#: An installer is a few hundred MB of OpenCV and NumPy, so the download gets a
#: far longer budget than the check does.
DOWNLOAD_TIMEOUT_S = 600.0

#: Inno Setup's silent-upgrade switches, every one of them load-bearing and
#: three of them learned the hard way from a real upgrade that aborted.
#:
#: ``/CLOSEAPPLICATIONS`` alone is not enough. It asks the Restart Manager to
#: close us, and the Restart Manager closes a GUI application by posting to its
#: top-level window — which a windowed-but-window-less server process does not
#: have. Setup then reports "some applications could not be shut down" and, with
#: message boxes suppressed, aborts. ``/FORCECLOSEAPPLICATIONS`` makes it
#: terminate us instead, which is safe precisely because ``install_update``
#: refuses to run while anything is flying.
#:
#: ``/RELAUNCH`` is ours, read by the RelaunchRequested check in comp1.iss: with
#: the app force-closed rather than politely shut down, the Restart Manager has
#: nothing registered to bring back, so the installer starts it again itself.
INSTALLER_ARGS = (
    "/SILENT",
    "/SUPPRESSMSGBOXES",
    "/NORESTART",
    "/CLOSEAPPLICATIONS",
    "/FORCECLOSEAPPLICATIONS",
    "/RELAUNCH",
)

Fetch = Callable[[str, float], bytes]


class UpdateError(Exception):
    """A problem in the *deliberate* half — downloading or verifying.

    :func:`check` never raises; this is for the path a user has actively asked
    for and is therefore owed an explanation of.
    """


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    #: Expected SHA-256 of the installer, read from the release's
    #: ``SHA256SUMS.txt``. A release without one is not offered: an unverified
    #: executable downloaded over the internet and then run silently is not
    #: something to hand a school laptop.
    sha256: str
    filename: str

    def to_json(self) -> dict:
        return {"version": self.version, "notes": self.notes}


def _fetch(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "comp1"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def parse_version(text: str) -> tuple[int, ...]:
    """``"v1.2.3"`` -> ``(1, 2, 3)``. Non-numeric parts stop the parse."""
    parts: list[int] = []
    for chunk in str(text).lstrip("vV").split("."):
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def is_newer(candidate: str, current: str) -> bool:
    """True when ``candidate`` is a strictly later version than ``current``.

    An unparseable candidate is never newer — better to miss an update than to
    push a laptop onto a release whose tag nobody can read.
    """
    left, right = parse_version(candidate), parse_version(current)
    if not left:
        return False
    return left > right


def _parse_checksums(text: str) -> dict[str, str]:
    """``SHA256SUMS.txt`` in the usual ``<hash>  <filename>`` layout."""
    sums: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and len(parts[0]) == 64:
            sums[parts[1].lstrip("*")] = parts[0].lower()
    return sums


def check(
    current: str, fetch: Fetch = _fetch, timeout: float = TIMEOUT_S
) -> Release | None:
    """The newest release, if it is newer than ``current``. Never raises."""
    try:
        payload = json.loads(fetch(LATEST_URL, timeout))
        tag = payload.get("tag_name") or ""
        if not is_newer(tag, current):
            return None
        assets = payload.get("assets") or []
        installer = next(
            (
                asset
                for asset in assets
                if str(asset.get("name", "")).lower().endswith(".exe")
            ),
            None,
        )
        checksums = next(
            (asset for asset in assets if asset.get("name") == CHECKSUM_ASSET), None
        )
        if not installer or not checksums:
            return None
        sums = _parse_checksums(
            fetch(checksums["browser_download_url"], timeout).decode("utf-8", "replace")
        )
        expected = sums.get(installer["name"])
        if not expected:
            return None
        return Release(
            version=str(tag).lstrip("vV"),
            notes=str(payload.get("body") or "").strip(),
            url=installer["browser_download_url"],
            sha256=expected,
            filename=installer["name"],
        )
    except Exception:
        # Offline is the normal case at a venue, not an error worth a word.
        return None


def download(
    release: Release,
    fetch: Fetch = _fetch,
    timeout: float = DOWNLOAD_TIMEOUT_S,
    directory: Path | None = None,
) -> Path:
    """Fetch the installer and verify it. Raises :class:`UpdateError` if not.

    The file is only moved to its final name *after* the digest matches, so a
    partial or tampered download can never be launched by a later run.
    """
    target_dir = directory or updates_dir()
    data = fetch(release.url, timeout)
    digest = hashlib.sha256(data).hexdigest()
    if digest != release.sha256.lower():
        raise UpdateError("the downloaded installer did not match its checksum")
    partial = target_dir / (release.filename + ".part")
    final = target_dir / release.filename
    partial.write_bytes(data)
    partial.replace(final)
    return final


def launch_installer(path: Path, args: tuple[str, ...] = INSTALLER_ARGS) -> None:
    """Start the installer detached and return.

    Detached matters: the installer's first act is to close this process, and a
    child that dies with its parent would take the upgrade down with it.
    """
    if sys.platform != "win32":
        raise UpdateError("the installer can only be launched on Windows")
    creationflags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen([str(path), *args], close_fds=True, creationflags=creationflags)
