import random

from three_signals.infrastructure.fault import RandomSlowQueryPlanner


def test_a_seeded_planner_repeats_exactly():
    first = RandomSlowQueryPlanner(0.05, 1.5, random.Random(7))
    second = RandomSlowQueryPlanner(0.05, 1.5, random.Random(7))
    assert [first.next_delay_seconds() for _ in range(200)] == [second.next_delay_seconds() for _ in range(200)]


def test_about_one_request_in_twenty_is_slow():
    planner = RandomSlowQueryPlanner(0.05, 1.5, random.Random(20261027))
    delays = [planner.next_delay_seconds() for _ in range(2000)]
    assert set(delays) == {0.0, 1.5}
    assert 60 <= delays.count(1.5) <= 140


def test_probability_zero_never_delays_and_one_always_does():
    assert {RandomSlowQueryPlanner(0.0, 1.5, random.Random(1)).next_delay_seconds() for _ in range(50)} == {0.0}
    assert {RandomSlowQueryPlanner(1.0, 1.5, random.Random(1)).next_delay_seconds() for _ in range(50)} == {1.5}
