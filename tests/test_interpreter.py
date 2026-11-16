from comp1.drone.mock import MockDrone
from comp1.interpreter import AUTO_LAND_WARNING, Interpreter
from comp1.protocol import Program
from comp1.vision.detector import Detection

from .helpers import seen

#: The default camera answer for programs that never look at one. Shared rather
#: than built per call because nothing mutates it.
NOTHING_SEEN = Detection(found=False)


def prog(blocks):
    return Program.model_validate({"version": 1, "blocks": blocks})


async def run(blocks, det=NOTHING_SEEN):
    drone, events = MockDrone(), []
    it = Interpreter(drone, lambda: det, events.append)
    await it.run(prog(blocks))
    return drone, events, it


async def test_sequential_ops_and_highlights():
    drone, events, _ = await run(
        [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "up", "cm": 30},
            {"id": "c", "op": "land"},
        ]
    )
    assert drone.log == [("takeoff",), ("move", "up", 30), ("land",)]
    assert [e["blockId"] for e in events if e["type"] == "highlight"] == ["a", "b", "c"]
    assert events[-1] == {"type": "finished", "reason": "done", "detail": ""}


async def test_debug_events_show_blocks_and_exact_adapter_calls():
    _, events, _ = await run(
        [
            {"id": "a", "op": "takeoff"},
            {"id": "b", "op": "move", "dir": "forward", "cm": 50},
            {"id": "c", "op": "mark_found"},
        ]
    )
    blocks = [
        (e["blockId"], e["op"])
        for e in events
        if e["type"] == "execution" and e["kind"] == "block"
    ]
    calls = [
        (e["adapter"], e["method"], e["args"])
        for e in events
        if e["type"] == "execution" and e["kind"] == "call"
    ]
    assert blocks == [("a", "takeoff"), ("b", "move"), ("c", "mark_found")]
    assert calls == [
        ("MockDrone", "takeoff", []),
        ("MockDrone", "move", ["forward", 50]),
        # the flip signal reads the charge first, because a Tello refuses to
        # flip on a low battery (see tests/test_find_signal.py)
        ("MockDrone", "battery", []),
        ("MockDrone", "flip", ["back"]),
        # the program ends in the air, so it is landed for the student -- and
        # the trace says so rather than the drone coming down unexplained
        ("MockDrone", "land", []),
    ]


async def test_stop_flag_halts_and_lands():
    drone, events = MockDrone(), []
    it = Interpreter(drone, lambda: Detection(found=False), events.append)
    it.request_stop()
    await it.run(
        prog([{"id": "a", "op": "takeoff"}, {"id": "b", "op": "flip", "dir": "left"}])
    )
    assert ("flip", "left") not in drone.log
    assert ("land",) in drone.log
    assert events[-1]["reason"] == "stopped"


def _stop_during(it, method, emergency=False):
    """An on_event hook that presses Stop while ``method`` is being called.

    The call trace is emitted on the loop thread just before the adapter call,
    which is exactly "during the block" as far as the stop flag can tell.
    """
    events = []

    def on_event(ev):
        events.append(ev)
        if ev.get("kind") == "call" and ev.get("method") == method:
            it[0].request_stop(emergency=emergency)

    return events, on_event


async def test_stop_during_the_last_block_still_stops_and_lands():
    """Field report: the whole program was `take off`, Stop was pressed while
    it climbed, and the run ended "done" with the drone left in the air."""
    drone, holder = MockDrone(), []
    events, on_event = _stop_during(holder, "takeoff")
    it = Interpreter(drone, lambda: NOTHING_SEEN, on_event)
    holder.append(it)
    await it.run(prog([{"id": "a", "op": "takeoff"}]))
    assert events[-1]["reason"] == "stopped"
    assert drone.log == [("takeoff",), ("land",)]
    assert not _auto_land_notices(events)  # Stop asked for the landing


async def test_emergency_during_the_last_block_does_not_land():
    drone, holder = MockDrone(), []
    events, on_event = _stop_during(holder, "takeoff", emergency=True)
    it = Interpreter(drone, lambda: NOTHING_SEEN, on_event)
    holder.append(it)
    await it.run(prog([{"id": "a", "op": "takeoff"}]))
    assert events[-1]["reason"] == "stopped"
    assert ("land",) not in drone.log


def _auto_land_notices(events):
    return [e for e in events if e["type"] == "warning" and e["message"] == AUTO_LAND_WARNING]


async def test_a_program_that_ends_in_the_air_is_landed():
    drone, events, _ = await run([{"id": "a", "op": "takeoff"}])
    assert events[-1]["reason"] == "done"
    assert drone.log == [("takeoff",), ("land",)]
    assert not drone.flying


async def test_the_automatic_landing_is_explained_to_the_student():
    """Installed-app walkthrough: the console said only "mission done" while
    the drone came down with no land block in the plan."""
    _, events, _ = await run([{"id": "a", "op": "takeoff"}])
    notices = _auto_land_notices(events)
    assert len(notices) == 1
    # before the land call, so it reads as the reason the drone is coming down
    land_at = next(i for i, e in enumerate(events) if e.get("method") == "land")
    assert events.index(notices[0]) < land_at


async def test_a_program_that_lands_itself_is_not_landed_twice():
    drone, events, _ = await run([{"id": "a", "op": "takeoff"}, {"id": "b", "op": "land"}])
    assert drone.log == [("takeoff",), ("land",)]
    assert not _auto_land_notices(events)


async def test_takeoff_while_flying_is_skipped_with_a_warning():
    """Field report: a second Run met a drone still up, its `take off` was
    refused, and the mission died on its first block."""
    drone, events = MockDrone(), []
    drone.flying = True
    it = Interpreter(drone, lambda: NOTHING_SEEN, events.append)
    await it.run(
        prog(
            [
                {"id": "a", "op": "takeoff"},
                {"id": "b", "op": "move", "dir": "up", "cm": 30},
                {"id": "c", "op": "land"},
            ]
        )
    )
    assert drone.log == [("move", "up", 30), ("land",)]
    warnings = [e for e in events if e["type"] == "warning"]
    assert warnings and warnings[0]["blockId"] == "a"
    assert "already flying" in warnings[0]["message"]
    assert events[-1]["reason"] == "done"


async def test_mark_found_signals_flip_and_counts():
    drone, events, it = await run([{"id": "a", "op": "mark_found"}])
    assert ("flip", "back") in drone.log
    assert {"type": "found_count", "count": 1} in events


async def test_repeat_until_condition_and_if():
    visible = seen(distance_m=3.0, bearing_deg=0.0)
    drone, events, _ = await run(
        [
            {
                "id": "a",
                "op": "if",
                "cond": {"sensor": "marker_visible"},
                "body": [{"id": "b", "op": "flip", "dir": "forward"}],
                "else_body": [{"id": "c", "op": "rotate", "dir": "cw", "deg": 30}],
            },
            {
                "id": "d",
                "op": "repeat_until",
                "cond": {"sensor": "marker_position_center"},
                "body": [{"id": "e", "op": "rotate", "dir": "ccw", "deg": 15}],
            },
            {
                "id": "f",
                "op": "repeat_n",
                "n": 2,
                "body": [{"id": "g", "op": "move", "dir": "forward", "cm": 20}],
            },
        ],
        det=visible,
    )
    assert ("flip", "forward") in drone.log  # if-branch taken
    assert ("rotate", "cw", 30) not in drone.log  # else-branch skipped
    assert ("rotate", "ccw", 15) not in drone.log  # until-condition already true
    assert drone.log.count(("move", "forward", 20)) == 2


async def test_continue_skips_to_the_next_repeat():
    drone, events, _ = await run(
        [
            {
                "id": "loop",
                "op": "repeat_n",
                "n": 3,
                "body": [
                    {"id": "flip", "op": "flip", "dir": "left"},
                    {"id": "continue", "op": "continue"},
                    {"id": "move", "op": "move", "dir": "forward", "cm": 20},
                ],
            }
        ]
    )
    assert drone.log == [("flip", "left")] * 3
    highlights = [e["blockId"] for e in events if e["type"] == "highlight"]
    assert highlights.count("continue") == 3
    assert "move" not in highlights


async def test_break_inside_if_exits_the_nearest_loop():
    drone, events, _ = await run(
        [
            {
                "id": "loop",
                "op": "repeat_n",
                "n": 5,
                "body": [
                    {"id": "flip", "op": "flip", "dir": "right"},
                    {
                        "id": "if",
                        "op": "if",
                        "cond": 1,
                        "body": [{"id": "break", "op": "break"}],
                    },
                    {"id": "move", "op": "move", "dir": "forward", "cm": 20},
                ],
            },
            {"id": "turn", "op": "rotate", "dir": "cw", "deg": 30},
        ]
    )
    assert drone.log == [("flip", "right"), ("rotate", "cw", 30)]
    assert events[-1] == {"type": "finished", "reason": "done", "detail": ""}


async def test_v1_program_runs_unchanged_under_the_v2_parser():
    # the same fixture as above, asserted event-for-event: the upgrade must be invisible
    blocks = [
        {"id": "a", "op": "takeoff"},
        {
            "id": "b",
            "op": "repeat_until",
            "cond": {"sensor": "marker_position_center"},
            "body": [{"id": "c", "op": "rotate", "dir": "cw", "deg": 20}],
        },
        {
            "id": "d",
            "op": "if",
            "cond": {"sensor": "marker_visible"},
            "body": [{"id": "e", "op": "mark_found"}],
        },
        {"id": "f", "op": "land"},
    ]
    drone, events, it = await run(blocks, det=seen(distance_m=2.0, bearing_deg=0.0))
    assert drone.log == [("takeoff",), ("flip", "back"), ("land",)]
    assert [e["blockId"] for e in events if e["type"] == "highlight"] == [
        "a",
        "b",
        "d",
        "e",
        "f",
    ]
    assert not [e for e in events if e["type"] == "warning"]
    assert it.found_count == 1
