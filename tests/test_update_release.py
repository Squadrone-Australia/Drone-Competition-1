"""The shipping contract the updater depends on, checked without building it.

An installed copy updates itself by reading ``SHA256SUMS.txt`` off a GitHub
release, finding the installer *by bare filename* inside it, and running that
installer with a fixed set of switches. Four separate files have to agree for
that to work -- ``comp1/__init__.py``, ``build.ps1``, ``.github/workflows/
release.yml`` and ``installer/comp1.iss`` -- and nothing but a real tagged
release exercises the agreement. A release that gets it wrong is not a bug that
shows up as a crash: it is a release every installed copy silently declines to
update to, discovered weeks later at a venue.

So these tests read those files as text and check the seams. They are cheap,
they need no Windows, no Inno Setup and no PyInstaller, and they fail on the
pull request that breaks the contract rather than on the tag that ships it.
"""

import re
import tomllib
from pathlib import Path

import pytest

from comp1 import __version__, update

ROOT = Path(__file__).resolve().parent.parent
BUILD_PS1 = (ROOT / "build.ps1").read_text(encoding="utf-8-sig")
RELEASE_YML = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
ISS = (ROOT / "installer/comp1.iss").read_text(encoding="utf-8-sig")

#: The regex build.ps1 and release.yml both use to lift the version out of
#: comp1/__init__.py. Duplicated here on purpose: if someone reformats that
#: assignment, this is the test that says so.
VERSION_PATTERN = r'__version__ = "([^"]+)"'


# ------------------------------------------------------------------- version


def test_the_version_is_readable_the_way_the_build_reads_it():
    source = (ROOT / "comp1/__init__.py").read_text(encoding="utf-8")
    found = re.search(VERSION_PATTERN, source)
    assert found, "build.ps1 and release.yml lift the version with this regex"
    assert found.group(1) == __version__


@pytest.mark.parametrize(
    "name,text", [("build.ps1", BUILD_PS1), ("release.yml", RELEASE_YML)]
)
def test_the_build_scripts_still_read_the_one_source_of_truth(name, text):
    assert VERSION_PATTERN in text, (
        f"{name} no longer lifts __version__ from comp1/__init__.py"
    )


def test_pyproject_takes_its_version_from_the_package():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "version" in data["project"]["dynamic"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "comp1.__version__"
    }
    assert "version" not in data["project"], "a second, forkable version"


def test_the_version_parses_and_orders():
    # check() compares the release tag against this string. A version it cannot
    # read is one that never looks older than anything, so no update is offered.
    assert update.parse_version(__version__), f"{__version__} does not parse"
    assert update.is_newer("v99.0.0", __version__)
    assert not update.is_newer(f"v{__version__}", __version__)


# ------------------------------------------------------- the installer's name


def test_the_installer_is_named_the_way_the_updater_expects():
    # Inno Setup decides the asset's name; both build scripts then have to hash
    # that same name, because update.check picks the release's .exe asset and
    # looks it up in SHA256SUMS.txt under exactly that string.
    assert "OutputBaseFilename=comp1-Setup-{#AppVersion}" in ISS
    # What Inno Setup will emit for the version in comp1/__init__.py today...
    expected = f"comp1-Setup-{__version__}.exe"
    assert expected.endswith(".exe"), "check() only ever offers a .exe asset"
    # ...and what each build script expands its own path to.
    built = BUILD_PS1.replace("$version", __version__)
    workflow = RELEASE_YML.replace("${{ steps.version.outputs.version }}", __version__)
    assert "dist" + chr(92) + expected in built
    assert "dist" + chr(92) + expected in workflow


def test_the_checksum_file_is_the_name_the_updater_asks_for():
    assert update.CHECKSUM_ASSET == "SHA256SUMS.txt"
    for text in (BUILD_PS1, RELEASE_YML):
        assert "SHA256SUMS.txt" in text


def test_both_build_paths_write_a_bare_filename_with_no_directory():
    # "<hash>  <filename>", and the filename must be bare: _parse_checksums
    # keys on it verbatim, so "dist\comp1-Setup-0.2.0.exe" would be a line the
    # updater reads and then fails to match.
    for name, text in (("build.ps1", BUILD_PS1), ("release.yml", RELEASE_YML)):
        assert "Split-Path $setup -Leaf" in text, f"{name} must hash a bare name"
        assert '"$hash  $name"' in text, f"{name} must write '<hash>  <name>'"


def test_a_line_in_that_exact_shape_round_trips_through_the_parser():
    # The literal the two scripts build, parsed by the code that will read it
    # off the release page.
    digest = "9f" * 32
    name = f"comp1-Setup-{__version__}.exe"
    assert update._parse_checksums(f"{digest}  {name}\n") == {name: digest}


def test_a_checksum_line_carrying_a_path_is_not_silently_accepted():
    # The failure this guards is a build script switching to `$setup` instead
    # of its leaf. It must not resolve to the asset name.
    line = f"{'9f' * 32}  dist{chr(92)}comp1-Setup.exe" + chr(10)
    parsed = update._parse_checksums(line)
    assert "comp1-Setup.exe" not in parsed


# --------------------------------------------------------- the silent upgrade


def test_the_installer_reads_the_relaunch_switch_the_updater_sends():
    # /RELAUNCH is not an Inno Setup switch -- it is ours, and comp1.iss has to
    # keep looking for it. Without this the upgrade installs and the student is
    # left staring at a closed program.
    assert "/RELAUNCH" in update.INSTALLER_ARGS
    assert "RelaunchRequested" in ISS
    assert "'/RELAUNCH'" in ISS
    assert "Check: RelaunchRequested" in ISS


def test_the_installer_allows_the_close_the_updater_asks_for():
    assert "CloseApplications=yes" in ISS
    assert "/CLOSEAPPLICATIONS" in update.INSTALLER_ARGS
    assert "/FORCECLOSEAPPLICATIONS" in update.INSTALLER_ARGS


def test_the_upgrade_stays_silent():
    # Message boxes are the failure mode here: Setup runs with no one in front
    # of it, so a prompt is an upgrade that hangs until the laptop is rebooted.
    assert "/SILENT" in update.INSTALLER_ARGS
    assert "/SUPPRESSMSGBOXES" in update.INSTALLER_ARGS
    assert "/NORESTART" in update.INSTALLER_ARGS


def test_the_installer_keeps_a_fixed_appid():
    # The AppId is how Windows knows this is an upgrade rather than a second
    # copy installed alongside the first. Changing it strands every existing
    # install on the version it already has.
    found = re.search(r"^AppId=(.+)$", ISS, re.MULTILINE)
    assert found, "comp1.iss must pin an AppId"
    assert found.group(1).strip() == "{{4C0F5A93-2E7B-4C1D-9E3A-8B7C6D5E4F21}"


# ---------------------------------------------------------- the release build


def test_the_release_workflow_refuses_a_tag_that_disagrees_with_the_version():
    assert "Tag $tag does not match comp1.__version__" in RELEASE_YML
    assert 'if ($tag -ne "v$version")' in RELEASE_YML


def test_the_release_uploads_both_assets_or_none():
    # update.check returns None unless *both* are present, so an installer
    # published without its checksum file is one nobody can ever update to.
    assert "SHA256SUMS.txt" in RELEASE_YML
    assert "if-no-files-found: error" in RELEASE_YML
    create = RELEASE_YML.split("gh release create", 1)[1]
    assert "comp1-Setup-" in create
    assert "SHA256SUMS.txt" in create


def test_the_release_is_gated_on_the_tests():
    assert "pytest -q" in RELEASE_YML
