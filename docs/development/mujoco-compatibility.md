# MuJoCo 版本与兼容检查

v0.2.0 的主 Python 环境在 `pyproject.toml` 中精确锁定 `mujoco==3.11.0`。它是本版仿真引擎基线；`make setup` 会安装该版本，RoboCasa 继续使用独立环境。升级仿真引擎属于需要重新验证的依赖变更，不能由一次普通安装悄悄切换。

本版同时在 [`pyproject.toml`](../../pyproject.toml) 固定核心 Python 依赖，保持代码生成、Runtime 和 ROS 镜像使用一致的工具链：

| 依赖 | v0.2.0 基线 | 用途 |
| --- | --- | --- |
| `grpcio` | `1.83.0` | Runtime RPC；兼容下列生成工具的最低版本要求 |
| `grpcio-tools` | `1.81.0` | 生成 Protobuf 6 存根，与实机 SDK 的依赖范围一致 |
| `protobuf` | `6.33.5` | 仿真、ROS 导航与 Robot Edge 的共同序列化基线 |
| `mujoco` | `3.11.0` | 主仿真引擎 |
| `numpy` | `2.4.6` | 位姿、深度与重建数值计算 |
| `pydantic` | `2.13.4` | profile、工具与观测合同验证 |
| `pydantic-core` | `2.46.4` | 由上述 Pydantic 版本精确要求的间接依赖 |

这不是对所有可选包的完整锁文件。MCP、开发工具和 RoboCasa 仍按各自安装入口管理。修改上述基线时需要一起检查生成存根与 ROS 镜像，运行 `make generate-check` 及相关回归，并保存当次环境版本。验收期间直接调用 `.venv/bin/python`，避免测试启动命令自动重新解析并升级环境。

Robot Edge 安装器使用 `.[robot-pi]`，其中固定 `lerobot[feetech]==0.4.1` 与 `opencv-python-headless==4.11.0.86`。Feetech extra 提供双臂驱动构造所需的 `scservo_sdk`。LeRobot 的 WandB 依赖要求 Protobuf `<7`；因此不能把生成工具直接升级到要求 Protobuf 7 的 1.83。OpenCV 4.12 要求 NumPy `<2.3`，这里选择支持共同 NumPy 2.4.6 基线的 4.11。调整生成器没有改变 `.proto` 或序列化 descriptor，导航工具和 Runtime 的线上协议不变。

默认 ROS2-free direct 安装使用隔离 venv，不继承系统 NumPy/SciPy；只有 ROS 路径需要 `--system-site-packages` 读取系统 `rclpy`。安装结束执行 `pip check`；CI 的 `robot-pi-dependencies` job 在 Ubuntu 24.04 ARM64 / Python 3.12 对实际 `.[robot-pi]` 做完整依赖解析并保存报告，补足 shell dry-run 不解析 Python 依赖的缺口。依赖解析通过不等于接通实机、相机标定或任务验收通过。新增传感器仍应安装其 SDK 并完成对应的接入验证。

```bash
.venv/bin/python -c 'import mujoco, numpy; print(mujoco.__version__, numpy.__version__)'
.venv/bin/python -m pytest -q sim/mujoco/tests/test_self_filter.py \
  sim/mujoco/tests/test_rgbd_workcell.py
```

## 为什么需要锁定与独立兼容门禁

2026-09-09 排查远程 CI 时，旧范围 `mujoco>=3.3,<4` 在 Linux 拉到了 3.12.0，本机验收环境仍为 3.11.0，两者的 NumPy 均为 2.4.6。3.12 触发 `self-filter CAD requires named scalar encoder joints`，进而让 RGB-D Runtime 无法就绪。

问题是关节类型比较，而非模型失去关节。对一个名为 `arm` 的 slide joint，模型数组返回 `numpy.int32(2)`，MuJoCo 枚举的整数值也是 2；3.12 下存在不对称相等比较：

```python
import mujoco
import numpy as np

kind = mujoco.mjtJoint.mjJNT_SLIDE
value = np.int32(int(kind))
print(value == kind)                         # 3.12：True
print(kind == value)                         # 3.12：False
print(value in (mujoco.mjtJoint.mjJNT_HINGE, kind))  # 3.12：False
```

tuple membership 使用的比较方向导致合法关节被拒绝。修复将模型类型和枚举类型都归一为 `int`，再判断允许集合。自体过滤仍只接受命名的标量 hinge/slide 编码器关节，并保留同帧关节、机器人独立 CAD、原始深度匹配、2 mm 默认匹配容差和首个表面约束；free、ball 与匿名关节仍被拒绝。

## 在独立依赖层复现

使用新的输出目录创建仅用于兼容实验的依赖覆盖层，主 `.venv` 保持 3.11.0：

```bash
mkdir -p artifacts/acceptance/mujoco-compat-run-1
.venv/bin/pip install --no-deps \
  --target artifacts/acceptance/mujoco-compat-run-1/mujoco-3.12 mujoco==3.12.0
PYTHONPATH=artifacts/acceptance/mujoco-compat-run-1/mujoco-3.12 \
  .venv/bin/python -m pytest -q sim/mujoco/tests/test_self_filter.py \
  sim/mujoco/tests/test_rgbd_workcell.py
```

CI 的 `test` job 使用发布基线执行完整回归，独立 `mujoco-compat` job 在 Ubuntu 24.04 / Python 3.11 中执行 3.12 覆盖层的自体过滤与工位测试。兼容测试通过只证明其列出的范围，不自动将 3.12 晋级为正式基线。

本次兼容修复阶段的实测记录如下。这些数值采集于本版后续地面纹理调整之前，最终工位与全量结论以[发布记录](../releases/v0.2.0.md)为准：

| 环境与范围 | 实际结果 |
| --- | --- |
| macOS arm64 / Python 3.11 / MuJoCo 3.12，自体过滤修复前 | 6 failed、7 passed；最小 XML 和 MjSpec 编译均保留关节名称 |
| 相同隔离环境，自体过滤修复后 | 13 passed |
| 相同隔离环境，加当时的参考工位测试 | 16 passed |
| macOS 主环境 MuJoCo 3.11，加当时的工位与版本合同 | 30 passed |
| 独立 Linux arm64 容器 / Python 3.12 / MuJoCo 3.12 | 11 passed、2 deselected；未运行该容器内的两项相机渲染测试 |

本机原始报告保存在忽略目录 `artifacts/acceptance/v0.2-mujoco-compat/`；目录不随 Git 分发，不作为签名证据包。Linux arm64 定向结果也不等于 GitHub Ubuntu amd64 全量 CI 的结论。

## 冷启动渲染与原始采集时间

0.2 的首次远程兼容门禁在 Ubuntu 24.04 / OSMesa / MuJoCo 3.12 遇到另一项边界：首个真实底部相机测试耗时 3.21 秒，渲染器首次建立图形上下文时，提前冻结的世界／编码器快照已经超过 2 秒采集预算。后续相机测试通过。`capture_with_state` 取世界快照与渲染时间中的较早值，拒绝旧数据是正确行为；不能将冷启动结束时间写回原帧。

几何自体过滤与同帧编码器测试现在先显式完成一次丢弃的原始 RGB-D 渲染，再发起全新的同步采集。预热结果不进入导航或自体过滤验证。确定性回归在真实 MuJoCo Renderer 初始化期间推进测试时钟 3 秒，证明未经预热的旧快照仍被拒绝、原时间不变，只有下一次重新采集的帧才通过。该修复仅调整测试启动边界，未修改生产采集逻辑、新鲜度预算或实机条件。

## 世界快照新鲜度的测试边界

另一个远程失败发生在 `test_authoritative_world_contains_live_harness_inputs`：fixture 先等待就绪，随后测试重新请求世界快照，原测试假设两次 HTTP 之间的来源始终新鲜。真实 source freshness 会随时间变化。

独立进程实验在 fixture 就绪后暂停两个 Edge 1.2 秒，使四个来源真实变为 `STALE`，再恢复 Edge。旧测试在首次读取时失败；修复后在原有等待期限内取得同一个四源均为 `FRESH` 的快照再断言，实验得到 `STALE → FRESH`。生产 freshness 预算保持 1 秒，永久不可用的输入仍会使等待超时。复现入口是：

```bash
.venv/bin/python -m pytest -q tests/e2e/test_fleet_handoff.py \
  -k authoritative_world_contains_live_harness_inputs
```

这项修复验证的是异步快照的正确读取时机，不能用于把旧的相机图像或历史任务证据重新标成新鲜。

## Fleet 调试预览的软件渲染成本

后续完整回归又在双机器人 fixture 的 60 秒启动期限内未得到四路同时新鲜的来源。这次瓶颈在实际渲染：Linux ARM64、4 CPU 配额、OSMesa 的分段实验中，两台机器人的单次观测中位耗时约 514–515 ms，其中 `mjr_render` 约 439 ms；PNG 编码约 1.5 ms。观测在渲染前冻结时间，双路持续渲染使 1 秒新鲜度窗口缺少余量。缩小离屏缓冲或阴影贴图没有解决这一成本。

`tangying_sim.fleet_server.create_fleet_services` 现在只让 `task_key` 主光源投射阴影；其他光源保留照明。三盏灯重复绘制复杂 STL 网格的开销因此减少。相同隔离环境的单主光源对照约 272–274 ms；这是特定机器上的短期实验，不是任意硬件的性能承诺。最终完整测试结果与对照记录随发布验收包保存。

该选择会改变补光阴影的外观，不是像素完全等价的优化。最终 PNG 仍为 320×240，保留主阴影、2048 阴影贴图、4 倍多重采样、场景几何及相机。配置仅修改 Fleet 工厂新建的两份独立模型，不改共享 XML、`SceneRenderer`、单机器人 RGB-D、ROS 或 RoboCasa。原始时间、1 秒来源预算、250 ms 采样周期和 60 秒启动等待均未放宽；资源不足或断流仍按原规则拒绝就绪。
