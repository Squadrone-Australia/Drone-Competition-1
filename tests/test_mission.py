"""Mission scoring: a find only counts where a fire actually is.

``mark found`` is a bare counter in both pathways, so without this a program that
back-flips three times on the start pad "wins". The simulator knows where the
fires are; these tests pin that it uses that knowledge.
"""

import pytest

from comp1.sim.drone import SimDrone
from comp1.sim.mission import ARRIVAL_RADIUS_M, CREDIT_RADIUS_M, MissionScorer
from comp1.sim.world import DESTINATION, FIRE, Marker, World


def _pose(x, y, flying=True):
    return {
        "x": x,
        "y": y,
        "z": 1.0,
        "heading": 0.0,
        "roll": 0.0,
        "pitch": 0.0,
        "flying": flying,
    }


def _scene(markers, **kw):
    return SimDrone(world=World(markers=markers, **kw), delay=0).scene()


CORRIDOR = dict(size_m=2.5, length_m=10.0, start=(1.25, 0.6), name="corridor")
MARKERS = [
    Marker(1.25, 9.4, DESTINATION),
    Marker(1.0, 3.0, FIRE),
    Marker(1.5, 6.0, FIRE),
]


@pytest.fixture
def scorer():
    return MissionScorer(_scene(MARKERS, **CORRIDOR))


def test_it_reads_fires_and_the_destination_out_of_the_scene(scorer):
    assert scorer.total == 2
    assert scorer.destination == (1.25, 9.4)


def test_a_signal_beside_a_fire_counts(scorer):
    assert scorer.signal(_pose(1.05, 3.1)) is True
    assert scorer.found == 1


def test_a_signal_in_an_empty_stretch_counts_for_nothing(scorer):
    assert scorer.signal(_pose(1.25, 0.6)) is False
    assert scorer.found == 0


def test_the_same_fire_cannot_be_claimed_twice(scorer):
    assert scorer.signal(_pose(1.0, 3.0)) is True
    assert scorer.signal(_pose(1.0, 3.0)) is False
    assert scorer.found == 1


def test_two_signals_by_two_fires_credit_both(scorer):
    assert scorer.signal(_pose(1.0, 3.0)) is True
    assert scorer.signal(_pose(1.5, 6.0)) is True
    assert scorer.found == 2


def test_the_credit_radius_is_the_boundary(scorer):
    assert scorer.signal(_pose(1.0, 3.0 + CREDIT_RADIUS_M * 0.99)) is True
    assert scorer.signal(_pose(1.5, 6.0 + CREDIT_RADIUS_M * 1.5)) is False


def test_a_signal_between_two_fires_credits_the_nearer_one(scorer):
    scorer.signal(_pose(1.4, 5.6))
    assert scorer.credited == {1}  # the (1.5, 6.0) marker


def test_arrival_needs_the_destination_not_just_the_far_end(scorer):
    assert scorer.at_destination(_pose(1.25, 9.3)) is True
    assert scorer.at_destination(_pose(1.25, 9.4 - ARRIVAL_RADIUS_M * 2)) is False


# --- success --------------------------------------------------------------


def test_success_needs_every_fire(scorer):
    scorer.signal(_pose(1.0, 3.0))
    assert scorer.state(_pose(1.25, 9.4, flying=False))["state"] == "flying"


def test_success_needs_the_drone_at_the_destination(scorer):
    scorer.signal(_pose(1.0, 3.0))
    scorer.signal(_pose(1.5, 6.0))
    assert scorer.state(_pose(1.25, 5.0, flying=False))["state"] == "flying"


def test_success_needs_the_drone_landed(scorer):
    scorer.signal(_pose(1.0, 3.0))
    scorer.signal(_pose(1.5, 6.0))
    assert scorer.state(_pose(1.25, 9.4, flying=True))["state"] == "flying"


def test_all_three_together_is_a_mission_success(scorer):
    scorer.signal(_pose(1.0, 3.0))
    scorer.signal(_pose(1.5, 6.0))
    state = scorer.state(_pose(1.25, 9.4, flying=False))
    assert state == {
        "found": 2,
        "total": 2,
        "at_destination": True,
        "needs_destination": True,
        "state": "success",
    }


def test_an_arena_with_no_destination_succeeds_on_the_fires_alone():
    """The original square arena predates the corridor and must still be winnable."""
    scorer = MissionScorer(_scene([Marker(2.0, 4.0, FIRE)], size_m=4.0))
    assert scorer.destination is None
    assert scorer.signal(_pose(2.0, 4.0)) is True
    state = scorer.state(_pose(2.0, 2.0, flying=True))
    assert state["state"] == "success" and state["needs_destination"] is False


def test_an_empty_arena_is_never_a_success():
    scorer = MissionScorer(_scene([], size_m=4.0))
    assert scorer.state(_pose(2.0, 2.0, flying=False))["state"] == "flying"


def test_a_corridor_cleared_of_fires_is_won_by_getting_there():
    """Clearing every fire in the plan editor is a teacher setting up a pure
    point A to point B lesson, not an unwinnable arena."""
    scorer = MissionScorer(_scene([Marker(1.25, 9.4, DESTINATION)], **CORRIDOR))
    assert scorer.state(_pose(1.25, 5.0, flying=False))["state"] == "flying"
    assert scorer.state(_pose(1.25, 9.4, flying=False))["state"] == "success"


def test_the_destination_sign_is_not_a_fire(scorer):
    """It is a red circle and it is at the far end — but flipping at it scores nothing."""
    assert scorer.signal(_pose(1.25, 9.4)) is False


def test_a_scorer_survives_a_pose_it_never_gets(scorer):
    assert scorer.signal(None) is False
    assert scorer.at_destination(None) is False
    assert scorer.state(None)["state"] == "flying"
