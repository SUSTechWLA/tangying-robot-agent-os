# 统一能力目标升级与闭环验收（2026-09-27）

本轮承接 [P0 审计](../development/2026-09-27-agent-capability-architecture-review.md)，记录随后的实现与试验。升级规格见[实施文档](../development/2026-09-27-capability-goal-implementation-spec.md)，使用与迁移见[操作指南](../guides/unified-capability-goals.md)。原规范和历史实测不覆盖。

## 架构交付

已接入同一任务权威的能力目录、完整参数验证、冻结计划审批、持久操作回执、独立完成读回、复合导航抓放、云端认证目录与 Worker 委托、claim 连续续租、Runtime 失联租约看门狗、GOAL 模型路由及逐阶段热配置。Frontend 不承担主目标编排。新增 Proto 字段、旧入口迁移、部署持久卷和错误恢复规则按实施规格记录。

## Gazebo 环境与失败样本

macOS 开发主机、Docker Gazebo/ROS 2、同源 XLeRobot CAD、home_furnished 完整多房间家庭资源。Runtime 127.0.0.1:50161，Agent 127.0.0.1:8897，机器人 ID gazebo-home_furnished。保持既有地图、标定和日志；仅在任务停止后显式重启仿真部署。重启会重建场景，不把跨重启结果说成连续试验。验收通过统一任务 API，先生成草案再批准；不直接调用 mapping RPC 推进任务。

| 样本 | 结果与原因 | 修复 |
| --- | --- | --- |
| run1 / task-8165261312ebc0757ccdf720 | 取消：发现内部 sessionId 跨建图段变化，终态含义混淆 | 新增稳定 operationId，分段 FINALIZING 不宣称整个操作完成 |
| run2 / task-df3cd4dde8a2e643d68c7386 | NAV_ENVELOPE 环视拒绝；不能继续导航 | 可选环视在明确空间拒绝后停止，保留前沿决策，安全包络不改 |
| run3 / task-495d7aff9819138b6395cb6d | 建图完成，模型又调用手动 mapping.finish，SCAN_NOT_READY 后失败 | 自动 mapping.build 独立完整工具；手动保存不提供给 GOAL；明确接纳前拒绝与未知副作用分开 |
| run4 / task-32c91d4f8a7dce428075802c | 传感器过期被当作空间拒绝，多次后未移动且建图失败 | 传感器/标定/取消故障向上传播，不把它们登记为道路障碍 |
| run5 / task-229e541cf759de8ab4fb0a29 | SLAM 保存启用后，复合导航被 Grounder ID 不匹配拒绝 | 启动探测后将开发默认 robot-local 解析为已核验 Runtime ID，Grounder/Runtime/Telemetry/模型路由统一绑定；显式不匹配仍拒绝启动 |

每次原始报告、目录、计划和事件在 artifacts/acceptance/capability-goal-20260927-runN。早期 run1–4 名为 approved-plan 的文件实际是审批前草案，真实审批事件在 task.json；新脚本分别保存 draft-plan 和 approved-plan，后者为实际批准响应。run2 的旧脚本在失败后发起取消；新版保持可恢复失败以便回溯。

run3 的地图 scan-b6438a4cc056，revision 9c52bbbad6ab3936e2690456d41811e0c8fc7810e5ddb886fa95406380babff9，42帧/33次配准/1次闭环，stopReason=no_reachable_frontier、unknownFraction=0.626144。这只证明该部分地图完成保存和启用，不证明全屋覆盖。任务没有在地图失败时继续运动。

run5 的 scan-1d9f428be91d 保存启用后完成独立核验（53帧、44配准、1回环、1.382米），随后身份接线错误阻断导航；这条失败不计为完整闭环通过。

重启还暴露 Docker/ROS 就绪晚于 Agent 的25秒身份探测；sim-stack 现先等待自己启动的 Runtime 就绪再启动 Agent，仍使用有界等待和精确进程回滚。

## Gazebo 完整闭环通过：run6

请求：`请先运行机器人标定，然后调用自动建图工具按已注册的巡检路线重新建立并启用家庭地图，最后前往厨房并确认到位。`

任务 `task-77d1b15939d54145bedd36ef`，2026-09-27 09:11:27–09:23:01 UTC，最终 SUCCEEDED。实际 GOAL 模型生成 calibration.run → mapping.build(mode=survey) → semantic.resolve(厨房) → robot.task；复合 Intent 在审批前绑定 gazebo-home_furnished 并冻结。模型路由记录为各阶段 OpenAI 兼容接口 https://api.deepseek.com / deepseek-flash，未输出密钥。四步均有 CAPABILITY_VERIFIED，未由页面循环或直接调用建图写 RPC 推进。

| 检查 | 实际证据 |
| --- | --- |
| 标定 | 独立 calibration.get 读回 revision 68c581e7c5e70c1b1909e66ee3e68560b2d5c51eee276f684ed22fe9be80f58c；来源 simulation |
| SLAM | 23.492米、230帧、220配准、2回环、72,382点；完整注册路线10个点执行结束 |
| 地图启用 | scan-e23523f77362；revision 613f0538530e2dea683fcc17b50dbff4d809dd97e931f419bac6bf64e5b50561；状态 completed 与独立 activeMap 身份一致 |
| 语义解析 | 厨房别名 → kitchen；commissioned_workspace 标注 + measured_navigation_grid；navigationReady=true，绑定同一地图/标定 revision |
| 导航 | Nav2 Goal succeeded；verify_arrival=CONFIRMED，动作后观测 gazebo-home_furnished/head-rgbd-403900000000-1790500981239 |
| 留存 | [机器可读验收摘要](2026-09-27-capability-goal-acceptance.json)含计划目录哈希、模型路由、完成证据、原始文件 SHA256 与失败样本摘要 |

客厅、走廊、厨房、卧室、浴室五个语义目标均通过地图可通行检查。本次保存栅格38,626格，其中25,988自由、1,625占据、11,013未知，未知比例28.512%；分母为地图矩形范围，**不是全屋覆盖率**。未观测区域不填自由，不将地图存在等同于全屋已认证。

原始目录为 artifacts/acceptance/capability-goal-20260927-run6，地图制品在 artifacts/sim-stack/gazebo-xlerobot-home/gazebo/maps/home_furnished/workflow/scan-e23523f77362。成功结束后停止本次自有仿真栈进行最终回归，保留数据库、地图、标定、原始事件和日志。

复跑需配置实际模型，使用新的输出目录，批准后会运动：

~~~bash
make build
bash scripts/sim-stack.sh start --engine gazebo --scene home_furnished \
  --artifacts-dir artifacts/sim-stack/gazebo-xlerobot-home --sim-port 50161 --agent-port 8897
.venv/bin/python scripts/evaluate_capability_goal.py \
  --request '请先运行机器人标定，然后调用自动建图工具按已注册的巡检路线重新建立并启用家庭地图，最后前往厨房并确认到位。' \
  --output artifacts/acceptance/capability-goal-rerun --timeout 1800
~~~

## 验证记录

本机执行 `make build && make lint && make generate-check && make test` 全部通过。Go 全量测试通过；Python 主测试集2431通过、40跳过，隔离 Runtime 边界测试另2通过；前端486通过。`make book-check` 验证24节源文与生成稿一致，`pytest -q tests/docs` 19通过。跳过项不等同于已认证。

云边真实 HTTP 集成测试覆盖认证目录→批准步骤→资源 claim→Worker SQLite 回执→Runtime 租约字段→独立证据→云端完成，并检查未批准不能执行、已完成不重放、owned cancel 取得停止证据后进入 CANCELLED。该测试的 Runtime 为 fixture。全量回归同时验证原 Fleet/RGB-D 闭环；恢复验收脚本使用显式隔离的阶段模型配置，避免继承开发者本机配置。

本轮修复 Coordinator 接到取消停止证据后未推进最终取消状态，以及宿主机 Worker 默认 journal 目录/SQLite 相对 URI 问题。每个写操作即使有异步终态也必须具备独立读回契约；只报告 completed 不获得执行资格。

提交 ba27d430e 的 [CI](https://github.com/SUSTechWLA/tangying-robot-agent-os/actions/runs/36307841291) 全部通过。后续身份修复和最终证据以 [PR15](https://github.com/SUSTechWLA/tangying-robot-agent-os/pull/15) 最终 head 的必需 release-gate 为合入门禁，旧 head 全绿不作为新提交通过依据。

## 未认证范围

实机、Orin NX 量化模型性能、GPU 超大模型服务、千台机群压测、真实硬件失联停稳、任意跨机器人物理目标分解仍待认证。HTTP 集成使用契约 fixture，不能写成云端服务器+真实机器人验收。仿真地图语义功能来自配置标注，几何分区不猜房间用途。SLAM 地图包与 RTAB-Map/Nav2 定位数据库是不同制品，不以一个存在证明另一个已完成。
