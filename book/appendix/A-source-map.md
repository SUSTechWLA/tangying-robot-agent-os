# 附录 A · 按问题阅读源码

## A.1 先追职责，再追调用

本版代码复核为 9e5aebae。包名是导航入口，接口和行为以该快照为准；正文中的目录链接在导出时固定到它。以下用“要解释的问题”组织阅读，不用文件数和测试函数数衡量系统质量。

| 问题 | 先读入口 | 再查对象 | 对应章 |
| --- | --- | --- | --- |
| 实际装配了什么？ | `cmd/local-agent/main.go`、`cmd/local-agent/agentruntime.go`、`cmd/fleet-control-plane/main.go` | 路由、数据库、角色与发布口 | 2、6、9、17 |
| 目标如何成为获批内容？ | `tasks/`、`internal/capabilityagent/` | TaskRevision、active revision、规划 trace | 7、14 |
| 什么工具对模型可见？ | `core/capability/`、`core/skills/`、`internal/agentharness/` | Schema、effects、目录与角色 | 5、16 |
| 如何判定物理完成？ | `core/closedloop/`、`core/harness/` | gate、evaluator、证据基线 | 3、4 |
| 世界事实来自哪里？ | `core/worldmodel/`、`fleet/worldhub/` | 来源、序号、实体与版本 | 4、17 |
| 谁可发布和处理事件？ | `core/agentcontract/`、`agentruntime/` | 注册身份、topic、sink、丢弃计数 | 6、11 |
| 只读恢复凭什么继续？ | `internal/autorecovery/`、`internal/recoveryexec/` | 原失败、异常、计划、执行/复验 | 6、11、19 |
| 上下文如何保存与裁剪？ | `core/agentcontext/`、`internal/actionloop/`、`tasks/` | guard、冷制品、分页、请求 SHA | 18 |
| 云边执行依据是什么？ | `core/contextcontract/basis.go`、`fleet/coordinator/` | claim、批准摘要、resource/fence | 8、17 |
| 重启为何不能重做动作？ | `edge/worker/context.go`、`fleet/coordinator/completion.go`、`fleet/coordinator/finalization.go` | checkpoint、receipt、pending、outbox | 8、17 |
| 如何接入新本体？ | `robot/gateway/tangying_robot_gateway/plugin_backend.py`、`robot/gateway/tangying_robot_gateway/` | Profile、Backend、观测、停止、journal | 5、10 |

### 新空间与边缘模块的查阅入口

| 问题 | 源码与测试 | 阅读重点 |
| --- | --- | --- |
| SLAM 算法与工作流怎样替换 | robot/gateway/tangying_robot_gateway/slam_provider.py、dense_slam_provider.py | 平面会话接口、独立快照、真实来源 |
| 三维测量怎样构造体素地图 | spatial_mapping.py、ROS spatial_mapping_node.py、test_spatial_mapping.py | 实际光心、三态证据、原子包与重放 |
| 地形/空中路径怎样拒绝障碍 | spatial_navigation.py、test_spatial_navigation.py | 有界搜索、连续线段、贴地和 unknown |
| 定位如何绑定路径与到达 | spatial_localization.py、ros2_profile.py、test_spatial_localization.py | 部署摘要、同次采集、planId 与完整位姿 |
| 设备数据怎样跨进程交换 | edge/exchange/、cmd/edge-exchange/ | peer/domain、inbox/outbox、序号/TTL/ACK |
| Worker 如何避免通知丢失 | edge/tasksource/、edge/worker/ | SQLite 提交先于队列确认，重启与 claim |

Python 空间模块位于 robot/gateway/tangying_robot_gateway/，测试位于 robot/gateway/tests/。具体配置和部署按 [空间导航](../../docs/development/spatial-navigation.md)、[采集节点](../../docs/development/spatial-mapping.md)与[数据交换](../../docs/development/edge-data-exchange.md)核对。

## A.2 一条任务的源码阅读记录

建议同时打开组合根、接口、实现和相关测试。按以下顺序记笔记：输入从哪里来；哪些字段由受信层生成；哪个存储权威保存版本；动作前校验什么；动作后读什么证据；失败在哪层分类；回放能恢复哪些事实。

```text
目标/批准 → 执行定义 → command/Basis → journal/checkpoint
             ↓                         ↓
         模型 trace                 世界/物理验证
             └──────── TaskEvent/回放 ───┘
```

选一个 taskId，列出 revision、stepId、commandId、robotId，再找到制品和事件。没有某个关联时写“缺失/未覆盖”，不要用时间相近猜测同一操作。

## A.3 修改入口与评审对象

| 修改 | 最少同时评审 |
| --- | --- |
| 新能力 | manifest、参数/效果/资源、Planner、执行与独立验证、unknown/停止路径 |
| 新 Agent | 订阅、发布权限、状态与工具范围、组合根、持久事实映射 |
| 新设备 | Profile/Backend、单位/坐标、反馈/停止、目录与现场接入包 |
| 新通讯/恢复行为 | 身份、冲突、落盘顺序、重复、超时、旧版本、恢复矩阵 |
| 新上下文视图 | 权威来源、guard、作用域、完整冷归档、预算与实际请求 |

项目源码会继续演进。若在更新的 main 开发，先比较上述契约和迁移记录，再运行匹配版本的测试。旧版规模统计和长源码保存在[出版说明](../front/01-publication-notes.md)所列档案中，不能称为当前统计。
