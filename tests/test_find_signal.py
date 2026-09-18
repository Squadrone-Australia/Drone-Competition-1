"""The find signal (§2.1): the student's choice, and the low-battery fallback.

A real Tello refuses ``flip`` below roughly 50% charge — it answers with an
error and keeps hovering. That makes the *one* action the judges are watching
for the one that silently does not happen, while the program counts the find
and flies on. Both pathways therefore read the battery before a flip and
downgrade a doomed one to a 360-degree spin.
"""

import pytest

from comp1.api import Drone
from comp1.drone.config import FlightConfig, choose_signal
from comp1.drone.mock import MockDrone
from comp1.interpreter import Interpreter
from comp1.protocol import Program
from comp1.vision.detector import Detection

from .test_api import mock_session


class BatteryDrone(MockDrone):
    """A mock whose charge the test picks, and which can fail to report it."""

    def __init__(self, level=100, flight=None):
        super().__init__()
        self.level = level
        self.battery_reads = 0
        if flight is not None:
            self.flight = flight

    def battery(self):
        self.battery_reads += 1
        if self.level is None:
            raise OSError("no reply")
        return self.level


async def run_signal(signal=None, level=100, flight=None):
    drone = BatteryDrone(level, flight)
    events = []
    block = {"id": "a", "op": "mark_found"}
    if signal is not None:
        block["signal"] = signal
    it = Interpreter(drone, lambda: Detection(found=False), events.append)
    await it.run(Program.model_validate({"version": 1, "blocks": [block]}))
    return drone, events


def warnings(events):
    return [e["message"] for e in events if e["type"] == "warning"]


# --- the policy itself ----------------------------------------------------


def test_choose_signal_keeps_a_healthy_flip():
    assert choose_signal("flip", 100) == ("flip", None)
    assert choose_signal("flip", 55) == ("flip", None)


def test_choose_signal_downgrades_a_flip_the_aircraft_would_refuse():
    kind, warning = choose_signal("flip", 54)
    assert kind == "spin"
    assert "54%" in warning and "55%" in warning


def test_choose_signal_treats_an_unknown_battery_as_too_risky_to_flip():
    kind, warning = choose_signal("flip", None)
    assert kind == "spin"
    assert "unknown" in warning


def test_a_spin_is_never_downgraded_and_never_warns():
    assert choose_signal("spin", 3) == ("spin", None)


def test_the_floor_is_tunable_on_site():
    strict = FlightConfig(flip_min_battery_pct=80)
    assert choose_signal("flip", 70, strict)[0] == "spin"
    # 0 opts out of the fallback entirely — a bench setting, but an honest one
    assert choose_signal("flip", 5, FlightConfig(flip_min_battery_pct=0)) == (
        "flip",
        None,
    )


# --- the block pathway ----------------------------------------------------


async def test_block_defaults_to_a_flip_when_the_program_predates_the_choice():
    drone, events = await run_signal(signal=None)
    assert ("flip", "back") in drone.log
    assert warnings(events) == []
    assert {"type": "found_count", "count": 1} in events


async def test_block_spins_when_the_student_picks_a_spin():
    drone, events = await run_signal(signal="spin")
    assert drone.log == [("rotate", "cw", 360)]
    assert warnings(events) == []


async def test_a_chosen_spin_never_costs_a_battery_round_trip():
    # On a real Tello every reading is an SDK command, and a spin cannot fail
    # for want of charge, so there is nothing to ask about.
    drone, _ = await run_signal(signal="spin", level=10)
    assert drone.battery_reads == 0


async def test_block_flip_falls_back_to_a_spin_on_a_low_battery():
    drone, events = await run_signal(signal="flip", level=40)
    assert drone.log == [("rotate", "cw", 360)]
    assert any("40%" in w for w in warnings(events))
    # the find still counts — the signal happened, just not as a flip
    assert {"type": "found_count", "count": 1} in events


async def test_block_flip_falls_back_when_the_battery_cannot_be_read():
    drone, events = await run_signal(signal="flip", level=None)
    assert drone.log == [("rotate", "cw", 360)]
    assert any("unknown" in w for w in warnings(events))


async def test_a_failed_battery_read_never_ends_the_mission():
    drone = BatteryDrone(None)
    events = []
    it = Interpreter(drone, lambda: Detection(found=False), events.append)
    await it.run(
        Program.model_validate(
            {
                "version": 1,
                "blocks": [
                    {"id": "a", "op": "mark_found", "signal": "flip"},
                    {"id": "b", "op": "move", "dir": "forward", "cm": 30},
                ],
            }
        )
    )
    assert ("move", "forward", 30) in drone.log
    assert events[-1]["reason"] == "done"


async def test_the_block_honours_an_on_site_flight_config():
    drone, events = await run_signal(
        signal="flip", level=70, flight=FlightConfig(flip_min_battery_pct=80)
    )
    assert drone.log == [("rotate", "cw", 360)]


def test_schema_rejects_an_unknown_signal():
    with pytest.raises(ValueError):
        Program.model_validate(
            {"version": 1, "blocks": [{"id": "a", "op": "mark_found", "signal": "wave"}]}
        )


# --- the python pathway ---------------------------------------------------


def api_drone(level=100, flight=None):
    events = []
    session, _ = mock_session(emit=events.append)
    adapter = BatteryDrone(level, flight)
    session.drone = adapter
    d = Drone()
    d._s, d._d = session, adapter
    return d, adapter, events


def test_api_defaults_to_a_flip():
    d, adapter, events = api_drone()
    d.mark_found()
    assert adapter.log == [("flip", "back")]
    assert d.found_count == 1


def test_api_spins_on_request():
    d, adapter, _ = api_drone()
    d.mark_found("spin")
    assert adapter.log == [("rotate", "cw", 360)]
    assert adapter.battery_reads == 0


def test_api_flip_falls_back_on_a_low_battery():
    d, adapter, events = api_drone(level=20)
    d.mark_found("flip")
    assert adapter.log == [("rotate", "cw", 360)]
    assert any("20%" in e.get("message", "") for e in events)
    assert d.found_count == 1


def test_api_flip_falls_back_when_the_battery_read_fails():
    d, adapter, events = api_drone(level=None)
    d.mark_found()
    assert adapter.log == [("rotate", "cw", 360)]


def test_api_warns_and_flips_on_a_typo_rather_than_raising():
    d, adapter, events = api_drone()
    d.mark_found("spinn")
    assert adapter.log == [("flip", "back")]
    assert any("spinn" in e.get("message", "") for e in events)
