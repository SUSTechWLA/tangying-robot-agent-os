#!/usr/bin/env python3
"""Device commissioning records. All commands are offline and never connect to hardware."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
PIN = "3d14695e40c9c68229c0aacffca6053c75cd3eb6"
KINDS = ("simulation", "safety", "estop", "network", "duplicate", "trial", "soak")
MIN_TRIALS = 30
MIN_SOAK_SECONDS = 3600
LIMITATION = "仅检查接入资料及人工记录，不验证实机效果，不授权电机运动；至少 30 次试验和 1 小时观察只是本版试点资料门槛。"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def wire(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def read_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"{path.name} 必须为 JSON 对象")
    return value


def private_write(path: Path, data: bytes) -> None:
    with path.open("xb") as output:
        os.chmod(path, 0o600)
        output.write(data)
        output.flush()
        os.fsync(output.fileno())


def local_file(kit: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("资料路径必须位于接入包内，使用相对路径")
    path = (kit / name).resolve()
    if not path.is_relative_to(kit.resolve()) or not path.is_file():
        raise ValueError(f"资料不存在或超出接入包: {name}")
    return path


def initialize(kit: Path, robot_id: str) -> None:
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", robot_id):
        raise ValueError("robot-id 只允许 1–64 位字母、数字、下划线和连字符")
    kit.mkdir(mode=0o700, parents=True, exist_ok=False)
    for name in ("artifacts", "evidence", "attachments"):
        (kit / name).mkdir(mode=0o700)
    profile = {
        "schemaVersion": "tangying.site.v1", "robotId": robot_id,
        "hardware": {"model": "xlerobot_2wheels", "upstreamCommit": PIN,
                     "leftSerial": "", "rightSerial": "", "mobileBaseEnabled": False},
        "softwareRevision": "", "calibrationRevision": "", "transformRevision": "",
        "taskScope": "", "fleetUrl": "https://fleet.example.invalid", "robotHost": "xlerobot.local",
        "worldId": "fleet-default",
        "files": {"calibration": "artifacts/tangying-xlerobot.json",
                  "transforms": "artifacts/transforms.json", "policyManifest": "artifacts/policy-manifest.json",
                  "policyArtifact": "artifacts/policy.bin", "perceptionValidation": "artifacts/perception-validation.json"},
    }
    private_write(kit / "site.json", wire(profile))
    robot_env = (ROOT / "deploy/config/robot-pi.env.example").read_text()
    robot_env += f"\nROBOT_ID={robot_id}\n"
    private_write(kit / "robot-pi.env", robot_env.encode())
    private_write(kit / "edge.env", f"""# 笔记本使用；填写凭据和证书，不要提交到 Git。此文件不自动加载。
EDGE_ROBOT_ID={robot_id}
EDGE_WORLD_ID=fleet-default
EDGE_FLEET_URL=https://fleet.example.invalid
EDGE_DEVICE_TOKEN=
EDGE_FLEET_CA=/absolute/path/fleet-ca.crt
EDGE_RUNTIME_ADDR=xlerobot.local:50051
EDGE_RUNTIME_INSECURE=0
EDGE_RUNTIME_CA=/absolute/path/robot-ca.crt
EDGE_RUNTIME_CERT=/absolute/path/client.crt
EDGE_RUNTIME_KEY=/absolute/path/client.key
EDGE_RUNTIME_SERVER_NAME=xlerobot.local
EDGE_ADAPTER=xlerobot_direct
EDGE_ROBOT_MODEL=xlerobot-dual-arm
EDGE_TRANSFORM_REVISION=
EDGE_CALIBRATION_REVISION=
EDGE_POLICY_MODE=http
EDGE_POLICY_ENDPOINT=http://127.0.0.1:8091
""".encode())
    private_write(kit / "README.md", ("# 实机接入包\n\n填写 site.json 和两个 env 文件，按 docs/sim2real/README.md 完成接入。\n"
        "本包不包含模型、相机识别或有效凭据。init/check/record/report 均不连接硬件。\n"
        "1. check --stage inventory：设备与任务信息。\n2. check --stage integration：模型、标定、感知验证和配置的一致性。\n"
        "3. record：完成实际测试后登记日志；不要伪造通过记录。\n4. report：交接资料及尚缺步骤。\n"
        "资料变更后旧记录失效，必须重新验证。文件权限为私有；日志附件自行去除密钥和个人信息。\n").encode())


def env_values(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or key in result:
            raise ValueError(f"{path.name}: 存在无效或重复配置键，请检查文件")
        result[key] = value
    return result


def inspect(kit: Path, stage: str = "integration") -> dict:
    blockers = []
    hashes = {}
    def require(ok, code, action):
        if not ok:
            blockers.append({"code": code, "action": action})
    try:
        profile = read_json(kit / "site.json")
        hashes["site.json"] = digest(wire(profile))
        hardware = profile.get("hardware", {})
        require(profile.get("schemaVersion") == "tangying.site.v1", "SITE_SCHEMA", "使用 init 生成本版接入包")
        require(isinstance(profile.get("robotId"), str) and re.fullmatch(r"[\w-]{1,64}", profile["robotId"], re.ASCII), "ROBOT_ID", "填写唯一设备 ID")
        require(hardware.get("model") == "xlerobot_2wheels" and hardware.get("upstreamCommit") == PIN and hardware.get("mobileBaseEnabled") is False,
                "HARDWARE_SCOPE", "本版仅支持锁定版本的 XLeRobot 双臂桌面任务，移动底盘关闭")
        require(bool(hardware.get("leftSerial")) and bool(hardware.get("rightSerial")) and hardware["leftSerial"] != hardware["rightSerial"],
                "SERIAL_IDENTITY", "设备集成人员填写两个不同的 USB 串口序列号，并核对左右侧 udev 映射")
        for field in ("softwareRevision", "taskScope", "robotHost", "worldId"):
            require(isinstance(profile.get(field), str) and bool(profile[field].strip()) and "\n" not in profile[field],
                    "SITE_" + field.upper(), f"填写 site.json 的 {field}，记录实际交付版本和批准的任务范围")
        url = urlparse(profile.get("fleetUrl", ""))
        require(url.scheme == "https" and bool(url.hostname) and not url.hostname.endswith(".invalid") and not url.username and not url.password,
                "FLEET_URL", "填写不含凭据的实际 HTTPS Fleet 地址")
        if stage != "inventory":
            for field in ("calibrationRevision", "transformRevision"):
                require(isinstance(profile.get(field), str) and bool(profile[field].strip()), "SITE_" + field.upper(), f"填写 {field}，每次重新标定后更新版本")
            files = profile.get("files", {})
            loaded = {}
            for name in ("calibration", "transforms", "policyManifest", "policyArtifact", "perceptionValidation"):
                try:
                    path = local_file(kit, files.get(name))
                    # Stream potentially large model weights.
                    with path.open("rb") as content:
                        hashes[name] = hashlib.file_digest(content, "sha256").hexdigest()
                    if path.stat().st_size == 0:
                        raise ValueError("文件为空")
                    if name != "policyArtifact":
                        loaded[name] = read_json(path)
                except (OSError, ValueError, TypeError) as exc:
                    require(False, "FILE_" + name.upper(), f"补齐 {name}：{exc}")
            if "policyManifest" in loaded:
                try:
                    sys.path.insert(0, str(ROOT / "policy/sidecar"))
                    from tangying_policy_sidecar.contracts import PolicyManifest
                    manifest = PolicyManifest.model_validate(loaded["policyManifest"])
                    require(manifest.framework != "deterministic" and "xlerobot_direct" in manifest.adapters and "xlerobot-dual-arm" in manifest.robot_models,
                            "POLICY_REAL_ADAPTER", "提供支持 xlerobot_direct / xlerobot-dual-arm 的真实推理模型，不能使用仿真 deterministic policy")
                    require(manifest.calibration_revision == profile["calibrationRevision"] and manifest.transform_revision == profile["transformRevision"],
                            "POLICY_REVISION", "策略清单与现场标定、坐标变换版本必须一致")
                    require(manifest.artifact_sha256 == hashes.get("policyArtifact"), "POLICY_HASH", "策略清单 artifactSha256 必须匹配实际模型文件")
                    require(set(manifest.required_observation_sources) == {"scene", "proprioception"},
                            "POLICY_SOURCES", "本版 Edge 默认提供 scene 和 proprioception；相机帧模型需先实现额外观测接入，不能直接使用此交付配置")
                    sys.path.insert(0, str(ROOT / "robot/gateway"))
                    from tangying_robot_gateway.xlerobot_backend import ALLOWED_ACTION_KEYS
                    require(manifest.action_schema == "xlerobot.named-joints.v1" and all(
                        key in ALLOWED_ACTION_KEYS and bound.minimum >= (0 if key.endswith("gripper.pos") else -100) and bound.maximum <= 100
                        for key, bound in manifest.action_bounds.items()), "POLICY_ACTION_BOUNDS", "使用 xlerobot.named-joints.v1 及 Runtime 允许的机械臂/头部关节范围，底盘禁用")
                    limit = int(env_values(kit / "robot-pi.env").get("XLEROBOT_MAX_ACTION_CHUNK_LENGTH", "64"))
                    require(0 < manifest.max_action_chunk_length <= min(limit, 64), "POLICY_CHUNK_LIMIT", "策略动作块长度必须在 Runtime 限制内，且不超过 64")
                    hashes["policyRevision"] = manifest.revision()
                except (ImportError, ValueError, KeyError) as exc:
                    require(False, "POLICY_MANIFEST", f"修复策略清单或安装项目 Python 依赖：{exc}")
            for name, revision in (("transforms", "transformRevision"), ("perceptionValidation", "calibrationRevision")):
                if name in loaded:
                    require(loaded[name].get("robotId") == profile["robotId"] and loaded[name].get(revision) == profile[revision],
                            "ARTIFACT_IDENTITY", f"{name} 必须包含匹配的 robotId 和 {revision}")
            if "perceptionValidation" in loaded:
                p = loaded["perceptionValidation"]
                require(p.get("transformRevision") == profile["transformRevision"] and p.get("passed") is True and bool(p.get("procedure")),
                        "PERCEPTION_VALIDATION", "感知验证资料须记录 procedure、passed 和当前坐标变换版本；实际效果需现场复核")
                require(p.get("calibrationSha256") == hashes.get("calibration") and isinstance(p.get("calibrationSha256"), str)
                        and p.get("transformsSha256") == hashes.get("transforms") and isinstance(p.get("transformsSha256"), str),
                        "PERCEPTION_INPUT_HASH", "感知验证资料的 calibrationSha256、transformsSha256 必须匹配本次验证实际使用的标定与变换文件；输入变更后重新验证")
            if "calibration" in loaded:
                sys.path.insert(0, str(ROOT / "robot/ros2_ws/src/xlerobot_adapter"))
                from xlerobot_adapter.calibration import validate_calibration_data
                try:
                    validate_calibration_data(loaded["calibration"])
                except ValueError as exc:
                    require(False, "CALIBRATION_INVALID", f"电机标定格式不合格：{exc}")
            if "transforms" in loaded:
                transforms = loaded["transforms"].get("transforms")
                valid = isinstance(transforms, list) and bool(transforms)
                for transform in transforms if isinstance(transforms, list) else []:
                    matrix = transform.get("matrix", []) if isinstance(transform, dict) else []
                    valid = valid and isinstance(transform, dict) and bool(transform.get("parent")) and bool(transform.get("child"))
                    valid = valid and isinstance(matrix, list) and len(matrix) == 16 and all(type(v) in (int, float) and math.isfinite(v) for v in matrix)
                    valid = valid and matrix[-4:] == [0, 0, 0, 1]
                require(valid, "TRANSFORMS_INVALID", "transforms.json 需要非空 transforms 数组，每项有 parent、child、16 位有限数的齐次变换 matrix")
            expected = {
                "robot-pi.env": {"ROBOT_ID": profile["robotId"]},
                "edge.env": {"EDGE_ROBOT_ID": profile["robotId"], "EDGE_WORLD_ID": profile["worldId"],
                    "EDGE_FLEET_URL": profile["fleetUrl"], "EDGE_RUNTIME_ADDR": profile["robotHost"] + ":50051",
                    "EDGE_ADAPTER": "xlerobot_direct", "EDGE_ROBOT_MODEL": "xlerobot-dual-arm",
                    "EDGE_RUNTIME_INSECURE": "0", "EDGE_RUNTIME_SERVER_NAME": profile["robotHost"], "EDGE_POLICY_MODE": "http",
                    "EDGE_TRANSFORM_REVISION": profile["transformRevision"], "EDGE_CALIBRATION_REVISION": profile["calibrationRevision"]},
            }
            for name, required in expected.items():
                values = env_values(kit / name)
                hashes[name] = digest((kit / name).read_bytes())
                if name == "robot-pi.env":
                    require(values.get("ROBOT_ALLOW_INSECURE", "0") == "0", "CONFIG_MISMATCH",
                            "robot-pi.env 必须保持 mTLS：ROBOT_ALLOW_INSECURE 应省略或设为 0")
                for key, value in required.items():
                    require(values.get(key) == value, "CONFIG_MISMATCH", f"{name} 的 {key} 必须与 site.json 一致")
                for key in (("ROBOT_ENTITY_PROVIDER", "ROBOT_VERIFIER_PROVIDER") if name == "robot-pi.env" else ("EDGE_DEVICE_TOKEN",)):
                    require(bool(values.get(key)), "CONFIG_REQUIRED", f"在 {name} 填写 {key}；不会在报告中输出其值")
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        require(False, "SITE_INVALID", f"修复接入包资料：{exc}")
    return {"scope": "offline_commissioning_records", "stage": stage, "checksPassed": not blockers,
            "blockers": blockers, "binding": digest(wire(hashes)), "artifactHashes": hashes, "limitation": LIMITATION}


def record(kit: Path, *, kind: str, operator: str, result: str, notes: str,
           evidence: list[Path], task_id: str = "", duration_seconds: int = 0) -> Path:
    report = inspect(kit)
    if not report["checksPassed"]:
        raise ValueError("请先通过 check --stage integration，再登记与当前配置绑定的测试记录")
    if kind not in KINDS or result not in ("passed", "failed") or not operator.strip() or not notes.strip() or not evidence:
        raise ValueError("必须提供测试类型、结果、操作者、过程说明和至少一份实际证据文件")
    if kind == "trial" and not task_id.strip():
        raise ValueError("trial 必须提供唯一 --task-id，对应真实任务日志")
    if type(duration_seconds) is not int or duration_seconds < 0:
        raise ValueError("duration-seconds 必须是非负整数时长")
    if kind == "soak" and result == "passed" and duration_seconds < MIN_SOAK_SECONDS:
        raise ValueError("soak 需至少 3600 秒的实测观察记录")
    record_id = str(uuid4())
    attachments = []
    for index, source in enumerate(evidence):
        data = source.read_bytes()
        if not data:
            raise ValueError("证据文件不能是空文件")
        relative = f"attachments/{record_id}-{index}.bin"
        private_write(kit / relative, data)
        attachments.append({"path": relative, "sha256": digest(data)})
    path = kit / "evidence" / f"{record_id}.json"
    private_write(path, wire({"schemaVersion": "tangying.commissioning-record.v1", "id": record_id,
        "recordedAt": datetime.now(UTC).isoformat(), "binding": report["binding"],
        "kind": kind, "operator": operator, "result": result, "notes": notes,
        "taskId": task_id, "durationSeconds": duration_seconds, "attachments": attachments}))
    return path


def pilot_report(kit: Path) -> dict:
    result = inspect(kit, "pilot")
    counts = dict.fromkeys(KINDS, 0)
    tasks = set()
    stale = 0
    for path in sorted((kit / "evidence").glob("*.json")):
        try:
            item = read_json(path)
            if item.get("binding") != result["binding"]:
                stale += 1
                continue
            if item.get("schemaVersion") != "tangying.commissioning-record.v1" or item.get("kind") not in KINDS or not item.get("operator") or not item.get("notes"):
                raise ValueError("记录结构不完整")
            if item.get("result") != "passed":
                raise ValueError("存在失败记录，需要调查并修订交付版本后重新验证")
            if not item.get("attachments"):
                raise ValueError("缺少附件")
            for attachment in item["attachments"]:
                if digest(local_file(kit, attachment["path"]).read_bytes()) != attachment["sha256"]:
                    raise ValueError("证据附件被修改")
            kind = item["kind"]
            if kind == "trial":
                if not isinstance(item.get("taskId"), str) or not item["taskId"].strip():
                    raise ValueError("缺少任务 ID")
                if item["taskId"] in tasks:
                    continue
                tasks.add(item["taskId"])
            if kind == "soak" and (type(item.get("durationSeconds")) is not int or item["durationSeconds"] < MIN_SOAK_SECONDS):
                raise ValueError("观察时长不足")
            counts[kind] += 1
        except (OSError, ValueError, TypeError, KeyError) as exc:
            result["blockers"].append({"code": "EVIDENCE_INVALID", "action": f"检查记录 {path.name}：{exc}"})
    for kind in KINDS:
        minimum = MIN_TRIALS if kind == "trial" else 1
        if counts[kind] < minimum:
            result["blockers"].append({"code": "EVIDENCE_MISSING", "action": f"完成并记录 {kind}：当前 {counts[kind]} / 至少 {minimum}"})
    result.update(checksPassed=not result["blockers"], evidenceCounts=counts, staleRecords=stale)
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="生成设备接入包，不覆盖已有目录")
    init.add_argument("--robot-id", required=True)
    init.add_argument("--output", type=Path, required=True)
    for name in ("check", "record", "report"):
        p = commands.add_parser(name)
        p.add_argument("--kit", type=Path, required=True)
        if name == "check":
            p.add_argument("--stage", choices=("inventory", "integration", "pilot"), default="integration")
        if name != "record":
            p.add_argument("--json", action="store_true")
        else:
            p.add_argument("--kind", choices=KINDS, required=True)
            p.add_argument("--operator", required=True)
            p.add_argument("--result", choices=("passed", "failed"), required=True)
            p.add_argument("--notes", required=True)
            p.add_argument("--evidence", type=Path, action="append", required=True)
            p.add_argument("--task-id", default="")
            p.add_argument("--duration-seconds", type=int, default=0)
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            initialize(args.output, args.robot_id)
            print(f"已生成接入包：{args.output.resolve()}；下一步填写 site.json 并执行 check --stage inventory")
            return 0
        if args.command == "record":
            values = vars(args).copy()
            values.pop("command")
            print(record(**values))
            return 0
        result = pilot_report(args.kit) if args.command == "report" or args.stage == "pilot" else inspect(args.kit, args.stage)
        if args.json:
            print(wire(result).decode(), end="")
        else:
            print("接入资料检查：" + ("通过" if result["checksPassed"] else "待补齐"))
            for item in result["blockers"]:
                print(f"- [{item['code']}] {item['action']}")
            if "evidenceCounts" in result:
                print("当前版本记录：" + json.dumps(result["evidenceCounts"], ensure_ascii=False))
                print(f"已失效旧记录：{result['staleRecords']}")
            print(LIMITATION)
        return 0 if result["checksPassed"] else 1
    except (OSError, ValueError, TypeError) as exc:
        print(f"接入包操作失败：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
