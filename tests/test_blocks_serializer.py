"""Runs the node test suite for comp1/frontend/blocks.js.

The serializer is half of the wire-format contract with protocol.py, so it needs tests — but
Blockly is a browser bundle and this project has no npm toolchain by design. So the tests feed
duck-typed fake blocks through the serializer under plain node. See tests/js/blocks.test.js.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
JS_DIR = ROOT / "tests" / "js"


def test_blocks_serializer_js():
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    tests = sorted(JS_DIR.glob("*.test.js"))
    assert tests, "no node tests found in tests/js"
    proc = subprocess.run(
        [node, "--test", *[str(t) for t in tests]],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
