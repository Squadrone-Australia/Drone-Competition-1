"""A window of our own, instead of a tab in whatever browser the machine has.

Three rules hold this module together, and every awkward-looking thing in it is
one of them being obeyed.

**The window is a front door, never a dependency.** pywebview missing, no GUI
toolkit, no WebView2 runtime, a probe that raises something nobody anticipated —
every one of those is a browser launch, which is the same program at the same
URL and is what every copy did before this file existed. Nothing here is allowed
to be the reason a student cannot fly. That is why :func:`usable` swallows
everything and why the caller treats ``False`` as ordinary.

**The window loads an http:// URL, never a file and never an HTML string.** The
page builds its WebSocket from ``location.host`` and the server checks ``Origin``
against ``Host``; served any other way the socket simply never opens, and the
failure looks like "the program is broken" rather than "the window is wrong".

**Nothing here knows what a drone is.** The close button asks a callback what
should happen and does as it is told. Whether a mission is flying, whether it
must land first, whether an update is mid-install — all of that is policy, it
lives in ``comp1/__main__.py`` where the app object is, and keeping it out of
here is what makes this file testable on a machine that has no pywebview at all.
"""

import importlib
import logging
import threading

from .paths import window_dir

log = logging.getLogger(__name__)

WINDOW_TITLE = "Squadrone Drone Coder"
#: Big enough that the Blockly workspace and the arena panel are both usable
#: without dragging anything: the side panel alone is 340px of the layout.
WINDOW_SIZE = (1360, 900)
MIN_WINDOW_SIZE = (1024, 700)
#: --bg from style.css. Without it the window paints white for the moment before
#: the page loads, which on a dark UI reads as a flash of something going wrong.
BACKGROUND = "#0b1017"

#: Renderers we will not accept. ``mshtml`` is pywebview's silent fallback on a
#: Windows machine with no WebView2 runtime, and it is Internet Explorer:
#: Blockly will not lay out in it and three.js will not draw at all. Falling
#: back to it would hand a student a window containing a broken program, where
#: falling back to the browser hands them a working one. Named rather than
#: detected by platform, because pywebview is the thing that knows what the
#: platform means here.
REFUSED_RENDERERS = frozenset({"mshtml"})

#: What :func:`NativeWindow` does about a close, as answered by ``on_close``.
CLOSE_NOW = "close"  # nothing to wait for — destroy the window
CLOSE_WAIT = "wait"  # the program is closing itself; hold the window open
CLOSE_STAY = "stay"  # the student changed their mind

#: How long a deferred close waits for the program to close itself before the
#: window gives up and shuts anyway. Longer than a landing, shorter than a
#: student's patience: a window whose X does nothing reads as a hung program,
#: and no server fault may be allowed to produce one.
CLOSE_GRACE_S = 20.0
#: The page is told "quitting" immediately before the window is destroyed. This
#: is how long it gets to write its block buffer. A settle rather than a
#: handshake, because the cost of being wrong is one lost edit, not a hang.
CLOSE_SETTLE_S = 0.15
FLUSH_JS = "window.COMP1_FLUSH && window.COMP1_FLUSH()"


def _probe() -> str | None:
    """Ask pywebview which engine it would actually use on this machine.

    ``from webview.guilib import initialize``, never ``webview.guilib.initialize``:
    the package rebinds its own ``guilib`` attribute to ``None`` at import time
    and only fills it in inside ``start()``, so the attribute path raises on
    ``None``. ``initialize()`` is exactly what ``start()`` calls, and the
    ``setup_app()`` behind it is idempotent, so probing here and starting later
    is not a double initialisation.
    """
    from webview.guilib import initialize

    return getattr(initialize(), "renderer", None)


def usable(probe=None) -> bool:
    """Can this machine give us a window worth having?

    Prove it or use the browser. The probe is injectable so the decision can be
    tested on a machine — CI, a Linux checkout, a Chromebook hub — where the
    honest answer is "no" and the import would not even resolve.

    Worth knowing, and not worth working around: on Windows with no WebView2,
    ``initialize()`` imports ``webview.platforms.winforms``, which at import
    time writes a browser-emulation value under ``HKCU`` for this executable,
    before we get the chance to refuse the renderer it found. It is harmless, it
    is not the application directory, and it only happens on machines that were
    never going to get a window. The alternatives are a registry probe (a
    platform branch this module is built to avoid) or a subprocess probe (a cost
    on every single launch).
    """
    try:
        renderer = (probe or _probe)()
    except Exception as exc:  # noqa: BLE001 — ImportError, WebViewException, any
        log.info("no native window (%s) — opening the browser instead", exc)
        return False
    if renderer is None or renderer in REFUSED_RENDERERS:
        log.warning("refusing the %r renderer — opening the browser instead", renderer)
        return False
    log.info("native window renderer: %s", renderer)
    return True


class NativeWindow:
    """The window, and the two-way close protocol that goes with it.

    ``on_close`` is called on the GUI thread when the student closes the window,
    and is handed a ``confirm(title, message) -> bool`` it may use to ask them
    something first. It answers with one of :data:`CLOSE_NOW`,
    :data:`CLOSE_WAIT` or :data:`CLOSE_STAY`.
    """

    def __init__(self, url, *, on_close, storage_path=None, webview=None):
        self._url = url
        self._on_close = on_close
        self._storage_path = storage_path
        # Injected in tests. Imported late otherwise, because merely importing
        # pywebview initialises a GUI toolkit, and the browser path must not pay
        # for a window it is not going to open.
        self._webview = webview
        self._window = None
        self._lock = threading.Lock()
        self._asked = False
        self._destroying = False
        self._deadline = None

    def open(self) -> None:
        """Show the window and block until it is gone. Main thread only."""
        webview = self._webview or importlib.import_module("webview")
        # The calibration panel hands vision_config.toml out through a synthetic
        # <a download>. pywebview cancels downloads by default, and a cancelled
        # one says nothing at all — the button would simply not work, which is
        # the worst of the available failures.
        webview.settings["ALLOW_DOWNLOADS"] = True
        self._window = webview.create_window(
            WINDOW_TITLE,
            self._url,
            width=WINDOW_SIZE[0],
            height=WINDOW_SIZE[1],
            min_size=MIN_WINDOW_SIZE,
            background_color=BACKGROUND,
        )
        self._window.events.closing += self._on_closing
        self._window.events.shown += self._on_shown
        # private_mode is pywebview's default, and it would wipe localStorage on
        # every launch: the block buffer a student comes back to and the saved
        # vision profiles both live there. storage_path keeps them under
        # data_dir(), which survives the update that replaces the program.
        webview.start(
            private_mode=False,
            storage_path=str(self._storage_path or window_dir()),
        )

    def close(self) -> None:
        """Destroy the window. Safe from any thread, and safe to repeat.

        Called by the program's own shutdown, so it must not block the caller —
        that caller is usually the server's event loop, and the flush below
        cannot run on the GUI thread anyway.
        """
        with self._lock:
            if self._destroying:
                return
            self._destroying = True
        if self._deadline is not None:
            self._deadline.cancel()
        threading.Timer(CLOSE_SETTLE_S, self._destroy).start()

    def _on_closing(self) -> bool:
        """The window is closing. Return False to stop it. Runs on the GUI thread."""
        if self._destroying:
            return True  # our own close(), or the second half of one
        if self._asked:
            # Clicked again while we were waiting for the program to close
            # itself. Twice is unambiguous: go now.
            self._destroying = True
            return True
        try:
            answer = self._on_close(self._confirm)
        except Exception:  # noqa: BLE001 — a broken policy must not trap anyone
            log.exception("the close handler failed — closing anyway")
            answer = CLOSE_NOW
        if answer == CLOSE_STAY:
            return False
        if answer != CLOSE_WAIT:
            self._destroying = True
            return True
        self._asked = True
        # Deferred, but never forever. A server that never answers must not
        # leave a window that cannot be shut.
        self._deadline = threading.Timer(CLOSE_GRACE_S, self._give_up)
        self._deadline.daemon = True
        self._deadline.start()
        return False

    def _on_shown(self) -> None:
        # close() before the window exists cannot destroy anything — pywebview's
        # destroy is a no-op until the form is up. Catch it on the way in.
        if self._destroying:
            self._destroy()

    def _confirm(self, title, message) -> bool:
        window = self._window
        if window is None:
            return True
        return bool(window.create_confirmation_dialog(title, message))

    def _give_up(self) -> None:
        log.warning("nothing closed the program in time — closing the window anyway")
        self.close()

    def _destroy(self) -> None:
        window = self._window
        if window is None:
            return
        # Best effort, and deliberately after the "quitting" message has had its
        # own chance to land: this is the one that catches a socket that died
        # before the server could say anything. Never from the GUI thread —
        # run_js waits on a semaphore the GUI thread is the one that releases —
        # and never before the page has loaded, or it blocks for pywebview's
        # full before-load timeout on a page that is never going to arrive.
        try:
            if window.events.loaded.is_set():
                window.run_js(FLUSH_JS)
        except Exception:  # noqa: BLE001 — a lost edit is not worth a stuck window
            log.debug("could not flush the workspace buffer", exc_info=True)
        try:
            window.destroy()
        except Exception:  # noqa: BLE001
            log.exception("could not destroy the window")
