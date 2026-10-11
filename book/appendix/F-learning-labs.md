# 附录 F · 分级学习路线与可检查实验

本附录让读者从“看懂图”走到“能解释测试和证据”。命令从仓库根目录运行，章节对应数字版1.2.0，代码核对11db0adb。出版构建的测试与机器人运行测试是不同依赖集合；只阅读不需要模型key或机器人。

## F.1 选择路线与验收产物

| 路线 | 顺序 | 应能提交的学习成果 |
| --- | --- | --- |
| 第一小时：理解全貌 | 前言→2→3→17→19 | 一张职责图、七阶段计划及三种成功的区别 |
| 后端/Agent工程师 | 5→6→7→8→9→18→实验1–4 | 事件因果链、上下文guard、checkpoint中断表 |
| 机器人集成工程师 | 1→3→4→5→10→13→实验5–6 | Profile/观测/停止/验证器的型号接入包 |
| 全系统开发者 | 1–19→14施工顺序→实验1–8 | 新能力的正例、拒绝/未知路径与完整回溯 |
| 研究/评测 | 3→11→12→18→附录C/D | 明确统计单位、判据与不夸大的覆盖报告 |

建议按完成产物推进，而非按读完页数推进。后面的历史源码长片段提供深入对照；当前行为先读每章“当前实现”及17–19章。章节中的自测答案是判据提示，不是对真实硬件的操作授权。

## F.2 环境分级

| 级别 | 前置条件 | 本附录动作 |
| --- | --- | --- |
| 阅读/出版 | Python3.11+，出版依赖可单独安装 | 构建/验证电子书，不联系机器人 |
| 软件测试 | Go1.26、Python3.11、Node22及项目依赖 | fixture/协议/历史回放测试，不证明物理成功 |
| 单机仿真 | Docker、Gazebo镜像、规定场景/标定/地图 | 主动仿真运动，保存独立episode与证据 |
| 多机仿真 | Fleet/MySQL/Redis、双Edge/Runtime、共享世界 | 按规定交接流程验证资源和证据 |
| 受监护实机 | 型号驱动/停止/标定、负责人批准与现场条件 | 本书不自动启用硬件或提供通用实机动作命令 |

从当前书稿检出开始学习，确认book/edition.json为1.2.0；其source_snapshot冻结的是代码复核身份。不要通过checkout代码快照后发现旧书，就认为新版没有生成；书籍编辑与代码快照是两个对象。

## F.3 实验1：从回执找到完成判据

**目标**：看懂成功与未知的差别。阅读第3章及core/closedloop，再在准备好项目依赖后运行：

```bash
go test ./core/closedloop ./core/harness
```

**检查产物**：选择一条“证据早于命令”或“来源过期”测试，写出输入、拒绝理由、接下来允许的动作。测试通过说明这些软件反例被识别，不表示真实相机时钟已经满足误差假设。

**反例与答案**：把观测接收时间改成现在不能证明新采集；必须保留采集时刻和来源/序号基线，验证器仍要判定目标后置条件。

## F.4 实验2：追一条多Agent因果链

```bash
go test ./core/agentcontract ./agentruntime ./internal/autorecovery ./internal/recoveryexec
```

**目标**：阅读注册发布口与恢复binding，画source→anomaly→plan→executor/verifier。找出观察者伪造执行、同ID不同参数和错robot/revision的反例。

**检查产物**：一张包含task/revision/robot/step/command及三个原因ID的表，说明哪层拒绝以及事实是否落盘。不得只抄topic名称。

**答案**：实时EventBus接收不等于sink持久化；自动只读续跑必须有完整匹配持久链。fixture verifier结果只验证接线，不是物理恢复证据。

## F.5 实验3：让长上下文触及边界

```bash
go test ./tasks ./core/agentcontext ./internal/actionloop
```

**目标**：找到含长历史、unknown/冲突和UTF-8分页的回归。观察热guard、source_record_ids、冷Artifacts和ModelRequestSHA。

**检查产物**：说明一个已完成历史记录为何可归档，一个未知命令为何不可删；列出同一中文/emoji页的offset/next_offset和完整标记。不要把归档率当正确率。

**答案**：必要状态超过预算应拒绝，作用域切换应结束旧循环；不能靠提高预算、截早期约束或将旧成功归到新命令“修复”。

## F.6 实验4：模拟完成响应丢失

```bash
go test ./core/contextcontract ./edge/worker ./fleet/coordinator
```

**目标**：找到completion丢响应、revision切换、SQLite关闭/重开与finalization崩溃窗口测试。

**检查产物**：列出ACCEPTED、EXECUTED、COMPLETED三阶段重启行为，并解释为什么只有EXECUTED能自动重发completion，不能重发物理图。

**答案**：成功回执确认既有结果；它不授予再次执行，也不关闭尚无确定结果的ACCEPTED。旧receipt不能推进新revision的状态。

## F.7 实验5：检查两个不同本体的协议

```bash
.venv/bin/pytest -q tests/contract/test_heterogeneous_runtime_boundary.py
```

**目标**：比较单臂与移动传感器Profile的关节/传感器/工具子集，观察真实Python gRPC和Go客户端的契约验证。

**检查产物**：分别列出支持、不支持与当前不可用工具，解释源码中测量和动作handler为何是fixture。不得把两个Profile跑通说成两台真实机器人已协同抓放。

**答案**：契约统一不提供厂商驱动、IK、载荷控制或跨adapter调度；Task/capability plan的当前绑定还需单独核对。

## F.8 实验6：观察本地仿真入口

首次准备按第13章及当前安装指南；已有服务时先确认任务与数据，不重复启动。

```bash
make home-furnished
curl --fail --silent http://127.0.0.1:8897/healthz
curl --fail --silent http://127.0.0.1:8897/v1/runtime
curl --fail --silent http://127.0.0.1:8897/v1/readiness
```

**目标**：在工作台辨认设备目录、标定、地图、相机与任务回放。先做只读检查；提交需要运动的仿真目标时检查草案、实际批准与独立证据目录。

**检查产物**：记录软件/场景/地图/标定、Runtime ready与Console readiness的区别。两者不同不是自动意味着服务坏了。

**答案**：历史未确认动作、当前新鲜度和审批可阻塞新工作。不能删历史、改阈值、禁用碰撞或重置世界后声称同episode续跑。

## F.9 实验7：双机交接的证据与资源

按[双机操作流程](../../docs/operations/robocasa-handoff.md)或[Fleet入口](../../docs/architecture/fleet-cloud.md)准备受限仿真。明确区分独立e2e夹具凭据和Compose生成的私有凭据，别混用端口、数据库与设备ID。

**目标**：跟踪A的claim、放置、世界新鲜稳定验证、共享owner/fence转换、B被ready outbox唤醒及最后完成。

**检查产物**：一份命令前/后证据基线、实体/容器关系、持物、资源归属及机器人身份对照。保存失败样本，不只保存最后截图。

**答案**：多次发送同一旧帧不是多个独立样本；低位fence被舍入不是合法转移；租约到期不证明A静止。

## F.10 实验8：长目标与严格重验

先阅读第19章，再按[完整Harness](../../docs/experiments/long-horizon-harness.md)操作。它要求Gazebo、有效标定/地图、客厅起点、GOAL模型与三角色接线；验收脚本会自动批准本次仿真运动，运行即该实验的明确授权。

先查看脚本参数，不在本书给出忽略前置条件的“一行成功”命令：

```bash
.venv/bin/python scripts/evaluate_long_horizon.py --help
.venv/bin/python scripts/revalidate_long_horizon.py --help
```

**检查产物**：原句、计划/revision、运行来源、全部终态、地图版本、物理观测、恢复因果链、暂停来源、Context/请求哈希、manifest及失败过程。

**答案**：严格重验必须读实际制品并检查来源/哈希/绑定。没有本地原图时只能验证摘要范围，不能宣称完整像素已重新核验；历史成功不能当新运行结果。

## F.11 综合作业：设计一个新能力

选择“巡检后回充”或“把物料交给另一台设备”，提交：能力/效果/资源/验证契约；批准范围；设备/目录/地图身份；成功与unknown判据；热guard/冷证据；checkpoint与completion；停止、取消、丢响应、旧revision和持物中断测试。

评审标准是能否指出每一个事实和权限的权威来源，以及哪个失败分支使执行停止。当前系统没有通用回充、任意多机匹配或自动无界分段，不得把作业设计改写成已经实现的能力。

[回到前言](../front/00-preface.md) · [完整案例](../chapters/ch19-long-horizon-walkthrough.md)
