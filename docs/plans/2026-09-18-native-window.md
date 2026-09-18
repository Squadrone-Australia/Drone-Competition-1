# Native window (pywebview)

**Status:** Implemented · **Date:** 2026-09-18 · Supersedes the ⏸ Deferred row in
[../architecture/platform-options.md](../architecture/platform-options.md) §3.

## Why

The packaged build had no window of its own. It started the server and poked
`webbrowser.open` at `http://localhost:8765`, so a student's front door was a tab in whatever
browser the machine defaulted to — address bar, bookmarks, other tabs, and no relationship
between "close the window" and "close the program". A good deal of the lifecycle machinery
existed to paper over that gap: the 30-second idle shutdown, the Close program button, the
second-launch message box, three README troubleshooting entries.

`platform-options.md:68` deferred pywebview rather than rejecting it — *"near-zero extra
weight and more kiosk-like … can be added later with no architecture change."* This is that
change, and the "no architecture change" clause is what shapes it: the window is a **front
door**, not a host. It loads the same URL any browser would, over the same WebSocket, and
everything underneath is untouched.

Electron and Tauri stay rejected for the reasons already recorded at `platform-options.md:69`.

## What was built

### The front door is chosen, not assumed

`_window_wanted()` in `comp1/__main__.py`, in order: `--no-browser` → nothing opens;
`--no-window` → system browser; `window.usable()` → native window; otherwise → system browser.

`window.usable()` proves a window is possible rather than assuming it. It asks pywebview which
renderer it would actually pick (`webview.guilib.initialize().renderer`) and refuses `mshtml`,
which is pywebview's silent fallback on a Windows machine with no WebView2 runtime and is
Internet Explorer — Blockly will not lay out in it and three.js will not draw at all. Every
failure, including an import that does not resolve at all, is an ordinary browser launch. The
student sees a different window style and nothing else.

### uvicorn moves to a worker thread

`webview.start()` must own the main thread. uvicorn only prefers it: its `capture_signals`
stands aside off the main thread by design, so the serve loop behind the window is
byte-for-byte the one the browser path runs. `_serve_behind_the_window()` starts the server
thread, waits for `server.started` before creating the window (WebView2 shows its own error
page for a refused port and never retries), and on the way out sets `should_exit` and joins.

Only the native path inverts. `--no-browser` and the browser fallback still call
`server.run()` on the main thread, unchanged — which is also what keeps `tests/test_main.py`'s
`FakeServer` seam valid.

Each direction of shutdown has exactly one path:

```
window closed  ->  webview.start() returns  ->  should_exit = True  ->  thread joins
program quits  ->  request_shutdown()       ->  native.close()      ->  start() returns
```

### Closing the window lands the drone

The `quit` message refuses while a mission is flying ("stop the mission before closing"), and
keeps doing so — refusing a button is fine. Refusing a window's X is not: it reads as a hang.
Letting it straight through is worse, because releasing an adapter never flies it, so the
lifespan shutdown would leave a real Tello in the air.

So the X gets its own path. `request_close()` in `__main__.py` holds the policy (it is where
`app.state` is); `comp1/window.py` knows only that a close can be deferred, confirmed or
refused, which is what lets it be tested on a machine with no pywebview.

| situation | what happens |
|---|---|
| update installing | close immediately — Inno is already closing this process |
| mission flying | native confirm; on cancel the window stays |
| otherwise | `app.state.request_quit()`, window held open while the program winds down |
| no event loop left | close immediately, `should_exit` set directly |
| nobody answers in 20s | the window closes anyway |

`app.state.request_quit` is the only thread-safe door into the event loop from outside it. It
runs `_land_then_quit`, which stops a flying mission and waits up to `QUIT_LANDING_TIMEOUT`
for it to land before `_quit`. That is the same choice `_unattended_loop` already makes, for
the same reason: a stop brings the aircraft down through the ordinary path.

### Three pywebview defaults that had to be overridden

- **`private_mode` defaults to `True`** — nothing persists between sessions. Left alone it
  would wipe `localStorage` on every launch, silently destroying the block buffer a student
  comes back to and the saved venue HSV profiles. Set `False`, with `storage_path` at
  `paths.window_dir()` under `data_dir()`, because an update replaces the application
  directory wholesale.
- **`ALLOW_DOWNLOADS` defaults to `False`**, and a cancelled download says nothing at all —
  the calibration panel's **Download TOML** button would simply not work. Enabled, the
  edgechromium backend shows a native Save dialog.
- **`pagehide` does not reliably fire** on a window that is destroyed rather than navigated
  away from, so up to a second of the student's last edits would be lost on every close. The
  fix is in the page and helps the browser build too: `app.js` now flushes the buffer when
  the server says `quitting`, which every close passes through. `window.py` also runs a
  best-effort `run_js` flush as it goes, for a socket that died first.

### Idle timeout: unchanged, narrower job

The countdown still arms behind a native window. It cannot fire spuriously — an open window,
minimized or behind another, keeps its WebSocket — and it is the only thing that would ever
notice a renderer that has crashed behind a frame still looking like a program.

## Deliberately not done

- **No WebView2 prerequisite check in the installer.** `comp1.iss` is
  `PrivilegesRequired=lowest`, the runtime ships with Windows 11 and with current Edge, and a
  machine without it falls back to the browser with nothing broken.
- **No `sys.platform` branches in `window.py`.** The dependency is Windows-marked because
  Windows is the only platform with a shipping installer and because pywebview's Linux
  backends need GTK/Qt system packages pip cannot supply. The code is platform-agnostic, so a
  macOS or Linux checkout that installs pywebview by hand gets the window for free.
- **Browser-owned keys (F5, devtools, Ctrl+F) are gone** inside the window. The escape hatch
  is `http://localhost:8765` in a real browser, which reaches the same program and is already
  the documented recovery path.

## Cutover note

The window and a browser are separate storage origins. A student's existing block buffer and
saved vision profiles live in the browser's `localStorage` and do not migrate; `restore()`
returns `"none"`, so the first launch after upgrading looks like a first run. One-time, worth
a line in the release notes. The *applied* HSV calibration is unaffected — it is persisted
server-side in `settings.json`.

## Verification

```bash
python -m pytest -q          # 533 tests
python -m ruff check .
node --test tests/js         # 71 tests
python -m comp1              # Windows: a window of its own
python -m comp1 --no-window  # unchanged browser behaviour
python -m comp1 --no-browser # nothing opens
```

Manual, on Windows, still owed:

1. Close the window → process exits (nothing left in Task Manager).
2. Edit blocks, close within a second, reopen → the blocks come back.
3. Sim mission running, close the window → confirm → drone lands → process exits.
4. Close program, drone switch and Update all still show a working confirm dialog.
5. Calibration → **Download TOML** → a Save dialog appears and writes the file.
6. `.\build.ps1` → install → the shortcut opens the window, and the in-app updater still
   completes a version-to-version upgrade.
7. A machine with no WebView2 runtime → the browser opens instead, with nothing said to the
   student.
