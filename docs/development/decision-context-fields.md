# 决策上下文生产者字段字典

此契约描述完整生产者接口设计。当前运行时强制 envelope 见 `decision-context.schema.json`；旧数据适配器尚未填齐下面所有字段。`decision-payload.schema.json` 的各 `$defs` 可作为新生产者的单独验收 schema。所有列出字段须出现，未知为 null；空数组只表示已知为空。模型不得补造批准、物理事实、时钟映射或历史身份。

共享记录元数据：`id/kind/scope/step_id/attempt_id/source/source_version/observed_ms/valid_until_ms/clock_domain/payload/evidence_ids/supersedes`。时间窗采用 observed_ms ≤ as_of_ms < valid_until_ms；单调时钟只在同一 boot ID 中比较；所有作用域与源认证均需独立校验。

## goal

用户目标解析器；原始用户要求是权威，模型提取须保留来源。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| request | string/null | 生产者声明的 request；无法建立时显式 null，不推断默认值。 |
| target_id | string/null | 生产者声明的 target_id；无法建立时显式 null，不推断默认值。 |
| destination_id | string/null | 生产者声明的 destination_id；无法建立时显式 null，不推断默认值。 |
| forbidden_ids | array/null | 生产者声明的 forbidden_ids；无法建立时显式 null，不推断默认值。 |
| clarification_required | boolean/null | 生产者声明的 clarification_required；无法建立时显式 null，不推断默认值。 |
| current_step_id | string/null | 生产者声明的 current_step_id；无法建立时显式 null，不推断默认值。 |
| object_id | string/null | 生产者声明的 object_id；无法建立时显式 null，不推断默认值。 |
| questions | array/null | 生产者声明的 questions；无法建立时显式 null，不推断默认值。 |

## steps[]

规划器；依赖图与动作契约用于可执行性检查。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| id | string/null | 生产者声明的 id；无法建立时显式 null，不推断默认值。 |
| action | string/null | 生产者声明的 action；无法建立时显式 null，不推断默认值。 |
| arguments | object/null | 生产者声明的 arguments；无法建立时显式 null，不推断默认值。 |
| state | string/null | 生产者声明的 state；无法建立时显式 null，不推断默认值。 |
| depends_on | array/null | 生产者声明的 depends_on；无法建立时显式 null，不推断默认值。 |
| expected | string/null | 生产者声明的 expected；无法建立时显式 null，不推断默认值。 |
| deadline_ms | integer/null | 生产者声明的 deadline_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| contract_ref | string/null | 生产者声明的 contract_ref；无法建立时显式 null，不推断默认值。 |
| invariants | array/null | 生产者声明的 invariants；无法建立时显式 null，不推断默认值。 |
| cancellation_policy | object/null | 生产者声明的 cancellation_policy；无法建立时显式 null，不推断默认值。 |

## records[].payload(tool_return)

工具执行器/执行账本；只声明派发及执行终态，不证明物理效果。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| command_id | string/null | 生产者声明的 command_id；无法建立时显式 null，不推断默认值。 |
| idempotency_key | string/null | 生产者声明的 idempotency_key；无法建立时显式 null，不推断默认值。 |
| execution_state | string/null | NOT_STARTED/STARTED/SUCCEEDED/FAILED/UNKNOWN；UNKNOWN 禁止未经对账重发。 |
| started_ms | integer/null | 生产者声明的 started_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| terminal_ms | integer/null | 生产者声明的 terminal_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| error_code | string/null | 生产者声明的 error_code；无法建立时显式 null，不推断默认值。 |
| acknowledgement_only | boolean/null | 生产者声明的 acknowledgement_only；无法建立时显式 null，不推断默认值。 |

## records[].payload(verification)

独立验证器；只转述有证据引用的验证结果。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| report_id | string/null | 生产者声明的 report_id；无法建立时显式 null，不推断默认值。 |
| verdict | string/null | VERIFIED/FALSIFIED/UNKNOWN；不从工具 SUCCEEDED 自动转换。 |
| failure_type | string/null | 生产者声明的 failure_type；无法建立时显式 null，不推断默认值。 |
| confidence | number/null | 声明者的置信度 [0,1]；不等于已校准的成功概率。 |
| evidence_completeness | number/null | 证据完整度 [0,1]，不等于事实正确率。 |
| verified_facts | array/null | 生产者声明的 verified_facts；无法建立时显式 null，不推断默认值。 |
| falsified_facts | array/null | 生产者声明的 falsified_facts；无法建立时显式 null，不推断默认值。 |
| unknowns | array/null | 生产者声明的 unknowns；无法建立时显式 null，不推断默认值。 |
| verifier_version | string/null | 生产者声明的 verifier_version；无法建立时显式 null，不推断默认值。 |
| contract_ref | string/null | 生产者声明的 contract_ref；无法建立时显式 null，不推断默认值。 |

## records[].payload(observation)

传感器适配器；保留单位、坐标系、校准与时钟域。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| value | JSON value | 原始或派生观测值；含数值、向量或对象，须由 unit/frame_id 和源 schema 解释。 |
| unit | string/null | 生产者声明的 unit；无法建立时显式 null，不推断默认值。 |
| frame_id | string/null | 生产者声明的 frame_id；无法建立时显式 null，不推断默认值。 |
| covariance | array/null | 与 value 同一坐标系和单位约定的协方差矩阵；null 不表示零噪声。 |
| sensor_id | string/null | 生产者声明的 sensor_id；无法建立时显式 null，不推断默认值。 |
| calibration_version | string/null | 生产者声明的 calibration_version；无法建立时显式 null，不推断默认值。 |
| arrival_ms | integer/null | 接收端到达时间，不等于物理采样时间。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| edge_boot_id | string/null | 生产者声明的 edge_boot_id；无法建立时显式 null，不推断默认值。 |
| edge_monotonic_ns | integer/null | 只在同一 edge_boot_id 内可比较，不能直接与 UTC 毫秒相减。；ns; boot-local monotonic |
| clock_uncertainty_ms | number/null | 生产者声明的 clock_uncertainty_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |

## records[].payload(guard)

权限与安全守卫；批准严格绑定具体动作参数与版本。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| approved | boolean/null | 生产者声明的 approved；无法建立时显式 null，不推断默认值。 |
| approval_id | string/null | 生产者声明的 approval_id；无法建立时显式 null，不推断默认值。 |
| approved_by | string/null | 生产者声明的 approved_by；无法建立时显式 null，不推断默认值。 |
| approval_task_id | string/null | 生产者声明的 approval_task_id；无法建立时显式 null，不推断默认值。 |
| approval_robot_id | string/null | 生产者声明的 approval_robot_id；无法建立时显式 null，不推断默认值。 |
| approval_revision | integer/null | 生产者声明的 approval_revision；无法建立时显式 null，不推断默认值。 |
| approval_step_id | string/null | 生产者声明的 approval_step_id；无法建立时显式 null，不推断默认值。 |
| approval_attempt_id | string/null | 生产者声明的 approval_attempt_id；无法建立时显式 null，不推断默认值。 |
| approval_arguments_hash | string/null | 规范化实际执行参数的哈希；缺失时不能证明批准覆盖该动作。 |
| expires_ms | integer/null | 生产者声明的 expires_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| revoked | boolean/null | 生产者声明的 revoked；无法建立时显式 null，不推断默认值。 |
| budget_remaining | integer/null | 生产者声明的 budget_remaining；无法建立时显式 null，不推断默认值。 |
| automatic_retry_forbidden | boolean/null | 生产者声明的 automatic_retry_forbidden；无法建立时显式 null，不推断默认值。 |

## records[].payload(hypothesis)

Ops/Reflection/OptiAgent；假设和提议不具备执行权限。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| claim | string/null | 生产者声明的 claim；无法建立时显式 null，不推断默认值。 |
| supporting_evidence_ids | array/null | 生产者声明的 supporting_evidence_ids；无法建立时显式 null，不推断默认值。 |
| contradicting_evidence_ids | array/null | 生产者声明的 contradicting_evidence_ids；无法建立时显式 null，不推断默认值。 |
| confidence | number/null | 声明者的置信度 [0,1]；不等于已校准的成功概率。 |
| proposed_test | object/null | 生产者声明的 proposed_test；无法建立时显式 null，不推断默认值。 |
| status | string/null | 生产者声明的 status；无法建立时显式 null，不推断默认值。 |

## attempts[]

执行账本；未知终态必须查询，不得凭语言重复派发。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| id | string/null | 生产者声明的 id；无法建立时显式 null，不推断默认值。 |
| tool | string/null | 生产者声明的 tool；无法建立时显式 null，不推断默认值。 |
| arguments | object/null | 生产者声明的 arguments；无法建立时显式 null，不推断默认值。 |
| arguments_hash | string/null | 生产者声明的 arguments_hash；无法建立时显式 null，不推断默认值。 |
| idempotency_key | string/null | 生产者声明的 idempotency_key；无法建立时显式 null，不推断默认值。 |
| execution_state | string/null | NOT_STARTED/STARTED/SUCCEEDED/FAILED/UNKNOWN；UNKNOWN 禁止未经对账重发。 |
| verdict | string/null | VERIFIED/FALSIFIED/UNKNOWN；不从工具 SUCCEEDED 自动转换。 |
| evidence_ids | array/null | 生产者声明的 evidence_ids；无法建立时显式 null，不推断默认值。 |
| failure_code | string/null | 生产者声明的 failure_code；无法建立时显式 null，不推断默认值。 |
| progress_measure | object/null | 生产者声明的 progress_measure；无法建立时显式 null，不推断默认值。 |
| approval_id | string/null | 生产者声明的 approval_id；无法建立时显式 null，不推断默认值。 |
| compensation_ref | string/null | 生产者声明的 compensation_ref；无法建立时显式 null，不推断默认值。 |

## tools[]

工具注册表；版本和参数 schema 应与实际执行端一致。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| name | string/null | 生产者声明的 name；无法建立时显式 null，不推断默认值。 |
| description | string/null | 生产者声明的 description；无法建立时显式 null，不推断默认值。 |
| argument_schema | object/null | 生产者声明的 argument_schema；无法建立时显式 null，不推断默认值。 |
| mutates_world | boolean/null | 生产者声明的 mutates_world；无法建立时显式 null，不推断默认值。 |
| requires_approval | boolean/null | 执行端仍独立校验批准，不由语言输出授予。 |
| preconditions | array/null | 生产者声明的 preconditions；无法建立时显式 null，不推断默认值。 |
| postconditions | array/null | 生产者声明的 postconditions；无法建立时显式 null，不推断默认值。 |
| timeout_ms | integer/null | 生产者声明的 timeout_ms；无法建立时显式 null，不推断默认值。；ms; UTC where a timestamp, duration where timeout; clock domain must be explicit |
| idempotency | object/null | 生产者声明的 idempotency；无法建立时显式 null，不推断默认值。 |
| read_only | boolean/null | 生产者声明的 read_only；无法建立时显式 null，不推断默认值。 |
| tool_version | string/null | 生产者声明的 tool_version；无法建立时显式 null，不推断默认值。 |

## handoff

编排器与持久化账本；恢复前必须处理未决命令。

| 字段 | 类型（均可未知） | 语义 / 单位 |
| --- | --- | --- |
| ledger_sequence | integer/null | 生产者声明的 ledger_sequence；无法建立时显式 null，不推断默认值。 |
| history_complete | boolean/null | 相对于声明的账本范围；false 必须保留缺口。 |
| omitted_events | integer/null | 生产者声明的 omitted_events；无法建立时显式 null，不推断默认值。 |
| unresolved_attempt_ids | array/null | 生产者声明的 unresolved_attempt_ids；无法建立时显式 null，不推断默认值。 |
| cancellation_state | string/null | 生产者声明的 cancellation_state；无法建立时显式 null，不推断默认值。 |
| resource_leases | array/null | 生产者声明的 resource_leases；无法建立时显式 null，不推断默认值。 |
| resume_preconditions | array/null | 生产者声明的 resume_preconditions；无法建立时显式 null，不推断默认值。 |
