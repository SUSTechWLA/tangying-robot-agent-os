"""Auditable deterministic measurement predicates; missing is never false."""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PredicateDefinition:
    parameters: tuple[str, ...]
    semantics: str
    signals: tuple[str, ...]
    modalities: tuple[str, ...]
    decide: Callable
    unknown_conditions: str = "缺失、遮挡、跨启动、窗口外、低置信度、证据哈希不匹配"
    confidence_model: str = "使用依赖观测置信度的最小值；不将相关帧当作独立概率相乘"


PREDICATES = {
    "At": PredicateDefinition(
        ("robot", "loc", "position_tolerance", "yaw_tolerance"),
        "机器人在目标区域且位置和航向误差均在阈值内",
        ("position_error_m", "yaw_error_rad"),
        ("pose",),
        lambda v, a: (
            abs(v["position_error_m"]) <= a.get("position_tolerance", 0.05)
            and abs(v["yaw_error_rad"]) <= a.get("yaw_tolerance", 0.12)
        ),
    ),
    "On": PredicateDefinition(
        ("object", "surface"),
        "物体底部与支撑面高度差不超过 2 厘米",
        ("height_above_surface_m",),
        ("rgb", "depth", "detection"),
        lambda v, a: abs(v["height_above_surface_m"]) <= a.get("tolerance", 0.02),
    ),
    "Holding": PredicateDefinition(
        ("gripper", "object"),
        "夹爪闭合、有负载且抓持身份与目标相符",
        ("gripper_closed", "load_n", "held_object_id"),
        ("gripper", "force", "rgb", "depth", "detection"),
        lambda v, a: (
            v["gripper_closed"] is True
            and v["load_n"] >= a.get("min_load_n", 0.1)
            and v["held_object_id"] == a.get("object")
        ),
    ),
    "In": PredicateDefinition(
        ("object", "container"),
        "物体边界完整位于目标容器内部",
        ("inside_container", "container_id"),
        ("rgb", "depth", "detection"),
        lambda v, a: v["inside_container"] is True and v["container_id"] == a.get("container"),
    ),
    "Clear": PredicateDefinition(
        ("path",),
        "测得整个待扫掠区域为可通行",
        ("path_clear",),
        ("depth",),
        lambda v, a: v["path_clear"] is True,
    ),
    "Stable": PredicateDefinition(
        ("object",),
        "相邻帧物体位移不超过阈值",
        ("displacement_m",),
        ("rgb", "depth", "detection"),
        lambda v, a: abs(v["displacement_m"]) <= a.get("tolerance", 0.01),
    ),
    "Released": PredicateDefinition(
        ("gripper",),
        "夹爪反馈已张开",
        ("gripper_closed",),
        ("gripper",),
        lambda v, a: v["gripper_closed"] is False,
    ),
    "Safe": PredicateDefinition(
        (),
        "接触力没有超过已配置的安全上限",
        ("collision_force_n",),
        ("force",),
        lambda v, a: v["collision_force_n"] <= a.get("limit_n", 20.0),
    ),
    "Capacity": PredicateDefinition(
        ("container",),
        "容器有可用空间",
        ("container_full",),
        ("rgb", "depth", "detection"),
        lambda v, a: v["container_full"] is False,
    ),
}

# Keep top-level classes aligned with core/closedloop, not a ninth recovery authority.
FAILURES = {
    "NONE": ("", "", []),
    "GRASP_MISS": (
        "PERCEPTION",
        "有观测但夹爪无负载或物体未离开支撑面",
        ["重新观察目标和夹爪", "由上层重新规划抓取"],
    ),
    "GRASP_SLIP": (
        "PERCEPTION",
        "先有抓持证据，随后负载消失或位置不稳定",
        ["观察物体落点", "由上层重新规划抓取"],
    ),
    "WRONG_OBJECT": (
        "PERCEPTION",
        "持有物身份与目标不符",
        ["重新识别物体", "请求上层决定安全放回"],
    ),
    "PLACE_UNSTABLE": (
        "PERCEPTION",
        "容器内关系或连续稳定条件不成立",
        ["观察容器与物体", "由上层重新规划放置"],
    ),
    "NAV_NOT_REACHED": (
        "PERCEPTION",
        "位姿误差超过阈值",
        ["重新定位并观察路径", "由上层重新规划路线"],
    ),
    "CONTAINER_FULL": ("PLANNING", "容器空间观测不足", ["选择其他容器或请求人工清空"]),
    "PERCEPTION_OCCLUDED": ("PERCEPTION", "目标被遮挡", ["重新观察", "请求上层选择新的观察位置"]),
    "EVIDENCE_INSUFFICIENT": (
        "UNKNOWN_OUTCOME",
        "依赖证据缺失、低置信度或时间不合法",
        ["重新观察并核对动作结果", "证据仍不足时请求人工"],
    ),
    "CONTRACT_VIOLATION": (
        "VALIDATION",
        "前置、安全、不变式或未知合约不满足",
        ["停止后续物理动作", "请求上层检查合约与安全条件"],
    ),
}
