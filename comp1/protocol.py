from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, model_validator

MOVE_DIRS = {"forward", "back", "left", "right", "up", "down"}
ROTATE_DIRS = {"cw", "ccw"}
FLIP_DIRS = {"forward", "back", "left", "right"}

# runtime ranges for the value-carrying fields; the interpreter clamps to these
LIMITS = {"cm": (20, 500), "deg": (1, 360), "n": (0, 50), "seconds": (0, 10)}

SENSORS = Literal[
    "target_visible",
    "target_distance_cm",
    "target_bearing_deg",
    "target_elevation_deg",
    "target_count",
    "target_position_left",
    "target_position_center",
    "target_position_right",
    "found_count",
    "battery",
    # Obstacles: anything the marker detector rejected. These are camera
    # measurements relative to the drone, so they are legal sensors under §4 —
    # unlike anything that would hand out an arena coordinate.
    "obstacle_visible",
    "obstacle_ahead",
    "obstacle_distance_cm",
    "obstacle_bearing_deg",
    "obstacle_count",
    "obstacle_position_left",
    "obstacle_position_center",
    "obstacle_position_right",
]
BIN_OPS = Literal["+", "-", "*", "/", "<", ">", "<=", ">=", "==", "!=", "and", "or"]
UN_OPS = Literal["not", "neg", "abs"]


class NumberLit(BaseModel):
    kind: Literal["number"] = "number"
    value: float


class SensorRead(BaseModel):
    kind: Literal["sensor"] = "sensor"
    sensor: SENSORS


class VarRead(BaseModel):
    kind: Literal["var"] = "var"
    name: str


class BinOp(BaseModel):
    kind: Literal["binop"] = "binop"
    op: BIN_OPS
    left: Value
    right: Value


class UnOp(BaseModel):
    kind: Literal["unop"] = "unop"
    op: UN_OPS
    operand: Value


def _lift(v):
    # a bare JSON number is shorthand for a number literal, anywhere a Value is taken
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return {"kind": "number", "value": float(v)}
    return v


# the inner Annotated must be tagged before the lift wraps it, hence the nesting
Value = Annotated[
    Annotated[
        NumberLit | SensorRead | VarRead | BinOp | UnOp, Field(discriminator="kind")
    ],
    BeforeValidator(_lift),
]


class Condition(BaseModel):
    """A v1 condition object. Kept only to parse and upgrade saved v1 programs."""

    sensor: Literal[
        "marker_visible",
        "found_count_gte",
        "marker_position_left",
        "marker_position_center",
        "marker_position_right",
    ]
    value: int = 0

    def to_value(self) -> dict:
        if self.sensor == "found_count_gte":
            return {
                "kind": "binop",
                "op": ">=",
                "left": {"kind": "sensor", "sensor": "found_count"},
                "right": {"kind": "number", "value": self.value},
            }
        return {
            "kind": "sensor",
            "sensor": self.sensor.replace("marker_", "target_", 1),
        }


class Block(BaseModel):
    id: str
    op: Literal[
        "takeoff",
        "land",
        "move",
        "rotate",
        "flip",
        "approach_marker",
        "avoid_obstacle",
        "mark_found",
        "end_mission",
        "repeat_n",
        "repeat_until",
        "while",
        "if",
        "set_var",
        "wait",
        "break",
        "continue",
    ]
    dir: str | None = None
    cm: Value | None = None
    deg: Value | None = None
    n: Value | None = None
    seconds: Value | None = None
    name: str | None = None  # set_var
    value: Value | None = None  # set_var
    cond: Value | None = None
    body: list[Block] = []
    else_body: list[Block] = []

    @model_validator(mode="after")
    def check_params(self):
        need = {
            "move": self.dir in MOVE_DIRS and self.cm is not None,
            "rotate": self.dir in ROTATE_DIRS and self.deg is not None,
            "flip": self.dir in FLIP_DIRS,
            "repeat_n": self.n is not None,
            "repeat_until": self.cond is not None,
            "while": self.cond is not None,
            "if": self.cond is not None,
            "set_var": bool(self.name) and self.value is not None,
            "wait": self.seconds is not None,
        }
        if self.op in need and not need[self.op]:
            raise ValueError(f"invalid params for op {self.op}")
        # A literal is checkable here; a computed value is not, so the interpreter
        # clamps at run time instead. Both paths use LIMITS.
        for field, (lo, hi) in LIMITS.items():
            v = getattr(self, field)
            if isinstance(v, NumberLit) and not lo <= v.value <= hi:
                raise ValueError(
                    f"{field} {v.value:g} outside {lo}..{hi} for op {self.op}"
                )
        return self


def _upgrade_block(b):
    if not isinstance(b, dict):
        return b
    out = dict(b)
    c = out.get("cond")
    if isinstance(c, dict) and "kind" not in c:  # v1 Condition object
        out["cond"] = Condition.model_validate(c).to_value()
    for key in ("body", "else_body"):
        if isinstance(out.get(key), list):
            out[key] = [_upgrade_block(x) for x in out[key]]
    return out


class Program(BaseModel):
    version: Literal[1, 2] = 2
    blocks: list[Block]

    @model_validator(mode="before")
    @classmethod
    def upgrade_v1(cls, data):
        if not isinstance(data, dict) or not isinstance(data.get("blocks"), list):
            return data
        data = {**data, "blocks": [_upgrade_block(b) for b in data["blocks"]]}
        if data.get("version") == 1:
            data["version"] = 2
        return data

    @model_validator(mode="after")
    def check_loop_controls(self):
        def visit(blocks: list[Block], loop_depth: int):
            for block in blocks:
                if block.op in {"break", "continue"} and loop_depth == 0:
                    raise ValueError(
                        f"{block.op} block '{block.id}' must be inside a loop"
                    )
                child_depth = loop_depth + (
                    block.op in {"repeat_n", "repeat_until", "while"}
                )
                visit(block.body, child_depth)
                visit(block.else_body, loop_depth)

        visit(self.blocks, 0)
        return self


for _m in (BinOp, UnOp, Block):
    _m.model_rebuild()
