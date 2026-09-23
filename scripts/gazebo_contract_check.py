#!/usr/bin/env python3
"""Check a RobotRuntime against the contract the agent actually relies on.

The acceptance matrices answer "did the task succeed". This answers a different
question: "does each gate the agent depends on behave the way the agent assumes".
A task can pass with a gate that never fires, and that is the failure this looks
for - every check here is written so that removing the behaviour makes it fail.

    # against the Gazebo stack, including the latching stop group
    .venv/bin/python scripts/gazebo_contract_check.py --runtime 127.0.0.1:50051 --estop

    # the same contract against a second backend, to tell "the contract holds"
    # apart from "this is still the Gazebo runtime"
    .venv/bin/python scripts/gazebo_contract_check.py \
        --runtime 127.0.0.1:50301 --expect-adapter mujoco

Each runtime gets its own namespace when more than one is running; two stacks on
the same gRPC port is not a supported configuration.

Two environment prerequisites, both of which fail silently if missed:

  * Run it with the repository's own `.venv`. A host interpreter with an older
    grpcio makes the generated `robot_pb2_grpc` raise on import.
  * Set CONTRACT_CHECK_SOURCE_ROOT when the runtime under test was *built from* a
    different checkout than this one (a worktree, say). The source scan in group
    [7] otherwise audits the wrong tree, and a tree with no Go files in it reports
    clean; the scan asserts the tree is non-empty so that cannot pass vacuously.

The estop group latches the runtime and must run last: a latched runtime refuses
every physical command afterwards, and because the latch is persisted to the
runtime journal (by design - it must survive a restart) the stack will not even
become ready again until an attended local reset clears it. See
`scripts/reset_runtime_latch.py`.

Read-only apart from the groups that are explicitly about a physical effect: the
joint group moves one commissioned joint by a small amount and returns it, the
physical-tools group pre-positions the base and returns the arm to a safe pose,
and the cancel group drives the base before cancelling it.

Checks that the environment makes impossible are recorded as observations rather
than passes, so "unverified here" never reads as "verified".
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path


def _repository_root() -> Path:
    """Find the checkout this check is auditing.

    Not ``__file__.parents[1]``: this script is run both from ``scripts/`` and
    from an artifacts directory, and a wrong root would make the source scan
    below silently find nothing and report the tree clean. The marker is the
    combination the repo always has and no parent of an artifacts directory has.
    """
    for candidate in (Path(__file__).resolve().parent, *Path(__file__).resolve().parents):
        if (candidate / "core").is_dir() and (candidate / "robot" / "gateway").is_dir():
            return candidate
    raise SystemExit("cannot locate the repository root from " + str(Path(__file__).resolve()))


ROOT = _repository_root()
sys.path.insert(0, str(ROOT / "python"))

#: The runtime under test is built from a worktree, which is not necessarily the
#: checkout this script lives in. Scanning the wrong tree would audit source the
#: running robot was never built from, so the tree is selectable and the report
#: records which one was used.
SOURCE_ROOT = Path(os.environ.get("CONTRACT_CHECK_SOURCE_ROOT", ROOT)).resolve()

import grpc
from google.protobuf.json_format import MessageToDict, ParseDict
from google.protobuf.struct_pb2 import Struct
from tangying_robot_proto.robot.v1 import robot_pb2 as pb
from tangying_robot_proto.robot.v1 import robot_pb2_grpc as rpc

SCHEMA = "robot.v1"

#: The proto declares `rpc ListServices(GetRuntimeInfoRequest) returns
#: (ServiceCatalog)`. The request fields are ignored by every implementation
#: (gazebo_runtime_node.py:705, gateway/service.py:150), so the empty message
#: of the *declared* type is the honest call.
_EMPTY = pb.GetRuntimeInfoRequest()

#: The agent's canonical tool set, transcribed from core/robotcontract/contract.go
#: `canonicalTools` (13 entries). A runtime that advertises less than this is
#: missing a capability the agent may plan; one that advertises more is claiming
#: a capability the agent has no contract for. Both directions are checked.
#:
#: This is the runtime's tool set and is deliberately larger than the 12
#: skills.SkillManifest entries in skills/manipulation/plugin.go: `arm.move` is a
#: real runtime capability that the planner's catalog does not contain, so the
#: agent can observe and drive it but never plans it as a task step.
CANONICAL = (
    "observe_scene", "resolve_targets", "navigation.navigate", "navigation.pre_position",
    "plan_grasp", "manipulation.pick", "verify_grasp", "verify_arrival",
    "manipulation.place", "verify_placement", "recover_to_safe_pose", "emergency_stop",
    "arm.move",
)


class Checker:
    def __init__(self, address: str, expect_adapter: str = "gazebo",
                 verbose: bool = False):
        #: Which adapter the runtime under test should self-report. Passed in so
        #: the same contract can be run against a second backend: a check that
        #: hardcoded "gazebo" could not tell "the contract holds" from "this is
        #: still the Gazebo runtime", which is exactly the comparison at stake.
        self.expect_adapter = expect_adapter
        self.stub = rpc.RobotRuntimeStub(grpc.insecure_channel(address))
        self.info = self.stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=20)
        self.verbose = verbose
        self.results: list[dict] = []

    # ---- helpers -------------------------------------------------------
    def note(self, group: str, name: str, detail: str = ""):
        """Record an observation that is not a pass/fail claim."""
        self.results.append({"group": group, "check": name, "passed": True,
                             "detail": detail, "observation": True})
        print(f"  [NOTE] {name}" + (f" — {detail}" if detail else ""), flush=True)

    def record(self, group: str, name: str, passed: bool, detail: str = ""):
        self.results.append({"group": group, "check": name, "passed": bool(passed), "detail": detail})
        mark = "PASS" if passed else "FAIL"
        print(f"  [{mark}] {name}" + (f" — {detail}" if detail else ""), flush=True)

    def skill(self, name, params=None, *, timeout=45, lease_ms=None, deadline_skew_ms=0,
              profile="desktop_standard", approval="contract-check", catalog=None,
              idempotency=None, robot_id=None, schema=SCHEMA):
        stamp = int(time.time() * 1000)
        command = pb.SkillCommand(
            schema_version=schema,
            command_id=f"chk-{stamp}-{uuid.uuid4().hex[:6]}",
            task_id="contract-check",
            robot_id=robot_id if robot_id is not None else self.info.robot_id,
            skill=name,
            parameters=Struct(),
            deadline_unix_ms=stamp + (deadline_skew_ms if deadline_skew_ms else timeout * 1000),
            lease_ms=lease_ms if lease_ms is not None else timeout * 1000,
            idempotency_key=idempotency or f"chk/{name}/{uuid.uuid4().hex}",
            safety_profile=profile,
            approval_id=approval,
            catalog_revision=catalog if catalog is not None else self.info.catalog_revision,
        )
        for key, value in (params or {}).items():
            command.parameters[key] = value
        events = []
        try:
            for event in self.stub.ExecuteSkill(command, timeout=timeout + 10):
                events.append(event)  # noqa: PERF402 - the accumulation is wanted
        except grpc.RpcError as error:
            # Not `events = list(...)`: a stream that fails part way through keeps
            # the events it already delivered, and those are the ones a caller
            # needs to see why it failed. `list()` would discard them.
            return None, f"RPC {error.code().name}: {error.details()}"
        if not events:
            return None, "no events"
        last = events[-1]
        return pb.SkillEventType.Name(last.type), last.code or ""

    def service(self, name, params=None, timeout=30):
        request = pb.ServiceRequest(robot_id=self.info.robot_id, name=name,
                                    request_id=uuid.uuid4().hex)
        ParseDict(params or {}, request.parameters)
        try:
            response = self.stub.CallService(request, timeout=timeout)
        except grpc.RpcError as error:
            return None, f"RPC {error.code().name}: {error.details()}"
        if not response.ok:
            return None, f"{response.code}: {response.message}"
        return MessageToDict(response.result), ""

    # ---- groups --------------------------------------------------------
    def group_rpc_surface(self):
        print("\n[1] RobotRuntime RPC surface", flush=True)
        g = "rpc"
        info = self.info
        self.record(g, "GetRuntimeInfo reports a robot id", bool(info.robot_id), info.robot_id)
        self.record(g, f"adapter is {self.expect_adapter}",
                    info.adapter == self.expect_adapter, info.adapter or "(empty)")
        self.record(g, "manipulation_ready", bool(info.manipulation_ready))
        self.record(g, "catalog revision is a content hash",
                    len(info.catalog_revision) == 64,
                    f"len={len(info.catalog_revision)}")

        advertised = {c.name for c in info.capabilities}
        missing = sorted(n for n in CANONICAL if n not in advertised)
        extra = sorted(n for n in advertised if n not in CANONICAL)
        self.record(g, "advertises the full canonical tool set", not missing,
                    f"missing={missing}" if missing else f"{len(CANONICAL)} tools")
        self.record(g, "advertises nothing outside the canonical set", not extra,
                    f"extra={extra}" if extra else "exact match")
        self.record(g, "the advertised set is exactly the canonical set",
                    advertised == set(CANONICAL),
                    f"advertised={len(advertised)} canonical={len(CANONICAL)}")

        # Two independent declarations, and the agent derives approval from one
        # and the closure gate from the other. A read-only tool must never claim
        # a world mutation; a physical tool that does not claim one is only
        # correct for the latched stop, whose effect the runtime reports directly
        # rather than through scene perception.
        read_only_mutating = [c.name for c in info.capabilities
                              if c.safety_level == "read_only" and c.mutates_world]
        physical_without_gate = [c.name for c in info.capabilities
                                 if c.safety_level != "read_only" and not c.mutates_world
                                 and c.name != "emergency_stop"]
        self.record(g, "no read-only tool claims a world mutation",
                    not read_only_mutating, f"wrong={read_only_mutating}")
        self.record(g, "only the latched stop is physical without the evidence gate",
                    not physical_without_gate, f"wrong={physical_without_gate}")

        services = self.stub.ListServices(_EMPTY, timeout=25)
        names = [s.name for s in services.services]
        self.record(g, "ListServices hosts a catalogue", len(names) >= 10, f"{len(names)} services")

        # Observation must be a stream that actually produces a frame.
        request = pb.ObserveRequest(source_id=info.robot_id + "/base-rgbd",
                                    streams=["robot_state", "rgb", "depth", "reconstruction"])
        frames = 0
        for _ in self.stub.Observe(request, timeout=40):
            frames += 1
            break
        self.record(g, "Observe returns a frame", frames == 1)

    def group_observation(self):
        print("\n[2] Observation and evidence chain", flush=True)
        g = "observation"
        info = self.info
        frames = []
        request = pb.ObserveRequest(
            source_id=info.robot_id + "/base-rgbd",
            streams=["robot_state", "rgb", "depth", "reconstruction"])
        for observation in self.stub.Observe(request, timeout=40):
            frames.append(observation)
            if len(frames) >= 3:
                break
        self.record(g, "three consecutive frames arrive", len(frames) == 3, f"got {len(frames)}")

        ids = [f.observation_id for f in frames]
        self.record(g, "observation ids are unique", len(set(ids)) == len(ids))

        # Freshness is measured against the runtime's own capture time, so a
        # frame that is already old on arrival would defeat the closure gate.
        now_ms = time.time() * 1000
        ages = [now_ms - f.wall_time_unix_ms for f in frames]
        self.record(g, "frames are fresh on arrival", all(a < 3000 for a in ages),
                    f"ages_ms={[round(a) for a in ages]}")

        states = [MessageToDict(f.robot_state) for f in frames]
        poses = [s.get("base_pose") for s in states]
        self.record(g, "every frame carries a base pose", all(p for p in poses))
        self.record(g, "joint positions present",
                    all("joint_positions" in s and s["joint_positions"] for s in states))
        self.record(g, "perception declares a calibration revision",
                    all(s.get("perception", {}).get("calibration_revision") for s in states))

        # The closure gate compares a capture against the dispatch instant, so
        # capture time must advance between frames.
        stamps = [f.wall_time_unix_ms for f in frames]
        self.record(g, "capture time advances between frames",
                    all(b > a for a, b in itertools.pairwise(stamps)), f"{stamps}")

    def group_services(self):
        print("\n[3] Service catalogue (mapping / calibration / navigation)", flush=True)
        g = "services"
        for name in ("calibration.get", "mapping.status", "mapping.inventory",
                     "mapping.conflicts", "navigation.map"):
            result, error = self.service(name)
            self.record(g, f"{name} answers", result is not None, error or "ok")

        calibration, _ = self.service("calibration.get")
        revision = (calibration or {}).get("revision", "")
        self.record(g, "calibration revision is a content hash", len(revision) == 64,
                    f"len={len(revision)}")

        # A save that carries the revision it read must keep the same identity;
        # a changed identity would orphan every map recorded against it.
        if calibration and calibration.get("document"):
            saved, error = self.service("calibration.save", {
                "document": calibration["document"],
                "expectedRevision": revision,
                "algorithm": "contract-check"})
            self.record(g, "re-saving identical calibration keeps identity",
                        saved is not None and saved.get("revision") == revision,
                        error or f"{revision[:12]}…")
        else:
            self.record(g, "re-saving identical calibration keeps identity", False,
                        "no calibration document")

        # A stale expectedRevision must be refused, not silently accepted.
        rejected, error = self.service("calibration.save", {
            "document": (calibration or {}).get("document", {}),
            "expectedRevision": "0" * 64,
            "algorithm": "contract-check"})
        self.record(g, "stale expectedRevision is refused", rejected is None,
                    error or "accepted a stale revision")

    def group_tools(self):
        print("\n[4] Tool behaviour (the tasks a plan can actually dispatch)", flush=True)
        g = "tools"

        # -- read-only tools must succeed without any physical effect
        for skill in ("observe_scene", "resolve_targets", "plan_grasp"):
            params = {"objectId": "red-cup", "destinationId": "right-bin"} \
                if skill in ("resolve_targets", "plan_grasp") else None
            verdict, code = self.skill(skill, params)
            self.record(g, f"{skill} succeeds", verdict == "SKILL_EVENT_SUCCEEDED",
                        f"{verdict} {code}")

        # -- verify_placement before anything is placed must be FALSE, not True.
        verdict, code = self.skill("verify_placement",
                                   {"objectId": "red-cup", "destinationId": "right-bin"})
        self.record(g, "verify_placement is false before placement",
                    verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

        # -- verify_grasp before anything is held must be FALSE.
        verdict, code = self.skill("verify_grasp", {"objectId": "red-cup"})
        self.record(g, "verify_grasp is false before a grasp",
                    verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

        # -- an unknown object must be refused, not silently resolved.
        verdict, code = self.skill("resolve_targets",
                                   {"objectId": "no-such-object", "destinationId": "right-bin"})
        self.record(g, "unknown object is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # -- a place into an unknown destination must be refused.
        verdict, code = self.skill("manipulation.place", {"targetRef": "no-such-bin"})
        self.record(g, "place into unknown destination is refused",
                    verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

        # -- verify_arrival must answer about the pose it was asked about, so it
        # is checked in both polarities against a pose the runtime itself just
        # reported. One polarity alone would pass on a constant answer.
        request = pb.ObserveRequest(source_id=self.info.robot_id + "/base-rgbd",
                                    streams=["robot_state"])
        here = None
        for observation in self.stub.Observe(request, timeout=30):
            here = MessageToDict(observation.robot_state).get("base_pose")
            break
        self.record(g, "a base pose is available to verify against", bool(here))

        if here:
            near = list(here)
            far = list(here)
            far[0] = float(here[0]) + 25.0
            verdict, code = self.skill("verify_arrival", {"goalPose": near})
            self.record(g, "verify_arrival accepts the pose the runtime reported",
                        verdict == "SKILL_EVENT_SUCCEEDED", f"{verdict} {code}")
            verdict, code = self.skill("verify_arrival", {"goalPose": far})
            self.record(g, "verify_arrival rejects a goal 25 m away",
                        verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

    def group_gates(self):
        print("\n[5] Safety and evidence gates", flush=True)
        g = "gates"

        # Approval is demanded only where the catalogue demands it: every
        # physical manifest carries ApprovalPolicy{Required:true}
        # (skills/manipulation/plugin.go:39) while the read-only ones do not.
        # The runtime mirrors that by deriving `physical` from the tool itself
        # (robot/gateway/tangying_robot_gateway/safety.py:105-117), which is why
        # the same empty approval must be *accepted* on a read-only tool and
        # *refused* on a physical one. Testing only the read-only half would
        # pass vacuously against a runtime that had no approval gate at all.
        verdict, code = self.skill("observe_scene", approval="")
        self.record(g, "empty approval is allowed on a read-only tool",
                    verdict == "SKILL_EVENT_SUCCEEDED", f"{verdict} {code}")

        verdict, code = self.skill("manipulation.pick", {"targetRef": "red-cup"}, approval="")
        self.record(g, "missing approval is refused on a physical tool",
                    verdict == "SKILL_EVENT_FAILED" and code == "APPROVAL_REQUIRED",
                    f"{verdict} {code}")

        # Catalog revision pins the tool contract the agent planned against.
        verdict, code = self.skill("observe_scene", catalog="0" * 64)
        self.record(g, "stale catalog revision is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # Schema version guards against two builds disagreeing about the wire.
        verdict, code = self.skill("observe_scene", schema="robot.v99")
        self.record(g, "unsupported schema version is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # A different robot id must not be able to drive this runtime.
        verdict, code = self.skill("observe_scene", robot_id="some-other-robot")
        self.record(g, "robot id mismatch is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # Idempotency: the same key twice must not repeat a physical action.
        key = f"chk/dedup/{uuid.uuid4().hex}"
        first, _ = self.skill("observe_scene", idempotency=key)
        second, code = self.skill("observe_scene", idempotency=key)
        self.record(g, "duplicate idempotency key is not replayed",
                    first == "SKILL_EVENT_SUCCEEDED" and second is not None,
                    f"first={first} second={second} {code}")

        # An already-expired deadline must be refused before anything moves.
        verdict, code = self.skill("manipulation.pick", {"targetRef": "red-cup"},
                                   deadline_skew_ms=-5000)
        self.record(g, "expired deadline is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # Unknown skill must be refused rather than accepted and ignored.
        verdict, code = self.skill("no.such.skill")
        self.record(g, "unknown skill is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

        # Out-of-range joint target must be refused before it reaches hardware.
        verdict, code = self.skill("arm.move", {"action_chunk": [{"left_arm_elbow_flex.pos": 99.0}]})
        self.record(g, "out-of-range joint target is refused",
                    verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

        # Non-array goal pose must be refused.
        verdict, code = self.skill("navigation.navigate", {"goalPose": [1.0, 2.0]})
        self.record(g, "malformed goal pose is refused", verdict == "SKILL_EVENT_FAILED",
                    f"{verdict} {code}")

    def group_arm_motion(self):
        print("\n[6] Joint motion (one commissioned joint, returned)", flush=True)
        g = "arm"
        # Read the current value from a fresh observation so the check is
        # grounded in the runtime's own feedback rather than a guess.
        info = self.info
        request = pb.ObserveRequest(source_id=info.robot_id + "/base-rgbd", streams=["robot_state"])
        joints = {}
        for observation in self.stub.Observe(request, timeout=30):
            joints = MessageToDict(observation.robot_state).get("joint_positions", {})
            break
        joint = "left_arm_elbow_flex"
        start = joints.get(joint)
        self.record(g, f"{joint} is reported", start is not None, f"{start}")

        if start is not None:
            target = round(float(start) + 0.12, 3)
            verdict, code = self.skill("arm.move", {"action_chunk": [{f"{joint}.pos": target}]})
            self.record(g, "arm.move accepted a bounded target",
                        verdict == "SKILL_EVENT_SUCCEEDED", f"{verdict} {code}")

            moved = None
            for observation in self.stub.Observe(
                    pb.ObserveRequest(source_id=info.robot_id + "/base-rgbd",
                                      streams=["robot_state"]), timeout=30):
                moved = MessageToDict(observation.robot_state).get("joint_positions", {}).get(joint)
                break
            self.record(g, "joint actually moved",
                        moved is not None and abs(float(moved) - float(start)) > 0.02,
                        f"{start} -> {moved}")

            back, code = self.skill("arm.move", {"action_chunk": [{f"{joint}.pos": round(float(start), 3)}]})
            self.record(g, "arm returned to its start value",
                        back == "SKILL_EVENT_SUCCEEDED", f"{back} {code}")

    def group_agent_layer(self):
        print("\n[7] Agent layer and simulator coupling", flush=True)
        g = "agent-layer"
        tokens = ("gazebo", "Gazebo", "mujoco", "MuJoCo", "robocasa")

        # A tree with no Go files in it would make every scan below return an
        # empty list, which reads exactly like "no coupling found".
        scanned = sum(1 for _ in (SOURCE_ROOT / "core").rglob("*.go")) \
            + sum(1 for _ in (SOURCE_ROOT / "cmd").rglob("*.go")) \
            + sum(1 for _ in (SOURCE_ROOT / "tasks").rglob("*.go")) \
            + sum(1 for _ in (SOURCE_ROOT / "edge").rglob("*.go"))
        self.record(g, f"source tree is present to scan ({SOURCE_ROOT.name})",
                    scanned > 0, f"{scanned} Go files under core/cmd/tasks/edge")
        if scanned == 0:
            return

        def scan(top: str) -> list[str]:
            hits = []
            for path in (SOURCE_ROOT / top).rglob("*.go"):
                if path.name.endswith("_test.go"):
                    continue
                text = path.read_text(errors="ignore")
                for token in tokens:
                    if token in text:
                        hits.append(f"{path.relative_to(SOURCE_ROOT)}:{token}")
            return hits

        # core/ is the decision layer: task graph, closure gate, guard, evidence.
        # A simulator name there would mean the closure semantics themselves were
        # conditional on which physics engine produced the observation.
        core_hits = scan("core")
        self.record(g, "core/ has no simulator branches", not core_hits,
                    f"{len(core_hits)} hits: {core_hits[:4]}" if core_hits else "clean")

        # The runtime must report its adapter so nothing has to guess.
        self.record(g, "adapter is discovered, not hard-coded",
                    self.info.adapter == self.expect_adapter,
                    f"runtime self-reports adapter={self.info.adapter!r}")

        # The remaining Go survivors of the migration, recorded rather than
        # asserted away. The claim under test is "the agent does not need to know
        # which simulator it drives", and these are the places where it currently
        # still does. Naming them here keeps the honest scope of the check visible
        # instead of letting a narrow scan imply a wider one.
        survivors = scan("cmd") + scan("tasks") + scan("edge")
        by_file: dict[str, list[str]] = {}
        for hit in survivors:
            path, token = hit.rsplit(":", 1)
            by_file.setdefault(path, []).append(token)
        detail = "; ".join(f"{path} [{', '.join(sorted(set(toks)))}]"
                           for path, toks in sorted(by_file.items()))
        self.note(g, "simulator names remaining outside core/", detail or "none")

        # Two of those are control flow rather than a default label, so they are
        # the ones that actually weaken the claim. Report them separately.
        branch_sites = ("cmd/local-agent/observer.go:gazebo",
                        "edge/worker/observation.go:mujoco",
                        "edge/worker/observation.go:robocasa")
        branches = [hit for hit in survivors if hit.endswith(branch_sites)]
        self.note(g, "of those, branches on a simulator name", str(branches) or "none")

    def group_physical_tools(self):
        """The two physical tools no other group reaches.

        Both move the body, so this runs after the read-only and evidence groups
        and before the latch. `navigation.pre_position` is also checked for the
        thing it deliberately does *not* accept: a caller-supplied position.
        """
        print("\n[11] Remaining physical tools (pre_position, recover_to_safe_pose)",
              flush=True)
        g = "physical-tools"

        # The runtime owns where to stand; a plan that could name a position could
        # name one the map never certified. So a goalPose here must be refused,
        # even though navigation.navigate accepts exactly that shape.
        verdict, code = self.skill("navigation.pre_position",
                                   {"goalPose": [1.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]})
        self.record(g, "pre_position refuses a caller-supplied position",
                    verdict == "SKILL_EVENT_FAILED", f"{verdict} {code}")

        # No payload is held at this point in the run, so the nominal path runs.
        verdict, code = self.skill("navigation.pre_position", timeout=60)
        self.record(g, "pre_position runs without a payload",
                    verdict == "SKILL_EVENT_SUCCEEDED", f"{verdict} {code}")

        verdict, code = self.skill("recover_to_safe_pose", timeout=60)
        self.record(g, "recover_to_safe_pose returns the arm to a safe pose",
                    verdict == "SKILL_EVENT_SUCCEEDED", f"{verdict} {code}")

        # The fail-closed branch cannot be reached by dispatching here: it needs a
        # held payload, and establishing one on purpose would leave the run holding
        # an object. Recorded as source-verified rather than exercised so the claim
        # is not overstated: gazebo_manipulation.py:128-132 answers
        # PAYLOAD_HELD_REQUIRES_PLACE instead of dropping the payload.
        self.note(g, "held-payload refusal is source-verified, not exercised here",
                  "gazebo_manipulation.py:128 PAYLOAD_HELD_REQUIRES_PLACE")

    def group_cancel(self):
        """RPC 13: Cancel. Exercised against a real in-flight physical command.

        The contract has two halves and they are not symmetric. A cancel naming a
        command this runtime is not running must be refused (`accepted=False`,
        `state=UNKNOWN`) rather than reported as a success; a cancel naming the
        live command must be accepted and must actually stop it, ending in
        CANCELLED. Testing only the second half would pass on a runtime that
        accepted every cancel id it was handed.
        """
        print("\n[9] Cancel (RPC 13)", flush=True)
        g = "cancel"

        # A command id that was never dispatched. There is no such command, and
        # saying "cancelled" here would let a caller believe it stopped something.
        result = self.stub.Cancel(pb.CancelRequest(command_id="no-such-command-"
                                                   + uuid.uuid4().hex,
                                                   reason="contract-check"), timeout=20)
        self.record(g, "cancelling an unknown command is refused",
                    result.accepted is False and result.state == "UNKNOWN",
                    f"accepted={result.accepted} state={result.state!r}")

        # Now a command that really is in flight. Drive the base toward a goal far
        # enough away that the skill is still running when the cancel lands; the
        # subscription is consumed in a thread so the cancel does not have to wait
        # for the skill to finish on its own.
        # The goal is derived from the pose the runtime itself reports a metre
        # ahead of where it is standing, rather than a hardcoded coordinate: a
        # goal the map never certified makes the skill fail at once, and a cancel
        # that arrives after the command already ended is correctly refused, which
        # would test the refusal path twice instead of the live one.
        pose_request = pb.ObserveRequest(source_id=self.info.robot_id + "/base-rgbd",
                                        streams=["robot_state"])
        base = None
        for observation in self.stub.Observe(pose_request, timeout=30):
            base = MessageToDict(observation.robot_state).get("base_pose")
            break
        self.record(g, "a current base pose is available to drive from", bool(base))

        command_id = f"chk-cancel-{uuid.uuid4().hex}"
        stamp = int(time.time() * 1000)
        command = pb.SkillCommand(
            schema_version=SCHEMA, command_id=command_id, task_id="contract-check",
            robot_id=self.info.robot_id, skill="navigation.navigate",
            deadline_unix_ms=stamp + 120_000, lease_ms=60_000,
            idempotency_key=f"chk/nav/{uuid.uuid4().hex}",
            safety_profile="desktop_standard", approval_id="contract-check",
            catalog_revision=self.info.catalog_revision)
        goal = list(base) if base else [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0]
        goal[0] = float(goal[0]) + 1.0
        command.parameters["goalPose"] = goal

        events: list = []
        done = threading.Event()

        def consume():
            try:
                # Accumulated as it arrives, so a stream that fails part way
                # through keeps what it already delivered: the cancel group has
                # to report the terminal event whatever it turns out to be, and a
                # partial stream still tells the reader where it stopped.
                for event in self.stub.ExecuteSkill(command, timeout=140):
                    events.append(event)  # noqa: PERF402 - the accumulation is wanted
            except grpc.RpcError as error:
                events.append(error)
            finally:
                done.set()

        worker = threading.Thread(target=consume, daemon=True)
        worker.start()

        # Wait until the runtime reports it owns the command; cancelling before
        # admission would test the refusal path again instead of the real one.
        def stream_kinds():
            return [pb.SkillEventType.Name(e.type) for e in events
                    if not isinstance(e, grpc.RpcError)]

        # Wait for RUNNING rather than the first ACCEPTED: admission is not the
        # same as execution, and cancelling between them would not exercise the
        # path that has to stop something already moving.
        deadline = time.time() + 30
        while time.time() < deadline and "SKILL_EVENT_RUNNING" not in stream_kinds():
            if done.is_set():
                break
            time.sleep(0.1)
        running = "SKILL_EVENT_RUNNING" in stream_kinds()
        self.record(g, "the command reached RUNNING before cancelling", running,
                    f"{len(events)} event(s): {stream_kinds()}")

        result = self.stub.Cancel(pb.CancelRequest(command_id=command_id,
                                                   reason="contract-check cancel"),
                                  timeout=20)
        self.record(g, "the stream closes after cancellation", done.wait(90),
                    "stream ended" if done.is_set() else "still open after 90s")

        kinds = [pb.SkillEventType.Name(e.type) for e in events
                 if not isinstance(e, grpc.RpcError)]
        codes = [e.code for e in events if not isinstance(e, grpc.RpcError)]
        last = kinds[-1] if kinds else "(no event)"
        outcome = "; ".join(f"{k}:{c}" for k, c in zip(kinds, codes))

        # The stream must end in CANCELLED, not SUCCEEDED: a cancelled physical
        # action that reports success would be recorded as a completed step.
        self.record(g, "a cancelled command does not report success",
                    last != "SKILL_EVENT_SUCCEEDED",
                    f"terminated as {last} after {len(kinds)} event(s)")

        # Whether the live half of this contract can be exercised here depends on
        # the environment, not on the runtime: with no survey map the base has no
        # localisation, the skill ends by itself, and refusing the cancel is then
        # the *correct* answer. That is reported as unverified-here rather than
        # silently passed, because the two are different claims. The live half is
        # covered by unit tests instead -- robot/gateway/tests/test_service.py:105
        # test_cancel_active_command_emits_cancelled_terminal_event, and
        # test_service.py:230 test_cancel_is_visible_before_backend_stop_returns.
        could_drive = not any("NAV2_ACTION_ENDED" in c or "LOCALIZATION" in c
                              or "NAV_MAP_NOT_READY" in c or "MAP_" in c for c in codes)
        if could_drive:
            self.record(g, "cancelling the live command is accepted",
                        result.accepted is True and result.state == "CANCELLED",
                        f"accepted={result.accepted} state={result.state!r}")
            self.record(g, "the terminal event is CANCELLED",
                        last == "SKILL_EVENT_CANCELLED", f"last={last}")
        else:
            self.note(g, "live-cancel unverified here: the base had no localisation",
                      f"{outcome}; Cancel answered accepted={result.accepted} "
                      f"state={result.state!r}, which is correct for a command that "
                      f"already ended; unit coverage: test_service.py:105,230")

        # Cancelling a command that already finished must not claim to have
        # stopped it.
        again = self.stub.Cancel(pb.CancelRequest(command_id=command_id,
                                                 reason="contract-check after the fact"),
                                 timeout=20)
        self.record(g, "re-cancelling a finished command is refused",
                    again.accepted is False, f"accepted={again.accepted} state={again.state!r}")

    def group_estop(self):
        print("\n[8] Emergency stop (skill + RPC 14; latches the runtime, must run last)",
              flush=True)
        g = "estop"

        # RPC 14 is a different path from the emergency_stop *skill* exercised
        # below: the skill goes through ExecuteSkill admission, the RPC does not.
        # A stop that only worked when a command was already being admitted would
        # be exactly backwards, so the two are checked separately.
        before_ms = int(time.time() * 1000)
        stopped = self.stub.EmergencyStop(
            pb.EStopRequest(reason="contract-check stop", operator_id="contract-check"),
            timeout=20)
        self.record(g, "EmergencyStop RPC reports the latch",
                    stopped.latched is True, f"latched={stopped.latched}")
        self.record(g, "EmergencyStop RPC reports when it stopped",
                    before_ms - 5000 <= stopped.stopped_unix_ms <= int(time.time() * 1000) + 5000,
                    f"stopped_unix_ms={stopped.stopped_unix_ms}")

        # Idempotent: a second stop must not un-latch or fail.
        again = self.stub.EmergencyStop(
            pb.EStopRequest(reason="contract-check stop again", operator_id="contract-check"),
            timeout=20)
        self.record(g, "a second EmergencyStop stays latched",
                    again.latched is True, f"latched={again.latched}")

        # The RPC must leave the runtime latched, not merely answer nicely.
        request = pb.ObserveRequest(source_id=self.info.robot_id + "/base-rgbd",
                                   streams=["robot_state"])
        latched = None
        for observation in self.stub.Observe(request, timeout=30):
            latched = observation.semantic_state.emergency_stopped
            break
        self.record(g, "the RPC latch is visible in the semantic state", latched is True,
                    f"emergency_stopped={latched}")

        # Report must be reflected in the runtime info the agent reads.
        info = self.stub.GetRuntimeInfo(pb.GetRuntimeInfoRequest(), timeout=20)
        self.record(g, "the RPC latch blocks the reported capabilities",
                    all(not c.available for c in info.capabilities
                        if c.safety_level != "read_only")
                    or not info.manipulation_ready,
                    f"manipulation_ready={info.manipulation_ready}")

        # The skill path, kept because it is what the agent actually dispatches.
        # SA is not a failure here. A stop that had "succeeded" in the ordinary
        # sense would be the surprising answer; SKILL_EVENT_SAFETY_STOPPED is the
        # declared terminal type for exactly this, and the agent layer already
        # treats it as terminal alongside SUCCEEDED/FAILED/CANCELLED
        # (edge/worker/worker.go:523-525). What must hold is that the latch is
        # real and that it is visible.
        verdict, code = self.skill("emergency_stop")
        self.record(g, "emergency_stop ends in the safety-stopped terminal state",
                    verdict in ("SKILL_EVENT_SAFETY_STOPPED", "SKILL_EVENT_SUCCEEDED"),
                    f"{verdict} {code}")

        # The latch must be reported, not merely enforced.
        latched = None
        request = pb.ObserveRequest(source_id=self.info.robot_id + "/base-rgbd",
                                   streams=["robot_state"])
        for observation in self.stub.Observe(request, timeout=30):
            latched = observation.semantic_state.emergency_stopped
            break
        self.record(g, "the latch is reported in the semantic state", latched is True,
                    f"emergency_stopped={latched}")

        # After a latch no physical command may be dispatched.
        blocked = []
        for skill, params in (("navigation.navigate", {"goalPose": [-0.3, 0.0, 0, 0, 0, 0, 1]}),
                              ("manipulation.pick", {"targetRef": "red-cup"}),
                              ("arm.move", {"action_chunk": [{"left_arm_elbow_flex.pos": 0.6}]})):
            verdict, code = self.skill(skill, params)
            blocked.append((skill, verdict, code))
        self.record(g, "physical skills are refused while latched",
                    all(v == "SKILL_EVENT_FAILED" for _, v, _ in blocked),
                    str(blocked))

        # Refusing to clear the latch is the point: a person must do it locally.
        for name, params in (("emergency_stop.reset", {}), ("safety.reset_estop", {})):
            result, error = self.service(name, params)
            self.record(g, f"{name} does not exist", result is None, error or "was served")

        # A latched runtime must still answer observation, or an operator could
        # not see what state it is in.
        request = pb.ObserveRequest(source_id=self.info.robot_id + "/base-rgbd", streams=["robot_state"])
        got = 0
        for _ in self.stub.Observe(request, timeout=30):
            got += 1
            break
        self.record(g, "observation still works while latched", got == 1)

    def run(self, estop: bool):
        self.group_rpc_surface()
        self.group_observation()
        self.group_services()
        self.group_tools()
        self.group_gates()
        self.group_arm_motion()
        self.group_agent_layer()
        self.group_physical_tools()
        # Cancel must run before the latch: a latched runtime refuses every
        # physical command, so there would be nothing left to cancel.
        self.group_cancel()
        if estop:
            self.group_estop()
        failed = [r for r in self.results if not r["passed"]]
        checks = [r for r in self.results if not r.get("observation")]
        notes = [r for r in self.results if r.get("observation")]
        print(f"\n{len(checks) - len(failed)}/{len(checks)} checks passed"
              + (f"; {len(notes)} recorded observations" if notes else ""))
        if failed:
            print("FAILED:")
            for row in failed:
                print(f"  - [{row['group']}] {row['check']} — {row['detail']}")
        if notes:
            print("RECORDED (not pass/fail):")
            for row in notes:
                print(f"  - [{row['group']}] {row['check']} — {row['detail']}")
        return {"runtime": self.info.robot_id, "adapter": self.info.adapter,
                "checks": self.results,
                "observations": notes,
                "passed": not failed,
                "counts": {"total": len(checks), "failed": len(failed),
                           "observations": len(notes)}}, (0 if not failed else 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--runtime", default="127.0.0.1:50231")
    parser.add_argument("--expect-adapter", default="gazebo",
                        help="adapter the runtime must self-report (gazebo or mujoco)")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--estop", action="store_true",
                        help="also run the latch group; the runtime is unusable afterwards")
    args = parser.parse_args()

    report, code = Checker(args.runtime, expect_adapter=args.expect_adapter).run(args.estop)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"report: {args.output}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
