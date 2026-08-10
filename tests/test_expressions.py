"""Value nodes, sensors, variables, and the runtime guards around them."""

import pytest
from pydantic import ValidationError

from comp1.drone.mock import MockDrone
from comp1.interpreter import MAX_EXPR_DEPTH, MAX_LOOP_ITERS, Interpreter
from comp1.protocol import Program

from .helpers import lost, seen


def prog(blocks, version=2):
    return Program.model_validate({"version": version, "blocks": blocks})


def make(det=None, drone=None):
    det = det if det is not None else lost()
    events = []
    it = Interpreter(drone or MockDrone(), lambda: det, events.append)
    return it, events


async def run(blocks, det=None, drone=None, version=2):
    it, events = make(det, drone)
    await it.run(prog(blocks, version))
    return it._drone, events, it


def ev(events, type_):
    return [e for e in events if e["type"] == type_]


def value_of(node, det=None, **vars_):
    it, events = make(det)
    it.vars.update(vars_)
    return it._eval(node), events


# --- value node kinds ---


def test_number_literal_and_bare_number_are_the_same():
    p = prog(
        [
            {"id": "a", "op": "set_var", "name": "x", "value": 7},
            {
                "id": "b",
                "op": "set_var",
                "name": "y",
                "value": {"kind": "number", "value": 7},
            },
        ]
    )
    assert p.blocks[0].value == p.blocks[1].value


def test_bare_numbers_are_lifted_inside_nested_values():
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "binop", "op": "+", "left": 2, "right": 3},
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert value_of(node)[0] == 5


def test_value_round_trips_through_json():
    src = {
        "version": 2,
        "blocks": [
            {
                "id": "a",
                "op": "move",
                "dir": "forward",
                "cm": {
                    "kind": "binop",
                    "op": "*",
                    "left": {"kind": "var", "name": "s"},
                    "right": 10,
                },
            }
        ],
    }
    once = Program.model_validate(src)
    assert Program.model_validate(once.model_dump()) == once


def test_var_read_and_unop_and_binop_evaluate():
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {
                        "kind": "binop",
                        "op": "-",
                        "left": {"kind": "unop", "op": "abs", "operand": -4},
                        "right": {
                            "kind": "unop",
                            "op": "neg",
                            "operand": {"kind": "var", "name": "s"},
                        },
                    },
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert value_of(node, s=6.0)[0] == 10  # abs(-4) - (-6)


@pytest.mark.parametrize(
    "op,left,right,want",
    [
        ("+", 3, 4, 7),
        ("-", 3, 4, -1),
        ("*", 3, 4, 12),
        ("/", 3, 4, 0.75),
        ("<", 3, 4, True),
        (">", 3, 4, False),
        ("<=", 4, 4, True),
        (">=", 3, 4, False),
        ("==", 4, 4, True),
        ("!=", 4, 4, False),
        ("and", 1, 0, False),
        ("and", 1, 2, True),
        ("or", 0, 0, False),
        ("or", 0, 3, True),
    ],
)
def test_every_binop(op, left, right, want):
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "binop", "op": op, "left": left, "right": right},
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert value_of(node)[0] == want


def test_nested_arithmetic_and_comparison():
    # (2 + 3) * 4 > 19
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {
                        "kind": "binop",
                        "op": ">",
                        "right": 19,
                        "left": {
                            "kind": "binop",
                            "op": "*",
                            "right": 4,
                            "left": {"kind": "binop", "op": "+", "left": 2, "right": 3},
                        },
                    },
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert value_of(node)[0] is True


def test_not_uses_truthiness():
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "unop", "op": "not", "operand": 0},
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert value_of(node)[0] is True


# --- sensors ---


def sensor(name, det=None):
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "sensor", "sensor": name},
                }
            ]
        )
        .blocks[0]
        .value
    )
    return value_of(node, det)[0]


def test_sensors_with_a_target_visible():
    det = seen(distance_m=2.5, bearing_deg=20.0, elevation_deg=-5.0)
    assert sensor("target_visible", det) is True
    assert sensor("target_distance_cm", det) == pytest.approx(250.0)
    assert sensor("target_bearing_deg", det) == pytest.approx(20.0)
    assert sensor("target_elevation_deg", det) == pytest.approx(-5.0)
    assert sensor("target_count", det) == 1
    assert sensor("target_position_right", det) is True
    assert sensor("target_position_left", det) is False
    assert sensor("target_position_center", det) is False


def test_sensors_without_a_target_use_the_documented_sentinels():
    assert sensor("target_visible") is False
    assert (
        sensor("target_distance_cm") == 9999.0
    )  # "far away", so naive searches keep turning
    assert sensor("target_bearing_deg") == 0.0
    assert sensor("target_elevation_deg") == 0.0
    assert sensor("target_count") == 0
    for side in ("left", "center", "right"):
        assert sensor(f"target_position_{side}") is False


async def test_distance_sentinel_keeps_a_naive_search_loop_running():
    drone, _, _ = await run(
        [
            {
                "id": "a",
                "op": "repeat_until",
                "cond": {
                    "kind": "binop",
                    "op": "<",
                    "left": {"kind": "sensor", "sensor": "target_distance_cm"},
                    "right": 120,
                },
                "body": [{"id": "b", "op": "rotate", "dir": "cw", "deg": 15}],
            }
        ]
    )
    assert drone.log.count(("rotate", "cw", 15)) == MAX_LOOP_ITERS  # never "arrived"


async def test_found_count_and_battery_sensors():
    it, _ = make()
    it.found_count = 3
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "sensor", "sensor": "found_count"},
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert it._eval(node) == 3
    node = (
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "sensor", "sensor": "battery"},
                }
            ]
        )
        .blocks[0]
        .value
    )
    assert it._eval(node) == 100.0  # MockDrone


def test_unknown_sensor_rejected_at_parse():
    with pytest.raises(ValidationError):
        prog(
            [
                {
                    "id": "a",
                    "op": "set_var",
                    "name": "x",
                    "value": {"kind": "sensor", "sensor": "gps_x"},
                }
            ]
        )


# --- variables ---


async def test_set_var_then_read_it():
    _, _, it = await run(
        [
            {"id": "a", "op": "set_var", "name": "steps", "value": 4},
            {
                "id": "b",
                "op": "set_var",
                "name": "steps",
                "value": {
                    "kind": "binop",
                    "op": "*",
                    "left": {"kind": "var", "name": "steps"},
                    "right": 2,
                },
            },
        ]
    )
    assert it.vars["steps"] == 8


async def test_variables_drive_a_move():
    drone, events, _ = await run(
        [
            {"id": "a", "op": "set_var", "name": "d", "value": 40},
            {
                "id": "b",
                "op": "move",
                "dir": "forward",
                "cm": {"kind": "var", "name": "d"},
            },
        ]
    )
    assert drone.log == [("move", "forward", 40)]
    assert ev(events, "warning") == []


async def test_unset_variable_reads_zero_with_a_warning():
    drone, events, _ = await run(
        [
            {
                "id": "a",
                "op": "move",
                "dir": "forward",
                "cm": {
                    "kind": "binop",
                    "op": "+",
                    "left": {"kind": "var", "name": "nope"},
                    "right": 30,
                },
            }
        ]
    )
    assert drone.log == [("move", "forward", 30)]
    warn = ev(events, "warning")[0]
    assert warn["blockId"] == "a" and "nope" in warn["message"]


async def test_variables_are_cleared_between_runs():
    it, events = make()
    await it.run(prog([{"id": "a", "op": "set_var", "name": "x", "value": 5}]))
    assert it.vars == {"x": 5}
    await it.run(prog([{"id": "b", "op": "land"}]))
    assert it.vars == {}


# --- truthiness ---


async def test_numbers_are_truthy_when_nonzero():
    drone, _, _ = await run(
        [
            {
                "id": "a",
                "op": "if",
                "cond": 0,
                "body": [{"id": "b", "op": "flip", "dir": "left"}],
                "else_body": [{"id": "c", "op": "flip", "dir": "right"}],
            },
            {
                "id": "d",
                "op": "if",
                "cond": -1,
                "body": [{"id": "e", "op": "flip", "dir": "forward"}],
            },
        ]
    )
    assert drone.log == [("flip", "right"), ("flip", "forward")]


async def test_bools_are_themselves():
    drone, _, _ = await run(
        [
            {
                "id": "a",
                "op": "if",
                "cond": {"kind": "sensor", "sensor": "target_visible"},
                "body": [{"id": "b", "op": "flip", "dir": "left"}],
            }
        ],
        det=seen(),
    )
    assert drone.log == [("flip", "left")]


# --- guards ---


async def test_expression_depth_cap_is_a_mission_error():
    node = {"kind": "number", "value": 1}
    for _ in range(MAX_EXPR_DEPTH + 2):
        node = {"kind": "unop", "op": "neg", "operand": node}
    _, events, _ = await run([{"id": "a", "op": "set_var", "name": "x", "value": node}])
    assert events[-1]["reason"] == "error"
    assert "nested" in events[-1]["detail"]


async def test_depth_just_under_the_cap_is_fine():
    node = {"kind": "number", "value": 1}
    for _ in range(MAX_EXPR_DEPTH - 1):
        node = {"kind": "unop", "op": "neg", "operand": node}
    _, events, it = await run(
        [{"id": "a", "op": "set_var", "name": "x", "value": node}]
    )
    assert events[-1]["reason"] == "done"
    assert abs(it.vars["x"]) == 1


async def test_division_by_zero_warns_and_yields_zero():
    _, events, it = await run(
        [
            {
                "id": "a",
                "op": "set_var",
                "name": "x",
                "value": {"kind": "binop", "op": "/", "left": 10, "right": 0},
            }
        ]
    )
    assert it.vars["x"] == 0
    assert ev(events, "warning")[0]["blockId"] == "a"
    assert "zero" in ev(events, "warning")[0]["message"]
    assert events[-1]["reason"] == "done"  # not a crash


# --- runtime clamping ---


def computed(v):
    """A value that pydantic cannot range-check: v + 0."""
    return {"kind": "binop", "op": "+", "left": v, "right": 0}


async def test_move_clamps_both_ways_with_a_warning():
    drone, events, _ = await run(
        [
            {"id": "lo", "op": "move", "dir": "forward", "cm": computed(3)},
            {"id": "hi", "op": "move", "dir": "back", "cm": computed(900)},
        ]
    )
    assert drone.log == [("move", "forward", 20), ("move", "back", 500)]
    low, high = ev(events, "warning")
    assert (low["blockId"], high["blockId"]) == ("lo", "hi")
    assert "20" in low["message"] and "500" in high["message"]


async def test_rotate_clamps_both_ways():
    drone, events, _ = await run(
        [
            {"id": "lo", "op": "rotate", "dir": "cw", "deg": computed(0)},
            {"id": "hi", "op": "rotate", "dir": "ccw", "deg": computed(400)},
        ]
    )
    assert drone.log == [("rotate", "cw", 1), ("rotate", "ccw", 360)]
    assert len(ev(events, "warning")) == 2


async def test_repeat_n_clamps_and_zero_skips_the_body():
    drone, events, _ = await run(
        [
            {
                "id": "z",
                "op": "repeat_n",
                "n": computed(0),
                "body": [{"id": "b", "op": "flip", "dir": "left"}],
            },
            {
                "id": "hi",
                "op": "repeat_n",
                "n": computed(80),
                "body": [{"id": "c", "op": "flip", "dir": "right"}],
            },
        ]
    )
    assert ("flip", "left") not in drone.log  # 0 is in range: skip, don't clamp up
    assert drone.log.count(("flip", "right")) == 50
    assert [w["blockId"] for w in ev(events, "warning")] == ["hi"]


async def test_wait_clamps_both_ways():
    it, events = make()
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    it._sleep = fake_sleep
    await it.run(
        prog(
            [
                {"id": "lo", "op": "wait", "seconds": computed(-5)},
                {"id": "hi", "op": "wait", "seconds": computed(99)},
            ]
        )
    )
    assert slept == [0.0, 10.0]
    assert [w["blockId"] for w in ev(events, "warning")] == ["lo", "hi"]


async def test_wait_actually_waits_and_is_interruptible():
    import asyncio

    it, events = make()
    task = asyncio.create_task(
        it.run(
            prog(
                [
                    {"id": "a", "op": "wait", "seconds": 10},
                    {"id": "b", "op": "flip", "dir": "left"},
                ]
            )
        )
    )
    await asyncio.sleep(0.05)
    it.request_stop()
    await task
    assert events[-1]["reason"] == "stopped"
    assert ("flip", "left") not in it._drone.log


# --- loops ---


async def test_while_runs_while_true_and_repeat_until_runs_until_true():
    # target visible: `while visible` spins to the bound, `repeat until visible` never enters
    vis = {"kind": "sensor", "sensor": "target_visible"}
    drone, events, _ = await run(
        [
            {
                "id": "u",
                "op": "repeat_until",
                "cond": vis,
                "body": [{"id": "x", "op": "flip", "dir": "left"}],
            },
            {
                "id": "w",
                "op": "while",
                "cond": {"kind": "unop", "op": "not", "operand": vis},
                "body": [{"id": "y", "op": "flip", "dir": "right"}],
            },
        ],
        det=seen(),
    )
    assert drone.log == []
    assert ev(events, "warning") == []


async def test_while_terminates_on_a_counter():
    drone, events, it = await run(
        [
            {"id": "s", "op": "set_var", "name": "i", "value": 0},
            {
                "id": "w",
                "op": "while",
                "cond": {
                    "kind": "binop",
                    "op": "<",
                    "left": {"kind": "var", "name": "i"},
                    "right": 3,
                },
                "body": [
                    {"id": "f", "op": "flip", "dir": "left"},
                    {
                        "id": "b",
                        "op": "set_var",
                        "name": "i",
                        "value": {
                            "kind": "binop",
                            "op": "+",
                            "left": {"kind": "var", "name": "i"},
                            "right": 1,
                        },
                    },
                ],
            },
        ]
    )
    assert drone.log.count(("flip", "left")) == 3
    assert it.vars["i"] == 3
    assert ev(events, "warning") == []


async def test_endless_while_hits_the_iteration_bound_and_warns():
    drone, events, _ = await run(
        [
            {
                "id": "w",
                "op": "while",
                "cond": 1,
                "body": [{"id": "f", "op": "flip", "dir": "left"}],
            }
        ]
    )
    assert drone.log.count(("flip", "left")) == MAX_LOOP_ITERS
    warn = ev(events, "warning")[0]
    assert warn["blockId"] == "w" and str(MAX_LOOP_ITERS) in warn["message"]
    assert events[-1]["reason"] == "done"


async def test_repeat_until_hits_the_same_bound():
    drone, events, _ = await run(
        [
            {
                "id": "u",
                "op": "repeat_until",
                "cond": 0,
                "body": [{"id": "f", "op": "flip", "dir": "left"}],
            }
        ]
    )
    assert drone.log.count(("flip", "left")) == MAX_LOOP_ITERS
    assert ev(events, "warning")[0]["blockId"] == "u"


# --- v1 compatibility ---

V1_CONDITIONS = [
    ({"sensor": "marker_visible"}, {"kind": "sensor", "sensor": "target_visible"}),
    (
        {"sensor": "marker_position_left"},
        {"kind": "sensor", "sensor": "target_position_left"},
    ),
    (
        {"sensor": "marker_position_center"},
        {"kind": "sensor", "sensor": "target_position_center"},
    ),
    (
        {"sensor": "marker_position_right"},
        {"kind": "sensor", "sensor": "target_position_right"},
    ),
    (
        {"sensor": "found_count_gte", "value": 2},
        {
            "kind": "binop",
            "op": ">=",
            "left": {"kind": "sensor", "sensor": "found_count"},
            "right": {"kind": "number", "value": 2},
        },
    ),
]


@pytest.mark.parametrize("v1,v2", V1_CONDITIONS)
def test_v1_conditions_upgrade_to_the_documented_value_nodes(v1, v2):
    p = prog([{"id": "a", "op": "if", "cond": v1}], version=1)
    assert p.version == 2
    assert (
        p.blocks[0].cond
        == Program.model_validate(
            {"version": 2, "blocks": [{"id": "a", "op": "if", "cond": v2}]}
        )
        .blocks[0]
        .cond
    )


def test_v1_conditions_upgrade_inside_nested_bodies():
    p = prog(
        [
            {
                "id": "a",
                "op": "repeat_until",
                "cond": {"sensor": "marker_visible"},
                "body": [
                    {
                        "id": "b",
                        "op": "if",
                        "cond": {"sensor": "marker_position_center"},
                        "else_body": [
                            {
                                "id": "c",
                                "op": "if",
                                "cond": {"sensor": "found_count_gte", "value": 1},
                            }
                        ],
                    }
                ],
            }
        ],
        version=1,
    )
    inner = p.blocks[0].body[0].else_body[0].cond
    assert inner.kind == "binop" and inner.left.sensor == "found_count"


async def test_v1_found_count_gte_still_gates_on_the_count():
    drone, _, _ = await run(
        [
            {
                "id": "a",
                "op": "if",
                "cond": {"sensor": "found_count_gte", "value": 1},
                "body": [{"id": "b", "op": "flip", "dir": "left"}],
                "else_body": [{"id": "c", "op": "mark_found"}],
            },
            {
                "id": "d",
                "op": "if",
                "cond": {"sensor": "found_count_gte", "value": 1},
                "body": [{"id": "e", "op": "flip", "dir": "forward"}],
            },
        ],
        version=1,
    )
    assert drone.log == [
        ("flip", "back"),
        ("flip", "forward"),
    ]  # mark_found, then the gate opens
