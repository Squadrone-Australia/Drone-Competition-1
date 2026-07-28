# Drone-Competition-1

Platform for an autonomous search-and-rescue drone competition (secondary school division): students use block-based programming to make a DJI Tello patrol a caged arena, detect red circular "victim" markers with an onboard-video OpenCV pipeline, signal each find, and return to start.

## Documentation

- [Platform architecture — options, comparison, and decision](docs/architecture/platform-options.md): why the platform is a custom Blockly web frontend + local Python service (FastAPI + djitellopy + OpenCV) packaged with PyInstaller, which alternatives were rejected, and the open items still to confirm (Chromebook support, safe-distance judging threshold).
- [Planning notes (2026-07-28)](docs/plans/2026-07-28-platform-architecture.md)
