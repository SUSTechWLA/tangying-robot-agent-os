# 2026-10-15 分层目标委托与边缘重规划功能实验

类型：软件合同与集成测试；基线 `b1223a0838eb3151afc2f2a4e31293849743e0fa`。设计、源码和迁移见[实施记录](../development/2026-10-15-hierarchical-edge-objectives.md)。本轮不运行新的 Gazebo 动力学或实体机器人；脚本决策器、HTTP 模型协议 fixture 与设备合同 fixture 的结果不能作为真实模型成功率或导航精度样本。

## 任务与判据

| 场景 | 预设条件和注入 | 必须满足的判据 | 实际结果 |
| --- | --- | --- | --- |
| 单目标 11 站巡检 | 目标为“巡检所有指定站点，遇到主路封闭时使用已标定绕行路线，完成后核验每个站点的记录”；第 5 站主路明确 REJECTED | 保留前 4 站；生成剩余计划；11 站逐条完成 | 11 次确认写动作、1 次拒绝、2 代计划、12 个步骤尝试、22 次业务读；通过 |
| 完成后宿主重建 | 复用 SQLite 创建新 Host | 仅补传证据，不再次规划或运动 | 写动作仍为 11；通过 |
| 明确授权重复访问 | 单个写调用的 grant 次数为 2，完成条件要求 visits=2 | 允许有意重复，仍受额度和总预算检查 | 确认 2 次；通过 |
| 错误重规划 | 模型再次提出已确认的前 4 站 | 超额度前拒绝，不重放已完成动作 | 写动作保持 4；通过 |
| 同步写响应丢失 | 设备已经动作，返回 transport error | 未知结果；不自动重规划/重放 | 1 次动作、1 代计划；重入拒绝；通过 |
| 写后验证读取丢失 | 写动作成功，核验服务不可用 | 仍为未知结果，不能普通失败后继续 | 1 次动作并进入对账；通过 |
| 替换恢复组件故障 | 未知写结果后 RecoveryPolicy 返回错误 | 组件故障不能遮蔽未知结果或授予重试权限 | 保持对账错误和 1 次动作；通过 |
| 模型越界 | 提议未授权地点 | 规划/执行前拒绝 | 0 次动作；通过 |
| 预算、期限与授权 | 步骤/读预算不足、过期、取消、未批准、目录错配 | 拒绝继续，不能重启重置预算 | 对应负例通过；计划额度耗尽后重建仍停止 |
| 模型 HTTP 协议 | 使用实际 LLMDecider 与本地模拟 OpenAI-compatible 响应 | 授权 Schema 和完成条件进入请求，合法计划进入同一执行器 | 1 次确认动作；通过；未测真实模型能力 |
| 云端粗目标交接 | 真 HTTP operator/device 鉴权、注册表、协调器、机器人租约、边缘 SQLite；云端未安装 GoalPlanner | 云端只保存父目标；边缘产生子调用；正确证据才能完成 | 1 个父节点、2 个子步骤；建图操作启动 1 次；通过 |
| 创建目标重试 | 同 actor/键/合同重复创建及服务重建；并发 8 次 | 返回同 Task，保留批准；改合同同键拒绝 | 不产生第二个目标；通过 |
| 最终结果响应丢失 | 在父验证事件提交前/后分别断开一次返回路径 | 完成结果保持待交付；Worker 重建后只补传 | 两种窗口均最终 SUCCEEDED，物理启动仍为 1；通过 |
| 拥有操作的取消 | 已持久回执/进度后发 CANCEL_REQUESTED | 确认原 operation 停止后才能给云端 CANCELLED | stopped=true、CANCELLED；通过 |
| 接口负例 | 设备创建目标、效果缺失、错目录、共享资源扩权、多 JSON、伪造最终 proof | 鉴权/合同/完成核验拒绝 | 全部拒绝；通过 |
| 本地入口 | Console session + Runtime 服务目录 | 只创建未批准包络，不执行工具；无 session 拒绝 | 通过 |

11 站顺序为 station-01 至 station-11；只有第 5 站第二代计划改用 detour。脚本生成器使用原目标、目标完成条件及持久诊断上下文，原调用及完整子规划 trace 由 SQLite 保存。确定性 Ops/Recovery 判断无副作用事实，Host 检查所有确认历史；不是自由运行多个模型竞争设备。

## 原始证据与复现

- [功能测试原始 JSONL](evidence/2026-10-15-hierarchical-edge/functional-tests.jsonl)：Go test 原始事件、测试名、结果、耗时、11 站目标、执行顺序、检查点和父事件 trace。
- [首次取消测试失败](evidence/2026-10-15-hierarchical-edge/debug-attempt01.json)：保留原输出与设置修正；未覆盖或改写成成功。
- [HTTP 模型 mock 设置失败](evidence/2026-10-15-hierarchical-edge/debug-attempt02.json)：原测试中止，修正函数名和记录 channel 后复验；不计作生产模型能力结果。
- [证据和源码清单](evidence/2026-10-15-hierarchical-edge/manifest.json)：测试/子测试数量、文件 SHA-256、基线及本轮 Go 源码身份。

```bash
go test -count=1 -json ./internal/edgeagent ./core/objective ./edge/worker ./console ./tasks \
  -run 'TestDelegated|TestObjective|TestCloudDelegates|TestLocalObjective'
go test -race ./internal/edgeagent ./edge/worker ./fleet/coordinator ./core/objective ./console ./tasks
make test-go
go test ./tests/docs
.venv/bin/pytest -q tests/docs tests/book
python3 scripts/build_docs_catalog.py --check
```

上述测试在独立临时数据库/HTTP 服务中运行，数据不会写入实际机器人日志。原始数据按软件案例统计，多个负例和重复执行不合并为实体机器人样本数。

最终功能运行：18 个顶层测试、含子测试共 31 项，0 失败。相关 6 个包的 race 检查通过；`make test-go` 全清单通过；Go 文档守卫通过；Python 文档/出版测试 48 项通过；`make lint`、`make book-check`、目录同步与三个程序编译通过。原始功能输出及 24 个本轮 Go 源文件 SHA 在 manifest 中，后续重跑应生成新目录，不能覆盖此轮证据。

## 失败、修正与局限

首次 owned-stop 测试只等待设备“开始”便取消，此时回执可能尚未持久化，实际 RECOVERABLE_FAILURE；原预期 CANCELLED 不成立。修正为等待持久 CAPABILITY_PROGRESS，再测试已知操作的取消。写响应丢失及验证丢失仍单独检查，系统没有把它们改判为已停止。

实施过程中还发现两个需要修复的边界：写后验证错误必须升级为未知结果，不能仅报告普通失败；完整目标已验证但最终上报丢失应进入 pending delivery，由检查点补传，不能将任务失败后重新运动。两者均有独立回归。

全仓 `go test ./...` 会收集 artifacts 中用于回溯的历史源码片段，因缺少当时其他文件而编译失败；本轮保留这些归档，使用项目 Makefile 的生产/开发包清单 `make test-go` 验收运行代码。书籍正文维持 2.1.0 固定身份，本轮以补充章连接当前实现。

本轮未测真实 GOAL 模型质量、Gazebo/实机运动、Orin 延迟、无线网络分区、跨机器人共享资源、异构机群调度和离线继续。完成核验依赖设备提供的可信观测；精确值条件是否覆盖全部业务需求，由目标合同设计和审查负责。ROS 2、SLAM、地形/三维导航的既有验收不得移作本轮新样本。
