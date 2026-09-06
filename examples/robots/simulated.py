"""In-memory contract examples, not physics, SLAM or hardware drivers.

Both factories are inert: no devices are opened. Simulated state changes are
explicitly reported as SIMULATION and sim_ground_truth. Real integrations must
replace these callbacks with calibrated perception, commissioned tool handlers,
an attended readiness check and a working stop implementation.
"""

from __future__ import annotations

import copy
import json
import threading
import time
from pathlib import Path

from tangying_robot_gateway.plugin_backend import PluginBackend
from tangying_robot_gateway.runtime import Result


class SimulatedRobot:
    def __init__(self, profile_filename: str):
        self.profile = json.loads(Path(__file__).with_name(profile_filename).read_text())
        self.lock = threading.RLock()
        self.sequence = 0
        self.stopped = False
        self.held = ""
        self.state = {"joints": {item["name"]: 0.0 for item in self.profile["joints"]}}
        self.state["base_pose"] = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0]
        self.entities = [
            {"entityId": "red-block", "category": "block", "attributes": {"color": "red"},
             "pose": [0.2, 0.0, 0.1, 1.0, 0.0, 0.0, 0.0], "confidence": 1.0, "relation": "on:table"},
            {"entityId": "tray", "category": "tray", "attributes": {"color": "blue"},
             "pose": [0.4, 0.0, 0.1, 1.0, 0.0, 0.0, 0.0], "confidence": 1.0, "relation": "on:table"},
            {"entityId": "table", "category": "table", "attributes": {},
             "pose": [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0], "confidence": 1.0, "relation": ""},
        ]

    def observe(self):
        with self.lock:
            self.sequence += 1
            sensor = self.profile["sensors"][0]
            return {
                "schemaVersion": "scene.reconstruction.v1", "robotId": self.profile["robotId"],
                "observationId": f"{sensor['sourceId']}/{self.sequence}",
                "sourceId": sensor["sourceId"], "sourceType": sensor["sourceType"],
                "sourceFrameId": sensor["frameId"], "frameId": "world", "units": "m",
                "transformRevision": sensor["transformRevision"],
                "observedAtUnixMs": int(time.time() * 1000), "sequence": self.sequence,
                "entities": copy.deepcopy(self.entities), "points": [[0.1, 0.0, 0.0], [0.2, 0.1, 0.0]],
            }

    def robot_state(self):
        with self.lock:
            return copy.deepcopy(self.state | {"held": self.held})

    def ready(self):
        with self.lock:
            return not self.stopped

    def stop(self, reason):
        with self.lock:
            self.stopped = True

    def execute(self, command):
        with self.lock:
            objects = {item["entityId"]: item for item in self.entities}
            values = command.parameters
            if command.capability in {"resolve_targets", "plan_grasp"}:
                valid = values["objectId"] in objects and values["destinationId"] in objects
            elif command.capability == "verify_grasp":
                valid = self.held == values["objectId"]
            elif command.capability == "verify_placement":
                item = objects.get(values["objectId"])
                valid = item is not None and not self.held and item["relation"] == "inside:" + values["destinationId"]
            elif command.capability == "manipulation.pick":
                valid = command.target_ref == "red-block" and not self.held
                if valid:
                    self.held = command.target_ref
                    objects[self.held]["relation"] = "held_by:" + self.profile["robotId"]
            elif command.capability == "manipulation.place":
                valid = bool(self.held) and command.target_ref == "tray"
                if valid:
                    objects[self.held]["relation"] = "inside:tray"
                    objects[self.held]["pose"] = list(objects["tray"]["pose"])
                    self.held = ""
            elif command.capability == "arm.move":
                valid = True
            elif command.capability == "navigation.navigate":
                goal = values["goalPose"]
                valid = abs(goal[0]) <= 2 and abs(goal[1]) <= 2 and goal[2] == 0
                if valid:
                    self.state["base_pose"] = list(goal)
            else:
                valid = False
            if valid:
                for action in values.get("action_chunk", []):
                    for key, value in action.items():
                        self.state["joints"][key.removesuffix(".position")] = value
            return Result(valid, "SIMULATED_OK" if valid else "SIMULATED_POSTCONDITION_FAILED",
                          "In-memory contract example; no real hardware or physics.",
                          confidence=1.0 if valid else 0.0)

    def backend(self):
        handlers = {name: self.execute for name in self.profile["tools"]
                    if name not in {"observe_scene", "emergency_stop"}}
        return PluginBackend(
            self.profile, observation_provider=self.observe, handlers=handlers,
            stop=self.stop, state_provider=self.robot_state,
            physical_ready=self.ready, simulation=True,
        )


def arm() -> PluginBackend:
    return SimulatedRobot("arm.profile.json").backend()


def mobile_sensor() -> PluginBackend:
    return SimulatedRobot("mobile_sensor.profile.json").backend()
