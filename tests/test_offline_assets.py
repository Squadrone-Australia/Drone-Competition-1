import re
from pathlib import Path

FRONTEND = Path(__file__).parent.parent / "comp1" / "frontend"


def test_no_external_urls_in_frontend():
    pattern = re.compile(rb"https?://(?!localhost)")
    offenders = []
    for f in FRONTEND.rglob("*"):
        if f.suffix in {".html", ".css", ".js"} and "vendor" not in f.parts:
            if pattern.search(f.read_bytes()):
                offenders.append(str(f))
    assert not offenders, f"external URLs found (breaks offline use): {offenders}"


def test_blockly_is_vendored():
    assert (FRONTEND / "vendor" / "blockly.min.js").stat().st_size > 500_000


def test_three_js_is_vendored():
    # three.module.min.js imports three.core.min.js relatively; OrbitControls
    # resolves through the import map in index.html. All three must be present or
    # the 3D view silently fails to load with no network to fall back on.
    for name in ("three.module.min.js", "three.core.min.js", "OrbitControls.js"):
        assert (FRONTEND / "vendor" / name).stat().st_size > 10_000, name


def test_the_import_map_points_at_the_vendored_three():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert '"three": "./vendor/three.module.min.js"' in html


def test_block_help_is_visible_and_frontend_scripts_are_versioned():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    assert 'id="block-help"' in html
    assert 'src="blocks.js?v=' in html
    assert 'src="app.js?v=' in html


def test_frontend_offers_a_reversible_real_tello_switch():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'id="drone-mode"' in html
    assert 'id="use-tello"' in html
    assert '"Use Simulator"' in app_js
    assert 'useSimulator ? "sim" : "tello"' in app_js
    assert "window.confirm" in app_js


def test_frontend_has_translation_and_execution_debug_views():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    css = (FRONTEND / "style.css").read_text(encoding="utf-8")
    assert 'id="debug-program"' in html
    assert 'id="debug-python"' in html
    assert 'id="debug-trace"' in html
    assert 'id="debug-toggle"' in html and 'id="debug-panes"' in html
    assert html.index('id="debug-panel"') < html.index("<aside>")
    assert "Only blocks connected beneath" in html
    assert "COMP1.programToPython(program)" in app_js
    assert 'typeof event.isUiEvent === "function"' in app_js
    assert "Blocks are present, but none is connected" in app_js
    assert 'classList.toggle("open")' in app_js
    assert "#debug-panes" in css and "scrollbar-gutter: stable" in css
    assert 'msg.type === "debug_program"' in app_js
    assert 'msg.type === "execution"' in app_js


def test_auto_calibration_is_reachable_from_the_dialog():
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    js = (FRONTEND / "calibration.js").read_text(encoding="utf-8")
    assert 'id="vision-auto"' in html
    assert '"vision_auto"' in js
    assert "message.roi" in js  # the sampled region is drawn back
    assert 'src="calibration.js?v=' in html
