#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

mkdir -p gen/go python/tangying_robot_proto
touch python/tangying_robot_proto/__init__.py

export PATH="$(go env GOPATH)/bin:$PATH"

protoc -I proto \
  --go_out=gen/go --go_opt=paths=source_relative \
  --go-grpc_out=gen/go --go-grpc_opt=paths=source_relative \
  proto/robot/v1/robot.proto

.venv/bin/python -m grpc_tools.protoc -I proto \
  --python_out=python/tangying_robot_proto \
  --grpc_python_out=python/tangying_robot_proto \
  proto/robot/v1/robot.proto

find python/tangying_robot_proto -type d -exec touch '{}/__init__.py' \;

.venv/bin/python - <<'PY'
from pathlib import Path

root = Path("python/tangying_robot_proto")
for path in root.rglob("*_pb2_grpc.py"):
    text = path.read_text()
    text = text.replace("from robot.v1", "from tangying_robot_proto.robot.v1")
    path.write_text(text)
PY
