# PyInstaller spec — build with: pyinstaller comp1.spec --noconfirm
# (or, for the whole shipping pipeline including the installer, .\build.ps1)
#
# onedir, not onefile: faster startup, far fewer antivirus false positives, and
# an on-venue failure is debuggable because the files are still there to look
# at. See docs/architecture/platform-options.md.
import re
from pathlib import Path

from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

# comp1/__init__.py is the one place the version lives — read it rather than
# repeating it, so a release can never ship an exe stamped with the wrong number.
VERSION = re.search(
    r'__version__ = "([^"]+)"', Path("comp1/__init__.py").read_text(encoding="utf-8")
).group(1)
QUAD = tuple((list(map(int, VERSION.split("."))) + [0, 0, 0, 0])[:4])

version_resource = VSVersionInfo(
    ffi=FixedFileInfo(filevers=QUAD, prodvers=QUAD),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Squadrone Australia"),
                        StringStruct("FileDescription", "Squadrone Drone Coder"),
                        StringStruct("FileVersion", VERSION),
                        StringStruct("InternalName", "comp1"),
                        StringStruct("OriginalFilename", "comp1.exe"),
                        StringStruct("ProductName", "Squadrone Drone Coder"),
                        StringStruct("ProductVersion", VERSION),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)

a = Analysis(
    ["comp1/launcher.py"],
    datas=[("comp1/frontend", "comp1/frontend")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto",
                   "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                   "uvicorn.lifespan.on"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    name="comp1",
    icon="installer/comp1.ico",
    # No console. Hiding Python from students is the whole point of the packaged
    # build — but a windowed process has nowhere to print, so comp1/__main__.py
    # logs to %LOCALAPPDATA%\comp1\logs and puts a message box up if startup
    # fails. Those two halves go together; do not flip this without them.
    console=False,
    version=version_resource,
)
coll = COLLECT(exe, a.binaries, a.datas, name="comp1")
