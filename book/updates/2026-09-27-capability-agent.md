# 统一能力目标架构增量（2026-09-27）

本文为书籍1.0.0冻结快照之后的增量说明，不改写正文的历史实验数字，也不自动纳入1.0.0出版包。

## 从固定 Intent 到注册工具

主入口先按 Runtime 注册目录建立能力计划：完整离线语法或独立 GOAL 模型给出业务参数，严格 Schema 校验后生成同一 Task/Revision 草案。审批冻结机器人、目录哈希、步骤和已解析的复合 Intent。标定/SLAM 不需要前端分支编排；新增工具由 Provider 定义输入、效果、资源与完成证据。

共享 Executor 用原 StepRun 和 TaskEvent 留存开始、回执、稳定操作 ID、deadline、进度与验证。地图写入按制品/生效 revision 核验，导航抓放仍由旧闭环执行器按动作后观测确认。写操作无持久回执保持未知，不根据“离线一段时间”重新运动。

## 云端规划与边缘执行

Server 读取设备认证的目录，边缘 Worker 领取已批准步骤、持有资源 lease 与 fencing token，再运行同一 Executor。连续续租保持原命令和新鲜度基准，Runtime 看门狗监督长操作失联。云端核对拥有者与绑定证据后提交完成；不能把设备工具返回的成功等同于全局物理谓词认证。

边缘可用量化模型、也可按阶段使用云端只读咨询；GOAL/INTENT/PLANNING/RECOVERY/SYSTEM 分别配置。Local 可逐阶段热应用。模型只提议，任务授权、资源、安全和完成判断在确定性后端。

## 本次经验

自动建图与操作员手动扫描应提供不同工具语义：mapping.build 自行采集、保存、激活，不再混用 mapping.finish。多段 SLAM 保持稳定 operationId，分段完成不是全操作完成。传感器过期不是道路障碍，不能由此黑名单地点或报告覆盖完成。

当前通用目标按单机器人绑定，只支持 robot 独占资源；任意多机器人目标分解、共享空间 Broker、Orin/GPU性能和机群规模尚待扩展认证。模型的表达能力不能扩大本体工具、安全和环境知识边界。

操作与模型配置见[统一目标指南](../../docs/guides/unified-capability-goals.md)，原规范见[ADR](../../docs/superpowers/specs/2026-09-27-unified-capability-agent-architecture-adr.md)，后续实施见[规格](../../docs/development/2026-09-27-capability-goal-implementation-spec.md)，通过与失败证据见[报告](../../docs/experiments/2026-09-27-capability-goal-closure.md)。
