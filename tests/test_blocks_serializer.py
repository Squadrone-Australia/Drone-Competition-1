"""Runs the node test suite for comp1/frontend/blocks.js.

The serializer is half of the wire-format contract with protocol.py, so it needs tests — but
Blockly is a browser bundle and this project has no npm toolchain by design. So the tests feed
duck-typed fake blocks through the serializer under plain node. See tests/js/blocks.test.js.
"""

import ast
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
JS_DIR = ROOT / "tests" / "js"


def _node_or_skip():
    """The node binary, or a skip.

    Two separate things can be missing. Node itself is optional -- a Python-only
    contributor should still get a green ``pytest -q``. So is ``npm ci``, which
    is a one-off step the README asks for and CI only runs in the js-test job:
    buffer.test.js and calibration.test.js import jsdom, and without
    node_modules they fail with MODULE_NOT_FOUND rather than a real
    disagreement about the wire format. The js-test job is the gate that always
    has both; this is a convenience for anyone running the Python suite.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not on PATH")
    if not (ROOT / "node_modules" / "jsdom").is_dir():
        pytest.skip("node_modules is not installed -- run `npm ci` for jsdom")
    return node


def test_blocks_serializer_js():
    node = _node_or_skip()
    tests = sorted(JS_DIR.glob("*.test.js"))
    assert tests, "no node tests found in tests/js"
    # node's reporter emits UTF-8 (including box-drawing characters), so the
    # decoding is pinned rather than left to the console codepage — on a
    # non-UTF-8 Windows locale the default decode raises inside subprocess's
    # reader thread and `proc.stdout` comes back as None.
    proc = subprocess.run(
        [node, "--test", *[str(t) for t in tests]],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_display_python_is_valid_python_syntax():
    node = _node_or_skip()
    program = {
        "version": 2,
        "blocks": [
            {"id": "start", "op": "takeoff"},
            {
                "id": "repeat",
                "op": "repeat_n",
                "n": 2,
                "body": [
                    {
                        "id": "if",
                        "op": "if",
                        "cond": {"kind": "sensor", "sensor": "target_visible"},
                        "body": [
                            {"id": "found", "op": "mark_found", "signal": "flip"}
                        ],
                        "else_body": [
                            {"id": "turn", "op": "rotate", "dir": "cw", "deg": 30}
                        ],
                    },
                ],
            },
            {
                "id": "set",
                "op": "set_var",
                "name": "distance read",
                "value": {
                    "kind": "binop",
                    "op": "/",
                    "left": {"kind": "sensor", "sensor": "target_distance_cm"},
                    "right": {"kind": "number", "value": 2},
                },
            },
            {"id": "finish", "op": "end_mission"},
        ],
    }
    script = (
        'const c = require("./comp1/frontend/blocks.js");'
        f"process.stdout.write(c.programToPython({json.dumps(program)}));"
    )
    proc = subprocess.run(
        [node, "-e", script],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stderr
    ast.parse(proc.stdout)
