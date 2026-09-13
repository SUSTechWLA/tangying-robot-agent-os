"""Per-invocation identity shared by atomic operations inside composite skills."""
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass
class Invocation:
    identity: str
    cancel_event: object = None
    step: int = 0


current_invocation = ContextVar('robot_tool_invocation', default=None)
