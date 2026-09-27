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

每次原始报告、目录、计划和事件在 artifacts/acceptance/capability-goal-20260927-runN。早期 run1–4 名为 approved-plan 的文件实际是审批前草案，真实审批事件在 task.json；新脚本分别保存 draft-plan 和 approved-plan，后者为实际批准响应。run2 的旧脚本在失败后发起取消；新版保持可恢复失败以便回溯。

run3 的地图 scan-b6438a4cc056，revision 9c52bbbad6ab3936e2690456d41811e0c8fc7810e5ddb886fa95406380babff9，42帧/33次配准/1次闭环，stopReason=no_reachable_frontier、unknownFraction=0.626144。这只证明该部分地图完成保存和启用，不证明全屋覆盖。任务没有在地图失败时继续运动。

重启还暴露 Docker/ROS 就绪晚于 Agent 的25秒身份探测；sim-stack 现先等待自己启动的 Runtime 就绪再启动 Agent，仍使用有界等待和精确进程回滚。

## 验证记录

验证完成后追加最终命令与实际结果，不把待运行门禁填为通过。

## 未认证范围

实机、Orin NX 量化模型性能、GPU 超大模型服务、千台机群压测、真实硬件失联停稳、任意跨机器人物理目标分解仍待认证。HTTP 集成使用契约 fixture，不能写成云端服务器+真实机器人验收。仿真地图语义功能来自配置标注，几何分区不猜房间用途。SLAM 地图包与 RTAB-Map/Nav2 定位数据库是不同制品，不以一个存在证明另一个已完成。
