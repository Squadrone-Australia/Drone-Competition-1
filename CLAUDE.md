# CLAUDE.md

Squadrone "Drone Coder": a local FastAPI server plus a vendored-Blockly web UI that lets
students fly a DJI Tello — or a built-in simulator — by dragging blocks. It ships as an
unsigned Windows installer and students never see a terminal. Setup and user-facing docs
are in [README.md](README.md); this file is the developer's shortcut.

## Commands

```bash
python -m pytest -q                        # 497 tests, ~45s
python -m pytest tests/test_fault_recovery.py -q
python -m ruff check .                     # the exact lint gate CI runs
python -m ruff check . --fix               # imports, unused names
node --test tests/js                       # 49 tests; needs `npm ci` once, for jsdom
python -m comp1                            # simulator on http://localhost:8765
python -m comp1 --drone sim --seed 42 --no-browser
```

Prefix with `venv\Scripts\` on a Windows checkout built by `start.ps1`. A checkout carrying
a `.conda/` env instead has that interpreter first on PATH already, so the bare commands
above work as written.

Never `pip install -r requirements.txt` — it is a UTF-16 `pip freeze` taken on Windows and
carries Windows-only pins, so it does not install on Linux or macOS. Use
`pip install -e .[dev]`, which reads `pyproject.toml`.

The Windows shipping path (`start.ps1`, `build.ps1`, PyInstaller, Inno Setup) is covered in
the README.

## Layout

- `comp1/server.py` (~1.4k lines) — the whole app. `create_app()` holds the WebSocket
  endpoint and every background loop (video, pose, link watchdog, battery, updates, idle
  shutdown) as closures over `app.state`. Start here.
- `comp1/interpreter.py` — runs a parsed block program against a `DroneAdapter`.
- `comp1/protocol.py` — pydantic models for the block-program wire format.
- `comp1/api.py` — the student-facing `Drone` class (the Python pathway, `--script`).
- `comp1/drone/` — `base.DroneAdapter` ABC plus `tello`/`mock`; `config.FlightConfig`.
- `comp1/sim/` — the hardware-free simulator: world, scene, scenery, render, mission scoring.
- `comp1/vision/` — HSV marker detection, distance/bearing estimation, obstacles, auto-calibration.
- `comp1/frontend/` — plain JS with Blockly and three.js vendored under `vendor/`. No build step.
- `comp1/paths.py`, `settings.py`, `update.py` — install-time concerns.
- `docs/specs/` and `docs/plans/` — read the matching spec before changing vision, the
  protocol, or drone switching. `docs/ISSUES.md` is the 2026-09-14 fault-injection report.

## Invariants

- **The version lives only in `comp1/__init__.py`.** pyproject, `build.ps1`, the installer
  and the updater all read it; the release workflow fails if tag `v<version>` disagrees.
- **A release needs both `comp1-Setup-<v>.exe` and `SHA256SUMS.txt`.** `comp1/update.py`
  looks the installer up by bare filename inside that file and refuses a digest mismatch,
  so a release missing it is one no installed copy can ever update to.
- **The block wire format is a two-sided contract.** `comp1/protocol.py` and
  `comp1/frontend/blocks.js` change together, per `docs/specs/2026-07-31-program-schema-v2.md`.
- **The stop flag is only read between blocks** (`interpreter.py`). Every adapter call must
  therefore stay bounded by `command_timeout_s`, and the e-stop path must never run inline
  in the WebSocket receive loop — an unanswered command would otherwise be a mission nobody
  can stop. `tests/test_fault_recovery.py` guards this.
- **Settings are defaults, never overrides.** `settings.resolve()` reads `None` as "flag not
  passed"; an explicit CLI flag always wins. Nothing in `settings.py` may raise — a corrupt
  file falls back to defaults rather than refusing to start on competition day.
- **Never write into the application directory.** An update replaces it wholesale; user
  state belongs in `paths.data_dir()`.
- **Warn, don't raise, at runtime.** Out-of-range values are clamped with a warning to the
  student; a mission that dies mid-flight is worse than a nudged one.
- Ruff's version is pinned in both `.github/workflows/ci.yml` and pyproject's `dev` extra —
  bump the two together. Line length 100, rule sets `E,F,W,I,UP,B`.

## Conventions

Comments explain *why*, often at length, and that is deliberate — match the surrounding
density rather than trimming it. Module docstrings carry the design rules (`settings.py`
and `paths.py` are the clearest examples). Student-facing strings are plain language.

## Git

Feature work branches off `dev`, the integration branch. `main` trails it — 24 commits
behind as of 2026-09-16 — so confirm the base before opening a PR.
