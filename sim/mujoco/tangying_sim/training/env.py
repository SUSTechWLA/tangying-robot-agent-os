from __future__ import annotations

import random
from dataclasses import dataclass

import mujoco

from tangying_sim.tools import ToolContext
from tangying_sim.world import TabletopWorld

_OBJECT_CATEGORIES = ("cup", "bottle", "block")
_COLORS = ("red", "blue", "green")
_OBJECT_IDS = tuple(f"{color}-{category}" for category in _OBJECT_CATEGORIES for color in _COLORS)
_DESTINATION_IDS = ("left-bin", "right-bin", "front-tray")
OBJECT_GROUNDING_ACTIONS = tuple(f"ground_object:{entity_id}" for entity_id in _OBJECT_IDS)
DESTINATION_GROUNDING_ACTIONS = tuple(
    f"ground_destination:{entity_id}" for entity_id in _DESTINATION_IDS
)
TOOL_ACTIONS = (
    "observe_scene",
    "plan_grasp",
    "manipulation.pick",
    "verify_grasp",
    "manipulation.place",
    "verify_placement",
    "recover_to_safe_pose",
)
ACTIONS = (
    "observe_scene",
    *OBJECT_GROUNDING_ACTIONS,
    *DESTINATION_GROUNDING_ACTIONS,
    *TOOL_ACTIONS[1:],
)

_PHASES = (
    "observe",
    "ground_object",
    "ground_destination",
    "plan",
    "pick",
    "verify_grasp",
    "place",
    "verify_placement",
    "complete",
)
_EXPECTED_TOOL_ACTIONS = {
    "observe": "observe_scene",
    "plan": "plan_grasp",
    "pick": "manipulation.pick",
    "verify_grasp": "verify_grasp",
    "place": "manipulation.place",
    "verify_placement": "verify_placement",
}
_PROGRESS_REWARDS = (0.10, 0.35, 0.35, 0.50, 2.0, 1.0, 4.0, 10.0)
_DESTINATIONS = (
    ("storage_bin", "left_side"),
    ("storage_bin", "right_side"),
    ("delivery_tray", "front_side"),
)


@dataclass(frozen=True)
class Goal:
    category: str
    color: str
    destination_category: str
    destination_relation: str

    @property
    def kind(self) -> str:
        if self.destination_category == "delivery_tray":
            return "fetch"
        return "pick_and_place"


@dataclass(frozen=True)
class SemanticObservation:
    goal: Goal
    phase: str
    object_id: str
    destination_id: str
    grounded_object_id: str
    grounded_destination_id: str
    grounded: bool
    held: str
    placement_state: str
    grasp_verified: bool
    placement_verified: bool
    recovery_required: bool
    remaining_budget: int

    def state_key(self) -> tuple[str, ...]:
        remaining_stages = max(0, 8 - _PHASES.index(self.phase))
        if self.remaining_budget <= 0:
            budget = "budget:exhausted"
        elif self.remaining_budget < remaining_stages:
            budget = "budget:tight"
        else:
            budget = "budget:ample"
        held_state = "held:goal" if self.held == self.object_id else "held:none"
        if self.held and self.held != self.object_id:
            held_state = "held:other"
        return (
            f"goal:{self.goal.kind}",
            f"goal_object:{self.goal.category}:{self.goal.color}:{self.object_id}",
            (
                "goal_destination:"
                f"{self.goal.destination_category}:{self.goal.destination_relation}:"
                f"{self.destination_id}"
            ),
            f"phase:{self.phase}",
            f"grounded_object:{self.grounded_object_id or 'none'}",
            f"grounded_destination:{self.grounded_destination_id or 'none'}",
            held_state,
            f"placement:{self.placement_state}",
            "grasp_verified:yes" if self.grasp_verified else "grasp_verified:no",
            "placement_verified:yes" if self.placement_verified else "placement_verified:no",
            "recovery:required" if self.recovery_required else "recovery:clear",
            budget,
        )


def candidate_action_indices(observation: SemanticObservation) -> tuple[int, ...]:
    if observation.recovery_required:
        return (ACTIONS.index("recover_to_safe_pose"),)
    if observation.phase == "ground_object":
        return tuple(ACTIONS.index(action) for action in OBJECT_GROUNDING_ACTIONS)
    if observation.phase == "ground_destination":
        return tuple(ACTIONS.index(action) for action in DESTINATION_GROUNDING_ACTIONS)
    return tuple(ACTIONS.index(action) for action in TOOL_ACTIONS)


class SemanticToolEnv:
    """Discrete semantic policy environment backed by the runtime's real tools."""

    STEP_COST = -0.05
    INVALID_ORDER_PENALTY = -0.75
    TOOL_FAILURE_PENALTY = -1.0
    REPEATED_NOOP_PENALTY = -0.25
    RECOVERY_REWARD = 0.30
    WRONG_GROUNDING_PENALTY = -1.25
    TIMEOUT_PENALTY = -2.0
    UNSAFE_STATE_PENALTY = -8.0

    def __init__(
        self,
        *,
        seed: int = 7,
        max_steps: int = 16,
        transient_failure_rate: float = 0.0,
        start_position_jitter: float = 0.005,
    ) -> None:
        if max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if not 0.0 <= transient_failure_rate <= 1.0:
            raise ValueError("transient_failure_rate must be between zero and one")
        if not 0.0 <= start_position_jitter <= 0.01:
            raise ValueError("start_position_jitter must be between zero and 0.01")
        self._seed = seed
        self._random = random.Random(seed)
        self.max_steps = max_steps
        self.transient_failure_rate = transient_failure_rate
        self.start_position_jitter = start_position_jitter
        self.world = TabletopWorld.seeded(seed)
        self._episode = 0
        self._goal: Goal | None = None
        self._object_id = ""
        self._destination_id = ""
        self._grounded_object_id = ""
        self._grounded_destination_id = ""
        self._start_position = (0.0, 0.0, 0.0)
        self._phase_index = 0
        self._steps = 0
        self._grasp_verified = False
        self._placement_verified = False
        self._recovery_required = False
        self._last_action = ""
        self._last_phase = -1
        self._done = True

    def reset(
        self, *, seed: int | None = None, goal: Goal | None = None
    ) -> tuple[SemanticObservation, dict[str, object]]:
        if seed is not None:
            self._seed = seed
            self._random.seed(seed)
        self.world.reset()
        self._episode += 1
        self._goal = goal or self._sample_goal()
        if (
            self._goal.category not in _OBJECT_CATEGORIES
            or self._goal.color not in _COLORS
            or (
                self._goal.destination_category,
                self._goal.destination_relation,
            )
            not in _DESTINATIONS
        ):
            raise ValueError(f"unsupported goal: {self._goal}")
        try:
            target = self.world.resolve(
                category=self._goal.category,
                color=self._goal.color,
            )
            destination = self.world.resolve(
                category=self._goal.destination_category,
                relation=self._goal.destination_relation,
            )
        except ValueError as error:
            raise ValueError(f"unsupported goal: {self._goal}") from error
        self._object_id = target.entity_id
        self._destination_id = destination.entity_id
        self._start_position = self._randomize_start_position()
        self._phase_index = 0
        self._steps = 0
        self._grounded_object_id = ""
        self._grounded_destination_id = ""
        self._grasp_verified = False
        self._placement_verified = False
        self._recovery_required = False
        self._last_action = ""
        self._last_phase = -1
        self._done = False
        observation = self._observation()
        return observation, self._info("RESET", success=False)

    def step(self, action: str) -> tuple[SemanticObservation, float, bool, bool, dict[str, object]]:
        if action not in ACTIONS:
            raise ValueError(f"unknown semantic tool action: {action}")
        if self._done:
            raise RuntimeError("episode is complete; call reset before step")

        reward = self.STEP_COST
        code = "OK"
        repeated = action == self._last_action and self._phase_index == self._last_phase
        unsafe_reason = self._unsafe_reason()
        if unsafe_reason:
            self._steps += 1
            self._done = True
            return (
                self._observation(),
                reward + self.UNSAFE_STATE_PENALTY,
                True,
                False,
                self._info("UNSAFE_STATE", success=False, unsafe_reason=unsafe_reason),
            )

        if self._recovery_required:
            if action == "recover_to_safe_pose":
                result = self.world.tools.execute(action, ToolContext(self.world), parameters={})
                if result.success:
                    self._recovery_required = False
                    if 4 <= self._phase_index <= 6:
                        self._phase_index = 3
                        self._grasp_verified = False
                    reward += self.RECOVERY_REWARD
                else:
                    reward += self.TOOL_FAILURE_PENALTY
                code = result.code
            else:
                code = "RECOVERY_REQUIRED"
                reward += self.INVALID_ORDER_PENALTY
        elif self._phase_index == 1 and action in OBJECT_GROUNDING_ACTIONS:
            selected = action.removeprefix("ground_object:")
            result = self.world.tools.execute(
                "resolve_targets",
                ToolContext(self.world),
                parameters={"objectId": selected},
            )
            self._grounded_object_id = selected
            if not result.success:
                code = result.code
                reward += self.TOOL_FAILURE_PENALTY
            elif selected != self._object_id:
                code = "WRONG_OBJECT"
                reward += self.WRONG_GROUNDING_PENALTY
            else:
                reward += _PROGRESS_REWARDS[self._phase_index]
                self._phase_index += 1
        elif self._phase_index == 2 and action in DESTINATION_GROUNDING_ACTIONS:
            selected = action.removeprefix("ground_destination:")
            result = self.world.tools.execute(
                "resolve_targets",
                ToolContext(self.world),
                parameters={"destinationId": selected},
            )
            self._grounded_destination_id = selected
            if not result.success:
                code = result.code
                reward += self.TOOL_FAILURE_PENALTY
            elif selected != self._destination_id:
                code = "WRONG_DESTINATION"
                reward += self.WRONG_GROUNDING_PENALTY
            else:
                reward += _PROGRESS_REWARDS[self._phase_index]
                self._phase_index += 1
        elif action != _EXPECTED_TOOL_ACTIONS.get(_PHASES[self._phase_index]):
            code = "INVALID_TOOL_ORDER"
            reward += self.INVALID_ORDER_PENALTY
        elif self._inject_transient_failure(action):
            code = "TRANSIENT_TOOL_FAILURE"
            reward += self.TOOL_FAILURE_PENALTY
            self._recovery_required = True
        else:
            result = self.world.tools.execute(
                action,
                ToolContext(self.world),
                target_ref=self._target_ref(action),
                parameters=self._parameters(action),
            )
            code = result.code
            if result.success:
                reward += _PROGRESS_REWARDS[self._phase_index]
                self._record_progress(action)
                self._phase_index += 1
            else:
                reward += self.TOOL_FAILURE_PENALTY
                if action in {"plan_grasp", "manipulation.pick", "manipulation.place"}:
                    self._recovery_required = True

        if repeated and self._phase_index == self._last_phase:
            reward += self.REPEATED_NOOP_PENALTY
        self._steps += 1
        unsafe_reason = self._unsafe_reason()
        succeeded = self._phase_index == len(_PHASES) - 1 and not unsafe_reason
        terminated = succeeded or bool(unsafe_reason)
        truncated = self._steps >= self.max_steps and not terminated
        if succeeded:
            self._placement_verified = True
            self._done = True
        elif unsafe_reason:
            code = "UNSAFE_STATE"
            reward += self.UNSAFE_STATE_PENALTY
            self._done = True
        elif truncated:
            code = "STEP_BUDGET_EXHAUSTED"
            reward += self.TIMEOUT_PENALTY
            self._done = True
        self._last_action = action
        self._last_phase = self._phase_index
        observation = self._observation()
        return (
            observation,
            reward,
            terminated,
            truncated,
            self._info(code, success=succeeded, unsafe_reason=unsafe_reason),
        )

    def _sample_goal(self) -> Goal:
        category = self._random.choice(_OBJECT_CATEGORIES)
        color = self._random.choice(_COLORS)
        destination_category, relation = self._random.choice(_DESTINATIONS)
        return Goal(category, color, destination_category, relation)

    def _randomize_start_position(self) -> tuple[float, float, float]:
        body_name = self._object_id.replace("-", "_")
        body_id = mujoco.mj_name2id(self.world.model, mujoco.mjtObj.mjOBJ_BODY, body_name)
        if body_id < 0 or self.world.model.body_jntnum[body_id] != 1:
            raise ValueError(f"object body cannot be randomized: {self._object_id}")
        joint_id = int(self.world.model.body_jntadr[body_id])
        if self.world.model.jnt_type[joint_id] != mujoco.mjtJoint.mjJNT_FREE:
            raise ValueError(f"object joint is not free: {self._object_id}")
        address = int(self.world.model.jnt_qposadr[joint_id])
        self.world.data.qpos[address] += self._random.uniform(
            -self.start_position_jitter, self.start_position_jitter
        )
        self.world.data.qpos[address + 1] += self._random.uniform(
            -self.start_position_jitter, self.start_position_jitter
        )
        mujoco.mj_forward(self.world.model, self.world.data)
        return tuple(float(value) for value in self.world.data.qpos[address : address + 3])

    def _inject_transient_failure(self, action: str) -> bool:
        if action in {"observe_scene", "recover_to_safe_pose"}:
            return False
        return self._random.random() < self.transient_failure_rate

    def _target_ref(self, action: str) -> str:
        if action in {"plan_grasp", "manipulation.pick", "verify_grasp"}:
            return self._grounded_object_id
        if action in {"manipulation.place", "verify_placement"}:
            return self._grounded_destination_id
        return ""

    def _parameters(self, action: str) -> dict[str, object]:
        if action in {"plan_grasp", "verify_placement"}:
            return {
                "objectId": self._grounded_object_id,
                "destinationId": self._grounded_destination_id,
            }
        if action in {"manipulation.pick", "verify_grasp"}:
            return {"objectId": self._grounded_object_id}
        if action == "manipulation.place":
            return {"destinationId": self._grounded_destination_id}
        return {}

    def _record_progress(self, action: str) -> None:
        if action == "verify_grasp":
            self._grasp_verified = True
        elif action == "verify_placement":
            self._placement_verified = True

    def _unsafe_reason(self) -> str:
        state = self.world.robot_state()
        held = str(state.get("held", ""))
        if held and held != self._object_id:
            return "WRONG_OBJECT_HELD"
        placements = state.get("placements", {})
        if (
            isinstance(placements, dict)
            and self._object_id in placements
            and placements[self._object_id] != self._destination_id
        ):
            return "WRONG_DESTINATION_PLACEMENT"
        return ""

    def _observation(self) -> SemanticObservation:
        if self._goal is None:
            raise RuntimeError("call reset before observing")
        state = self.world.robot_state()
        placements = state.get("placements", {})
        placement = "unplaced"
        if isinstance(placements, dict) and self._object_id in placements:
            placement = "goal" if placements[self._object_id] == self._destination_id else "other"
        return SemanticObservation(
            goal=self._goal,
            phase=_PHASES[self._phase_index],
            object_id=self._object_id,
            destination_id=self._destination_id,
            grounded_object_id=self._grounded_object_id,
            grounded_destination_id=self._grounded_destination_id,
            grounded=bool(
                self._grounded_object_id == self._object_id
                and self._grounded_destination_id == self._destination_id
            ),
            held=str(state.get("held", "")),
            placement_state=placement,
            grasp_verified=self._grasp_verified,
            placement_verified=self._placement_verified,
            recovery_required=self._recovery_required,
            remaining_budget=max(0, self.max_steps - self._steps),
        )

    def _info(self, code: str, *, success: bool, unsafe_reason: str = "") -> dict[str, object]:
        info = {
            "success": success,
            "code": code,
            "episode": self._episode,
            "seed": self._seed,
            "goal": self._goal,
            "startPosition": self._start_position,
        }
        if unsafe_reason:
            info["unsafeReason"] = unsafe_reason
        return info
