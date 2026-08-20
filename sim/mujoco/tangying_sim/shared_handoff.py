from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import RLock


class StaleHandoffToken(RuntimeError):
    """Raised when a delayed actor attempts to mutate an old ownership epoch."""


@dataclass(frozen=True)
class SharedObjectView:
    visible: bool
    position: tuple[float, float, float] | None


class SharedHandoffBridge:
    """Single logical resource shared by otherwise independent simulators.

    The bridge contains no MuJoCo objects and never calls into a world. Worlds
    pull their view lazily, which gives every lock the same world -> bridge
    ordering and avoids cross-simulator deadlocks.
    """

    RESOURCE_ID = "block:red-block"
    OBJECT_ID = "red-block"

    def __init__(
        self,
        *,
        sender_id: str = "robot-1",
        receiver_id: str = "robot-2",
        handoff_position: tuple[float, float, float] = (0.32, 0.34, 0.82),
        target_position: tuple[float, float, float] = (0.96, 0.34, 0.755),
    ) -> None:
        self._lock = RLock()
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.handoff_position = handoff_position
        self.target_position = target_position
        self._owner = sender_id
        self._custodian = sender_id
        self._fencing_token = 1
        self._completed = False
        self._fixed_position: tuple[float, float, float] | None = None
        self._listeners: list[Callable[[str, int], None]] = []

    @property
    def owner(self) -> str:
        with self._lock:
            return self._owner

    @property
    def custodian(self) -> str:
        with self._lock:
            return self._custodian

    @property
    def fencing_token(self) -> int:
        with self._lock:
            return self._fencing_token

    @property
    def completed(self) -> bool:
        with self._lock:
            return self._completed

    def command_grant(self) -> tuple[str, int]:
        """Return the lease identity expected on the next runtime command."""
        with self._lock:
            owner = self._owner
            if owner == "environment" and self._custodian == self.receiver_id and not self._completed:
                # Cloud transfers the lease after verified placement while the
                # environment retains physical custody until receiver pickup.
                owner = self.receiver_id
            return owner, self._fencing_token

    def add_grant_listener(self, listener: Callable[[str, int], None]) -> None:
        with self._lock:
            self._listeners.append(listener)

    def _notify_grant(self) -> None:
        owner, token = self.command_grant()
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            listener(owner, token)

    def transfer(
        self,
        *,
        expected_owner: str,
        new_owner: str,
        expected_token: int,
        custodian: str,
    ) -> int:
        with self._lock:
            if self._owner != expected_owner or self._fencing_token != expected_token:
                raise StaleHandoffToken(
                    "shared block ownership epoch changed "
                    f"(owner={self._owner}, token={self._fencing_token})"
                )
            self._owner = new_owner
            self._custodian = custodian
            self._fencing_token += 1
            return self._fencing_token

    def view_for(self, robot_id: str) -> SharedObjectView:
        with self._lock:
            return SharedObjectView(
                visible=self._custodian == robot_id,
                position=self._fixed_position if self._custodian == robot_id else None,
            )

    def can_pick(self, robot_id: str) -> bool:
        with self._lock:
            return self._custodian == robot_id and self._owner in {
                robot_id,
                "environment",
            }

    def on_picked(self, robot_id: str) -> None:
        changed = False
        with self._lock:
            if self._custodian != robot_id:
                raise StaleHandoffToken(f"{robot_id} is not the current custodian")
            if self._owner == "environment":
                # This acknowledges the cloud's already-fenced transfer; it is
                # not a new lease epoch, so subsequent place uses the same token.
                self._owner = robot_id
                changed = True
            elif self._owner != robot_id:
                raise StaleHandoffToken(f"{robot_id} does not own the block")
            self._fixed_position = None
        if changed:
            self._notify_grant()

    def on_placed(
        self,
        robot_id: str,
        destination_id: str,
        position: tuple[float, float, float],
    ) -> None:
        changed = False
        with self._lock:
            if robot_id == self.sender_id and destination_id == "handoff-zone":
                self.transfer(
                    expected_owner=self.sender_id,
                    new_owner="environment",
                    expected_token=self._fencing_token,
                    custodian=self.receiver_id,
                )
                self._fixed_position = self.handoff_position
                changed = True
            elif robot_id == self.receiver_id and destination_id == "right-target-zone":
                self.transfer(
                    expected_owner=self.receiver_id,
                    new_owner="environment",
                    expected_token=self._fencing_token,
                    custodian=self.receiver_id,
                )
                self._completed = True
                self._fixed_position = position
                changed = True
        if changed:
            self._notify_grant()


def seeded_handoff_worlds(seed: int = 7, human_speed: float = 0.0):
    """Build the two-cell fixture used by tests and the fleet simulator."""
    from .world import TabletopWorld

    assets = Path(__file__).resolve().parents[1] / "assets"
    bridge = SharedHandoffBridge()
    sender = TabletopWorld.seeded(
        seed,
        xml_path=str(assets / "xlerobot_tabletop.xml"),
        robot_id=bridge.sender_id,
        shared_handoff=bridge,
        human_speed=human_speed,
    )
    receiver = TabletopWorld.seeded(
        seed + 1,
        xml_path=str(assets / "xlerobot_tabletop_handoff_r2.xml"),
        robot_id=bridge.receiver_id,
        shared_handoff=bridge,
        human_speed=human_speed,
    )
    return bridge, sender, receiver
