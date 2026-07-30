# PyInstaller spec — build with: pyinstaller comp1.spec --noconfirm
a = Analysis(
    ["comp1/launcher.py"],
    datas=[("comp1/frontend", "comp1/frontend")],
    hiddenimports=["uvicorn.logging", "uvicorn.loops.auto",
                   "uvicorn.protocols.http.auto", "uvicorn.protocols.websockets.auto",
                   "uvicorn.lifespan.on"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, name="comp1", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="comp1")
