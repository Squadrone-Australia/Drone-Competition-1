import pytest
from pydantic import ValidationError
from comp1.protocol import Program


def make(blocks):
    return {"version": 1, "blocks": blocks}


def test_valid_program_parses():
    p = Program.model_validate(make([
        {"id": "a", "op": "takeoff"},
        {"id": "b", "op": "move", "dir": "forward", "cm": 50},
        {"id": "c", "op": "repeat_until",
         "cond": {"sensor": "marker_visible"},
         "body": [{"id": "d", "op": "rotate", "dir": "cw", "deg": 30}]},
        {"id": "e", "op": "land"},
    ]))
    assert p.blocks[2].body[0].op == "rotate"


def test_move_requires_valid_distance():
    with pytest.raises(ValidationError):
        Program.model_validate(make([{"id": "a", "op": "move", "dir": "forward", "cm": 5}]))


def test_unknown_op_rejected():
    with pytest.raises(ValidationError):
        Program.model_validate(make([{"id": "a", "op": "goto_xy", "x": 1, "y": 2}]))
