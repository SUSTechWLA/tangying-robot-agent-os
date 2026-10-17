# 三维异构探索软件实验配置

这些配置运行离线几何软件实验，不启动 Docker、Gazebo 或真实机器人。

| 配置 | 用途 | 配对任务块 | 变体 | Episode 数 |
| --- | --- | ---: | ---: | ---: |
| `spatial_exploration_ablation.json` | 保留的早期小规模设计，旧联合消融兼容入口 | 3 场景 × 2 seeds | 5 | 30 |
| `spatial_exploration_development.json` | 新场景开发与参数比较 | 5 场景 × 4 seeds | 7 | 140 |
| `spatial_exploration_holdout.json` | 参数和实现冻结后进行留出评估 | 5 场景 × 6 seeds | 7 | 210 |

运行与回放：

```bash
.venv/bin/python scripts/spatial_exploration_benchmark.py \
  --config examples/experiments/spatial_exploration_development.json \
  --output artifacts/experiments/<新的实验目录>
.venv/bin/python scripts/spatial_exploration_benchmark.py --replay artifacts/experiments/<实验目录>
```

每个 `sceneId + seed` 的真值、初始位姿、机器人包络、目标范围和故障计划在任何策略调用前写入 `truth/` 与 `preregistration.json`；所有变体使用相同冻结输入。`scoring-contract.json` 冻结覆盖分母、阈值与评分源码身份。策略只收到模拟传感器产生的地图快照，真值文件用于模拟传感器和独立评分。这是接口边界，不是针对恶意策略的进程隔离沙箱。

新配置包含货架通道、错位门洞、高处遮挡、封闭不可观测空腔和观测交换故障。种子改变尺寸、开口、障碍位置和高度。开发与留出种子不重叠，运行前同时逐场景、跨场景检查真实 occupancy 布局哈希是否跨 split 重复。恢复场景故意复用门洞场景的几何并添加故障；报告同时提供任务块数量和真实独立地图数量，不将换名字视为新增物理地图。

`cooperation.assignment_overlap` 控制任务分配的重叠协调，`cooperation.shared_coverage_guidance` 控制通过显式覆盖 ID 消息接收的共享覆盖引导。`no_assignment_overlap` 与 `no_shared_coverage_guidance` 分别只关闭其中一个因素；旧 `no_coordination` 同时关闭两者。它们始终保留占据地图交换、路径碰撞检查、执行 fence 和未知结果停止确认。

`mapExchangeDelayRounds` 只延迟占据地图下行；上传、共享覆盖 ID、重连全量快照分别计量。新设计设为 2 轮，旧配置默认 0。延迟单位是离线决策轮次，不是实测网络时延；字节数不包含 TLS、网络帧和任务租约控制流量。断联故障仅隔离观测交换，本地调度器与停止确认端口仍可用。

`studyDesignHash` 是跨 split 共用的配置设计身份，`studyImplementationHash` 另外绑定冻结源码。论文进行留出评估前，应固定这两个身份并保存外部选择记录；`selectionFreeze: null` 不证明已经完成盲测或防止人工反复调参。修改参数后继续使用同一留出数据，不应再称为未经使用的留出评估。

覆盖指标仍为 ROI 内自由与占据体素的观测比例，分母包含不可达或被遮挡的区域。该指标是机器人观测日志的事后并集；`coordinatorCoverageFraction` 另行统计协调器实际收到并接纳的证据，不能把未上传的离线观测称为协调器已确认。封闭空腔场景故意暴露无法完成的部分；策略耗尽候选不等于全部覆盖。这里的地面支撑是平面 fixture 假设，未验证多层在线探索、飞行动力学、步态、真实定位误差或实机成功。回放校验事件、冻结输入和独立指标，不重新运行原规划器。
