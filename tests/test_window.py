"""The native window, and its refusal to ever be the reason nothing opens.

Every test here runs on a machine with no pywebview — CI, a Linux checkout, the
Chromebook hub — because that is exactly the machine whose behaviour matters
most: the one that has to fall back to the browser without the student noticing.
"""

import time

import pytest

from comp1 import window as win


def wait_for(predicate, timeout=2.0):
    """The close path hands work to a timer thread, so the test simply waits."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def fire(self, *args):
        return [handler(*args) for handler in self.handlers]


class FakeLoaded:
    def __init__(self, loaded=True):
        self.loaded = loaded

    def is_set(self):
        return self.loaded


class FakeEvents:
    def __init__(self):
        self.closing = FakeEvent()
        self.shown = FakeEvent()
        self.loaded = FakeLoaded()


class FakeWindow:
    def __init__(self, title, url, **kwargs):
        self.title = title
        self.url = url
        self.kwargs = kwargs
        self.events = FakeEvents()
        self.destroyed = 0
        self.scripts = []
        self.dialogs = []
        self.answer = True

    def create_confirmation_dialog(self, title, message):
        self.dialogs.append((title, message))
        return self.answer

    def run_js(self, code):
        self.scripts.append(code)

    def destroy(self):
        self.destroyed += 1


class FakeWebview:
    """Stands in for the pywebview module, which CI does not have."""

    def __init__(self):
        self.settings = {}
        self.started = None
        self.window = None

    def create_window(self, title, url, **kwargs):
        self.window = FakeWindow(title, url, **kwargs)
        return self.window

    def start(self, **kwargs):
        self.started = kwargs


class Answer(list):
    """Records the confirm callable handed to on_close, and what to answer."""

    reply = None


@pytest.fixture(autouse=True)
def quick_settle(monkeypatch):
    # The real settle is a tenth of a second of politeness towards the page.
    # Waiting it out in every test here is a fifth of the suite's runtime.
    monkeypatch.setattr(win, "CLOSE_SETTLE_S", 0.01)


@pytest.fixture
def opened(tmp_path):
    """A window that has been shown, plus what its close handler was asked."""
    calls = Answer()
    calls.reply = win.CLOSE_NOW

    def on_close(confirm):
        calls.append(confirm)
        return calls.reply

    fake = FakeWebview()
    native = win.NativeWindow(
        "http://localhost:8765/",
        on_close=on_close,
        storage_path=tmp_path,
        webview=fake,
    )
    native.open()
    return native, fake, calls


def test_a_machine_without_pywebview_uses_the_browser():
    """The whole point of the fallback: no window is never an error."""

    def missing():
        raise ImportError("No module named 'webview'")

    assert win.usable(probe=missing) is False


def test_the_deprecated_ie_renderer_is_refused():
    # mshtml is Internet Explorer. A window that cannot draw the program is
    # worse than a browser tab that can.
    assert win.usable(probe=lambda: "mshtml") is False
    assert win.usable(probe=lambda: "edgechromium") is True
    assert win.usable(probe=lambda: "gtkwebkit2") is True


def test_a_probe_that_explodes_is_not_a_window():
    def boom():
        raise RuntimeError("no GUI toolkit of any kind")

    assert win.usable(probe=boom) is False


def test_a_probe_that_will_not_name_its_engine_is_refused():
    """Prove it or fall back — an unnamed renderer is an unknown one."""
    assert win.usable(probe=lambda: None) is False


def test_the_window_loads_the_server_url_not_a_file(opened):
    # The page builds its socket from location.host, so a file:// URL or an
    # inline HTML string is a window in which nothing ever connects.
    _, fake, _ = opened
    assert fake.window.url.startswith("http://")


def test_downloads_are_allowed_so_calibration_can_be_saved(opened):
    # pywebview cancels downloads by default, and says nothing when it does:
    # the "Download TOML" button would simply not work.
    _, fake, _ = opened
    assert fake.settings["ALLOW_DOWNLOADS"] is True


def test_the_page_can_be_zoomed(opened):
    # pywebview's default injects a script that swallows Ctrl+wheel and pinch.
    _, fake, _ = opened
    assert fake.window.kwargs["zoomable"] is True


# --- "Interface size": page zoom on the WebView2 control -----------------------


class FakeWebView2:
    """Strict like the real control: any member touched off the UI thread
    throws (InvalidOperationException in .NET), reads included."""

    on_ui_thread = False

    def __init__(self):
        self._zoom = 1.0

    def _check(self):
        if not self.on_ui_thread:
            raise RuntimeError(
                "CoreWebView2Controller members can only be accessed from the UI thread."
            )

    @property
    def ZoomFactor(self):  # noqa: N802 — the .NET name
        self._check()
        return self._zoom

    @ZoomFactor.setter
    def ZoomFactor(self, value):  # noqa: N802
        self._check()
        self._zoom = value


class FakeForm:
    def __init__(self):
        self.webview = FakeWebView2()


@pytest.fixture
def zoomable(opened, monkeypatch):
    """A shown window whose native form hosts a (fake) WebView2 control."""
    native, fake, _ = opened
    fake.window.native = FakeForm()
    ran = []

    def on_ui_thread(form, fn):
        ran.append(form)
        form.webview.on_ui_thread = True
        try:
            fn()
        finally:
            form.webview.on_ui_thread = False

    monkeypatch.setattr(win, "_on_ui_thread", on_ui_thread)
    return native, fake.window.native.webview, ran


def test_the_page_can_ask_for_its_size_and_only_that(opened):
    # pywebview publishes every public attribute of js_api to the page. One
    # method, and no route from it to the window object itself.
    _, fake, _ = opened
    api = fake.window.kwargs["js_api"]
    assert [n for n in dir(api) if not n.startswith("_")] == ["set_zoom"]


def test_set_zoom_sets_the_controls_zoom_on_the_ui_thread(zoomable, opened):
    native, view, ran = zoomable
    _, fake, _ = opened
    assert fake.window.kwargs["js_api"].set_zoom(1.25) is True
    assert view._zoom == 1.25
    assert ran == [fake.window.native]


def test_set_zoom_clamps_whatever_the_page_sends(zoomable):
    native, view, _ = zoomable
    assert native.set_zoom(9) is True and view._zoom == win.ZOOM_RANGE[1]
    assert native.set_zoom(0.01) is True and view._zoom == win.ZOOM_RANGE[0]
    for junk in ("big", None, float("nan"), float("inf"), [1.25]):
        assert native.set_zoom(junk) is False
    assert view._zoom == win.ZOOM_RANGE[0]  # untouched by the junk


def test_set_zoom_without_a_webview2_control_is_a_quiet_no(opened):
    # Any other platform, or a window not shown yet: nothing to set, no raise.
    native, fake, _ = opened
    assert native.set_zoom(1.25) is False
    fake.window.native = object()
    assert native.set_zoom(1.25) is False


def test_set_zoom_never_raises_out_of_the_ui_thread(zoomable, monkeypatch):
    native, _, _ = zoomable

    def boom(form, fn):
        raise RuntimeError("the form is being disposed")

    monkeypatch.setattr(win, "_on_ui_thread", boom)
    assert native.set_zoom(1.25) is False


def test_the_window_keeps_its_storage_between_launches(opened, tmp_path):
    """private_mode is pywebview's default and would discard every session."""
    _, fake, _ = opened
    assert fake.started["private_mode"] is False
    assert fake.started["storage_path"] == str(tmp_path)


def test_closing_the_window_asks_the_program_to_quit(opened):
    native, fake, calls = opened
    calls.reply = win.CLOSE_WAIT

    assert fake.window.events.closing.fire() == [False]  # the close is deferred
    assert len(calls) == 1
    assert fake.window.destroyed == 0  # ... and the window is still there

    native.close()
    assert wait_for(lambda: fake.window.destroyed == 1)


def test_a_student_can_change_their_mind(opened):
    native, fake, calls = opened
    calls.reply = win.CLOSE_STAY

    assert fake.window.events.closing.fire() == [False]
    assert fake.window.destroyed == 0
    # Still answering: "stay" must not latch the window into a closing state.
    calls.reply = win.CLOSE_NOW
    assert fake.window.events.closing.fire() == [True]


def test_a_close_nobody_answers_still_closes(opened, monkeypatch):
    """No server fault may produce a window whose X does nothing."""
    monkeypatch.setattr(win, "CLOSE_GRACE_S", 0.02)
    _, fake, calls = opened
    calls.reply = win.CLOSE_WAIT

    fake.window.events.closing.fire()

    assert wait_for(lambda: fake.window.destroyed == 1)


def test_clicking_close_twice_closes_now(opened):
    _, fake, calls = opened
    calls.reply = win.CLOSE_WAIT

    assert fake.window.events.closing.fire() == [False]
    assert fake.window.events.closing.fire() == [True]


def test_the_program_can_close_the_window(opened):
    native, fake, _ = opened

    native.close()

    assert wait_for(lambda: fake.window.destroyed == 1)
    # The last edit of a session is written here, not by `pagehide`: a native
    # window is destroyed rather than navigated away from.
    assert fake.window.scripts == [win.FLUSH_JS]


def test_a_page_that_never_loaded_is_not_asked_to_flush(opened):
    # run_js on an unloaded page blocks for pywebview's whole before-load
    # timeout, which would turn a failed launch into a hung one.
    native, fake, _ = opened
    fake.window.events.loaded.loaded = False

    native.close()

    assert wait_for(lambda: fake.window.destroyed == 1)
    assert fake.window.scripts == []


def test_closing_twice_destroys_once(opened):
    native, fake, _ = opened

    native.close()
    native.close()

    assert wait_for(lambda: fake.window.destroyed == 1)
    assert not wait_for(lambda: fake.window.destroyed > 1, timeout=0.2)


def test_our_own_destroy_is_not_routed_back_through_quit(opened):
    """Closing the program must not ask the program whether it may close."""
    native, fake, calls = opened

    native.close()
    assert wait_for(lambda: fake.window.destroyed == 1)

    assert fake.window.events.closing.fire() == [True]
    assert calls == []


def test_a_close_before_the_window_appears_still_closes_it(opened):
    # destroy() is a no-op until the form exists, so a close landing in that gap
    # would otherwise leave a window nobody asked for.
    native, fake, _ = opened
    native.close()
    assert wait_for(lambda: fake.window.destroyed == 1)
    fake.window.destroyed = 0

    fake.window.events.shown.fire()

    assert fake.window.destroyed == 1


def test_a_broken_close_handler_does_not_trap_the_student(opened):
    native, fake, _ = opened

    def explode(confirm):
        raise RuntimeError("policy went wrong")

    native._on_close = explode

    assert fake.window.events.closing.fire() == [True]


def test_the_handler_is_given_a_way_to_ask_the_student(opened):
    """Landing a real Tello because somebody brushed the X is not acceptable."""
    native, fake, _ = opened
    asked = []

    def on_close(confirm):
        asked.append(confirm("Close?", "A mission is flying."))
        return win.CLOSE_STAY

    native._on_close = on_close
    fake.window.answer = False

    assert fake.window.events.closing.fire() == [False]
    assert asked == [False]
    assert fake.window.dialogs == [("Close?", "A mission is flying.")]


def test_close_never_blocks_its_caller(opened):
    """It is called from the server's event loop, which must not stop."""
    native, _, _ = opened
    started = time.time()

    native.close()

    assert time.time() - started < 0.5
