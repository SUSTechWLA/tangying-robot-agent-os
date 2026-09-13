"""Translate ROS stamps to Unix time without rejuvenating cached simulator data."""
from bisect import bisect_left


class ClockDomain:
    def __init__(self):
        self.ros_ms = None
        self.ros_anchors = []
        self.unix_anchors = []
        self.dropped_before = None

    def project(self, stamp_ms, *, ros_now_ms, unix_now_ms):
        if self.ros_ms is not None and ros_now_ms < self.ros_ms:
            # New simulation generation; ROS TF also clears its buffer on rewind.
            self.ros_anchors.clear()
            self.unix_anchors.clear()
            self.dropped_before = None
        if self.ros_ms is None or ros_now_ms != self.ros_ms:
            self.ros_anchors.append(ros_now_ms)
            self.unix_anchors.append(unix_now_ms)
            if len(self.ros_anchors) > 4096:
                self.dropped_before = self.ros_anchors.pop(0)
                self.unix_anchors.pop(0)
        self.ros_ms = ros_now_ms
        if (stamp_ms <= 0 or stamp_ms > ros_now_ms + 250
                or (self.dropped_before is not None and stamp_ms <= self.dropped_before)):
            return 0
        # Use the FIRST anchor which could have observed this stamp. Advancing
        # the ROS clock after a pause must never re-date an old cached TF, even
        # when this particular stamp was not requested before the pause.
        index = min(bisect_left(self.ros_anchors, stamp_ms), len(self.ros_anchors)-1)
        return self.unix_anchors[index] - (self.ros_anchors[index] - stamp_ms)
