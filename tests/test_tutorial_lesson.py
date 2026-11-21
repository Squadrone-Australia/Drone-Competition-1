"""The first-run tutorial's lesson program must fly cleanly in every arena.

The tutorial (``comp1/frontend/tutorial.js``) ends with a student pressing Run
on a program it walked them through: out 50 cm, turn round, back 50 cm, land.
That is somebody's first ever flight, so it has to work whatever arena they
start in and whatever layout the arena rolled. A first lesson that ends with
"you crashed" teaches only that the app is unfair.

It was chosen because it cannot hit anything rather than because it usually
does not: nothing solid is placed within ``PAD_CLEARANCE_M`` of the start pad,
and 50 cm is well inside that. The obvious first program, "fly forward 100 cm",
was measured before this one was picked. It flew into the wall of the
competition arena on every single attempt, because the pad there sits a metre
from it.

``tests/fixtures/tutorial_lesson.json`` is the shared copy. The JS suite checks
the tutorial still builds exactly that program; this file flies it.

The optional camera lesson that follows is held to the same standard, in
``tests/fixtures/tutorial_vision_lesson.json``: take off, turn until a target
is seen, approach it, signal, land. It has to *find* something as well as not
crash, and it is flown with both marker finders, because new installs start on
the AI one and a student can switch to colour at any time. Its signal is
scored the way the server scores it, so "found" means a find that counted, not
just a signal block that ran.
"""

import json
import math
from dataclasses import replace
from pathlib import Path

import pytest

from comp1.interpreter import Interpreter
from comp1.protocol import Program
from comp1.sim import scenery
from comp1.sim.drone import SimDrone
from comp1.sim.mission import MissionScorer
from comp1.vision.config import DEFAULT_CONFIG
from comp1.vision.detector import TargetTracker, detect_red_circle

FIXTURES = Path(__file__).parent / "fixtures"
LESSON = json.loads((FIXTURES / "tutorial_lesson.json").read_text("utf-8"))
VISION_LESSON = json.loads((FIXTURES / "tutorial_vision_lesson.json").read_text("utf-8"))
SEEDS = range(25)
# A camera flight renders and searches dozens of frames, up to 3 s a layout in
# the corridor, so this is a regression guard over a few layouts, not the
# measurement. The measurement (40 layouts per arena on colour, 20 on the AI
# finder, every one found and none crashed) is in
# docs/plans/2026-11-20-tutorial-camera-lesson.md.
VISION_SEEDS = {"hsv": range(4), "yolo": range(2)}


def _with_ids(blocks, prefix="b"):
    out = []
    for i, b in enumerate(blocks):
        b = {"id": f"{prefix}{i}", **b}
        if "body" in b:
            b["body"] = _with_ids(b["body"], f"{prefix}{i}_")
        out.append(b)
    return out


def lesson_program(lesson=LESSON) -> Program:
    return Program.model_validate(
        {"version": lesson["version"], "blocks": _with_ids(lesson["blocks"])}
    )


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


@pytest.mark.parametrize("detector", sorted(VISION_SEEDS))
@pytest.mark.parametrize("name", scenery.names())
async def test_the_camera_lesson_finds_a_target_in_every_arena(name, detector):
    cfg = replace(DEFAULT_CONFIG, detector=detector)
    for seed in VISION_SEEDS[detector]:
        drone = SimDrone(delay=0, seed=seed, scenery_name=name)
        start = (drone.x, drone.y)
        # As the server wires it: one tracker the run reads through, and a
        # scorer that judges each signal where the drone is at that instant.
        tracker = TargetTracker(cfg)
        scorer = MissionScorer(drone.scene())
        credited = []
        events = []

        def on_event(ev, drone=drone, scorer=scorer, credited=credited, events=events):
            events.append(ev)
            if ev.get("type") == "found_count":
                credited.append(scorer.signal(drone.pose()))

        interp = Interpreter(
            drone,
            lambda d=drone, t=tracker: t.update(d.get_frame()),
            on_event,
            cfg,
            select_nearest_target=tracker.reset,
        )
        await interp.run(lesson_program(VISION_LESSON))

        where = (name, detector, seed)
        finished = events[-1]
        assert finished["type"] == "finished", where
        assert finished["reason"] == "done", (*where, finished)
        assert not drone.crashed, f"{where}: the camera lesson hit {drone.crashed_into}"
        assert not drone.flying, where
        # The tutorial only praises a find the scorer counted.
        assert credited == [True], (*where, credited)
        # It went somewhere: a program that never left the pad found nothing.
        assert math.dist(start, (drone.x, drone.y)) > 0.2, where
        warnings = [e["message"] for e in events if e.get("type") == "warning"]
        if name == "corridor":
            # Known, and not the tutorial's doing: corridor signs hang at 2 m
            # over a 1 m hover, so one full approach step carries the sign out
            # of the top of the frame and the approach ends on "lost sight",
            # 1.26 m short - inside the credit radius, so the find counts. The
            # fix is in the approach controller, not here. When it lands, this
            # branch goes and the corridor is held to silence too.
            assert all("lost sight" in w for w in warnings), (*where, warnings)
        else:
            assert warnings == [], (*where, warnings)
