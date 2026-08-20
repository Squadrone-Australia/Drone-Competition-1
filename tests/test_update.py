"""The update check, offline-first.

Every test here injects its own ``fetch``. Nothing in this file may open a
socket — the suite is hardware-free and network-free by the same rule.
"""

import hashlib
import json

import pytest

from comp1 import update


def release_payload(tag="v0.2.0", installer="comp1-Setup-0.2.0.exe"):
    return json.dumps(
        {
            "tag_name": tag,
            "body": "Faster arena loading.",
            "assets": [
                {
                    "name": installer,
                    "browser_download_url": f"https://example.invalid/{installer}",
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://example.invalid/SHA256SUMS.txt",
                },
            ],
        }
    ).encode()


def fetcher(installer_bytes=b"MZ fake installer", **overrides):
    digest = hashlib.sha256(installer_bytes).hexdigest()
    pages = {
        update.LATEST_URL: release_payload(),
        "https://example.invalid/SHA256SUMS.txt": (
            f"{digest}  comp1-Setup-0.2.0.exe\n".encode()
        ),
        "https://example.invalid/comp1-Setup-0.2.0.exe": installer_bytes,
    }
    pages.update(overrides)

    def fetch(url, timeout):
        return pages[url]

    return fetch


# --------------------------------------------------------------- version maths


@pytest.mark.parametrize(
    "candidate,current,expected",
    [
        ("v0.2.0", "0.1.0", True),
        ("0.1.1", "0.1.0", True),
        ("v0.1.0", "0.1.0", False),
        ("v0.1.0", "0.2.0", False),
        ("v0.10.0", "0.9.0", True),  # not a string comparison
        ("v1.0", "0.9.9", True),  # short tags still order correctly
        ("nightly", "0.1.0", False),  # unreadable tag is never newer
    ],
)
def test_is_newer(candidate, current, expected):
    assert update.is_newer(candidate, current) is expected


# ------------------------------------------------------------------- the check


def test_check_returns_the_release_with_its_checksum():
    release = update.check("0.1.0", fetch=fetcher())
    assert release is not None
    assert release.version == "0.2.0"
    assert release.filename == "comp1-Setup-0.2.0.exe"
    assert release.sha256 == hashlib.sha256(b"MZ fake installer").hexdigest()
    assert "Faster arena" in release.notes


def test_check_is_silent_when_already_current():
    assert update.check("0.2.0", fetch=fetcher()) is None


def test_check_is_silent_when_offline():
    def fetch(url, timeout):
        raise OSError("network is unreachable")

    assert update.check("0.1.0", fetch=fetch) is None


def test_check_is_silent_on_junk_from_the_network():
    # A captive portal answering every request with a login page is the shape
    # of this: a 200, and nothing that parses.
    def fetch(url, timeout):
        return b"<html>Sign in to continue</html>"

    assert update.check("0.1.0", fetch=fetch) is None


def test_check_is_silent_without_a_published_checksum():
    payload = json.loads(release_payload())
    payload["assets"] = [payload["assets"][0]]  # installer, no SHA256SUMS.txt
    fetch = fetcher(**{update.LATEST_URL: json.dumps(payload).encode()})
    assert update.check("0.1.0", fetch=fetch) is None


def test_check_is_silent_when_the_checksum_file_omits_the_installer():
    fetch = fetcher(
        **{"https://example.invalid/SHA256SUMS.txt": b"deadbeef  something-else.zip\n"}
    )
    assert update.check("0.1.0", fetch=fetch) is None


# ---------------------------------------------------------------- the download


def test_download_verifies_and_lands_the_file(tmp_path):
    release = update.check("0.1.0", fetch=fetcher())
    path = update.download(release, fetch=fetcher(), directory=tmp_path)
    assert path.read_bytes() == b"MZ fake installer"
    assert path.name == "comp1-Setup-0.2.0.exe"
    assert not list(tmp_path.glob("*.part"))  # nothing half-written left behind


def test_download_refuses_a_file_that_does_not_match_its_checksum(tmp_path):
    release = update.check("0.1.0", fetch=fetcher())
    tampered = fetcher(
        **{"https://example.invalid/comp1-Setup-0.2.0.exe": b"something else entirely"}
    )
    with pytest.raises(update.UpdateError):
        update.download(release, fetch=tampered, directory=tmp_path)
    assert list(tmp_path.iterdir()) == []  # and it is not left on disk to be run
