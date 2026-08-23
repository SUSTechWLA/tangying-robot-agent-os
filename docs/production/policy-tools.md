# 学习型策略工具与 sim2real 接入手册

本页定义 Tangying Robot AgentOS 如何把 VLA、模仿学习和强化学习模型接入抓取、放置等工具，同时保持分布式任务的一致性、可解释性和物理安全。策略只负责从观测生成候选 `action_chunk`；Robot Runtime 负责硬件约束与幂等执行；Harness Agent 只依据执行后的环境状态确认任务完成。任何模型响应都不能直接把任务标记为成功。

## 1. 为什么策略不是一个普通工具

自然语言任务会先被拆成稳定的任务步骤，再解析为工具链。以“两台机器人传递红色方块”为例：

1. Harness Agent 读取权威 WorldModel，确认方块、交接区和机器人状态。
2. Coordinator 给 robot-1 分配资源 lease 和 fencing token。
3. Edge Worker 执行观察、解析目标、规划、`manipulation.pick`、验证抓取、`manipulation.place`、验证放置。
4. 对抓取和放置，Edge Worker 使用冻结的 PolicyManifest 和同一时刻的 ObservationBundle 请求策略。
5. Edge Worker 校验策略身份、观测身份、动作字段、数值范围和 action chunk 长度，合格后才将动作交给 Runtime。
6. Runtime 再校验 command、revision、deadline、catalog、world basis、resource/fencing、幂等和本机安全限制。
7. Harness Agent 从新观测确认方块已进入交接区，再允许 robot-2 获得资源并执行第二段。

因此模型输出、工具返回和任务成功是三个不同层级。该边界让仿真确定性策略、VLA、模仿学习（IL）和强化学习（RL）可以互换，而任务编排、异常恢复、审计和用户端保持不变。

## 2. 模块与责任

| 模块 | 当前实现 | 唯一责任 |
| --- | --- | --- |
| Go 策略契约 | `edge/policy` | PolicyManifest、观测、推理、动作边界、兼容性与跨语言 revision |
| HTTP Provider | `edge/policy/http.go` | 有界请求、超时、清单发现、响应限长和错误脱敏 |
| 仿真 Provider | `edge/policy/deterministic.go` | 仅在仿真 adapter 中生成可复现抓取/放置动作 |
| Edge Worker | `edge/worker/policy.go` | 在运动前获取本地观测、调用策略、复核动作并附加执行审计证据 |
| Python sidecar | `policy/sidecar/tangying_policy_sidecar` | 将任意 Python VLA/IL/RL callable 包装为稳定 HTTP 协议 |
| Robot Runtime | gRPC `ExecuteSkill` | 最终硬件限位、command journal、幂等和实际执行 |
| Harness Agent | Coordinator + WorldModel | 使用新鲜环境证据验证后置条件，处理 custody 转移 |
| Web Console | `web/app.js` | 面向普通用户解释“控制方式、执行阶段、环境确认和恢复过程” |

不允许让 Python 模型进程直接访问串口、CAN、ROS 控制器或 Fleet 数据库。它只接收受控观测并返回候选动作。

## 3. PolicyManifest

每个模型制品必须随不可变 PolicyManifest 发布。核心字段：

| 字段 | 含义 |
| --- | --- |
| `schemaVersion` | 固定为 `policy.manifest.v1` |
| `policyId` / `version` | 人可识别策略及版本 |
| `framework` | `vla`、`imitation`、`reinforcement` 或 `deterministic` |
| `artifactSha256` | 学习模型必须为 64 位 SHA-256；确定性仿真用 `deterministic:` 前缀 |
| `capabilities` | 允许驱动的工具，例如 `manipulation.pick`、`manipulation.place` |
| `robotModels` / `adapters` | 允许的机械结构和执行 adapter |
| `observationSchema` | 当前固定为 `policy.observation.v1` |
| `requiredObservationSources` | 推理前必须新鲜且无 anomaly 的源 |
| `maxObservationAgeMs` | 观测最大年龄 |
| `actionSchema` | 命名关节或其他动作 schema |
| `maxActionChunkLength` | 单次最多动作帧数 |
| `actionBounds` | 每个动作维度的闭区间 |
| `transformRevision` / `calibrationRevision` | 可选但实机推荐强制绑定 |
| `training` | dataset、algorithm、seed、evaluationPack、promotedBy |

清单 revision 是规范化 JSON 的 SHA-256：集合字段去重并排序、对象键排序，Go 与 Python 必须得到相同结果。Edge 在获取清单后冻结 revision；推理响应若返回不同 revision，按 manifest drift 拒绝，不能热切换正在执行的 command。

示例：

```json
{
  "schemaVersion": "policy.manifest.v1",
  "policyId": "xlerobot-red-block-vla",
  "version": "2026.08.24-1",
  "framework": "vla",
  "artifactSha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
  "capabilities": ["manipulation.pick", "manipulation.place"],
  "robotModels": ["xlerobot-dual-arm"],
  "adapters": ["xlerobot"],
  "observationSchema": "policy.observation.v1",
  "requiredObservationSources": ["scene", "proprioception"],
  "maxObservationAgeMs": 500,
  "actionSchema": "xlerobot.named-joints.v1",
  "maxActionChunkLength": 8,
  "actionBounds": {
    "left_arm_gripper.pos": {"minimum": 0, "maximum": 100}
  },
  "transformRevision": "site-a-map-v3",
  "calibrationRevision": "robot-1-cal-20260824",
  "training": {
    "dataset": "red-block-handoff-v5",
    "algorithm": "act-v2",
    "seed": "7",
    "evaluationPack": "eval-20260824-01",
    "promotedBy": "robot-safety-review"
  }
}
```

## 4. ObservationBundle：Harness Agent 与策略的共同事实基础

策略请求不是一张未标识的图片。ObservationBundle 必须包含：

- `observationId`、`observedAt`、`robotId`、adapter、robot model；
- transform 和 calibration revision；
- `scene`、`proprioception` 等源的新鲜度、置信度和 anomaly；
- 有限数值的关节/底盘/夹爪状态；
- 带稳定 entity ID、类别、pose、关系和置信度的环境实体；
- 可选 FrameReference：URI、MIME、SHA-256、宽高和采集时间。

图像建议放在对象存储、共享内存或本机只读缓存，通过 FrameReference 传递；不要把大图塞进任务事件、数据库或前端状态流。模型需要多相机时，manifest 列出所需源，sidecar 根据 `frames` 获取内容并再次验证 SHA-256。

观测若过期、未来时间、缺源、低置信度、有 anomaly、transform 不符或含 NaN/Inf，Edge 在运动前失败关闭。Harness Agent 使用后续 ObservationEnvelope/WorldSnapshot 确认动作结果，不能复用推理输入充当完成证据。

## 5. Sidecar 接口

### `GET /healthz`

只表示 HTTP 进程存活，返回 `{"status":"ok"}`。生产 readiness 还应在部署层检查模型已加载、GPU/CPU 可用和 manifest 可读取。

### `GET /v1/manifest`

返回完整 PolicyManifest。Edge 会在推理前发现并验证清单兼容性。响应必须设置 `Cache-Control: no-store`；模型升级应发布新实例并走显式晋级，不应在同一实例静默替换清单。

### `POST /v1/infer`

请求为 `policy.inference.request.v1`，包含 request/command/task/revision/step/robot/capability、冻结的 manifest revision、ObservationBundle、world revision、resource 和 fencing token。响应为 `policy.inference.result.v1`，必须逐项回显 request ID、command ID、manifest revision、observation ID，并生成唯一 inference ID。

sidecar 默认最大请求 1 MiB。无 Content-Length、超限、schema 错误、模型异常分别返回稳定公开错误码；响应不能回显请求正文、栈、模型路径、令牌或原始异常。Edge 还会限制响应体并统一映射超时/不可用。

## 6. 包装 VLA、模仿学习或强化学习

在模型服务中创建 manifest，把推理函数适配为 `InferenceRequest -> list[dict[str, float]]`，再使用 CallableProvider：

```python
from tangying_policy_sidecar.contracts import PolicyManifest
from tangying_policy_sidecar.providers import CallableProvider
from tangying_policy_sidecar.server import create_server

manifest = PolicyManifest.model_validate(manifest_document)

def infer_actions(request):
    tensors = preprocess(request.observation)
    raw_actions = loaded_policy.predict(tensors, instruction=request.target_ref)
    return decode_named_joint_actions(raw_actions)

provider = CallableProvider(manifest, infer_actions)
server = create_server(provider, host="127.0.0.1", port=8091)
server.serve_forever()
```

三种训练路线只影响 `infer_actions`：

- VLA：使用任务文本、图像、实体和机器人状态生成动作；必须冻结 tokenizer、归一化参数和相机顺序。
- 模仿学习：ACT/Diffusion Policy/BC 等从示教数据输出 action chunk；manifest 记录 dataset 与算法。
- 强化学习：策略输出同一动作 schema；奖励函数和仿真随机化不进入在线协议，但 evaluationPack 必须记录安全与成功率。

模型的归一化/反归一化放在 sidecar 内，返回值必须已经是 manifest 声明的物理或命名关节范围。禁止让 Edge 猜测模型单位。

## 7. Edge 与 Runtime 配置

| 变量 | 说明 |
| --- | --- |
| `EDGE_POLICY_MODE` | `disabled`、`deterministic` 或 `http` |
| `EDGE_POLICY_ENDPOINT` | `http` 模式的 sidecar 基地址 |
| `EDGE_POLICY_TIMEOUT` | 单次请求超时，默认 10 秒 |
| `EDGE_ROBOT_MODEL` | 与 manifest 的 `robotModels` 精确匹配 |
| `EDGE_ADAPTER` | 与 manifest 的 `adapters` 精确匹配 |
| `EDGE_TRANSFORM_REVISION` | 当前地图/坐标变换版本 |
| `EDGE_CALIBRATION_REVISION` | 当前实机标定版本 |

仿真双机器人链路使用：

```bash
make setup
make policy-handoff
make policy-faults
```

`deterministic` 只允许 MuJoCo/RoboCasa 等仿真 adapter；实机配置会在 Edge 启动时拒绝。实机 Runtime 若在 capability input 中声明需要 `action_chunk`，Edge 未配置策略时同样启动失败，避免静默退回未经验证的控制。

HTTP 策略示例：

```bash
export EDGE_POLICY_MODE=http
export EDGE_POLICY_ENDPOINT=http://127.0.0.1:8091
export EDGE_POLICY_TIMEOUT=800ms
export EDGE_ROBOT_MODEL=xlerobot-dual-arm
export EDGE_CALIBRATION_REVISION=robot-1-cal-20260824
```

## 8. 恢复状态机

| 类别 | 典型原因 | 自动动作 | 是否可直接重试运动 |
| --- | --- | --- | --- |
| `OBSERVATION_WAIT` | 观测过期、缺失、有 anomaly | 重读相机与机器人状态 | 仅在运动前重新推理 |
| `POLICY_RETRY` | sidecar 超时或暂时不可用 | 同一 command 有界重试 | 仅在 Runtime 尚未接收动作时 |
| `POLICY_BLOCKED` | 清单漂移、机器人/地图/标定不匹配、越界动作 | 保持暂停并告警 | 否 |
| `EXECUTION_RECONCILE` | Runtime 在返回结果前断开 | 通过环境观测确认最后状态 | 否，禁止盲重放 |
| `SAFE_RECOVERY` | Runtime 未就绪或未知失败 | 保留最后可信状态并停止推进 | 否 |
| `SAFETY_STOP` | 急停、碰撞或安全锁存 | 本地硬停止、等待人工复位 | 否 |

重试必须满足“尚未跨越物理副作用边界”。一旦 Runtime 可能执行过动作，系统只能用 command journal、机器人状态、物品关系和新的 Harness evidence 对账。不能因为 HTTP/gRPC 超时就再次抓取或放置。

用户端显示安全中文说明、恢复次数和自动动作；专业详情显示技术码、policy/version、manifest revision、inference/observation ID 和制品 hash 前缀。原始 action chunk、完整制品路径、原始异常和密钥永不进入任务事件或浏览器接口。

## 9. sim2real 晋级流程

### 数据与训练

1. 在 RoboCasa/MuJoCo 采集任务文本、观测、动作、结果、失败原因、地图/标定版本。
2. 将每条 episode 绑定 observation schema、robot model、action schema 和数据集 revision。
3. 训练 VLA/IL/RL，保存模型 SHA-256、代码版本、随机种子和评估包。
4. 在未见场景、光照、遮挡、物体位置和延迟随机化下评估。

### `SIMULATION_GO`

- `make policy-handoff` 通过；四个学习型工具均有环境确认。
- `make policy-faults` 通过；过期观测、超时、清单漂移、越界动作和未知执行都安全处理。
- 运行多种 seed、场景扰动和网络故障，成功率/碰撞率达到项目门槛。
- 发布证据包含模型 hash、manifest revision、数据集和 evaluation pack。

### `SHADOW_GO`

- 实机执行器禁用或由稳定控制器执行；新策略只做旁路推理。
- 比较候选动作、实际动作、观测延迟、越界拒绝率和 Harness 结果。
- 验证真实相机、地图、标定、单位、关节命名和时钟同步。
- 任何异常都不得影响在用控制器。

### `PHYSICAL_GO`

- 现场完成机械、电气、急停、限位和安全评审。
- 从低速、轻物体、单机器人开始，再做双机器人交接。
- 每次仅晋级一个 policy/robot model/calibration/transform 组合。
- 保留上一制品和 manifest；故障时停止派发、释放资源、锁存必要急停并回滚。

仿真通过不等于 `PHYSICAL_GO`。真实机器人安全批准必须由现场责任人签署，不能由自动流水线代替。

## 10. 生产监控与排障

至少按 policy ID/version、robot ID、capability 和结果类别统计：推理延迟 p50/p95/p99、超时率、观测等待率、动作拒绝率、manifest drift、每任务推理数、执行对账次数、Harness 确认时延和任务成功率。日志关联键使用 task/revision/step/command/inference/observation ID，禁止记录 action chunk 和原始图像凭据。

排障顺序：

1. 确认 Runtime 是否已经可能执行；若是，先进入 EXECUTION_RECONCILE。
2. 对比 Edge 当前 robot model、adapter、transform/calibration revision 与 `GET /v1/manifest`。
3. 检查 required sources 的 freshness、confidence、anomalies 和 observedAt。
4. 检查 sidecar health、模型加载、资源、延迟和有界公开错误码。
5. 使用 inference/observation ID 对齐 Edge、sidecar、Runtime journal 和 WorldModel。
6. 只在确定未执行运动时重试；否则依赖环境证据恢复。

发布前运行 `make test`、`make e2e`、`make policy-handoff`、`make policy-faults` 和 `make fleet-chaos`。RoboCasa 可视化另运行 `make robocasa-acceptance`；实机按 [仿真到实机](sim-to-real.md) 的分阶段验收独立签字。
