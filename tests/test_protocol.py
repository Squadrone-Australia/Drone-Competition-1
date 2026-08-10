import pytest
from pydantic import ValidationError

from comp1.protocol import Program


def make(blocks, version=1):
    return {"version": version, "blocks": blocks}


def test_valid_program_parses():
    p = Program.model_validate(
        make(
            [
                {"id": "a", "op": "takeoff"},
                {"id": "b", "op": "move", "dir": "forward", "cm": 50},
                {
                    "id": "c",
                    "op": "repeat_until",
                    "cond": {"sensor": "marker_visible"},
                    "body": [{"id": "d", "op": "rotate", "dir": "cw", "deg": 30}],
                },
                {"id": "e", "op": "land"},
            ]
        )
    )
    assert p.blocks[2].body[0].op == "rotate"


def test_move_requires_valid_distance():
    with pytest.raises(ValidationError):
        Program.model_validate(
            make([{"id": "a", "op": "move", "dir": "forward", "cm": 5}])
        )


def test_unknown_op_rejected():
    with pytest.raises(ValidationError):
        Program.model_validate(make([{"id": "a", "op": "goto_xy", "x": 1, "y": 2}]))


def test_v2_program_with_value_nodes_parses():
    p = Program.model_validate(
        make(
            [
                {"id": "a", "op": "set_var", "name": "sweep", "value": 45},
                {
                    "id": "b",
                    "op": "while",
                    "cond": {
                        "kind": "unop",
                        "op": "not",
                        "operand": {"kind": "sensor", "sensor": "target_visible"},
                    },
                    "body": [
                        {
                            "id": "c",
                            "op": "rotate",
                            "dir": "cw",
                            "deg": {"kind": "var", "name": "sweep"},
                        }
                    ],
                },
                {"id": "d", "op": "wait", "seconds": 2},
            ],
            version=2,
        )
    )
    assert p.blocks[1].body[0].deg.name == "sweep"


def test_computed_out_of_range_passes_parsing_and_is_clamped_at_runtime():
    # only a literal can be range-checked statically; this one must survive to the interpreter
    Program.model_validate(
        make(
            [
                {
                    "id": "a",
                    "op": "move",
                    "dir": "forward",
                    "cm": {"kind": "binop", "op": "*", "left": 900, "right": 1},
                }
            ],
            version=2,
        )
    )


@pytest.mark.parametrize(
    "block",
    [
        {"id": "a", "op": "set_var", "value": 1},  # no name
        {"id": "a", "op": "set_var", "name": "x"},  # no value
        {"id": "a", "op": "wait"},  # no seconds
        {"id": "a", "op": "while", "body": []},  # no cond
        {"id": "a", "op": "wait", "seconds": 60},  # literal out of range
        {"id": "a", "op": "rotate", "dir": "cw", "deg": 0},
    ],
)
def test_new_ops_validate_their_params(block):
    with pytest.raises(ValidationError):
        Program.model_validate(make([block], version=2))


def test_unknown_value_kind_rejected():
    with pytest.raises(ValidationError):
        Program.model_validate(
            make([{"id": "a", "op": "if", "cond": {"kind": "lambda"}}], version=2)
        )


@pytest.mark.parametrize("op", ["break", "continue"])
def test_loop_control_must_be_inside_a_loop(op):
    with pytest.raises(ValidationError, match="must be inside a loop"):
        Program.model_validate(make([{"id": "control", "op": op}], version=2))


def test_loop_control_inside_nested_if_is_valid():
    program = Program.model_validate(
        make(
            [
                {
                    "id": "loop",
                    "op": "repeat_n",
                    "n": 2,
                    "body": [
                        {
                            "id": "choice",
                            "op": "if",
                            "cond": 1,
                            "body": [{"id": "stop", "op": "break"}],
                            "else_body": [{"id": "skip", "op": "continue"}],
                        }
                    ],
                }
            ],
            version=2,
        )
    )
    assert program.blocks[0].body[0].body[0].op == "break"
