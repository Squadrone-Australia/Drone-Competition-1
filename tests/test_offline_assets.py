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


def test_blockly_media_is_vendored_and_injected():
    # Blockly's default pathToMedia is blockly-demo.appspot.com. Left unset, the
    # trashcan/zoom sprite sheet, the drag cursors and the click sounds are all
    # network fetches, which is nothing at all on a venue's TELLO-xxxx Wi-Fi.
    app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'media: "vendor/blockly-media/"' in app_js
    assert (FRONTEND / "vendor" / "blockly-media" / "sprites.png").exists()


def test_every_media_file_blockly_asks_for_is_vendored():
    # Derived from the bundle rather than hardcoded, so a Blockly upgrade that
    # renames an asset (12.x wants sprites.png, later releases sprites.svg) or
    # adds one fails here instead of silently going back to the network.
    bundle = (FRONTEND / "vendor" / "blockly.min.js").read_bytes().decode(
        "utf-8", "replace"
    )
    wanted = set(re.findall(r"<<<PATH>>>/([\w.-]+\.\w+)", bundle))
    wanted |= set(re.findall(r"\$\{a\}([\w.-]+\.\w+)", bundle))
    wanted |= set(re.findall(r'url:\s*"([\w.-]+\.\w+)"', bundle))
    assert wanted, "no media references found - did the bundle format change?"
    media = FRONTEND / "vendor" / "blockly-media"
    missing = sorted(n for n in wanted if not (media / n).exists())
    assert not missing, f"Blockly media not vendored (breaks offline use): {missing}"


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


def test_the_workspace_is_buffered_and_restored_on_load():
    # The buffer is the only thing standing between a student and a workspace
    # thrown away by a closed tab, so the wiring is asserted here rather than
    # left to a manual check: buffer.js must be served, it must load before
    # app.js (which restores as it injects Blockly), and app.js must still seed
    # the `start` hat when a restore does not bring one - it is not in the
    # toolbox, so a workspace without it cannot be repaired by dragging.
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    app_js = (FRONTEND / "app.js").read_text(encoding="utf-8")
    assert 'src="buffer.js?v=' in html
    assert html.index('src="buffer.js') < html.index('src="app.js')
    assert "window.COMP1_BUFFER.restore(workspace)" in app_js
    assert 'workspace.getBlocksByType("start", false).length === 0' in app_js
    assert "window.COMP1_BUFFER.capture(workspace)" in app_js
    # A tab closed inside the debounce window must still write the last edit.
    assert '"pagehide"' in app_js
