# 任务观测证据与历史回看

任务运行时的实时画面和历史证据是两条明确的路径。后台每秒观测只更新实时界面，保存在内存中；Runner 在任务定位与工具执行完成后采集带 `TaskID`、`StepID` 的观测，才写入本地 `agent.db`。关闭页面或重启 Local Agent 后，已保存的历史采集仍可以通过任务页面查看。

## 一次证据包含什么

`tasks.EvidenceRecord` 的 schema 是 `evidence.capture.v1`，包含：

- 任务 ID、任务版本、执行步骤 ID，以及原始 `captureId`（`Reconstruction.ObservationID`）。
- 机器人、相机来源、坐标系、标定版本、源序列、原始采集 Unix 时间和落库时间。
- 标准 `telemetry.v1` snapshot JSON，包括当时的 `scene.reconstruction.v1` 点云、可选逐点 RGB（`pointColors`）、实体、关系及机器人状态。颜色与 XYZ 同序保存，参与 snapshot 哈希；不会用实时图像给历史点云补色。
- 与重建同次采集的 RGB PNG 和深度预览 PNG；标准 JSON 不内嵌 base64 图片。
- JSON、RGB、深度预览各自的 SHA-256、原始字节数，以及原始内容是否已因保留策略过期。

深度 PNG 是固定色阶预览，不是原始逐像素浮点深度。原始米制数据用于当时的重建，保存的标准点云反映该次处理结果。本机制支持查看和审计当时机器人看到、理解了什么；它不是完整 RGB-D 原始数据集录制器，不支持仅凭预览图无损重跑深度感知。相关定义见 [ROS RGB-D 接入](ros2-rgbd.md)。

图像必须是有效 PNG，分辨率不超过 3840×2160，RGB 与深度分辨率一致，两图合计不超过 2 MiB；snapshot JSON 不超过 2 MiB。机器人身份和采集时间必须与重建契约一致。实际部署仍建议首版采用 640×480，降低保存和传输延迟。

## 任务事件怎样连接证据

工具成功回执持久化后，Runner 获取一次 post-tool 观测，强制写入当前任务/步骤/版本，再同步交给存储。如果存储成功，`TOOL_ACTIVITY` 的 `CONFIRMED.evidenceIds` 引用这次观测的原始 `captureId`。工具回执自身携带的观测 ID 单独放在 `receiptObservationId`，避免把一个没有落库的回执 ID 当成可回看的图片。

每条数据库记录另有 URL 安全的 `id`：对 `[taskId, stepId, captureId]` 的标准 JSON 计算 SHA-256。同一次相机采集可以作为不同步骤的证据，每个步骤具有独立记录；任务版本大于 1 时，Runner 的存储步骤 ID 带 `revision-N/` 前缀。

重复保存完全相同的任务/步骤/采集是幂等操作。相同身份的数据或图片发生改变会报 `ErrEvidenceConflict`，不能覆盖历史。若保存失败，Runner 不会生成可回看证据 ID；Local Agent 尝试追加 `OBSERVATION_EVIDENCE_FAILED` 事件并写运行日志，工具回执仍独立保留。

仅修改逐点颜色同样属于冲突；颜色列表在遥测复制时逐行复制，调用方之后修改数组不能改变已接收的证据。旧 snapshot 未保存颜色时继续兼容，不视为彩色证据。

SHA-256 用于发现已保存原始内容损坏，并非硬件签名或对数据库管理员修改的独立认证。读取原始内容前会重新核验哈希；不匹配时接口返回明确错误，不返回损坏图片。

## 保留策略

全局最多保留最新写入的 **512 次采集**，且保留的 snapshot JSON + RGB + 深度预览合计不超过 **256 MiB**。任何一个限制触发时，按写入顺序清除最早记录的原始内容。

过期记录仍保留任务/步骤/源时间、字节数和哈希；`expired` 变为 `true`，`expiredAt` 记录清除时间。旧内容不会被当前画面替代，也不会被重新打上当前时间。对过期记录进行幂等重试不会复活已清除的内容。

256 MiB 是逻辑原始内容预算，不是 SQLite 文件总大小的硬上限。元数据持续保留，数据库索引和 WAL 也占空间；SQLite 会复用释放的页，但文件不一定立即缩小。备份完整 `agent.db` 时应采用一致性备份方式，保留相关任务记录和证据表。

## HTTP 接口

所有查询均以现有任务为作用域，复用 Local Agent Console 的访问边界。不存在的任务或跨任务记录返回 404；接口没有任意文件路径参数。

| 方法和路径 | 返回 |
| --- | --- |
| `GET /v1/tasks/{taskId}/observations?limit=100&before=0` | `{taskId,historical:true,records:[...],nextBefore?}`，仅元数据 |
| `GET /v1/tasks/{taskId}/observations/{id}` | 完整记录和 snapshot；图片单独获取 |
| `GET /v1/tasks/{taskId}/observations/{id}/rgb` | 原始保存的 RGB PNG |
| `GET /v1/tasks/{taskId}/observations/{id}/depth` | 原始保存的深度预览 PNG |

`id` 是记录的 SHA-256 URL 标识，`captureId` 是原始观测 ID，两者不可混淆。列表按 `recordIndex` 倒序；`limit` 范围 1–200，下一页把 `nextBefore` 作为 `before` 传回。`snapshot`、RGB 和深度二进制不进入列表响应。

历史接口不套用实时数据新鲜度规则。采集于很久以前的记录仍可正常返回，其 `historical:true` 和 `observedAtUnixMs` 必须在界面明确显示；不能据此认为机器人当前仍处于该状态。

原始内容过期后，元数据详情仍为 HTTP 200，`snapshot` 省略，图片为 HTTP 410 / `EVIDENCE_EXPIRED`。某次采集从未提供该图片时为 HTTP 404 / `EVIDENCE_IMAGE_UNAVAILABLE`。校验失败为 HTTP 500 / `EVIDENCE_CHECKSUM_FAILED`。历史图片使用 `private, no-store`，防止客户端把已过期内容当成仍可从服务端获取。

## 开发定位

- `tasks/evidence.go`：记录结构、应用接口、大小/来源/图片校验、哈希。
- `middleware/sqlite/evidence.go`：建表、事务写入、不可改写幂等、保留清理和任务范围查询。
- `console/evidence.go`：历史 API 与过期/损坏处理；通过 `console.WithEvidence(store)` 接入。
- `cmd/local-agent/main.go`：只把任务关联采集送入证据存储，后台采集主动清空任务关联。
- `edge/agent/runner.go`：同次 post-tool 观测与 `CONFIRMED` 事件的关联。

运行 `go test -race ./middleware/sqlite ./console ./tasks ./edge/agent ./cmd/local-agent`。回归覆盖重启恢复、历史时间、跨任务读取、编码路径、不可改写幂等、数量/字节预算、过期元数据、哈希损坏，以及工具事件和持久采集 ID 的一致性。
