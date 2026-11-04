"""Passively sample simulation state at 2 Hz; never publish or issue motion."""
import argparse
import json
import signal
import time
from pathlib import Path

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import qos_profile_sensor_data
from sensor_msgs.msg import JointState, PointCloud2
from std_msgs.msg import String

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--duration-seconds', type=float, default=2100)
parser.add_argument('--stop-file', type=Path)
args = parser.parse_args()
if not 0 < args.duration_seconds <= 7200:
    parser.error('duration must be between 0 and 7200 seconds')
stopping = False
def stop_signal(*_):
    global stopping
    stopping = True
signal.signal(signal.SIGTERM, stop_signal)
signal.signal(signal.SIGINT, stop_signal)
rclpy.init()
node = Node('user_sim_20261104_observer')
latest = {}
counts = dict.fromkeys(('odom', 'joints', 'suction', 'base_cloud', 'head_cloud'), 0)
started = time.monotonic()


def on_message(kind, message):
    counts[kind] += 1
    row = {'receiptUnixMs': int(time.time() * 1000), 'receiptMonotonicNs': time.monotonic_ns()}
    if kind == 'suction':
        row['value'] = json.loads(message.data)
    else:
        row['sensorStampNs'] = message.header.stamp.sec * 10**9 + message.header.stamp.nanosec
        row['frameId'] = message.header.frame_id
        if kind == 'odom':
            p, v = message.pose.pose, message.twist.twist
            row['pose'] = [p.position.x, p.position.y, p.position.z, p.orientation.w,
                           p.orientation.x, p.orientation.y, p.orientation.z]
            row['linear'] = [v.linear.x, v.linear.y, v.linear.z]
            row['angular'] = [v.angular.x, v.angular.y, v.angular.z]
        elif kind == 'joints':
            row.update(names=list(message.name), positions=list(message.position), velocities=list(message.velocity))
        else:
            row['width'], row['height'] = message.width, message.height
    latest[kind] = row


for kind, topic, message_type in (
    ('odom', '/odom', Odometry), ('joints', '/joint_states', JointState),
    ('suction', '/tangying/suction/state', String),
    ('base_cloud', '/camera/base/nav_points', PointCloud2),
    ('head_cloud', '/camera/head/nav_points', PointCloud2),
):
    node.create_subscription(message_type, topic, lambda m, k=kind: on_message(k, m), qos_profile_sensor_data)

last = -1.0
try:
    while (time.monotonic() - started < args.duration_seconds and not stopping
           and not (args.stop_file and args.stop_file.exists())):
        rclpy.spin_once(node, timeout_sec=.05)
        now = time.monotonic()
        if now - last >= .5:
            print(json.dumps({'event': 'sample', 'atUnixMs': int(time.time() * 1000),
                              'elapsedSeconds': now-started, 'counts': counts, 'latest': latest}), flush=True)
            last = now
finally:
    print(json.dumps({'event': 'end', 'durationSeconds': time.monotonic()-started,
                      'counts': counts, 'scope': 'Read-only subscriptions sampled at 2 Hz; odometry estimate, not independent ground truth or collision/contact proof'}), flush=True)
    node.destroy_node()
    rclpy.shutdown()
