"""The console's "how to fix" lines (comp1/hints.py).

The messages are produced by the real code wherever that is cheap, so a reworded
error that no longer matches its fix fails here rather than at a venue.
"""

import pytest

from comp1.drone.tello_link import TelloNoReply, plain_refusal
from comp1.hints import fix_for
from comp1.server import VIDEO_BLOCKED_MESSAGE, VIDEO_PORT_BUSY_MESSAGE


def test_a_bare_takeoff_refusal_lists_what_stops_a_takeoff():
    """The field case: the aircraft says only 'error', and the causes are a
    flat battery, heat, or a crash it has not been rebooted since."""
    fix = fix_for(plain_refusal("takeoff", "error"))
    for cause in ("battery", "hot", "crash", "off and on"):
        assert cause in fix


@pytest.mark.parametrize(
    ("reply", "expect"),
    [
        ("error Not joystick", "at the same time"),
        ("error No valid imu", "calibrat"),
        ("error Auto land", "battery"),
        ("error Motor stop", "off and on"),
    ],
)
def test_each_known_refusal_has_its_own_fix(reply, expect):
    assert expect in fix_for(plain_refusal("forward 100", reply))


def test_an_unknown_refusal_still_gets_the_general_advice():
    assert "battery" in fix_for(plain_refusal("cw 90", "error Something new"))


def test_no_answer_to_the_handshake_mentions_the_first_program_rule():
    """The drone answers only the first port that spoke to it since boot."""
    fix = fix_for(TelloNoReply("the drone did not answer 'command' within 2s"))
    assert "Wi-Fi" in fix and "first program" in fix


def test_any_other_silence_points_at_the_wi_fi_and_reconnect():
    for message in (
        "the drone did not answer 'forward 50' within 17s",
        "the drone is not connected — reconnecting, try again in a moment",
        "lost contact with the drone",
        "could not connect to the drone: the Tello is not connected",
    ):
        fix = fix_for(message)
        assert fix and "Wi-Fi" in fix and "Reconnect" in fix, message


def test_the_two_camera_problems_get_different_fixes():
    firewall = fix_for(VIDEO_BLOCKED_MESSAGE)
    busy = fix_for(VIDEO_PORT_BUSY_MESSAGE)
    assert "firewall" in firewall and "11111" in firewall
    assert "Jupyter" in busy and "firewall" not in busy


@pytest.mark.parametrize(
    "message",
    [
        "stop the mission before resetting",
        "already running",
        "wait for the drone connection",
        "'forward 50' was cut short by an emergency stop",
        "",
    ],
)
def test_no_hint_rather_than_a_wrong_one(message):
    assert fix_for(message) is None


@pytest.mark.parametrize("odd", [None, 42, object(), b"\xff"])
def test_never_raises(odd):
    fix_for(odd)
