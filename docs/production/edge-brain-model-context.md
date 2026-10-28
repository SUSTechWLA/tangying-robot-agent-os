# 边缘模型请求、缓存提示与失败记录

更新：2026-10-28。适用：`local-agent` / `edge-worker` 的统一目标规划，以及 Local Agent 的受限恢复。它们共用 `internal/actionloop.LLMDecider`；模型返回候选，Host / 执行器 / Runtime 保留动作准入与证据责任。

同日后续增量增加阶段输出预算、可选调用计量账本、离线汇总及持久规划经验，详见[成本与经验运行指南](model-cost-and-recipes.md)。规划经验和后端 KV 前缀缓存是两个机制；前者重新校验候选，后者仅向推理服务发送提示。

## 1. 实际发送什么

每轮先从持久任务、当前观察与历史形成 managed projection，再发送两条文本消息：固定角色规则的 system，及内容严格等于 `Projection.Text` 的 user。目标在 user 的结构化上下文内。另带当轮工具 schema、`tool_choice=auto`、`parallel_tool_calls=false` 与输出 token 上限。返回必须恰好选择一个当轮工具；内部含点的名字在模型侧使用可逆别名，未提供的工具被拒绝。固定前缀不是绕过审批的理由。

`ModelRequestJSON` 保留实际 HTTP JSON 正文，`ModelRequestSHA256` 对其取 SHA-256。API Key 在 HTTP header，不写入该正文。源码见 `internal/actionloop/llmdecider.go`、`internal/actionloop/context.go`。这些归档仍可能包含任务、语义观测和参数，应按业务敏感数据管理，不向无关机器人公开。

## 2. 显式启用，默认不发送提示

Local Agent 使用传给 `--config` 的配置文件；Worker 使用环境变量。例如，已配置本地 vLLM 的 GOAL 阶段：

```dotenv
AGENT_GOAL_PROVIDER=openai
AGENT_GOAL_BASE_URL=http://127.0.0.1:8000/v1
AGENT_GOAL_MODEL=your-deployed-model
AGENT_GOAL_CACHE_MODE=vllm
AGENT_GOAL_CACHE_NAMESPACE=site-a
```

| 模式 | 线上字段 | 条件 |
| --- | --- | --- |
| 空或 `off` | 两种提示都省略 | 默认；不等于关闭服务端已有缓存 |
| `openai` | `prompt_cache_key` | 确认直连服务支持该字段 |
| `vllm` | `cache_salt` | 确认直连 vLLM 版本及 APC 配置 |

RECOVERY 单独配置 `AGENT_RECOVERY_CACHE_MODE` / `AGENT_RECOVERY_CACHE_NAMESPACE`。没有全局 CACHE 回退，GOAL 设置不复制到别的阶段或地址。开启需要非空、最多 128 bytes 的 namespace；`off` 时应删除 namespace，避免配置被误以为已启用。INTENT、旧 PLANNING、无任务作用域的 SYSTEM 配置不支持本轮提示，启用会被校验拒绝。现有云端 `/v1/assist` 的请求白名单尚无缓存字段，走该代理必须用 `off`；支持的 usage 响应仍可解析。云端同事若扩展代理，应基于已认证的机器人/租户重新绑定 cache partition，不能只信任客户端提交的 namespace。

提示值是下列规范化 JSON 的 SHA-256：模板版本、部署 namespace、端点、模型名、角色、完整 scope（robot / task / revision）、实际发出的工具 schema 哈希、固定规则哈希。scope 至少具有真实 robot 或 task 身份，不能为了命中伪造 taskId。目录、revision、机器人或模型变化都会更换键；同一 scope 内观测变动不会改变键，但会改变实际请求哈希，也未必仍命中相同 token 前缀。当前工具顺序参与 schema 哈希，不承诺随意换序后仍复用。

namespace 和键均不是秘密或 ACL。相同键不证明内容相同、GPU 命中或授权有效。代理/服务端必须实施身份认证。此实现不传输 KV 张量，不管理服务器逐出，不固定供应商 TTL。

## 3. 调用记录如何解释

`Projection.model_call`（`agent.model-call.v1`）随决策轮返回，并进入原有任务/计划归档路径。进入 HTTP 请求准备阶段后记录；更早的配置、上下文/请求预算错误由上游错误记录，不编造网络调用。

| 字段 | 含义 |
| --- | --- |
| `call_id` / `stage` / `model` / `endpoint_sha256` | 单次模型尝试身份、装配阶段、模型名和端点摘要；可选独立计量账本用 call ID 配对去重 |
| `output_tokens` | 实际发送的响应上限；不是已经使用的 token 数 |
| `prompt_template_version` / `tools_sha256` | 固定规则版本和当轮实际工具描述身份 |
| `cache_mode` / `cache_identity_sha256` | 客户端发送策略与派生身份，不是命中结果 |
| `started_at` / `duration_ms` | UTC 起点与客户端单调计时的请求耗时；启用计量时含 start 落盘，不含投影构建和 result 落盘/锁等待；不是 TTFT 或整个 Decide 耗时 |
| `request_bytes` / `response_bytes` | 实际 JSON 请求大小及客户端读取的响应字节数；错误响应有读取上限，不是网卡总流量 |
| `http_status` | 收到响应时才有；没有不能解释为 HTTP 200 |
| `outcome` | `accepted`、`invalid_decision`、`output_limit`、`http_error`、`transport_error`、`timeout`、`canceled`、`response_error`、`request_error` |
| `response_sha256` | 有界完整成功状态响应正文的身份；未保存该字段不等于空响应哈希 |
| `usage_status` | `valid` / `missing` / `invalid` |
| `usage` | 可选 prompt / completion / total / cached / cache-write tokens，单字段缺失保留未知 |
| `finish_reason` / `response_truncated` | 服务报告的终止原因；截断既包括服务端 `length`，也包括客户端响应读取上限（含错误正文），不都代表输出 token 耗尽；截断候选不直接消费 |
| `usage_log_status` | 启用独立计量时区分开始、成功记录与写入失败；不是收费状态 |

`accepted` 只表示模型选择通过解析，不表示工具已执行。服务端少报字段时不得补零；非整数、负数、超出可精确 JSON 整数范围、cached 大于已知 prompt 等数据标为 invalid。响应中可选 usage 非法不抹掉有效工具选择；但显式启用本地计量后，账本写入失败会拒绝消费候选并保留 HTTP 尝试记录，二者不可混淆。累计命中率仅在相应字段齐全的样本上计算，并同时报告缺失分母；不要只挑低延迟样本。

恢复执行若中途取消或后续观察失败，已发生的调用与决策轮现在保留到 Result / Record；记录回调用独立、有 5 秒期限的清理 context，执行的取消不再直接取消记录。该接口不承诺消息总线必达或磁盘故障下持久化成功；机器人级无 task 的恢复也不会伪挂到某个任务账本。精确任务记录必须核查最终持久事件，而不是仅看到 UI 响应。

## 4. 验证与部署边界

```bash
go test -race ./internal/actionloop ./internal/modelroute ./internal/recoveryexec ./cmd/local-agent -count=1
python3 scripts/experiment_edge_brain_reliability.py --help
```

软件 fixture 验证参数接线、用量缺失/零/错误、超时/取消、真实 SQLite 重开和边缘故障语义，不测 GPU 速度。上线前对自己的模型服务验证请求字段与使用量；对 ROS 2 设备仍须完成控制器、传感器标定、停止响应和通信故障验收。单个模型请求默认 20 秒不等于已满足某台机器人的停止预算；任务 deadline、动作 lease 和本地停止不能依赖模型及时回答。

字段参考（核验于 2026-10-27）：[OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)、[vLLM prefix caching](https://docs.vllm.ai/en/latest/design/prefix_caching/)。产品参数可变，实测必须固定服务版本。
