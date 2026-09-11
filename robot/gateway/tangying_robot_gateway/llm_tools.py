"""Build OpenAI / LangChain function-calling tools from the tool layer.

The schema is generated from the same ``RobotTool`` definitions the runtime
executes, so a tool cannot be advertised to a model without also being
registered, validated and routable. Nothing here is hand-maintained: a tool
whose schema is wrong is a tool that does not exist.

Usage::

    .venv/bin/python -m tangying_robot_gateway.llm_tools            # print to stdout
    .venv/bin/python -m tangying_robot_gateway.llm_tools --write    # update tools.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .semantic_map import DEFAULT_LAYOUT_PATH, SemanticMap
from .tool_layer import ToolRegistry
from .tools import build_registry

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT = REPO_ROOT / "tools.json"

#: Behavioural guidance appended to every description. The runtime enforces the
#: same rules; stating them here is what lets a model choose correctly instead
#: of discovering them by failing.
_GLOBAL_NOTES = (
    "每次调用都返回统一结构：success、error_code、error_message、recoverable、data。"
    "error_code 取值 TIMEOUT / NOT_FOUND / UNREACHABLE / COLLISION_RISK / HARDWARE_ERROR / "
    "PERMISSION_DENIED / INVALID_PARAM / BUSY / CANCELLED / SAFETY_STOP；"
    "recoverable=true 才适合原样重试。"
)


def _stub_executor(registry: ToolRegistry) -> Any:
    """An executor that never runs anything, for schema export only.

    Exporting a schema must not require a robot, so the tools are backed by an
    adapter whose calls would fail if they were ever reached. Nothing in this
    module calls ``execute``.
    """

    class _Forbidden:
        def __getattr__(self, name: str):
            raise AssertionError(f"schema export must not touch the robot (called {name})")

    return _Forbidden()


def build_catalog(*, registry: ToolRegistry | None = None) -> dict[str, Any]:
    """Return the full function-calling catalogue with metadata sidecars."""

    if registry is None:
        from .semantic_map import SemanticMap as _SemanticMap

        registry = _build_offline_registry(_SemanticMap.from_file(DEFAULT_LAYOUT_PATH))

    tools = []
    for tool in registry.select(llm_only=True):
        entry = tool.openai_schema()
        entry["function"]["description"] = f"{tool.description} {_GLOBAL_NOTES}"
        tools.append(entry)

    return {
        "schema_version": "llm.tools.v1",
        "tools": tools,
        "metadata": {
            name: {
                "safety_level": tool.safety_level,
                "timeout_s": tool.timeout_s,
                "distributed_node": tool.distributed_node,
                "idempotent": tool.idempotent,
                "mutates_world": tool.mutates_world,
                "returns": dict(tool.returns_schema.get("properties") or {}),
            }
            for name, tool in ((item.name, item) for item in registry.select(llm_only=False))
        },
        "excluded_from_llm": sorted(
            tool.name for tool in registry.select(llm_only=False) if tool.llm_visibility != "primary"
        ),
    }


def _build_offline_registry(semantic_map: SemanticMap) -> ToolRegistry:
    """Build the registry with a schema-only adapter.

    Tool definitions do not depend on a live connection, so the catalogue can be
    generated on a laptop with no robot present. The adapter refuses every call
    so an accidental execute during export is a loud failure, not a silent no-op.
    """

    from .tools.registry import ArmPlan, HardwareHealth, ObservationView

    class OfflineAdapter:
        def execute(self, *_args, **_kwargs):  # pragma: no cover - never called by export
            raise AssertionError("schema export must not dispatch commands")

        def observe(self, **_kwargs):
            return ObservationView(fresh=False)

        def cancel(self, *_args, **_kwargs) -> bool:
            return False

        def health(self) -> HardwareHealth:
            return HardwareHealth(reachable=False, errors=("OFFLINE_EXPORT",))

        def set_speed_limit(self, *_args, **_kwargs):  # pragma: no cover - never called by export
            raise AssertionError("schema export must not dispatch commands")

        def plan_arm_motion(self, **_kwargs) -> ArmPlan:
            return ArmPlan(ok=False, code="OFFLINE_EXPORT", message="schema export only")

        def gripper_waypoints(self, *_args, **_kwargs):
            return ()

    return build_registry(OfflineAdapter(), semantic_map)


def render(catalog: dict[str, Any]) -> str:
    return json.dumps(catalog, indent=2, ensure_ascii=False, sort_keys=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help=f"update {DEFAULT_OUTPUT}")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the output file is not up to date")
    args = parser.parse_args(argv)

    payload = render(build_catalog())
    if args.check:
        existing = args.output.read_text() if args.output.exists() else ""
        if existing != payload:
            print(f"{args.output} is stale; run: python -m tangying_robot_gateway.llm_tools --write",
                  file=sys.stderr)
            return 1
        print(f"{args.output} is up to date ({len(json.loads(payload)['tools'])} tools)")
        return 0
    if args.write:
        args.output.write_text(payload)
        print(f"wrote {args.output} ({len(json.loads(payload)['tools'])} tools)")
        return 0
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
