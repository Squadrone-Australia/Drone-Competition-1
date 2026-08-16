from comp1.sim.world import World


def test_seeded_world_is_reproducible():
    assert World.random(seed=42).markers == World.random(seed=42).markers


def test_counts_and_wall_placement():
    w = World.random(seed=1)
    assert len(w.markers) == 7 and len(w.fires) == 3
    for m in w.markers:
        assert m.x in (0.0, 4.0) or m.y in (0.0, 4.0)  # on a wall
        assert 0.5 <= (m.y if m.x in (0.0, 4.0) else m.x) <= 3.5  # away from corners


def test_min_spacing():
    w = World.random(seed=7)
    ms = w.markers
    for i, a in enumerate(ms):
        for b in ms[i + 1 :]:
            assert (a.x - b.x) ** 2 + (a.y - b.y) ** 2 >= 0.6**2
