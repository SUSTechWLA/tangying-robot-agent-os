"""Gazebo 接触物理实验夹具；复用五房间场景，绝不把 oracle 输入验证器。"""

from __future__ import annotations

import json
import math
import os
import signal
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = (-2.0, -1.6, 0.55)


def build_world(output):
    tree = ET.parse(ROOT / "robot/ros2_ws/src/tangying_navigation/worlds/tangying_home.sdf")
    world = tree.getroot().find("world")
    # Keep the five-room geometry and robot. Hide onboard cameras from this controlled
    # fixture's sensor budget; the production runtime has a separate acceptance run.
    for link in world.findall(".//link"):
        for sensor in list(link.findall("sensor")):
            link.remove(sensor)
    physics = world.find("physics")
    physics.find("max_step_size").text = "0.001"
    physics.find("real_time_factor").text = "0"
    contact = ET.SubElement(
        world, "plugin", filename="gz-sim-contact-system", name="gz::sim::systems::Contact"
    )
    del contact

    def add(xml):
        world.append(ET.fromstring(xml))

    def inertia(m=0.1):
        return f"<inertial><mass>{m}</mass><inertia><ixx>0.001</ixx><iyy>0.001</iyy><izz>0.001</izz></inertia></inertial>"

    def box(name, xyz, size, color, static=True):
        x, y, z = xyz
        add(
            f'<model name="{name}"><static>{str(static).lower()}</static><pose>{x} {y} {z} 0 0 0</pose><link name="link">{inertia()}<collision name="collision"><geometry><box><size>{size}</size></box></geometry><surface><friction><ode><mu>3</mu><mu2>3</mu2></ode></friction></surface></collision><visual name="visual"><geometry><box><size>{size}</size></box></geometry><material><ambient>{color}</ambient><diffuse>{color}</diffuse></material></visual></link></model>'
        )

    x, y, z = ORIGIN
    box("gvf_table", (x, y, 0.25), "1.0 0.7 0.5", "0.65 0.65 0.65 1")
    box("gvf_tray", (x + 0.28, y - 0.015, 0.505), ".20 .22 .01", "0.1 0.1 0.8 1")
    for i, (dx, dy, s) in enumerate(
        [
            (-0.105, 0, ".01 .24 .04"),
            (0.105, 0, ".01 .24 .04"),
            (0, -0.115, ".20 .01 .04"),
            (0, 0.115, ".20 .01 .04"),
        ]
    ):
        box("gvf_tray_wall" + str(i), (x + 0.28 + dx, y - 0.015 + dy, 0.525), s, "0.1 0.1 0.8 1")
    for name, dx, r, h, col in [
        ("cup", 0, 0.03, 0.08, "0.9 0.02 0.02 1"),
        ("plate", -0.14, 0.04, 0.025, "0.02 0.9 0.02 1"),
    ]:
        add(
            f'<model name="{name}"><pose>{x + dx} {y} {0.5 + h / 2 + 0.001} 0 0 0</pose><link name="link">{inertia(0.04)}<collision name="collision"><geometry><cylinder><radius>{r}</radius><length>{h}</length></cylinder></geometry><surface><friction><ode><mu>3</mu><mu2>3</mu2></ode></friction></surface></collision><visual name="visual"><geometry><cylinder><radius>{r}</radius><length>{h}</length></cylinder></geometry><material><ambient>{col}</ambient><diffuse>{col}</diffuse></material></visual></link></model>'
        )
    box("occluder", (x + 2, y, 1.0), ".6 .5 .03", "0.1 0.1 0.1 1")
    box("full_block", (x + 2, y, 0.56), ".18 .20 .09", "0.95 0.75 0.02 1")
    links = '<link name="anchor"><gravity>false</gravity>' + inertia() + "</link>"
    links += '<link name="carriage"><gravity>false</gravity>' + inertia() + "</link>"
    links += '<link name="lift"><gravity>false</gravity>' + inertia() + "</link>"
    for name, dy in [("left", -0.055), ("right", 0.055)]:
        sensor = (
            '<sensor name="contact" type="contact"><always_on>true</always_on><update_rate>100</update_rate><topic>/gvf/contact</topic><contact><collision>finger</collision></contact></sensor>'
            if name == "left"
            else ""
        )
        links += f'<link name="{name}"><pose>0 {dy} 0 0 0 0</pose><gravity>false</gravity>{inertia(0.1)}<collision name="finger"><geometry><box><size>.07 .02 .09</size></box></geometry><surface><friction><ode><mu>3</mu><mu2>3</mu2></ode></friction></surface></collision><visual name="v"><geometry><box><size>.07 .02 .09</size></box></geometry><material><ambient>.1 .1 .1 1</ambient><diffuse>.1 .1 .1 1</diffuse></material></visual>{sensor}</link>'
    joints = '<joint name="mount" type="fixed"><parent>world</parent><child>anchor</child></joint><joint name="left_fixed" type="fixed"><parent>lift</parent><child>left</child></joint>'
    plugins = '<plugin filename="gz-sim-joint-state-publisher-system" name="gz::sim::systems::JointStatePublisher"><topic>/gvf/joints</topic></plugin>'
    for name, parent, child, axis, lower, upper, p, d in [
        ("slide", "anchor", "carriage", "1 0 0", -0.3, 0.5, 400, 15),
        ("lift", "carriage", "lift", "0 0 1", -0.04, 0.3, 400, 15),
        ("close", "lift", "right", "0 1 0", -0.08, 0, 150, 3),
    ]:
        joints += f'<joint name="{name}_joint" type="prismatic"><parent>{parent}</parent><child>{child}</child><axis><xyz>{axis}</xyz><limit><lower>{lower}</lower><upper>{upper}</upper><effort>50</effort><velocity>2</velocity></limit><dynamics><damping>1</damping></dynamics></axis></joint>'
        plugins += f'<plugin filename="gz-sim-joint-position-controller-system" name="gz::sim::systems::JointPositionController"><joint_name>{name}_joint</joint_name><topic>/gvf/{name}</topic><p_gain>{p}</p_gain><d_gain>{d}</d_gain><cmd_max>30</cmd_max><cmd_min>-30</cmd_min></plugin>'
    add(f'<model name="gvf_gripper"><pose>{x} {y} {z} 0 0 0</pose>{links}{joints}{plugins}</model>')
    add(
        f'<model name="gvf_camera"><static>true</static><pose>{x + 0.10} {y} 1.45 0 1.57079632679 0</pose><link name="link"><sensor name="rgbd" type="rgbd_camera"><always_on>true</always_on><update_rate>20</update_rate><topic>/gvf/camera</topic><camera><horizontal_fov>1.0</horizontal_fov><image><width>128</width><height>128</height><format>R8G8B8</format></image><clip><near>.05</near><far>3</far></clip></camera></sensor></link></model>'
    )
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tree.write(output, encoding="unicode")
    return output


class GazeboSession:
    """Own one simulator process group per seed: world reset can remove contact publishers."""

    def __init__(self, world, log, seed):
        self.log = Path(log).open("w")  # noqa: SIM115 - owned until close()
        self.process = subprocess.Popen(
            ["gz", "sim", "-s", "--seed", str(seed), "--headless-rendering", str(world)],
            stdout=self.log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )

    def close(self):
        if self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait(timeout=3)
        self.log.close()


class Transport:
    def __init__(self, binary="/tmp/gvf-build/grounded-transport"):
        self.proc = subprocess.Popen(
            [binary], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
        )

    def call(self, **request):
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        import select

        if not select.select([self.proc.stdout], [], [], 20)[0]:
            raise TimeoutError("Gazebo transport 超时")
        reply = json.loads(self.proc.stdout.readline())
        if not reply.get("ok"):
            raise RuntimeError(reply)
        return reply

    def move(self, **values):
        self.call(op="publish", values={"/gvf/" + k: v for k, v in values.items()})
        self.step(600)

    def step(self, steps=100):
        self.call(op="step", steps=steps, wait_ms=1000)

    def snapshot(self, path):
        return self.call(op="snapshot", path=str(path))

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


def decode_snapshot(path):
    path = Path(path)
    meta = json.loads((path / "rgb.json").read_text())
    rgb = np.fromfile(path / "rgb.bin", dtype=np.uint8).reshape(meta["height"], meta["width"], 3)
    depth = np.fromfile(path / "depth.bin", dtype="<f4").reshape(rgb.shape[:2])
    joints = json.loads((path / "joints.json").read_text())
    contact = json.loads((path / "contact.json").read_text())
    return rgb, depth, joints, contact


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="生成复用五房间的 Gazebo 接触实验场景")
    parser.add_argument("--world", required=True)
    args = parser.parse_args()
    print(build_world(args.world))


def _stamp(message):
    stamp = message.get("header", {}).get("stamp", {})
    return int(stamp.get("sec", 0)) * 1_000_000_000 + int(stamp.get("nsec", 0))


def measurements(path, target, previous=None):
    """Calibrated top-down RGB-D segmentation, encoder and tactile measurements."""
    rgb, depth, joints, contacts = decode_snapshot(path)
    stamp = _stamp(json.loads((Path(path) / "rgb.json").read_text()))
    focal = rgb.shape[1] / (2 * math.tan(0.5))
    objects = {}
    for name, index, height in [("cup", 0, 0.08), ("plate", 1, 0.025)]:
        v = rgb.astype(float)
        mask = (
            (v[:, :, index] > 50)
            & (v[:, :, index] > v[:, :, (index + 1) % 3] * 2)
            & (v[:, :, index] > v[:, :, (index + 2) % 3] * 2)
        )
        mask &= np.isfinite(depth) & (depth > 0)
        if mask.sum() < 8:
            continue
        rows, cols = np.nonzero(mask)
        d = float(np.median(depth[mask]))
        position = np.array(
            [
                ORIGIN[0] + 0.10 - (float(np.mean(rows)) - rgb.shape[0] / 2) * d / focal,
                ORIGIN[1] - (float(np.mean(cols)) - rgb.shape[1] / 2) * d / focal,
                1.45 - d - height / 2,
            ]
        )
        objects[name] = {
            "position": position.tolist(),
            "bottom_z": 1.45 - d - height,
            "pixels": int(mask.sum()),
        }
    q = {j["name"]: j.get("axis1", {}).get("position", 0.0) for j in joints.get("joint", [])}
    values = {
        "occluded": target not in objects,
        "gripper_closed": float(q.get("close_joint", 0)) < -0.005,
        "container_id": "tray",
    }
    if abs(_stamp(joints) - stamp) > 200_000_000:
        values.pop("gripper_closed")
    # Contact messages can stop when contact ends. A stale force is missing, not
    # a persistent load or a fabricated zero-current reading.
    if abs(_stamp(contacts) - stamp) <= 200_000_000:
        forces = [
            w.get("body1Wrench", {}).get("force", {})
            for c in contacts.get("contact", [])
            for w in c.get("wrench", [])
        ]
        values["load_n"] = sum(math.sqrt(sum(float(v) ** 2 for v in f.values())) for f in forces)
    held = [n for n, o in objects.items() if o["bottom_z"] > 0.54]
    values["held_object_id"] = held[0] if len(held) == 1 else ""
    if target in objects:
        p = np.array(objects[target]["position"])
        old = np.array(previous) if previous is not None else None
        radius = 0.03 if target == "cup" else 0.04
        values["height_above_surface_m"] = objects[target]["bottom_z"] - 0.5
        values["inside_container"] = bool(
            abs(p[0] - (ORIGIN[0] + 0.28)) + radius <= 0.10
            and abs(p[1] - (ORIGIN[1] - 0.015)) + radius <= 0.11
            and objects[target]["bottom_z"] < 0.56
        )
        if old is not None:
            values["displacement_m"] = float(np.linalg.norm(p - old))
    yellow = (rgb[:, :, 0] > 60) & (rgb[:, :, 1] > 40) & (rgb[:, :, 2] < 30)
    values["container_full"] = bool(yellow.sum() > 100)
    return values, objects, stamp


def physical_truth(path, target, kind, previous=None, goal=None):
    """Independent oracle from simulator pose; never sent to any verifier."""
    path = Path(path)
    poses = {
        p["name"]: p.get("position", {})
        for p in json.loads((path / "oracle.json").read_text()).get("pose", [])
    }
    if kind == "navigation.navigate":
        p = poses.get("tangying_robot")
        if p is None:
            return None, None
        # Simulator world pose is an independent oracle; wheel odometry may drift.
        position = np.array([p.get("x", 0) + 4.7, p.get("y", 0) + 0.8])
        entries = json.loads((path / "oracle.json").read_text()).get("pose", [])
        q = next(
            entry.get("orientation", {}) for entry in entries if entry["name"] == "tangying_robot"
        )
        yaw = math.atan2(
            2 * (q.get("w", 1) * q.get("z", 0) + q.get("x", 0) * q.get("y", 0)),
            1 - 2 * (q.get("y", 0) ** 2 + q.get("z", 0) ** 2),
        )
        target_yaw = 2 * math.atan2(goal[6], goal[3])
        yaw_error = abs(math.atan2(math.sin(yaw - target_yaw), math.cos(yaw - target_yaw)))
        return bool(
            np.linalg.norm(position - np.array(goal[:2])) <= 0.05 and yaw_error <= 0.12
        ), position.tolist()
    if target not in poses:
        return None, None
    p = np.array([poses[target].get(k, 0) for k in ["x", "y", "z"]])
    half = 0.04 if target == "cup" else 0.0125
    stable = previous is not None and np.linalg.norm(p - np.array(previous)) <= 0.01
    joints = json.loads((path / "joints.json").read_text())
    q = {j["name"]: j.get("axis1", {}).get("position", 0.0) for j in joints.get("joint", [])}
    if kind == "manipulation.pick":
        expected = (
            p[2] - half > 0.52
            and stable
            and q.get("close_joint", 0) < -0.005
            and abs(p[0] - (ORIGIN[0] + q.get("slide_joint", 0))) < 0.07
        )
    else:
        radius = 0.03 if target == "cup" else 0.04
        expected = (
            abs(p[0] - (ORIGIN[0] + 0.28)) + radius <= 0.10
            and abs(p[1] - (ORIGIN[1] - 0.015)) + radius <= 0.11
            and p[2] - half < 0.56
            and stable
            and q.get("close_joint", 0) > -0.005
        )
    return bool(expected), p.tolist()


class ExperimentBackend:
    """Simulation-only driver, used through RobotRuntimeService in the demo."""

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.transport = Transport()
        self.counter = 0
        self.now_ns = 0
        self.fault = "none"
        self.target = "cup"
        self.latest_frames = []
        self.goal = None
        for _ in range(20):
            self.transport.step(100)
            try:
                self.capture("initial")
                if self.now_ns > 0:
                    break
            except RuntimeError:
                continue
        if not self.now_ns:
            raise RuntimeError("Gazebo 同步相机未就绪")

    def capabilities(self):
        from tangying_robot_gateway.backend import capability
        from tangying_robot_gateway.runtime import RuntimeInfo

        names = [
            "manipulation.pick",
            "manipulation.place",
            "navigation.navigate",
            "verify_grasp",
            "verify_placement",
            "verify_arrival",
            "observe_scene",
            "emergency_stop",
        ]
        return RuntimeInfo(
            robot_id="gazebo-gvf-fixture",
            adapter="gazebo",
            manipulation_ready=True,
            capabilities=[
                capability(
                    n,
                    "Gazebo 实验夹具",
                    available=True,
                    safety_level="physical_motion" if n in names[:3] else "read_only",
                    mutates_world=n in names[:3],
                )
                for n in names
            ],
        )

    def capture(self, label):
        self.counter += 1
        path = self.root / f"{self.counter:06d}-{label}"
        metadata = self.transport.snapshot(path)
        self.now_ns = metadata["stamp_ns"]
        self.last_path = path
        return path

    def reset(self, seed, target="cup"):
        rng = np.random.default_rng(seed)
        self.target = target
        self.fault = "none"
        self.goal = None
        self.transport.move(close=0.0, lift=0.20)
        self.transport.move(slide=0.0)
        x, y, _ = ORIGIN
        for name, dx, h in [("cup", 0, 0.08), ("plate", -0.14, 0.025)]:
            self.transport.call(
                op="pose",
                name=name,
                xyz=[
                    x + dx + float(rng.uniform(-0.004, 0.004)),
                    y + float(rng.uniform(-0.003, 0.003)),
                    0.5 + h / 2 + 0.002,
                ],
            )
        for name, z in [("occluder", 1.0), ("full_block", 0.56)]:
            self.transport.call(op="pose", name=name, xyz=[x + 2, y, z])
        self.transport.step(500)
        self.capture("reset")

    def execute(self, command):
        from tangying_robot_gateway.runtime import Result

        name = command.capability
        if name.startswith("verify_") or name == "observe_scene":
            return Result(True)
        if name == "navigation.navigate":
            self.navigate(
                command.parameters["goalPose"], stop_short=self.fault == "nav_not_reached"
            )
        elif name == "manipulation.pick":
            target = (
                ("plate" if self.target == "cup" else "cup")
                if self.fault == "wrong_object"
                else self.target
            )
            slide = 0.0 if target == "cup" else -0.14
            if self.fault == "grasp_miss":
                slide += 0.09
            self.transport.move(close=0.0, lift=0.20)
            self.transport.move(slide=slide)
            self.transport.move(lift=0.0 if target == "cup" else -0.028)
            self.transport.move(close=-0.055)
            self.transport.move(lift=0.15)
        elif name == "manipulation.place":
            self.transport.move(slide=0.28 if self.fault != "place_outside" else 0.43)
            self.transport.move(lift=0.035)
            self.transport.move(close=0.0)
            self.transport.move(lift=0.20)
        else:
            return Result(False, "CAPABILITY_UNAVAILABLE")
        self.capture("tool-return")
        return Result(True, "OK")  # Fault injection deliberately preserves the SUCCESS receipt.

    def stop(self, reason):
        self.transport.call(op="velocity", linear=0.0, angular=0.0)

    def navigate(self, goal, stop_short=False):
        self.goal = goal
        for _ in range(100):
            path = self.capture("nav-control")
            odom = json.loads((path / "odom.json").read_text())
            pose = odom.get("pose", {})
            pos = pose.get("position", {})
            q = pose.get("orientation", {})
            yaw = 2 * math.atan2(q.get("z", 0), q.get("w", 1))
            dx, dy = goal[0] - pos.get("x", 0), goal[1] - pos.get("y", 0)
            dist = math.hypot(dx, dy)
            heading = math.atan2(dy, dx) if dist > 0.025 else 2 * math.atan2(goal[6], goal[3])
            delta = math.atan2(math.sin(heading - yaw), math.cos(heading - yaw))
            if dist < (0.20 if stop_short else 0.025) and (stop_short or abs(delta) < 0.06):
                break
            self.transport.call(
                op="velocity",
                linear=min(0.5, dist * 2) if abs(delta) < 0.2 else 0.0,
                angular=max(-1.5, min(1.5, delta * 2)),
            )
            self.transport.step(200)
        self.stop("navigation_complete")
        self.transport.step(100)

    def collect_grounded_evidence(
        self, *, command, action_id, start_ns, edge_boot_id, store, phase
    ):
        from tangying_robot_gateway.grounded.model import canonical

        previous = None
        frames = []
        self.latest_frames = []
        # First sample seeds displacement, followed by three independently timestamped frames.
        for i in range(4):
            if self.fault == "grasp_slip" and i == 2:
                self.transport.call(op="publish", values={"/gvf/close": 0.0})
            if self.fault == "occluded":
                self.transport.call(
                    op="pose", name="occluder", xyz=[ORIGIN[0] + 0.1, ORIGIN[1], 1.0]
                )
            self.transport.step(100)
            path = self.capture("evidence")
            values, objects, stamp = measurements(path, self.target, previous)
            previous = objects.get(self.target, {}).get("position")
            if (
                command.capability == "navigation.navigate"
                or command.capability == "verify_arrival"
            ):
                pose = json.loads((path / "odom.json").read_text()).get("pose", {})
                p, q = pose.get("position", {}), pose.get("orientation", {})
                goal = command.parameters["goalPose"]
                yaw = 2 * math.atan2(q.get("z", 0), q.get("w", 1))
                gyaw = 2 * math.atan2(goal[6], goal[3])
                values = {
                    "position_error_m": math.hypot(
                        p.get("x", 0) - goal[0], p.get("y", 0) - goal[1]
                    ),
                    "yaw_error_rad": abs(math.atan2(math.sin(yaw - gyaw), math.cos(yaw - gyaw))),
                }
            refs = [
                store.put((path / f).read_bytes(), kind, {"file": str(path.name) + "/" + f})
                for f, kind in [
                    ("rgb.bin", "rgb"),
                    ("depth.bin", "depth"),
                    ("joints.json", "gripper"),
                    ("contact.json", "force"),
                    ("odom.json", "pose"),
                ]
            ]
            refs.append(
                store.put(
                    canonical(
                        {
                            "measurements": values,
                            "objects": objects,
                            "intrinsics": {"horizontal_fov": 1.0, "size": 128},
                            "sensor_stamp_ns": stamp,
                        }
                    ).encode(),
                    "detection",
                )
            )
            confidence = 0.95
            if self.fault == "low_confidence":
                confidence = 0.45
            if self.fault == "missing_depth":
                refs = [r for r in refs if r.kind != "depth"]
            if self.fault == "missing_force":
                refs = [r for r in refs if r.kind != "force"]
            sample = store.record_sample(
                sample_id=str(stamp),
                edge_boot_id=edge_boot_id,
                edge_monotonic_ts_ns=stamp,
                action_id=action_id,
                source_id="gazebo/contact-fixture",
                object_id=self.target,
                confidence=confidence,
                values=values,
                evidence_refs=refs,
            )
            frames.append(sample)
            self.latest_frames.append(str(path))
        return frames
