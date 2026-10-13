# 云边与 SLAM 模块边界升级记录

日期：2026-10-13；基线：`f5869138b81e6f8e71ba94d4925706bd1f3572bb`。本次以当前源码评价扩展性，并补齐会妨碍独立扩展的实现边界。历史 Gazebo 实验与真实硬件验证分别保留，不把接口重构写成新物理实验。

## 任务规划与验收约定

1. 分别审计云边执行、异构机器人、SLAM/地图/导航，核对已有契约、实际调用者和测试。
2. 将边缘实际使用的云边值与存储端口从 Fleet 实现中分离；保留原 API 的兼容别名/包装，避免改变 JSON、命令授权、租约、完成回执和持久化语义。
3. 将建图算法封装在 SLAM Provider 后。工作流只消费有单位、坐标模型、采集身份和点来源的快照；保留已有优化算法、地图格式和导航判据。用不继承 DenseSLAM 的测试提供者验证替换，不把它称为新算法实测。
4. 增加机械化依赖守卫，保证 `core` 与 Runtime 端口不反向依赖部署实现，边缘不导入 Fleet 实现，地图消费者不导入具体 SLAM 算法。
5. 跑受影响包、跨语言异构合同、云边故障恢复和地图/语义回归；记录实际命令、失败修复、通过数及未覆盖项。
6. 更新架构评价、扩展操作入口与目录，提交并推送当前分支。发布/合并另按现有分支流程处理。

## 基线发现

- `edge/worker` 直接使用 `fleet/coordinator.IntentNode`、`fleet/eventlog`、`fleet/telemetry`，Redis 队列实现在 worker 包内。部署虽然分云边，代码依赖边界仍会让修改云端实现牵动边缘。
- `RobotWorkflow` 的 `slam_factory` 实际要求实例公开 `frames`、`registrations`、`loops`、`previews`、`MAX_FRAMES`，不是独立算法合同。导航和语义服务还从 `dense_slam` 导入通用几何函数。
- Profile/Runtime 已支持异构设备接入，但 Fleet 任务仍是单 Adapter 和有序意图；通用能力分工、跨机器人坐标融合与多写主控尚未完成。完整判断与后续验收门槛见[模块化与扩展性](../architecture/modular-extensibility.md)。

## 完成情况

已完成以下代码与文档变更；机器可读结果、42 个变更源码/测试文件的 SHA-256、24 份本地证据文件的大小与摘要见[验证清单](2026-10-13-modular-extensibility.validation.json)。计数按本轮取证时的文件集合计算，多个测试批次有重叠，不相加宣传为独立测试数量。

### 云边契约与异构身份

- 新增 `core/fleetcontract`、`core/eventstore`，下沉共享值和原子存储端口；`orchestration/handoff` 共享执行摘要和上下文验证。Fleet 旧类型保留 alias、旧函数保留 wrapper。
- Redis 取任务实现在 `edge/tasksource`，由 `cmd/edge-worker` 接线。`edge/worker` / `edge/cloudclient` 的传递依赖中不再包含 Fleet 实现或具体数据库/Redis 驱动。
- 修复 Fleet 遥测把外来 Runtime 数据重命名为当前设备的问题。每次采样核对 snapshot、可选 Profile、可选 reconstruction 的 robotId；不匹配/缺失先于遥测和世界事件生成拒绝，合法旧设备不被要求凭空提供 Profile。嵌入式 Local 的显式别名投影保留。
- 架构检查禁止 core/Runtime 端口反向依赖应用实现，并禁止边缘通过中间帮助包重新依赖 Fleet。UUID 的 `database/sql/driver` 值接口不创建数据库连接，允许该标准值接口，仍拒绝 `database/sql` 和具体驱动。

### SLAM Provider 与续建证据

新增纯合同 `slam_provider.py` 和独立适配器 `dense_slam_provider.py`。工作流消费有单位/坐标/来源/时间/身份的快照，提供者构造在驱动装配处完成；深度不足使用明确异常类型。采样阶段只生成有界进度，不组装完整云；地图/实时网格需要时再生成独立快照。参考算法和设备原参数保持。

不继承 DenseSLAM 的合成 Provider 完成了采样、网格、保存、MapCatalog 验证和启用，证明工作流替换边界已接通。它是接口 fixture，不是第二套实测 SLAM 算法。会话身份变化、非法来源、错坐标/单位、时间倒退、伪造行程和异常 factory 均有拒绝回归。

独立审查复现了基线已有的续建缺陷并修复：

| 缺陷 | 修复与独立判据 |
| --- | --- |
| 继承云已在 map 中，却再次应用锚点 | 只转换本次 driver-world 点；非零平移和 90 度旋转测试确认继承点保持 |
| 新轨迹仍在 world，地图点已转到 map | 新云和 xyz 轨迹采用同一锚点，旧轨迹保持 |
| 当前来源索引从 0 开始，却枚举合并后的轨迹作为采集起点 | 起点字典只由当前快照生成；逐 source 校验对应位置 |
| 继承 `source=-1` 被 NumPy 当负索引，借用最后一个当前起点清障 | 明确排除负来源；包括全部为继承点的测试，未知格保持未知 |
| 合并坐标数与本次时间数不一致 | 继承真实时间正确配对；历史缺失/无法可靠配对的值为 `null`；新写入拒绝长度错误 |

修复前 5 项续建失败和另 1 项错误清障失败，分别保存在 `artifacts/experiments/modular-extensibility-20261013/continuation-before.txt` 与 `continuation-negative-source-before.txt`；修复后独立 7 项通过。最初测试 fixture 的零高度 bounds 制作错误另存，未计作产品缺陷。新 Provider 校验引入时发现的 factory 失败占用与身份过早更新问题也已修复并回归。

原地图制品字节与历史报告不改写；旧版本续建地图需要重新核对或重新建图，新软件不会自动纠正其几何/通行证据。`timesUnixMs` 的未知值经 Web 地图读取兼容测试；Console 继续提供原制品和哈希，不伪造采集时间。

## 最终验证

| 验证 | 实际结果 | 日志/说明 |
| --- | --- | --- |
| 受影响 Go race 回归 | 40 个有测试包通过，5 个包无测试文件 | `artifacts/architecture/20261013-extensibility/go-regression.log` |
| Gateway 全包、工具层、Python 架构、语义回放脚本 | **1465 passed / 4 skipped**，37.62 秒 | `python-regression-final.log`；3 项需要本机 RTAB-Map 样本数据库，1 项无 self_recover 故障声明可配对 |
| Python 服务 ↔ Go 客户端异构合同 + 地图依赖 | **21 passed** | `heterogeneous-contract.log`；真实 gRPC，传感器/执行器为 fixture |
| SLAM 独立续建 | **7 passed** | 已包含在整包中，不重复计数 |
| Web 地图读取 | **14 passed** | `artifacts/experiments/modular-extensibility-20261013/trajectory-web-tests.txt` |
| 文档 / 仓库 | **36 passed**；Go 文档检查通过 | `docs-python.log`、`docs-go.log` |
| Ruff / 目录生成 / diff | 通过 | 新文档已分类，原书稿版次不变 |

核心命令（仓库根目录）：

```bash
go test -race ./core/... ./orchestration/... ./edge/... ./fleet/... \
  ./middleware/sqlite ./cmd/edge-worker ./cmd/local-agent ./tests/architecture
.venv/bin/python -m pytest -q robot/gateway/tests tests/tool_layer tests/architecture \
  tests/install/test_semantic_cloud_eval.py
.venv/bin/python -m pytest -q tests/contract/test_heterogeneous_runtime_boundary.py \
  tests/architecture/test_mapping_dependencies.py
node --test web/map_explorer_test.mjs
```

测试准备错误也保留：第一次全量 Python 命令误写不存在的 `test_semantic_cloud_evaluator.py`，退出码 4、未执行测试；修正为实际文件后通过。初版 Go 依赖守卫误拦 UUID 的标准值接口也保留原日志，修正规则后通过，未放开具体存储实现。

### 云端与双边缘进程验收

命令：

```bash
.venv/bin/python scripts/run_fleet_harness.py --scenario normal \
  --output artifacts/architecture/20261013-extensibility/fleet-normal
```

原始输入：“让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区”。任务 `task-10adc5c3ab1eb8c922397c5b` 完成；Fleet、两个 Edge Worker 和双 Runtime 仿真通过实际 HTTP、mTLS Link 与 gRPC 通道交接。两段 intent 由 robot-1 / robot-2 执行，9 项 invariant 全通过：任务成功、世界修订前进、资源单一归属、目标区关系、一次可交接事件、一次送达事件、4 个环境确认的策略工具、策略证据完整、公共投影不泄漏动作块。

本次使用现有自然语言解析与确定性仿真策略，没有新调用 LLM。引擎为双 MuJoCo，共享方块场景；不是 Gazebo SLAM、异构自动分配、长期网络分区或真机实验。这里只运行 normal 场景；故障/重放边界由本轮 Go/Python 回归覆盖，不称为新进程级故障矩阵。

保存 task、intents、world、leases、devices、事件与 experience JSON，共同摘要见验证清单。Harness 的临时进程与证书目录已清理，最终 JSON 证据保留在上述输出目录。修正脚本原先无条件写“浏览器已验收”的字段为 `consoleScreenshot: null`；本轮没有浏览器截图验收。原始证据仅在本机，不假称已经公开上传数据集。

## 兼容、回滚与仍有的缺口

- HTTP/gRPC/JSON、批准、lease/fence、命令幂等与完成回执版本不变，既有地图读取不改写原制品。
- Go 组装迁移：`worker.NewRedisTaskSource` → `tasksource.NewRedisTaskSource`。Python `RobotWorkflow` 调用必须显式传 `slam_factory`，旧 Dense factory 改为 `DenseSlamProvider`。仓库内调用已迁移。
- 新地图包含 provider provenance，摘要改变是新制品的预期行为；旧地图的身份不会因更新代码自动改变。当前轨迹时间允许显式未知值，外部消费者若额外假设全部为整数，应按新语义处理后再接入。
- 回滚代码前停止新派发、对账在飞命令，保留数据库/journal/地图；使用对应版本制品，不能通过降低任务/地图修订或删除 journal 恢复。
- 当前仍非独立云端自治多 Agent 宿主、任意 SLAM 即插即用、通用异构 DAG、跨机器人地图融合、多主 HA 或真实设备长程认证。跨重启 Profile 指纹、第二套实际 SLAM/导航、逐节点能力分配和云边断网矩阵按[架构路线](../architecture/modular-extensibility.md)继续。

提交前核对的基线 `f5869138b` 远程 CI 已全部通过；它不替代本轮新提交的 CI。新提交仍在现有 Draft PR #26，基于 #25 的分支，不把本轮推送写成 main 发布。
