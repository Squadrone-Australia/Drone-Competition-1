"""The first-run tutorial's lesson program must fly cleanly in every arena.

The tutorial (``comp1/frontend/tutorial.js``) ends with a student pressing Run
on a program it walked them through: out 50 cm, turn round, back 50 cm, land.
That is somebody's first ever flight, so it has to work whatever arena they
start in and whatever layout the arena rolled. A first lesson that ends with
"you hit an obstacle" teaches only that the app is unfair.

It was chosen because it cannot hit anything rather than because it usually
does not: nothing solid is placed within ``PAD_CLEARANCE_M`` of the start pad,
and 50 cm is well inside that. The obvious first program, "fly forward 100 cm",
was measured before this one was picked. It flew into the wall of the
competition arena on every single attempt, because the pad there sits a metre
from it.

``tests/fixtures/tutorial_lesson.json`` is the shared copy. The JS suite checks
the tutorial still builds exactly that program; this file flies it.
"""

import json
import math
from pathlib import Path

import pytest

from comp1.interpreter import Interpreter
from comp1.protocol import Program
from comp1.sim import scenery
from comp1.sim.drone import SimDrone
from comp1.vision.detector import detect_red_circle

LESSON = json.loads(
    (Path(__file__).parent / "fixtures" / "tutorial_lesson.json").read_text("utf-8")
)
SEEDS = range(25)


def lesson_program() -> Program:
    blocks = [{"id": f"b{i}", **b} for i, b in enumerate(LESSON["blocks"])]
    return Program.model_validate({"version": LESSON["version"], "blocks": blocks})


def test_the_lesson_never_comes_near_anything_solid():
    # The structural reason the flights below cannot crash. If the pad
    # clearance ever shrinks below the lesson's reach, this is the test that
    # should fail first, with the reason in its name.
    reach_m = max(b.get("cm", 0) for b in LESSON["blocks"]) / 100
    assert reach_m < scenery.PAD_CLEARANCE_M


@pytest.mark.parametrize("name", scenery.names())
async def test_the_lesson_flies_and_comes_home_in_every_arena(name):
    for seed in SEEDS:
        drone = SimDrone(delay=0, seed=seed, scenery_name=name)
        start = (drone.x, drone.y)
        events = []
        interp = Interpreter(
            drone, lambda d=drone: detect_red_circle(d.get_frame()), events.append
        )
        await interp.run(lesson_program())

        finished = events[-1]
        assert finished["type"] == "finished", (name, seed)
        assert finished["reason"] == "done", (name, seed, finished)
        assert not drone.crashed, f"{name} seed {seed}: the lesson hit something"
        assert not drone.flying
        # "Come home" is what the tutorial tells the student will happen.
        assert math.dist(start, (drone.x, drone.y)) < 0.05, (name, seed)
