# 当前发布验证证据

本文是 `v0.2.0-rc.2`（2026-08-24）生产交付候选的可复核记录。代码身份以本次 PR head、合并 commit 和本文件中的签名 runId 共同确定。验证工作区为 macOS Darwin 25.5.0 arm64，Go 1.26.2、主 Python 3.11.9、隔离 RoboCasa Python 3.11.15、Node 25.9.0、npm 11.12.1。

## 1. 已交付能力

- 中文自然语言生成双机器人任务，并在用户端用人话展示“理解结果、执行步骤、调用工具、环境证据和最终结果”。
- 运行中的任务可以提出更新、预览差异、确认 revision 2，并在安全点切换；旧 revision、旧命令和重复提交不能推进新任务图。
- 云端 Fleet、双 Edge、双 Robot Runtime、共享 WorldModel、Coordinator、资源 custody/fencing 和 Harness Agent 形成闭环。
- RoboCasa/MuJoCo 中两台完整 XLeRobot 完成红色方块交接，目标在任务执行中更新为右侧蓝色垫子。
- WebGL 显示完整厨房、双机器人、方块标记、状态/路径/证据；WebGL 故障时保留 `WORLD LIVE` 并降级到语义 Canvas。
- 工具执行生命周期固定为 `SENDING → RUNNING → AWAITING_EVIDENCE → CONFIRMED`；本地闭环由 Runtime 后置观测完成，云端 Harness 再确认跨机器人结果。
- 抓取/放下工具经 Edge Policy Provider 接入 VLA、模仿学习或强化学习 sidecar；manifest、robot model、calibration、观测和动作边界不匹配时失败关闭，未知物理终态不自动重放。

## 2. 自动化门禁

| 门禁 | 结果 | 说明 |
| --- | --- | --- |
| `make generate-check` | 通过 | protobuf 生成物无漂移 |
| `make build` | 通过 | `robot-agent`、`local-agent` 可构建 |
| `make lint` | 通过 | Go 格式与 Ruff 静态检查全绿 |
| `make test` | 通过 | Go 包全绿；Python `504 passed, 33 skipped`，206.48 秒；Web 核心 `45 passed` |
| RoboCasa/更新/故障/视觉聚焦 | 通过 | 隔离环境 `173 passed`，145.34 秒 |
| 完整 Web fresh install | 通过 | `npm ci && npm run build && npm test`，`112 passed` |
| 文档与视觉契约 | 通过 | `150 passed`，包含 4 个生产文档契约 |
| `make robocasa-acceptance` | 通过 | tracked signed round4 pack 离线重验，23/23 checks |
| `make e2e` / `make fleet-chaos` | 通过 | `194 passed, 6 skipped`；17 类分布式/策略异常矩阵全通过 |
| 30 回合仿真验收 | 通过 | 30/30 成功、0 safety violations、18/18 对象目标、双目标序列成功 |
| `make lint` / `git diff --check` | 通过 | 无静态或空白错误 |

主 `.venv` 不安装可选的视觉导出依赖；全量 Python 门禁使用临时、未写入仓库的依赖层提供 Pillow、trimesh、pygltflib、SciPy，RoboCasa 专项则始终使用 `tangying-robocasa` 隔离环境。没有跳过失败测试或修改验收门槛。

首次全量运行发现 3 个旧测试仍断言升级前的 `STEP_SUCCEEDED`/严格事件数组；实现实际已输出面向用户的 `TOOL_ACTIVITY`。测试先失败，随后改为验证每一步完整工具状态链，聚焦 4 项通过后才重新执行全量门禁。

## 3. 签名 RoboCasa 证据

证据包：`artifacts/robocasa-harness/round4`，由临时 Ed25519 密钥签名并由 tracked anchor 绑定；私钥和上传 bearer 未保留。

| 字段 | 值 |
| --- | --- |
| schema | `tangying.robocasa-acceptance-summary.v6` |
| runId | `447f7c92868046c293daf8c2c0006511` |
| taskId | `task-7e9b0de15e8db9ef9f0afa90` |
| 原始任务 | 1 号机器人送到交接区，再由 2 号机器人送到右侧目标区 |
| 运行中更新 | `最后放到右侧蓝色垫子上` |
| 最终 revision | 2 |
| summary | `passed=true`，23/23 checks |
| WebGL bundle SHA-256 | `17052a7e5629ed408b30074e155caad9760a6aba5084967425631439289cec96` |

23 项检查包括：签名认证、任务/场景/模型身份、14 关节、关节运动、双 intent、最终放置、held 清空、观测新鲜度、custody、快照顺序、nonce、custody 轨迹、Harness 证据、资产 hash、同源、provenance、截图、浏览器网络、浏览器性能、版本化任务更新和策略工具证据。

五张 1404×794 截图分别原子绑定 WorldSnapshot revision：overview 2646、robot-1 2674、robot-2 2730、handoff-final 2800、fallback 3808。fallback 同时证明 `WORLD LIVE / VISUAL DEGRADED`，且语义 Canvas 仍可读。

## 4. 性能证据

| 指标 | 实测 |
| --- | --- |
| 首次用户交互 | 814 ms |
| 刷新恢复 | 958 ms |
| 浏览器实际稳定 display rAF | 39.7535 FPS |
| renderer submission capacity | 142.4907 FPS |
| render duration mean / p90 / p95 / max | 7.018 / 8.100 / 8.800 / 12.500 ms |
| 稳定 render 样本 | 300 |

display rAF 与 renderer submission capacity 是不同指标；本次受控浏览器 submission capacity 达到自动门槛，但受录屏/受控调度影响的 display rAF 为 39.7535 FPS，不能表述为用户显示达到 50 FPS。生产仍必须在目标终端、显示器和真实网络的非限频可见窗口独立验收。

## 5. 可交付文档覆盖

本目录现包含架构、快速上手、全部接口、数据契约、配置与安全、异常运维、仿真到实机、测试验收、部署容量以及本发布证据。它们覆盖开发、云端运维、机器人技术员、系统集成、安全审核和项目负责人六类读者。接口表由测试与实际 Go 路由交叉核对；内部 Markdown 链接、Make target、引用路径和默认密码说明也受文档测试约束。

## 6. 已知边界与上线前置条件

- 仿真通过不等于实机安全认证。实机必须完成机械限位、速度/力矩限制、实体急停、工作区隔离、碰撞测试和安全负责人签字。
- 当前证据证明单机开发环境中的分布式进程边界，不证明生产 MySQL/Redis HA、跨地域灾备、峰值容量或 24/72 小时长稳。
- 客户现场必须重新完成地图、坐标系、相机外参、观测 source freshness、抓取成功率、断网停止、旧 fencing 拒绝和备份恢复演练。
- 开发回环环境可用 `admin / admin123`；生产脚本生成随机管理员密码。生产不得复制演示密码，也不得把密码写入截图、日志或证据包。
- 回滚时按 [部署与容量](deployment-and-capacity.md) 的版本兼容规则回退 Fleet/Edge，并保持数据库、任务 revision、资源 fencing token 和证据日志单调，不能回滚世界事实。

上线签字前，应把本文件复制为客户环境验收记录，替换平台、commit、实机设备序列号、测试结果、性能、责任人和签字日期；任何未通过项都必须保留为阻断项。

## 7. 学习型策略链证据

生产升级包含独立 PolicyManifest/Observation/Inference 契约、Go HTTP/确定性 Provider、Python VLA/模仿学习/强化学习 sidecar、Edge 运动前校验和六类恢复状态。自然语言双机器人传递验收记录四个已由环境确认的策略工具、四个唯一 inference ID、完整 manifest/observation 证据和零 action chunk 前端泄露。签名 `round4` 的 `policyToolEvidence=true`；这仍是仿真证据，不是 PHYSICAL_GO 证明。
