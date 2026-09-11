#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON="${NAVIGATION_STACK_PYTHON:-$ROOT_DIR/.venv/bin/python}"
exec "$PYTHON" - "$ROOT_DIR" "$@" <<'PY'
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen

ROOT = Path(sys.argv[1])
parser = argparse.ArgumentParser(description="Manage only the tangying-navigation Compose project and its RGB-D simulation.")
parser.add_argument("operation", choices=("start", "restart", "status", "logs", "stop"))
parser.add_argument("--build", action="store_true", help="Build the navigation image explicitly; default reuses an existing image.")
parser.add_argument("--mode", choices=("mapping", "localization"), help="Persist mapping or localization mode; never delete maps.")
parser.add_argument("--scene", choices=("tabletop", "home", "home_task"), help="Use the commissioned tabletop, four-room home, or home_task scene.")
parser.add_argument("--artifacts-dir", default=os.environ.get("SIM_STACK_ARTIFACTS_DIR", str(ROOT / "artifacts/sim-stack")))
parser.add_argument("--follow", action="store_true", help="Follow navigation container logs.")
args = parser.parse_args(sys.argv[2:])
ARTIFACTS = Path(args.artifacts_dir).expanduser().resolve()
CONFIG = ARTIFACTS / "navigation.env"
MARKER = ARTIFACTS / "run/navigation-runtime.json"
METADATA = ARTIFACTS / "run/stack.env"
DOCKER = os.environ.get("NAVIGATION_STACK_DOCKER", "docker")
SIM_SCRIPT = os.environ.get("NAVIGATION_STACK_SIM_SCRIPT", str(ROOT / "scripts/sim-stack.sh"))
PROJECT = "tangying-navigation"


def read_pairs(path):
    if not path.exists():
        return {}
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("configuration must be a bounded regular file")
    values = {}
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key in values:
            raise ValueError("invalid or repeated configuration key")
        values[key] = value
    return values


def private_write(path, content):
    if path.is_symlink():
        raise ValueError("refusing to replace a symbolic configuration link")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name+".", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_config():
    saved = read_pairs(CONFIG)
    keys = {"TANGYING_NAVIGATION_TOKEN", "TANGYING_NAVIGATION_MODE", "TANGYING_NAVIGATION_SCENE", "TANGYING_NAVIGATION_DATABASE", "TANGYING_NAVIGATION_PORT",
            "TANGYING_NAVIGATION_INPUT_MODE", "TANGYING_RUNTIME_ADDRESS", "TANGYING_RUNTIME_ROBOT_ID",
            "TANGYING_RUNTIME_HEAD_SOURCE", "TANGYING_RUNTIME_BASE_SOURCE", "TANGYING_RUNTIME_INSECURE", "ROS_DOMAIN_ID", "ROS_IMAGE", "RMW_IMPLEMENTATION"}
    if set(saved)-keys:
        raise ValueError("unknown navigation configuration key")
    if saved and not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", saved.get("TANGYING_NAVIGATION_TOKEN", "")):
        raise ValueError("stored navigation token is invalid; configuration is data, never shell code")
    metadata = read_pairs(METADATA)
    sim_port = os.environ.get("SIM_STACK_SIM_PORT", metadata.get("SIM_PORT", "50051"))
    agent_port = os.environ.get("SIM_STACK_AGENT_PORT", metadata.get("AGENT_PORT", "8787"))
    robot = os.environ.get("TANGYING_RUNTIME_ROBOT_ID", saved.get("TANGYING_RUNTIME_ROBOT_ID", "xlerobot-mujoco-tabletop"))
    token = os.environ.get("TANGYING_NAVIGATION_TOKEN", saved.get("TANGYING_NAVIGATION_TOKEN", ""))
    scene = args.scene or os.environ.get("TANGYING_NAVIGATION_SCENE", saved.get("TANGYING_NAVIGATION_SCENE", "tabletop"))
    database = os.environ.get(
        "TANGYING_NAVIGATION_DATABASE",
        saved.get("TANGYING_NAVIGATION_DATABASE", "/data/maps/home_task/rtabmap.db" if scene == "home_task" else "/data/maps/home/rtabmap.db" if scene == "home" else "/data/maps/rtabmap.db"),
    )
    if saved and token != saved["TANGYING_NAVIGATION_TOKEN"]:
        raise ValueError("supplied token differs from the saved private token; remove the conflicting environment override")
    config = {
        "TANGYING_NAVIGATION_TOKEN": token or secrets.token_urlsafe(32),
        "TANGYING_NAVIGATION_MODE": args.mode or os.environ.get("TANGYING_NAVIGATION_MODE", saved.get("TANGYING_NAVIGATION_MODE", "mapping")),
        "TANGYING_NAVIGATION_SCENE": scene,
        "TANGYING_NAVIGATION_DATABASE": database,
        "TANGYING_NAVIGATION_PORT": os.environ.get("TANGYING_NAVIGATION_PORT", saved.get("TANGYING_NAVIGATION_PORT", "18790")),
        "TANGYING_NAVIGATION_INPUT_MODE": os.environ.get("TANGYING_NAVIGATION_INPUT_MODE", saved.get("TANGYING_NAVIGATION_INPUT_MODE", "runtime")),
        "TANGYING_RUNTIME_ADDRESS": os.environ.get("TANGYING_RUNTIME_ADDRESS", saved.get("TANGYING_RUNTIME_ADDRESS", "host.docker.internal:"+sim_port)),
        "TANGYING_RUNTIME_ROBOT_ID": robot,
        "TANGYING_RUNTIME_HEAD_SOURCE": os.environ.get("TANGYING_RUNTIME_HEAD_SOURCE", saved.get("TANGYING_RUNTIME_HEAD_SOURCE", robot+"/head-rgbd")),
        "TANGYING_RUNTIME_BASE_SOURCE": os.environ.get("TANGYING_RUNTIME_BASE_SOURCE", saved.get("TANGYING_RUNTIME_BASE_SOURCE", robot+"/base-rgbd")),
        "TANGYING_RUNTIME_INSECURE": "1",
        "ROS_DOMAIN_ID": os.environ.get("ROS_DOMAIN_ID", saved.get("ROS_DOMAIN_ID", "61")),
        "ROS_IMAGE": os.environ.get("ROS_IMAGE", saved.get("ROS_IMAGE", "ros:jazzy-ros-base")),
        "RMW_IMPLEMENTATION": os.environ.get("RMW_IMPLEMENTATION", saved.get("RMW_IMPLEMENTATION", "rmw_cyclonedds_cpp")),
    }
    if config["TANGYING_NAVIGATION_MODE"] not in ("mapping", "localization"):
        raise ValueError("navigation mode must be mapping or localization")
    if config["TANGYING_NAVIGATION_SCENE"] not in ("tabletop", "home", "home_task"):
        raise ValueError("navigation scene must be tabletop, home or home_task")
    if not re.fullmatch(r"/data/maps/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.db", config["TANGYING_NAVIGATION_DATABASE"]):
        raise ValueError("navigation database must stay under /data/maps and use a .db filename")
    if config["TANGYING_NAVIGATION_INPUT_MODE"] != "runtime":
        raise ValueError("this local simulator entrypoint requires runtime input; use deploy/navigation for real ROS drivers")
    if not re.fullmatch(r"[A-Za-z0-9_-]{24,128}", config["TANGYING_NAVIGATION_TOKEN"]):
        raise ValueError("navigation token must contain 24-128 URL-safe characters")
    for port in (sim_port, agent_port, config["TANGYING_NAVIGATION_PORT"]):
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("service ports must be integers from 1 to 65535")
    if len({int(sim_port), int(agent_port), int(config["TANGYING_NAVIGATION_PORT"])}) != 3:
        raise ValueError("simulation, agent and navigation ports must differ")
    if not config["ROS_DOMAIN_ID"].isdigit() or not 0 <= int(config["ROS_DOMAIN_ID"]) <= 232:
        raise ValueError("ROS domain must be an integer from 0 to 232")
    if config["RMW_IMPLEMENTATION"] not in ("rmw_cyclonedds_cpp", "rmw_fastrtps_cpp"):
        raise ValueError("RMW_IMPLEMENTATION must be a supported navigation DDS implementation")
    if config["TANGYING_RUNTIME_ADDRESS"] != "host.docker.internal:"+sim_port:
        raise ValueError("local navigation Runtime address must match the same sim-stack port via host.docker.internal")
    if not re.fullmatch(r"(?:docker.io/library/)?ros(?::[A-Za-z0-9_.-]+|@sha256:[a-f0-9]{64})", config["ROS_IMAGE"]):
        raise ValueError("ROS_IMAGE must identify an official ROS image tag or digest")
    for key in ("TANGYING_RUNTIME_ROBOT_ID", "TANGYING_RUNTIME_HEAD_SOURCE", "TANGYING_RUNTIME_BASE_SOURCE"):
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,160}", config[key]):
            raise ValueError("robot and source identities contain unsupported characters")
    return config, sim_port, agent_port


def main():
    if args.operation not in ("start", "restart") and not CONFIG.exists():
        print("导航尚未配置；请先运行 navigation-start。")
        return 0 if args.operation == "stop" else 1
    config, sim_port, agent_port = load_config()
    serialized = "".join(key+"="+value+"\n" for key, value in sorted(config.items()))
    fingerprint = hashlib.sha256(serialized.encode()).hexdigest()
    env = os.environ.copy()
    env.update(config)
    env["SIM_STACK_PERCEPTION"] = "rgbd"
    env["SIM_STACK_SCENE"] = config["TANGYING_NAVIGATION_SCENE"]
    env.update(SIM_STACK_ARTIFACTS_DIR=str(ARTIFACTS), SIM_STACK_SIM_PORT=sim_port,
               SIM_STACK_AGENT_PORT=agent_port, SIM_STACK_NAVIGATION_CONFIG_SHA256=fingerprint,
               TANGYING_NAVIGATION_URL="http://127.0.0.1:"+config["TANGYING_NAVIGATION_PORT"])
    compose = [DOCKER, "compose", "-p", PROJECT, "-f", str(ROOT / "deploy/robot/navigation/compose.yaml")]

    def sim(operation, quiet=False):
        command = [SIM_SCRIPT, operation]
        if operation in ("start", "restart"):
            command += ["--perception", "rgbd"]
            if config["TANGYING_NAVIGATION_SCENE"] != "tabletop":
                command += ["--scene", config["TANGYING_NAVIGATION_SCENE"]]
        return subprocess.run(command, env=env, cwd=ROOT, check=False,
                              stdout=subprocess.DEVNULL if quiet else None, stderr=subprocess.DEVNULL if quiet else None).returncode

    def docker(*extra):
        result = subprocess.run(compose+list(extra), env=env, cwd=ROOT, check=False)
        if result.returncode:
            raise RuntimeError("导航 Compose 命令失败；首次启动请使用 --build，查看 Docker 与 navigation-logs 的具体状态")

    def runtime_owned():
        metadata = read_pairs(METADATA)
        try:
            marker = json.loads(MARKER.read_text())
        except (OSError, ValueError):
            return False
        return bool(metadata.get("GENERATION") and marker.get("generation") == metadata["GENERATION"]
                    and marker.get("configSha256") == fingerprint
                    and metadata.get("NAVIGATION_CONFIG_SHA256") == fingerprint)

    if args.operation == "logs":
        docker("logs", "--tail", "100", *(["--follow"] if args.follow else []), "navigation")
        return 0
    if args.operation == "status":
        docker("ps")
        sim_status = sim("status")
        try:
            request = Request(env["TANGYING_NAVIGATION_URL"]+"/v1/navigation/map",
                              headers={"Authorization": "Bearer "+config["TANGYING_NAVIGATION_TOKEN"]})
            with urlopen(request, timeout=2) as response:
                status = json.load(response)
            print(json.dumps({key: status.get(key) for key in ("ready", "mode", "localizationState", "mapRevision", "poseSource")}, ensure_ascii=False))
            return 0 if sim_status == 0 and runtime_owned() and status.get("ready") is True else 1
        except Exception:
            print("导航 API 尚不可用，不能视为已完成建图或定位。", file=sys.stderr)
            return 1

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with open(ARTIFACTS / "navigation.lifecycle.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("另一个导航生命周期操作正在执行，请稍后重试") from None
        # Reload after taking the lock: a previous writer may have created the
        # private token while this invocation was waiting for filesystem access.
        if CONFIG.exists() and read_pairs(CONFIG).get("TANGYING_NAVIGATION_TOKEN") != config["TANGYING_NAVIGATION_TOKEN"]:
            raise RuntimeError("导航配置在启动期间已变化，请重新运行")
        if args.operation == "stop":
            if runtime_owned():
                if sim("stop"):
                    raise RuntimeError("身份匹配的 Runtime 未能停止；请检查 sim-status")
                MARKER.unlink(missing_ok=True)
            else:
                print("当前 Runtime 不是此导航配置启动的代次，予以保留。")
            docker("down")
            print("导航已停止，任务数据库、私有配置与地图卷已保留。")
            return 0

        running = sim("status", quiet=True) == 0
        owned = runtime_owned()
        if args.operation == "start" and (running or read_pairs(METADATA).get("GENERATION")) and not owned:
            raise RuntimeError("当前 Runtime 无法确认已加载此导航配置；请运行 navigation-restart（scripts/navigation-stack.sh restart）")
        private_write(CONFIG, serialized)
        options = ["up", "-d", "--build" if args.build else "--no-build"]
        if args.operation == "restart":
            options.append("--force-recreate")
        docker(*options)
        if args.operation == "restart" or not running:
            if sim(args.operation):
                raise RuntimeError("导航容器已启动，但 RGB-D Runtime 启动失败；请检查 sim-logs 后重试 restart")
            metadata = read_pairs(METADATA)
            if not metadata.get("GENERATION") or metadata.get("NAVIGATION_CONFIG_SHA256") != fingerprint:
                raise RuntimeError("Runtime 启动记录未确认导航配置，不能声明接入成功；请使用 restart")
            private_write(MARKER, json.dumps({"generation": metadata["GENERATION"], "configSha256": fingerprint})+"\n")
        print("导航服务与 RGB-D 工作台已启动：http://127.0.0.1:"+agent_port)
        print("场景："+config["TANGYING_NAVIGATION_SCENE"]+"；模式："+config["TANGYING_NAVIGATION_MODE"]+"；使用 navigation-status 核对地图、定位与导航就绪状态。")
        return 0


try:
    raise SystemExit(main())
except (OSError, ValueError, RuntimeError) as exc:
    print("navigation-stack: "+str(exc), file=sys.stderr)
    raise SystemExit(1) from None
PY
