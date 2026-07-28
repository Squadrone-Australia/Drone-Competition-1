# Competition 1 — Platform Architecture Comparison & Decision (Plan)

## Context

The COMP1 requirements doc (§3.3) leaves the platform architecture undecided: frontend (custom Blockly app vs JupyterLab+jupyterlab-blockly), local delivery model (PyInstaller-style bundle), and backend (Python + djitellopy + OpenCV over WebSocket). The task is to compare these options, surface any alternatives not yet considered, and produce a written comparison **with a decision**.

Confirmed decisions from discussion (2026-07-28):

- Live camera feed with detection overlay must be visible in the student UI.
- Target OS is undecided — possibly Windows + macOS + **Chromebook**.

## Deliverable

One new document: **`docs/architecture/platform-options.md`** containing the comparison, the decision, and the remaining items to confirm. No code changes yet (repo is empty apart from README).

## Research findings to capture in the doc

### The constraint that decides most of it

The vision blocks ("detect red circular marker", "approach and stop") require a **local OpenCV pipeline over the Tello video stream**. No off-the-shelf block-coding product exposes that:

- **DroneBlocks, Tello EDU app, Scratch+Tello, mBlock 5** — mature Tello flight blocks, but no custom CV injection; vision is the core of the competition → disqualified as the platform (keep as UX reference for block design).
- **Node-RED + Tello nodes** — flow-based, not kid-friendly block coding; vision still custom → poor fit.
- **BIPES, M5Stack UIFlow** — Blockly→Python for embedded/MicroPython; UIFlow has Tello blocks but no custom CV → poor fit.

Therefore a local Python backend (djitellopy + OpenCV) is unavoidable, and the real decision is frontend + packaging.

### Frontend comparison

#### A. Custom standalone Blockly web app — RECOMMENDED

- Blockly is a vendorable JS library (fully offline-capable); toolbox shows ONLY the ~15 competition blocks from §2 — no UI clutter for first-time coders.
- One Python process serves static frontend + WebSocket + drone control + vision.
- Execution model: Blockly generates a JSON command program that the backend interprets step-by-step (not raw Python exec) → enables currently-running-block highlighting, pause, and instant emergency stop mid-program.
- Blockly was transferred from Google to the Raspberry Pi Foundation (Nov 2025); actively maintained, Apache-2.0.

#### B. JupyterLab + jupyterlab-blockly — rejected

- Custom blocks ARE supported (`IBlocklyRegistry`), but require writing a JupyterLab TypeScript extension anyway → no dev-effort saving vs A.
- v0.3.x maturity; JupyterLab cells/kernels/menus are overhead and confusion for the audience; PyInstaller-packaging JupyterLab (labextension data files) is fragile.

#### C. Off-the-shelf apps — rejected

See constraint above.

### Delivery / packaging comparison

1. **PyInstaller onedir bundle, auto-open system browser** (Jupyter/OctoPrint pattern) — RECOMMENDED for Windows/macOS. Proven cv2/numpy hooks; onedir (not onefile) for faster startup and easier on-venue debugging. Built per-OS.
2. **pywebview native window** — rejected as the primary shell: near-zero cost but makes the frontend desktop-bound. Keeping the frontend purely browser-served preserves the Chromebook thin-client path (below). Could be added later without architecture change.
3. **Electron / Tauri + Python sidecar** — rejected: adds Node/Rust toolchains and a second runtime for no functional gain.
4. **Nuitka** — rejected vs PyInstaller: slower builds, fewer prebuilt hooks for cv2/numpy.

### Chromebook contingency (flag prominently in doc)

A PyInstaller executable cannot run on ChromeOS; school-managed Chromebooks often have Linux (Crostini) disabled. But because the recommended frontend is pure-browser, a **hub deployment** stays possible: the Python backend runs on one bridging device (instructor laptop or Raspberry Pi joined to the Tello's WiFi), and Chromebooks connect to it as thin clients over a second network interface/LAN. The doc should list what must be confirmed with schools/organisers: whether Chromebooks are actually required, whether Crostini is enabled, and whether a bridge device per team is acceptable.

### Backend (confirming the requirements doc's leaning)

- Single Python process: FastAPI (or aiohttp) serving static assets + WebSocket; djitellopy for UDP control; OpenCV detection; **video preview streamed as JPEG frames over the WebSocket with detection overlay drawn server-side** (confirmed requirement).
- Browsers cannot open UDP sockets, so every browser-based frontend needs this local bridge — the requirements doc's reasoning is correct.

### Answers to §3.3 "still needs research" bullets

- jupyterlab-blockly customizability: possible but not worth it (see B).
- Cross-platform packaging: PyInstaller per-OS builds for Win/macOS; Chromebook needs the hub deployment instead.
- Single bundle vs separate components: single bundle (one process serves everything).
- Offline asset bundling: fully achievable — vendor Blockly + all JS/CSS/fonts into the served static directory; no CDN references.

## Recommended architecture

Custom Blockly web frontend + single local Python service (FastAPI + djitellopy + OpenCV, WebSocket) + PyInstaller onedir bundle auto-opening the browser; all assets vendored for offline use; hub deployment as the Chromebook fallback.

## Implementation steps

1. Create `docs/architecture/platform-options.md` with the sections above: constraint analysis → frontend options table → delivery options table → recommended architecture (mermaid diagram) → Chromebook contingency → items to confirm with organisers/schools.
2. Update `README.md` with a one-paragraph project summary linking to the doc.

## Verification

- Doc renders cleanly (mermaid diagram valid), every option in §3.3 of the requirements is addressed, all four "still needs research" bullets have answers or explicit confirm-with-organisers items, and sources are linked.

## Sources

- [jupyterlab-blockly docs — extending with custom blocks](https://jupyterlab-blockly.readthedocs.io/en/latest/other_extensions.html)
- [QuantStack/jupyterlab-blockly](https://github.com/QuantStack/jupyterlab-blockly)
- [Blockly transfer to Raspberry Pi Foundation (Nov 2025)](https://developers.google.com/blockly)
- [DJITelloPy](https://github.com/damiafuentes/DJITelloPy)
- [DroneBlocks curriculum](https://learn.droneblocks.io/p/introduction-to-tello-edu-drone-programming-with-droneblocks)
- [BIPES project](http://www.bipes.net.br/source.html)
- [pywebview vs Electron comparison](https://johal.in/pywebview-python-tiny-electron-cef-alternative-cross-platform-2025/)
- [Tauri overview](https://en.wikipedia.org/wiki/Tauri_(software_framework))