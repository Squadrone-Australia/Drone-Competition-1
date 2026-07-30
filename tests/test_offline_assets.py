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
