"""The planted fault: one request in twenty has a slow items query."""

from __future__ import annotations

import random


class RandomSlowQueryPlanner:
    """Returns the configured delay with the configured probability, drawing from an injected generator."""

    def __init__(self, probability: float, delay_seconds: float, rng: random.Random) -> None:
        """Take the probability (0 to 1), the delay and the random generator to draw from."""
        self._probability = probability
        self._delay_seconds = delay_seconds
        self._rng = rng

    def next_delay_seconds(self) -> float:
        """Return the delay for this request: ``delay_seconds`` with the given probability, else 0.0."""
        return self._delay_seconds if self._rng.random() < self._probability else 0.0
