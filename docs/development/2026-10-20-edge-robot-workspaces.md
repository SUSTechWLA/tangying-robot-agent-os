# 边缘机器人工作空间与任务回溯升级

日期：2026-10-20。变更前基线：`558e7887aa8be62b9f66a9eff2569c0e9ae94ff2`。操作入口：[部署、切换与消息作用域](../operations/edge-robot-workspaces.md)。本轮聚焦边缘工作台及现有任务/证据的设备隔离，物理验收范围沿用[前次通信就绪评估](2026-10-18-edge-communication-readiness.md)，不将界面或 HTTP 验证记为新增实机实验。

## 问题与决定

原 Local 工作台围绕一个 Agent 的默认连接显示状态。多台机器人需要分别打开入口，浏览器本地状态与同主机不同端口的会话还可能互相影响。简单增加一个全局“当前机器人”变量，会让并发页面、迟到回包及正在执行的任务共用可变路由，难以确认操作实际落在哪台设备。

本轮采用**每台机器人独立 Agent、每次请求显式工作空间、切换整页导航**。原单机根入口继续可用；添加远端无需在一个任务执行器内热换 Runtime。各设备保有自己独立 SQLite、执行检查点和模型上下文。

## 实现位置

| 模块 | 变化与目的 |
| --- | --- |
| `cmd/local-agent/main.go` | 增加 `--workspaces-file` / `LOCAL_WORKSPACES_FILE`，启动验证注册表和 Runtime 身份，向 Console 注入不可变设备身份 |
| `console/workspaces.go` | 严格读取 `edge.workspaces.v1`，提供目录和身份 API，按 `/robots/<id>/` 路由既有 API/WebSocket，预检身份后才发送目标会话，拒绝错误身份/重定向/任意浏览器目标 |
| `console/guard.go`、`scripts/console_session.py` | 同步采用 host/port 命名空间的 Cookie，保持 CLI 与旧 Cookie 兼容；不同端口的 Agent 不再覆盖彼此会话 |
| `console/maps.go`、`console/workspace_artifacts.go` | 地图清单/制品和标定文件核对设备来源，排除共享目录中的外机或无身份产物 |
| `web/edge_workspace.js`、`web/app.js` | 顶部选择器、连接身份、任务统计、作用域请求与最近任务恢复；整页切换清理上一设备页面生命周期 |
| 地图、服务、导航、任务回溯相关前端模块 | 统一保留工作空间 URL 和本地状态分区，避免旁路请求根设备；安全确认与当前页面绑定 |
| Console 任务/World WebSocket | 增加读取关闭帧的生命周期监听，连接关闭后取消请求 context，释放数据库轮询/订阅 |
| `web/task_trace.js` | 新增 `SUCCESS_WITHOUT_EXECUTION_HISTORY` 提示；成功状态缺少工具步骤时不再宣称执行证据完整 |
| `console/testdata/workspace-preview/main.go` | 可复现的界面验收程序，提供 AGV、无人机、Runtime 离线和 Agent 离线四种工作空间，禁止物理执行 |

完整字段、请求顺序、会话文件部署及异常代码保留在[操作指南](../operations/edge-robot-workspaces.md#4-消息会话与身份怎样走)。云边目标合同、Runtime 命令和跨机器人 `agent.data.v1` 协议不因工作台选中项改变。

## 必须成立的行为

1. 两台 Agent 即使存在相同任务 ID，也只能读写各自任务；任务事件、观察证据与 WebSocket 一起遵守作用域。
2. A 执行中切到 B，A 继续自己的任务；B 不获得 A 的计划、上下文或安全确认。多标签页各自独立选择。
3. 未注册路径、身份不一致、缺少凭据和离线明确失败；既不降级读根设备，也不向错误端点发动作。
4. 入口不向浏览器公开上游 URL 或凭据，预检时不带目标令牌；实际请求再次携带预期 `robotId`，处理预检后换机的时间窗口。
5. 已启动 Agent 的 Runtime 断开后，可读取该 Agent 的历史；Agent 本身离线时不能伪装为“有离线历史副本”。
6. 地图与标定只接受当前机器人来源；原始传感器历史不会因本轮新增 UI 而自动连续录制。

## 验收过程中发现并修复的问题

- **关闭页面后仍然轮询。** 任务与 World WebSocket 原来只写不读；没有新事件触发写错误时，服务端不能及时发现浏览器已经关闭连接，继续占用数据库轮询或事件订阅。现在通过读取连接关闭事件取消 context，结束相应后台工作；切换工作空间本身不终止后台机器人任务。
- **成功状态被误当成证据完整。** 浏览器查看标为 `SUCCEEDED`、但没有工具历史的样例时，原页面仍声称每一步完成都有证据。现在明确提示 `SUCCESS_WITHOUT_EXECUTION_HISTORY`，空执行链也说明缺少可回溯步骤，要求核对原始记录；任务状态本身不被 UI 改写。
- **连接状态与服务页初始化不清晰。** “我的机器人”分开呈现 Runtime 连接和相机可用性；工作空间身份初始化完成后重新进入当前标定/建图页，确保请求使用已确认身份。异步进入服务页增加路由代数校验，过期初始化不能创建重复轮询。
- **开发预览的会话归属不一致。** 预览工具过去在 HTTP 转发时改写 Host，而 WebSocket 保留浏览器 Host；引入端口命名空间 Cookie 后会导致认证不一致。现在两种转发均保留浏览器 Host，支持工作空间静态路径，并由真实 HTTP 预览测试覆盖。

## 验证记录

下表记录本轮实际执行结果。原始压缩日志、源文件 SHA-256 和环境信息保存在[验收证据目录](../experiments/evidence/2026-10-20-edge-workspaces/README.md)。测试代码覆盖项不是已完成实机验证。

| 验证层 | 命令/入口 | 实际结果 |
| --- | --- | --- |
| Go 后端、CLI 与嵌入文档 | `go test -count=1 -json ./console ./cmd/local-agent ./web ./tests/docs`，含同任务 ID、身份预检、换机窗口、会话、WebSocket、真实 SQLite 重启隔离 | 4 包通过；383 个测试/子测试通过，无失败或跳过 |
| Go 并发检查 | `go test -race -count=1 -json ./console ./cmd/local-agent` | 2 包通过；368 个测试/子测试通过，无失败或跳过 |
| 前端行为 | 在 `web/` 执行 `npm test`，含 URL、请求身份、本地状态、预览转发和导航 | 529 通过，无失败或跳过；2.048 秒 |
| Python 会话、文档与仓库 | `.venv/bin/python -m pytest -q tests/install/test_console_session.py tests/docs tests/test_repository.py` | 48 通过；0.19 秒 |
| 构建与资料检查 | `go build -o bin/local-agent ./cmd/local-agent`；`go test -count=1 ./web ./tests/docs`；`python3 scripts/build_docs_catalog.py --check`；`git diff --check` | 全部通过；已构建本机二进制 |
| 浏览器实际操作 | 多工作空间切换、离线、任务与响应式界面 | 4 组流程检查通过；任务/设备页 × 390、980、1440 px 共 6 组无横向溢出；最终预览无 Console error |
| Gazebo / 实机 / 无线网络 | 本轮没有将软件工作空间验证计作这些验收 | 未执行 |

Go 数量包括父测试及子测试，race 与功能用例重叠，不能相加作为独立实验次数；只检查上表包，不代表整个仓库或机器人长程任务均完成 race 验证。SQLite 集成用真实数据库和 HTTP/WebSocket 服务验证隔离、重启、会话轮换及关闭订阅；浏览器使用下述界面样例。

文档检查首次发现两项问题：新身份/目录 API 未登记到接口参考，新增说明使 README 的首个启动命令超出新人入口合同范围。已补齐接口参考并把工作空间说明放到首个启动示例之后；复测为上表结果。分类目录重建同时补齐既有通信审计、Gazebo 和 UI 资料的未登记项，未改写这些历史报告。

浏览器验收使用仓库内[界面验收程序](../../console/testdata/workspace-preview/main.go)，运行与观察步骤见[复现指南](../operations/edge-robot-workspaces.md#7-复现界面验收)。它使用样例数据和明确禁止执行的 executor，不连接物理 Runtime；样例中的 `SUCCEEDED` 用于检验呈现，不是任务实验成功。

留存：[流程与尺寸检查](../frontend/assets/edge-workspaces-20261020/browser-checks.json)、[桌面设备页](../frontend/assets/edge-workspaces-20261020/desktop-devices.png)、[桌面任务回溯](../frontend/assets/edge-workspaces-20261020/desktop-task-history.png)。窄屏验收保留 DOM 尺寸检查结果；浏览器窄屏截图出现缩放失真，未将其归档为视觉证据。已结束临时预览进程，没有重启现有机器人服务。

## 升级与回滚

先升级各目标 Agent，使其提供身份接口并校验请求头，再部署入口注册表。为每台设备保留独立数据目录和当前会话文件；运行中的任务按既有升级流程冻结派发并核对未决命令，不能通过切换工作空间代替停止或对账。入口自身启动仍依赖自己的 Runtime，启动前配置检查不代表现场动作可用。

未配置注册表时保持原单机器人入口。移除某注册项只移除入口的访问路径，不停止对方 Agent，也不删除对方任务。回滚前保留数据库、Runtime journal、产物与身份绑定；旧版本不支持新作用域时分别访问各 Agent 的直接入口，不改写任务或清空状态。

## 当前可支持的结论

新增能力是明确按设备定位的操作与回溯入口，便于先把每个边缘机器人系统运行和诊断好。它复用现有 Harness、任务库与证据模型；不是新的任务协作算法或生产规模认证。Runtime 可达、UI 连接成功、模拟设备测试通过，均不能代替厂商控制器、实际传感器、制动和现场长期运行验收。

后续可在现有稳定设备身份之上增加只读历史副本、独立聚合进程、用户级授权和设备发现；跨机器人的任务协调和语义地图共享继续遵循独立的目标/空间合同，不能依赖浏览器当前选择完成一致性控制。
