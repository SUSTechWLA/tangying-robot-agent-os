# 测试、签名验收与发布检查

## 1. 测试矩阵

| 层级 | 命令 | 证明内容 |
| --- | --- | --- |
| Go 单元测试 | `go test ./...` | Task CAS、Coordinator、World/Harness、Fleet/Console、mTLS/幂等 |
| Python 单元/契约 | `python -m pytest -q` | 安装、Adapter、RoboCasa、证据验证、故障矩阵 |
| Web 单元测试 | `(cd web && npm ci && npm run build && npm test)` | 任务体验、revision ordering、WebGL、交互、fallback、资源闭包 |
| RoboCasa 冒烟 | `make robocasa-smoke` | 环境、模型、共享世界可加载 |
| RoboCasa 故障 | `make test-robocasa-faults` | 断连、重复、倒序、stale、custody、恢复 |
| 签名证据重验 | `make robocasa-acceptance` | tracked round3 未篡改且 22 项语义检查全通过 |
| 构建/生成 | `make generate-check`, `make build`, `make lint` | 生成代码、二进制、格式和静态检查 |
| 文档契约 | `python -m pytest -q tests/docs/test_production_docs.py` | 文档集、路由、链接、命令、路径和秘密扫描 |

## 2. RoboCasa 真实流程

验收启动 Fleet、两个 Edge、两个 Runtime 和一个共享 RoboCasa/MuJoCo 世界；创建中文任务；在 robot-1 步骤 RUNNING 时提议“最后放到右侧蓝色垫子上”；确认 revision 2；验证 `WAITING_SAFE_POINT`；等待双 Harness `SATISFIED`；最终方块在右目标、held 清空、environment token 3。

浏览器验证：完整厨房和两台 XLeRobot；人话理解、编号步骤、工具活动；更新轨道；专业详情折叠；左拖/右拖/滚轮/预设/跟随/双击/F；刷新恢复；模型/边界/标签/路径独立；WebGL 故障后 `WORLD LIVE / VISUAL DEGRADED` 与语义 Canvas。

## 3. 签名证据的信任模型

candidate runner 在启动栈前生成不可预测 episode nonce、一次性 bearer 接收端和临时 Ed25519 key。浏览器提交五张 1404×794 截图、与每张原子绑定的 WorldSnapshot/DOM revision、完整 page-assets inventory、真实性能时间序列和交互事件。runner 独立请求 document、CSS、三个脚本、manifest、scene、robot、binding 九角色并交叉核对 URL、无重定向、nonce header、bytes 和 SHA-256。

接收端只接受一个有效 POST；PNG 规范化后写入私有 staging；summary 逐项重算 Task/Scene/Model/Joints/Intent/Placement/Freshness/Custody/Trajectory/Harness/Asset/Provenance/Screenshot/Network/Performance/VersionedUpdate。随后 final attestation 签住 summary、capture envelope 和全部 retained regular files，私钥销毁。任何 symlink、特殊文件、未签额外文件、hash/signature/anchor 变化都失败关闭。

日常只运行：

```bash
make robocasa-acceptance
```

它离线验证 `artifacts/robocasa-harness/round3`，不启动栈。只有有意更新黄金证据时才：

```bash
make robocasa-acceptance-candidate
# 使用仓库 uploader 对 runner 输出的 0600 session 发一次本地 POST
make robocasa-acceptance-promote
make robocasa-acceptance
```

不要直接编辑 summary、截图、anchor 或 attestation。

## 4. 性能指标如何解释

签名证据同时记录实际 display rAF 与 `gpuRenderer.render()` 调用耗时。自动 gate 要求稳定 renderer submission capacity ≥50 FPS；p90/p95/max 仍如实保留。submission capacity 可能高估显示器、合成器或 GPU completion 下用户看到的流畅度，因此不能写成“用户显示达到 50 FPS”。生产实机必须在目标浏览器、目标显示器和可见窗口独立验收 display rAF ≥50，或由产品明确接受更低门槛。

## 5. 发布检查

```bash
make generate-check
make build
make lint
make test
conda run --no-capture-output -n tangying-robocasa python -m pytest -q \
  tests/e2e/test_robocasa_task_updates.py \
  tests/e2e/test_versioned_task_faults.py \
  tests/e2e/test_robocasa_faults.py \
  tests/e2e/test_robocasa_visual_twin.py
(cd web && npm ci && npm run build && npm test)
make robocasa-acceptance
git diff --check
git status --short
```

再从干净 checkout/`git archive` 运行 `make robocasa-acceptance`，证明没有依赖忽略文件。检查无 `node_modules`、session、私钥、token、临时端口或后台进程。

## 6. 实机验收

仿真通过后仍需：mTLS 身份、实体急停、限位、断网停止、命令幂等、旧 fencing 拒绝、地图/外参、source freshness、单/双机器人持物恢复、实际抓取成功率、碰撞测试、可见帧率、24/72 小时长稳、备份恢复。安全负责人签字前不能把仿真结论表述为实机安全认证。

## 7. 发布证据应记录

commit、平台、Go/Python/Web/文档测试数量和耗时、bundle hash、acceptance run/task/nonce 的非秘密引用、22 项检查、截图摘要、性能 mean/p90/p95、已验证异常、已知限制、回滚版本和实机待办。详见发布时生成的 `release-evidence.md`。
