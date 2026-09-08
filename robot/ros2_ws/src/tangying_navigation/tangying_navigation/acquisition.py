"""Bound reconnecting snapshot RPCs without changing sensor timestamps."""

import time


class RequestPacer:
    def __init__(self, *, clock=time.monotonic):
        self.clock = clock
        self.next_request = 0.0

    def wait(self, interruptible_wait):
        delay = max(0.0, self.next_request - self.clock())
        if interruptible_wait(delay):
            return False
        # Set from the actual request time, never catch up missed periods in a burst.
        self.next_request = self.clock() + 0.2
        return True
