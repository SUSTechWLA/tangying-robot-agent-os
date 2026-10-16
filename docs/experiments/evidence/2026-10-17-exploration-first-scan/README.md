# 首次探索扫描：脱敏证据包

对应[完整报告](../../2026-10-17-exploration-first-scan.md)、[JSON 指标](../../2026-10-17-exploration-first-scan.json)及[CSV](../../2026-10-17-exploration-first-scan.csv)。四次实际 Gazebo 运行各自冻结源码，不合并为统计成功率。

| 目录 | Runtime 地图发布/加载验收 | 初始转角目标 | 停止 |
| --- | --- | --- | --- |
| `001/` | 通过，7.923570 m / 132 帧 | 未达，约 270° | travel_budget |
| `002/` | 通过，7.876807 m / 122 帧 | 未达，约 174° | travel_budget |
| `003/` | 通过，6.152850 m / 96 帧 | 达成，约 358.5° | no_progress，部分结果 |
| `004/` | 通过，7.963161 m / 130 帧 | 达成，约 359.2° | travel_budget，部分结果 |

每个目录的 `runtime/` 完整复制对应 Harness 输出，包括 29 份请求/响应、初终态、轮询、源码摘要、环境、地图及来源文件；其原 `manifest.json` 不改写。地图声明的全部二进制/JSON/栅格产物均在包内，可以独立校验。

旁边的 `source-manifest.json` 与 `source-dirty.patch` 对应从源码基线 `980e8b4d8` 冻结的本轮 Runtime；实际 Python 以只读 mount 覆盖精确镜像，ROS 二进制保持镜像基线。Compose 仅用于回溯启动条件，路径含当时本机目录，复现需创建自己的新目录/端口/ROS domain，不能直接覆盖原运行。私有环境文件未纳入证据。

`external-raw.json` 指向本地独立完整观测流与 ROS 日志，保留 bytes/SHA；数据库与完整源码快照也在各次 `artifacts/experiments/` 本地目录。它们没有全部进入 Git，缺少本机原始数据时不能声称复核了原图像/完整观察流。

`post004-software/` 单独记录最终审查发现的非零续建坐标帧修复和软件源摘要；它不是第五次物理实验，不把004原始结果升级为非零锚点续建认证。

本目录 `manifest.json` 为外层逐文件 SHA/大小清单，不包含它自身。复核示例：

```bash
.venv/bin/python - <<'PY'
from pathlib import Path
import hashlib, json
root = Path('docs/experiments/evidence/2026-10-17-exploration-first-scan')
manifest = json.loads((root/'manifest.json').read_text())
for name, expected in manifest['files'].items():
    path = root/name
    assert path.stat().st_size == expected['bytes'], name
    assert hashlib.sha256(path.read_bytes()).hexdigest() == expected['sha256'], name
print('verified', len(manifest['files']), 'files')
PY
```

各次 `bounded-steps.jsonl` 与 `motion-stop-checks.json` 独立检查同一 operation 的全部 ended 记录，确认最终零速度命令已发布；这不是独立轮速计的静止测量。

地图通过 `tangying_robot_gateway.map_manifest.load_manifest/verify_artifacts` 逐次核验；框架状态中的 `completed` 只证明约定长操作结束。全环境覆盖、定位真值精度、自然语言 Host 和实体设备验收在本轮均未新增测量。
