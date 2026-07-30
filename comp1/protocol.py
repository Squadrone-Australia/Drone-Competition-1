from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

MOVE_DIRS = {"forward", "back", "left", "right", "up", "down"}
ROTATE_DIRS = {"cw", "ccw"}
FLIP_DIRS = {"forward", "back", "left", "right"}


class Condition(BaseModel):
    sensor: Literal["marker_visible", "found_count_gte",
                    "marker_position_left", "marker_position_center",
                    "marker_position_right"]
    value: int = 0


class Block(BaseModel):
    id: str
    op: Literal["takeoff", "land", "move", "rotate", "flip", "approach_marker",
                "mark_found", "end_mission", "repeat_n", "repeat_until", "if"]
    dir: str | None = None
    cm: int | None = Field(None, ge=20, le=500)  # Tello SDK distance range
    deg: int | None = Field(None, ge=1, le=360)
    n: int | None = Field(None, ge=1, le=50)
    cond: Condition | None = None
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
            "if": self.cond is not None,
        }
        if self.op in need and not need[self.op]:
            raise ValueError(f"invalid params for op {self.op}")
        return self


class Program(BaseModel):
    version: Literal[1]
    blocks: list[Block]
