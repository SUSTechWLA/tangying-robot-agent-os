# 测试、签名验收与发布检查

单机器人 RGB-D 验收使用 `.venv/bin/pytest -q tests/e2e/test_rgbd_recovery.py`，会在独立端口启动相机仿真、HTTP/gRPC Agent 和 SQLite，测试工具边界暂停/只重启 Agent/同任务继续，以及动作中崩溃后的未知结果阻断。保留可读证据时运行 `.venv/bin/python scripts/run_rgbd_acceptance.py --output 新目录`；负向增加 `--scenario unknown-outcome`。不连接真实设备，不能用于实机性能或急停认证。

家庭场景的合同与路线验收使用 `PYTHONPATH=sim/mujoco .venv/bin/pytest -q sim/mujoco/tests/test_home_scene.py`，覆盖五个房间、无桌面真值、双 RGB-D 原始帧、家庭场景选择和 `verify_arrival` 的底部相机证据。完整家庭仿真入口为 `bash scripts/home-slam-stack.sh restart --sim-port 51051 --agent-port 8878`；需要 RTAB-Map/Nav2 时使用 `make navigation-restart NAVIGATION_ARGS='--build --mode mapping --scene home'`。当前本机验证不等于真实房屋地图、制动距离或 XLeRobot 生产验收，现场放行按[家庭发布清单](../operations/release-checklist.md)执行。

Gazebo Harmonic 后端的静态合同检查使用 `PYTHONPATH=robot/ros2_ws/src/tangying_navigation .venv/bin/pytest -q robot/ros2_ws/src/tangying_navigation/test/test_launch_config.py`；容器内可用 `make gazebo-house-start GAZEBO_HOUSE_ARGS='--build --mode mapping'` 启动真实 RGB-D/odom/cmd_vel bridge。没有完成五房间受控探索和自然语言路线的逐段新观测前，不能标记为 `SIMULATION_GO`。

v0.2 的真实 ROS 导航验收先启动 `make navigation-start NAVIGATION_ARGS='--build --mode mapping'`，等待地图就绪，再运行 `.venv/bin/python scripts/run_navigation_acceptance.py --output 新目录`。它明确创建并批准一条本地 MuJoCo 双物体任务，要求从距操作位至少 60 厘米的位置出发，校验 18 个唯一步骤、六次物理工具各执行一次、全部历史图像哈希、抵达后新观测、真实导航位移与三帧稳定放置。`--pause-seconds 65` 在首个导航工具边界暂停后继续；只读重观测产生额外事件是正常恢复行为，不计为重复物理执行。脚本不重置现场或删除 journal；下一次演示先在任务结束且未持物时显式重启仿真。建图／已保存地图定位、暂停和故障分别留独立输出目录，结果见[发布记录](../releases/v0.2.0.md)。

## 1. 测试矩阵

| 层级 | 命令 | 证明内容 |
| --- | --- | --- |
| Go 单元测试 | `make test-go` | Task CAS、Coordinator、World/Harness、Fleet/Console、mTLS/幂等 |
| Python 单元/契约 | `make test-python` | 安装、Adapter、RoboCasa、证据验证、故障矩阵 |
| 异构接入与跨语言 | `.venv/bin/pytest -q robot/gateway/tests tests/contract/test_heterogeneous_runtime_boundary.py` | 不同结构/来源、真实 Go→Python gRPC、输入限幅/审批/旧帧拒绝；不证明真实硬件性能 |
| MCP 协议 | `.venv/bin/pytest -q tests/mcp` | 官方 SDK 真 stdio 初始化/工具发现/调用、HTTP 鉴权和审批保留 |
| Web 单元测试 | `make test-web` | 任务体验、revision ordering、WebGL、交互、fallback、资源闭包 |
| 导航与双相机 | `.venv/bin/pytest -q sim/mujoco/tests/test_rgbd_navigation.py sim/mujoco/tests/test_rtabmap_client.py robot/ros2_ws/src/tangying_navigation/test` | 同帧 RGB-D、速度租约、坐标系、Nav2 HTTP 合同、失联停止；单测不代替实际 ROS 节点验收 |
| RoboCasa 冒烟 | `make robocasa-smoke` | 环境、模型、共享世界可加载 |
| RoboCasa 故障 | `make test-robocasa-faults` | 断连、重复、倒序、stale、custody、恢复 |
| 自然语言固定评测 | `scripts/evaluate_natural_language.py --output 新目录`（主 `.venv/bin/python` 执行） | 当前检出的真实仿真进程；5 条成功动作、6 条解析拒绝、2 条执行前失败检查 |
| 签名证据重验 | `make robocasa-acceptance` | tracked round4 未篡改且 23 项语义检查全通过 |
| 构建/生成 | `make generate-check`, `make build`, `make lint` | 生成代码、二进制、格式和静态检查 |
| 文档契约 | `.venv/bin/python -m pytest -q tests/docs/test_production_docs.py` | 文档集、路由、链接、命令、路径和秘密扫描 |

语言评测先安装独立 RoboCasa 环境，用 `ROBOCASA_PYTHON` 指向其解释器；准确命令、可选 `--case`、`--keep-running`、输出权限和证据口径见[复现指南](../development/natural-language-evaluation.md#复现)。脚本自动构建当前 Fleet/Edge；错误解析在审批前取消。13/13 表示符合预期，不等于全部任务都应执行。反向搬运额外探索失败单独保存，不从报告中删除。

测试数量按每轮日志记录；各轮前端数量以[V1 状态](v1-release-status.md)记录为准，不能沿用旧轮次。此次文档同步不重写历史实测日期，也不等同于重新采集签名包。

## 2. RoboCasa 真实流程

上述新接入测试需要 `mcp` 可选依赖（`make setup` 已安装）。Python JSON Schema 与 Go 消费验证必须同时通过，不能只验证 provider 输出能序列化。三维重建算法质量、坐标标定误差、真实停机/碰撞/负载等仍需目标设备验收。当前三项 MuJoCo 交接回归通过在感知源补齐真实 inside/held_by 关系修复，未放松 Agent 起点检查。

验收启动 Fleet、两个 Edge、两个 Runtime 和一个共享 RoboCasa/MuJoCo 世界；创建中文任务；在 robot-1 步骤 RUNNING 时提议“最后放到右侧蓝色垫子上”；确认 revision 2；验证 `WAITING_SAFE_POINT`；等待双 Harness `SATISFIED`；最终方块在右目标、held 清空、environment token 3。

浏览器验证：完整厨房和两台 XLeRobot；人话理解、编号步骤、工具活动；更新轨道；专业详情折叠；左拖/右拖/滚轮/预设/跟随/双击/F；刷新恢复；模型/边界/标签/路径独立；WebGL 故障后 `WORLD LIVE / VISUAL DEGRADED` 与语义 Canvas。

## 3. 签名证据的信任模型

candidate runner 在启动栈前生成不可预测 episode nonce、一次性 bearer 接收端和临时 Ed25519 key。浏览器提交五张 1404×794 截图、与每张原子绑定的 WorldSnapshot/DOM revision、完整 page-assets inventory、真实性能时间序列和交互事件。runner 独立请求 document、CSS、五个脚本（含 `console_ui.js`、`navigation_view.js`）、manifest、scene、robot、binding 十一角色并交叉核对 URL、无重定向、nonce header、bytes 和 SHA-256。历史签名包按它当时的资产集合严格验证，不追加当前脚本。

接收端只接受一个有效 POST；PNG 规范化后写入私有 staging；summary 逐项重算 Task/Scene/Model/Joints/Intent/Placement/Freshness/Custody/Trajectory/Harness/Asset/Provenance/Screenshot/Network/Performance/VersionedUpdate/PolicyToolEvidence。随后 final attestation 签住 summary、capture envelope 和全部 retained regular files，私钥销毁。任何 symlink、特殊文件、未签额外文件、hash/signature/anchor 变化都失败关闭。

日常只运行：

```bash
make robocasa-acceptance
```

它离线验证历史 `artifacts/robocasa-harness/round4`，不启动栈、不证明当前源码已通过新采集。通过原始 anchor、签名和全部文件哈希后，验证器使用已签名的历史 frontendBuild 核对网络证据；当前候选及基线提升仍按当前源码的十一角色和哈希验证。round4 的历史资产身份保持原样，不能用历史包直接提升不同的当前界面。只有有意更新黄金证据时才：

```bash
make robocasa-acceptance-candidate
# 使用仓库 uploader 对 runner 输出的 0600 session 发一次本地 POST
make robocasa-acceptance-promote
make robocasa-acceptance
```

不要直接编辑 summary、截图、anchor 或 attestation。

## 4. 性能指标如何解释

签名证据同时记录实际 display rAF 与 `gpuRenderer.render()` 调用耗时。自动 gate 要求稳定 renderer submission capacity ≥50 FPS；p90/p95/max 仍如实保留。submission capacity 可能高估显示器、合成器或 GPU completion 下用户看到的流畅度，因此不能写成“用户显示达到 50 FPS”。生产实机必须在目标浏览器、目标显示器和可见窗口独立验收 display rAF ≥50，或由产品明确接受更低门槛。

## 5. 发布检查

```bash
make generate-check
make build
make lint
make test
conda run --no-capture-output -n tangying-robocasa python -m pytest -q \
  tests/e2e/test_robocasa_task_updates.py \
  tests/e2e/test_versioned_task_faults.py \
  tests/e2e/test_robocasa_faults.py \
  tests/e2e/test_robocasa_visual_twin.py
make test-web
# WebGL bundle 或依赖变化时，另在 web 目录 npm ci / npm run build / npm test
make robocasa-acceptance
git diff --check
git status --short
```

再从干净 checkout/`git archive` 运行 `make robocasa-acceptance`，证明没有依赖忽略文件。检查无 `node_modules`、session、私钥、token、临时端口或后台进程。

## 6. 实机验收

仿真通过后仍需：mTLS 身份、实体急停、限位、断网停止、命令幂等、旧 fencing 拒绝、地图/外参、source freshness、单/双机器人持物恢复、实际抓取成功率、碰撞测试、可见帧率、24/72 小时长稳、备份恢复。安全负责人签字前不能把仿真结论表述为实机安全认证。

## 7. 发布证据应记录

commit、平台、Go/Python/Web/文档测试数量和耗时、bundle hash、acceptance run/task/nonce 的非秘密引用、23 项检查、截图摘要、性能 mean/p90/p95、已验证异常、已知限制、回滚版本和实机待办。当前结论写入 [V1 状态](v1-release-status.md)；`release-evidence.md` 是历史 rc.2 记录，不覆写历史结果。

## 8. 策略工具验收

`make policy-handoff` 从中文自然语言启动双 Edge/Runtime，验证两台机器人依次抓取和放置、四个独立 inference/observation 身份、最终 WorldModel 与 custody。`make policy-faults` 覆盖过期/异常观测、Provider 超时/不可用、manifest/robot/calibration 漂移、畸形/越界动作、未知物理终态和前端脱敏。`make fleet-chaos` 将该矩阵与协调器、revision、断线和 fencing 故障合并为发布证据。实机仍必须按[学习型策略工具](policy-tools.md#9-sim2real-晋级流程)独立完成三阶段晋级。

## 9. Sim2Real 离线工具

`robot-agent sim2real init/check/record/report` 用于配置 kit、阶段检查与操作员证据汇总；不连接硬件、不运行 trial，也不验证人填证据是否真实发生。inventory/integration/pilot 的确切材料和命令见[购机后上手](../sim2real/README.md)。至少 30 次 trial 和规定 soak 是受限试点记录门槛，不等于 PHYSICAL_GO 或生产安全认证。
