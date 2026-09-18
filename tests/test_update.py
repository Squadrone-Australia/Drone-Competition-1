"""The update check, offline-first.

Every test here injects its own ``fetch``. Nothing in this file may open a
socket — the suite is hardware-free and network-free by the same rule.
"""

import hashlib
import json
import subprocess

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


def test_check_ignores_assets_that_are_not_the_installer():
    # Release pages routinely carry extra assets -- source tarballs, a PDB, a
    # zip of the examples. Only the .exe may be offered as an installer.
    payload = json.loads(release_payload())
    payload["assets"] = [
        {"name": "examples.zip", "browser_download_url": "https://example.invalid/z"},
        *payload["assets"],
    ]
    fetch = fetcher(**{update.LATEST_URL: json.dumps(payload).encode()})
    release = update.check("0.1.0", fetch=fetch)
    assert release is not None
    assert release.filename == "comp1-Setup-0.2.0.exe"

def test_check_is_silent_when_the_release_carries_no_installer():
    payload = json.loads(release_payload())
    payload["assets"] = [payload["assets"][1]]  # SHA256SUMS.txt, no .exe
    fetch = fetcher(**{update.LATEST_URL: json.dumps(payload).encode()})
    assert update.check("0.1.0", fetch=fetch) is None

def test_the_banner_payload_carries_no_url_or_digest():
    # to_json is what reaches the browser. The download is the server's job;
    # handing the page a URL invites a student to fetch the installer by hand
    # and skip the checksum that is the whole point of publishing one.
    release = update.check("0.1.0", fetch=fetcher())
    assert release.to_json() == {"version": "0.2.0", "notes": "Faster arena loading."}

def test_checksums_are_parsed_from_the_shipped_format():
    # Exactly what build.ps1 and release.yml write: lower-case hex, two spaces,
    # a bare filename. The binary-mode "*name" variant is tolerated because
    # sha256sum writes it and someone will eventually regenerate the file that
    # way.
    digest = "a" * 64
    parsed = update._parse_checksums(
        f"{digest}  comp1-Setup-0.2.0.exe\n"
        f"{'b' * 64} *comp1-Setup-0.3.0.exe\n"
        "# a comment nobody promised not to add\n"
        "\n"
    )
    assert parsed == {
        "comp1-Setup-0.2.0.exe": digest,
        "comp1-Setup-0.3.0.exe": "b" * 64,
    }


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


def test_a_failed_download_leaves_no_partial_file_to_be_found_later(tmp_path):
    # The .part name matters: download() writes there and only renames after
    # the digest matches, so a crash mid-write can never leave something that
    # launch_installer would happily run.
    release = update.check("0.1.0", fetch=fetcher())
    tampered = fetcher(
        **{"https://example.invalid/comp1-Setup-0.2.0.exe": b"not the installer"}
    )
    with pytest.raises(update.UpdateError):
        update.download(release, fetch=tampered, directory=tmp_path)
    assert list(tmp_path.glob("*.part")) == []
    assert list(tmp_path.glob("*.exe")) == []


def test_download_overwrites_a_stale_installer_of_the_same_name(tmp_path):
    # A previous attempt that got as far as landing the file must not make the
    # next one refuse or, worse, launch the old bytes.
    stale = tmp_path / "comp1-Setup-0.2.0.exe"
    stale.write_bytes(b"an older build")
    release = update.check("0.1.0", fetch=fetcher())
    path = update.download(release, fetch=fetcher(), directory=tmp_path)
    assert path.read_bytes() == b"MZ fake installer"


# -------------------------------------------------------------- the handover


class FakePopen:
    """Records one Popen call instead of starting anything."""

    calls: list[tuple] = []

    def __init__(self, argv, **kwargs):
        FakePopen.calls.append((argv, kwargs))


@pytest.fixture
def popen(monkeypatch):
    FakePopen.calls = []
    monkeypatch.setattr(update.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(update.sys, "platform", "win32")
    return FakePopen.calls


def test_launch_installer_passes_every_silent_upgrade_switch(popen, tmp_path):
    installer = tmp_path / "comp1-Setup-0.2.0.exe"
    update.launch_installer(installer)
    argv, _ = popen[0]
    assert argv[0] == str(installer)
    assert tuple(argv[1:]) == update.INSTALLER_ARGS


def test_launch_installer_keeps_the_switches_the_upgrade_needs():
    # Each of these was learned from an upgrade that aborted; dropping one
    # turns a silent update into a dialog nobody is standing in front of.
    # /RELAUNCH is ours, and installer/comp1.iss must keep reading it.
    assert set(update.INSTALLER_ARGS) >= {
        "/SILENT",
        "/SUPPRESSMSGBOXES",
        "/CLOSEAPPLICATIONS",
        "/FORCECLOSEAPPLICATIONS",
        "/RELAUNCH",
    }


def test_launch_installer_detaches_from_this_process(popen, tmp_path):
    # The installer's first act is to close us. A child in our process group
    # would be taken down with us and the upgrade would never happen.
    update.launch_installer(tmp_path / "comp1-Setup-0.2.0.exe")
    _, kwargs = popen[0]
    assert kwargs["close_fds"] is True
    expected = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    assert kwargs["creationflags"] == expected


def test_launch_installer_refuses_anywhere_but_windows(monkeypatch, tmp_path):
    monkeypatch.setattr(update.sys, "platform", "linux")
    monkeypatch.setattr(
        update.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("nothing may be launched off Windows"),
    )
    with pytest.raises(update.UpdateError):
        update.launch_installer(tmp_path / "comp1-Setup-0.2.0.exe")
