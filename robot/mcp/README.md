# 躺营 MCP 接入

此包让支持 Model Context Protocol 的外部 Agent 通过相同工具访问不同机器人的 Fleet 控制层。协议运行在 **stdio**，使用官方 Python SDK 维护线 `mcp>=1.28,<2`；不依赖机器人厂商 SDK，也不向外部 Agent 暴露关节、电机或审批接口。

## 安装和启动

在已完成 `make setup` 的仓库根目录执行：

```bash
.venv/bin/python -m pip install -e '.[mcp]'
export TANGYING_MCP_FLEET_URL=http://127.0.0.1:18080
read -rs TANGYING_MCP_TOKEN
export TANGYING_MCP_TOKEN
.venv/bin/tangying-mcp
```

`read` 后粘贴通过已有 Fleet 登录流程获得的操作员 Bearer token 并回车。输入不回显，不要将真实 token 放入版本库或命令行参数。服务启动后等待 MCP 客户端的 JSON-RPC 输入；直接运行不会展示聊天界面。等效入口为 `.venv/bin/python -m tangying_mcp`。

MCP 客户端配置中的 `command` 指向虚拟环境内的绝对路径 `.venv/bin/tangying-mcp`；通过客户端的环境配置或凭据管理传入上述变量。如果客户端不会继承终端环境，需要在客户端侧配置；不要把真实 token 写入本项目的示例文件。stdio 客户端拥有该桥接进程的操作员权限，应只向可信的本地客户端提供。

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `TANGYING_MCP_FLEET_URL` | `http://127.0.0.1:18080` | Fleet 根地址。远程必须 HTTPS；HTTP 仅允许 loopback。禁止 URL 内凭据、路径、查询和 fragment。 |
| `TANGYING_MCP_TOKEN` | 无，必填 | Fleet 操作员 Bearer token，仅通过环境传递。没有匿名或自动登录回退。 |
| `TANGYING_MCP_CA_FILE` | 无，使用 certifi 默认信任根 | 可选的可信本地 PEM CA 文件路径，用于私有 CA 的 Fleet HTTPS。文件不可读、无效或不含 CA 证书会在启动时拒绝。 |
| `TANGYING_MCP_TIMEOUT_SECONDS` | `10` | 单次 HTTP 请求的总体超时，范围 0.05～60 秒。 |
| `TANGYING_MCP_RATE_LIMIT` | `120` | 每个进程每 60 秒的普通工具请求上限，范围 1～600。急停不受此预算阻挡。 |

HTTP 客户端校验 TLS 证书和主机名、禁用环境代理和重定向；默认不重试请求。单次返回 JSON 限制为 2 MiB，过大快照会明确报错，当前没有自动截断或分页。私有 CA 通过 `export TANGYING_MCP_CA_FILE=/etc/tangying/fleet/ca.pem` 显式配置；它只从可信本地文件加载信任根，不下载证书、不关闭 TLS 校验。错误 CA 或主机名不匹配仍然拒绝请求。未设置时使用 certifi 默认信任根；`SSL_CERT_FILE`、`SSL_CERT_DIR` 和操作系统另行安装的 CA 不会自动成为本桥接的信任配置。

## 统一工具

| MCP 工具 | 参数 | Fleet API | 语义 |
| --- | --- | --- | --- |
| `list_robots` | 无 | `GET /v1/devices` | 机器人注册记录和在线状态。 |
| `get_robot_capabilities` | `robot_id` | `GET /v1/devices/{id}` | 返回 capabilities、toolCatalog 及其 revision、observationSources、adapter 等。 |
| `observe_world` | 无 | `GET /v1/world` | 已融合的规范世界快照，保留坐标系、版本、有效性和来源信息；不会额外触发传感器采样。 |
| `list_tasks` | 无 | `GET /v1/tasks` | 查看已有任务，供提交结果不明时核对。 |
| `create_task` | `request`, `adapter` | `POST /v1/tasks` | 仅提交待审批任务，adapter 应从机器人能力查询结果取得；不假定机械结构。 |
| `get_task` | `task_id` | `GET /v1/tasks/{id}` | 获取任务状态和执行证据。 |
| `cancel_task` | `task_id` | `POST /v1/tasks/{id}/cancel` | 请求取消任务；取消成功不等于硬件已经停止。 |
| `emergency_stop` | `robot_id`, `reason` | `POST /v1/devices/{id}/estop` | 向现有边缘通道发送急停。`status=pushed` 只表示已推送，物理停止须结合设备反馈核验。 |

机器人 ID、任务 ID、adapter 最长 128 个 ASCII 字符，首字符为字母或数字，其余允许字母、数字、`_ . : -`；禁止路径字符。输入严格校验类型且拒绝额外参数，因此 `approved`、`auto_approve`、任意 URL 和原始动作参数不能被夹带执行。任务文本最长 4000 字符；急停原因最长 500 字符。

工具支持统一 JSON Schema 输入和输出，返回同时包含 MCP `structuredContent` 与等值 JSON 文本：

```json
{
  "schema_version": "tangying.mcp/v1",
  "operation": "create_task",
  "ok": true,
  "data": {"id": "task-example", "state": "READY", "approved": false},
  "error": null,
  "approval_required": true
}
```

`data` 保留 Fleet 资源的原生字段。世界坐标、几何重建和观测有效性由机器人适配器及 Fleet 的规范感知入口负责验证，MCP 不会将缺失深度的二维识别伪装成三维数据。Agent 应先查询可用能力，再检查快照新鲜度和有效性，最后创建任务；新型号的传感器适配和机械执行由统一机器人协议后的驱动完成。

创建成功必须同时满足 `approved=false` 且状态为 `READY` 或 `WAITING_APPROVAL`。操作者随后在控制台检查任务并审批，现有 planner、tool catalog 和执行前安全校验继续生效。MCP 不提供批准、自动批准、重置急停、上电解锁、关节控制或厂商 SDK 任意调用工具。这个桥接不会扩充自然语言解析器已经支持的技能范围，也不能仅凭能力注册记录证明某台实机已完成安全验收。

失败返回 `ok=false`、MCP `isError=true` 和结构化 `error`：

- `INVALID_ARGUMENTS`：按工具 schema 修正参数。
- `UNAUTHENTICATED` / `FORBIDDEN`：检查操作员 token 或权限。
- `NOT_FOUND`：刷新机器人或任务列表。
- `RATE_LIMITED`：等请求预算恢复；急停仍可调用。
- `FLEET_UNAVAILABLE`：只读请求失败或超时，可稍后重试。
- `OUTCOME_UNKNOWN`：变更请求可能已经生效；先查询任务或设备状态，不能直接重复提交。
- `UNSAFE_TASK_RESPONSE`：Fleet 没有确认任务处于未审批状态，立即到控制台核查。
- `INVALID_RESPONSE` / `RESPONSE_TOO_LARGE`：检查 Fleet 返回和快照规模。
- `FLEET_HTTP_ERROR`：Fleet 拒绝了请求；具体业务原因到已鉴权控制台和服务日志核查。

桥接不会把上游错误正文、异常堆栈、请求 URL 或 token 写回 MCP。成功正文中的配置 token 和常见凭据字段也会脱敏。业务文本、机器人标签和观测内容仍是非可信数据，不应被客户端当成系统指令。

## 测试

```bash
.venv/bin/python -m pip install -e '.[dev,mcp]'
.venv/bin/python -m pytest -q tests/mcp
```

测试使用官方 `ClientSession` 启动真实 stdio 服务进程，完成 initialize、tools/list、tools/call；独立本地 HTTP fixture 核对 Authorization、草稿边界、路径注入、额外批准参数、重定向、响应大小、非有限数值、错误脱敏、请求限流、超时不重试和急停优先。真实本地 HTTPS fixture 另验证默认不信任私有 CA、显式 CA 后成功、错误 CA／主机名拒绝，以及无效 CA 配置启动失败且不泄漏路径和 token。fixture 没有驱动真实硬件，这些协议测试不构成实机验收证据。

官方 SDK 维护线与接口文档：[Python SDK v1 文档](https://py.sdk.modelcontextprotocol.io/v1/)、[官方 Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x)。当前选择固定 `<2` 防止后续安装无意切换主版本；升级 SDK 大版本应重新运行协议测试。
