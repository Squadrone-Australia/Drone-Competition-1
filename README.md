# Drone-Competition-1

Platform for an autonomous search-and-rescue drone competition (secondary school division): students use block-based programming to make a DJI Tello patrol a caged arena, detect red circular "victim" markers with an onboard-video OpenCV pipeline, signal each find, and return to start.

## Quick start

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]

# run with the built-in mock drone (no hardware needed)
venv\Scripts\python -m comp1

# run with a real Tello (join the TELLO-xxxx WiFi first)
venv\Scripts\python -m comp1 --drone tello
```

The app starts a local server on port 8765 and opens the block-coding UI in your browser (`--no-browser` to skip, `--port` to change). Drag blocks under **🚁 when mission starts** and press **Run**; the currently executing block is highlighted, and the right-hand panel shows the drone camera with the red-circle detection overlay.

Run the tests with `venv\Scripts\pytest`. Build the standalone Windows bundle with `venv\Scripts\pyinstaller comp1.spec --noconfirm` (output in `dist\comp1\comp1.exe`).

## Documentation

- [Platform architecture — options, comparison, and decision](docs/architecture/platform-options.md): why the platform is a custom Blockly web frontend + local Python service (FastAPI + djitellopy + OpenCV) packaged with PyInstaller, which alternatives were rejected, and the open items still to confirm (Chromebook support, safe-distance judging threshold).
- [Architecture planning notes (2026-07-28)](docs/plans/2026-07-28-platform-architecture.md)
- [Implementation plan (2026-07-28)](docs/plans/2026-07-28-implementation-plan.md)
