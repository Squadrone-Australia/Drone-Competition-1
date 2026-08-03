# Repository Guidelines

## Project Structure & Module Organization

`comp1/` is the Python package. Core program validation and execution live in `protocol.py` and `interpreter.py`; `api.py` exposes the student-facing Python pathway, and `server.py` hosts the FastAPI/WebSocket service. Drone adapters are under `comp1/drone/`, simulation and scoring under `comp1/sim/`, and OpenCV detection under `comp1/vision/`. The offline Blockly/three.js UI is in `comp1/frontend/`, including vendored assets. Put Python tests in `tests/test_*.py`, JavaScript serializer tests in `tests/js/`, runnable examples in `examples/`, and design records in `docs/architecture/`, `docs/specs/`, or dated `docs/plans/`.

## Build, Test, and Development Commands

Use PowerShell from the repository root:

```powershell
python -m venv venv
venv\Scripts\pip install -e .[dev]      # editable install with test/build tools
venv\Scripts\python -m comp1 --drone sim --seed 42
venv\Scripts\pytest                     # complete Python suite
venv\Scripts\pytest tests\test_api.py::test_battery_and_height
node --test tests\js\blocks.test.js     # frontend serializer suite
venv\Scripts\pyinstaller comp1.spec --noconfirm
```

The default `python -m comp1` uses `MockDrone`; use `--drone tello` only after joining the Tello network. Packaged output is written to `dist/comp1/`.

## Coding Style & Naming Conventions

Follow existing Python style: four-space indentation, type hints for public interfaces, `snake_case` functions/modules, `PascalCase` classes, and uppercase constants. Keep JavaScript dependency-free and compatible with the existing vanilla-JS frontend. No formatter or linter is configured, so preserve nearby formatting and keep changes focused. Never add generated caches, virtual environments, `build/`, or `dist/` artifacts.

## Testing Guidelines

Pytest is configured in `pyproject.toml`; async tests run automatically through `pytest-asyncio`. Name tests `test_<behavior>` and keep them hardware-free with `MockDrone` or `SimDrone`. Add regression tests beside the affected layer and run both Python and Node suites when changing `frontend/blocks.js`. No formal coverage threshold is configured.

## Architecture & Safety Constraints

Student block programs remain validated JSON; do not execute generated Python. A new drone action normally requires coordinated changes in `protocol.py`, `interpreter.py`, `drone/base.py` plus all adapters, and `frontend/blocks.js`; expose it in `api.py` when appropriate. Keep duplicated frontend/backend limits synchronized. Do not expose simulator coordinates or add fixed-coordinate flight blocks. Frontend assets must remain fully offline.

## Commit & Pull Request Guidelines

History uses concise, imperative subjects, often Conventional Commit prefixes such as `feat:` and `docs:`. Keep each commit scoped. Pull requests should explain behavior and safety impact, link relevant issues/specs, list tests run, and include screenshots for visible UI or simulator changes.
