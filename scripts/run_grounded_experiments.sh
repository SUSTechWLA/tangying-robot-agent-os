#!/usr/bin/env bash
set -euo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT="$ROOT/artifacts/grounded-verification/run"
ARGS=(--limit 30)
JOBS=1
DEMO=0
LLM_ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --output) OUTPUT="$2"; shift 2 ;;
    --demo) ARGS+=(--demo); DEMO=1; shift ;;
    --jobs) JOBS="$2"; shift 2 ;;
    --llm) LLM_ARGS+=(--llm); shift ;;
    --llm-config|--llm-workers) LLM_ARGS+=("$1" "$2"); shift 2 ;;
    --seeds|--limit|--offset) ARGS+=("$1" "$2"); shift 2 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
[[ ! -f "$OUTPUT/trials.jsonl" ]] || { echo '该目录已有实验，使用新目录或 --replay 重放。' >&2; exit 2; }
if [[ "$JOBS" != 1 && "$DEMO" == 0 ]]; then
  exec "$ROOT/.venv/bin/python" "$ROOT/scripts/grounded_experiment.py" --output "$OUTPUT" --jobs "$JOBS" "${ARGS[@]}" "${LLM_ARGS[@]}"
fi
"$ROOT/.venv/bin/python" "$ROOT/scripts/grounded_gazebo.py" --world "$OUTPUT/world.sdf"
NAME="tangying-gvf-$(date +%s)-$$"
IMAGE="${TANGYING_GAZEBO_IMAGE:-tangying-navigation:dev}"
docker image inspect "$IMAGE" --format '{{.Id}}' > "$OUTPUT/docker-image.txt"
cleanup() { docker logs "$NAME" > "$OUTPUT/container.log" 2>&1 || true; docker rm -f "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT INT TERM
# Isolated Gazebo partition and no published ports; existing stacks stay untouched.
docker run --name "$NAME" --rm --entrypoint bash \
  -e LIBGL_ALWAYS_SOFTWARE=1 -e QT_QPA_PLATFORM=offscreen -e GZ_PARTITION="$NAME" \
  -e PYTHONPATH=/workspace/robot/gateway:/workspace/python:/opt/navigation-venv/lib/python3.12/site-packages \
  -e GZ_SIM_RESOURCE_PATH=/workspace/artifacts/sim-assets/harmonic-models \
  -v "$ROOT:/workspace:ro" -v "$OUTPUT:/results:rw" "$IMAGE" -lc '
    set -euo pipefail
    set +u
    source /opt/ros/jazzy/setup.bash
    set -u
    cmake -S /workspace/sim/gazebo -B /tmp/gvf-build -DCMAKE_PREFIX_PATH="/opt/ros/jazzy/opt/gz_transport_vendor;/opt/ros/jazzy/opt/gz_msgs_vendor;/opt/ros/jazzy/opt/gz_utils_vendor;/opt/ros/jazzy/opt/gz_math_vendor;/opt/ros/jazzy/opt/gz_cmake_vendor" > /results/build.log 2>&1
    cmake --build /tmp/gvf-build -j2 >> /results/build.log 2>&1
    python3 /workspace/scripts/grounded_experiment.py --output /results "$@"
  ' bash "${ARGS[@]}" 2>&1 | tee "$OUTPUT/execution.log"
# Model credentials remain on the host; robot containers only collect evidence.
if [[ ${#LLM_ARGS[@]} -gt 0 ]]; then
  "$ROOT/.venv/bin/python" "$ROOT/scripts/grounded_experiment.py" --replay --output "$OUTPUT" "${LLM_ARGS[@]}"
fi
[[ -s "$OUTPUT/report.md" ]] || { echo "实验未生成报告，检查 execution.log" >&2; exit 3; }
echo "报告：$OUTPUT/report.md"
