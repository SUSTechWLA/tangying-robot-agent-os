import itertools

from tangying_navigation.acquisition import RequestPacer


class Clock:
    value = 0.0
    stopped = False

    def monotonic(self):
        return self.value

    def wait(self, delay):
        self.value += delay
        return self.stopped


def test_snapshot_reconnections_are_limited_without_bursts_or_retiming():
    clock = Clock()
    pacer = RequestPacer(clock=clock.monotonic)
    starts = []
    for _ in range(6):
        assert pacer.wait(clock.wait)
        starts.append(clock.value)
        clock.value += 0.01  # A fast one-frame RPC completes immediately.
    assert all(b - a >= 0.199999 for a, b in itertools.pairwise(starts))
    clock.value += 3  # Slow/disconnected source must not accumulate request credit.
    assert pacer.wait(clock.wait)
    recovered = clock.value
    assert pacer.wait(clock.wait)
    assert clock.value - recovered >= 0.199999


def test_sources_have_independent_cadence_and_shutdown_interrupts_wait():
    clock = Clock()
    base = RequestPacer(clock=clock.monotonic)
    head = RequestPacer(clock=clock.monotonic)
    assert base.wait(clock.wait) and head.wait(clock.wait)
    assert clock.value == 0
    clock.stopped = True
    assert not base.wait(clock.wait)
