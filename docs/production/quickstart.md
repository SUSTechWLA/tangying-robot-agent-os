# 从零开始快速上手

先按[开发者快速上手](../development/getting-started.md)准备工作区；购机用户从[Sim2Real 上手](../sim2real/README.md)开始。本页汇总服务器端、用户端、机器人仿真与机器人实机的入口。

## 1. 准备环境

支持 macOS 13+、Ubuntu 22.04/24.04，开发基线为 Python 3.11、Go 1.26、Node.js 和 Git；Fleet Compose 另需 Docker，RoboCasa 另需 Conda。RoboCasa 使用独立 Conda 环境，避免污染主 Python 环境。

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make setup
make build
# 首次先运行轻量仿真；完整开发检查见开发者指南
```

## 2. 最快跑通 RoboCasa 双机器人

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
ROBOCASA_HUMAN_SPEED=0.02 bash scripts/robocasa-fleet.sh start
```

打开 `http://127.0.0.1:18080/`。此入口使用 Compose，读取私有 `deploy/cloud/.env` 中生成的操作员凭据；`admin / admin123` 仅属于独立 E2E/自然语言评测夹具。上面的 `ROBOCASA_HUMAN_SPEED` 为仿真动作加入可观看节流，便于在执行中修改任务；不设置时默认 0，任务可能很快完成。确认页面标记“仿真环境”“场景已同步”且三维场景加载成功后，输入：

```text
让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区
```

Fleet 页面“创建并开始任务”会依次创建并批准任务；点击前核对提示。运行期间展开“更新任务”，输入：

```text
最后放到右侧蓝色垫子上
```

先核对预览，再确认更新。正常 UI 依次显示“系统已经理解这次更新”“机器人先完成了手上的安全动作”“新任务版本已经启用”。最终两个步骤均为“已完成并通过环境验证”，方块位于 `right-target-zone`，owner 为 `environment`，fencing token 为 3。

停止与清理：

```bash
bash scripts/robocasa-fleet.sh status
bash scripts/robocasa-fleet.sh logs
bash scripts/robocasa-fleet.sh stop
```

当前场景每次启动验证一轮单向交接，完成后需重新启动该 profile。反向搬运及完成后的通用重新授权未实现，不能靠再次提交或修改句式获得支持。更多口语例子和隔离评测命令见[自然语言评测](../development/natural-language-evaluation.md)。

## 3. 服务器端使用：云端 Fleet

```bash
./scripts/fleet-up.sh up
./scripts/fleet-up.sh status
./scripts/fleet-up.sh env   # 只在受控终端查看生成的账号/密码
```

云端正式入口为 HTTPS 443，机器人 mTLS gRPC 为 8444，本机调试 Console 为 loopback 18080。生产账号和强随机密码保存在 mode 0600 的 `deploy/cloud/.env`，不要复制示例密码。启动仿真 Edge：

```bash
./scripts/fleet-sim.sh start
./scripts/fleet-sim.sh handoff
./scripts/fleet-sim.sh status
./scripts/fleet-sim.sh stop
```

服务器端成功检查：`/healthz` 返回 200；两台设备 ONLINE；世界 sources 全部 FRESH；没有资源 conflict；任务领域事件顺序单调。

## 4. 用户端使用

用户只需完成四件事：

1. 输入“希望机器人做什么”。Local 创建后单独批准；Fleet 的“创建并开始任务”会立即批准，不能假定提交后还有第二次确认。
2. 核对编号步骤、负责机器人和人话能力，例如“移动到方块”“夹取方块”“放到蓝色垫子”。
3. 按所在模式完成创建/批准；执行中查看“机器人正在做什么、为什么等待、环境是否确认”。专业事件和 evidence ID 默认折叠。
4. 需要改变结果时输入更新，查看“保留/修改/取消”预览，再确认。系统会在安全点切换。

数字孪生操作：左键拖动平移；右键拖动旋转；滚轮按鼠标位置缩放；双击实体聚焦；`F` 复位；“总览/俯视/R1/R2/跟随”切换视角；“模型/构件边界/标签/任务路径”可独立开关。Fleet 可选择历史任务并恢复版本与更新轨道；Local 当前 UI 仅展示当前会话任务。

在开发诊断中如果看到 `WORLD LIVE / VISUAL DEGRADED`，表示数据仍可信但 3D 资产失败，页面会显示语义 Canvas；不要把视觉降级误判为机器人离线。

## 5. Local Brain：无网络环境

```bash
./install.sh local --dry-run --yes
./install.sh local --yes
robot-agent configure local
robot-agent doctor local
robot-agent start local
robot-agent status local
```

打开 `http://127.0.0.1:8787/`。Local Brain 把任务和审批保存在 SQLite，经本机 RobotRuntime 执行；LLM 不可达时，支持的任务由确定性解析器处理。查看和停止：

```bash
robot-agent logs local --follow
robot-agent restart local
robot-agent stop local
```

## 6. 机器人仿真

轻量 MuJoCo：

```bash
./install.sh sim --yes
./bin/robot-agent doctor sim
./bin/robot-agent start sim
./bin/robot-agent status sim
```

完整 RoboCasa 使用 `scripts/setup-robocasa.sh`，两个 Runtime 默认监听 51051/51052；`scripts/robocasa-fleet.sh` 启动共享厨房，不能为两台机器人分别启动两个互不相干的世界。资产变更后执行 `make robocasa-web-assets`，确认 manifest 的 SHA-256 与浏览器同源响应一致。

## 7. 机器人实机

购机后先按[Sim2Real 上手](../sim2real/README.md)固定硬件/软件版本，完成无动作 inventory、串口与标定、mTLS 配对、感知/策略/verifier 集成，再进入受监督 bench。不要直接从仿真任务跳到实机启动。

树莓派安装用 `./install.sh robot-pi --dry-run --yes` 预览，再 `./install.sh robot-pi --yes`；服务保持停止。`sudo robot-agent doctor robot-pi` 无动作检查不授权扭矩；默认 Runtime 不自动 arm。完整顺序见[树莓派安装](../install/robot-pi.md)和[首次实验](../install/xlerobot-experiment.md)。工具注册、地图坐标系与系统迁移见[仿真到实机](sim-to-real.md)。

## 8. 常用验证

```bash
make test
make test-policy-sidecar
make policy-handoff
make policy-faults
make test-robocasa-faults
make robocasa-acceptance
make test-go
make test-web
# 仅修改 WebGL bundle/依赖时，在 web 目录执行 npm ci / npm run build
```

`make robocasa-acceptance` 只离线重验证仓库固定的签名证据，不启动新任务。重新采集必须使用专门 candidate 工作流，详见[测试与验收](testing-and-acceptance.md)。

## 9. 学习策略快速接入

仿真默认使用只允许仿真 adapter 的确定性策略，方便不安装机器学习框架也能跑通完整协议。接入实际 VLA/模仿学习/强化学习服务时，将模型包装为 Python sidecar 的 CallableProvider，先验证 `GET /v1/manifest` 和 `POST /v1/infer`，再配置 `EDGE_POLICY_MODE=http`、`EDGE_POLICY_ENDPOINT`、robot model、地图和标定 revision。实机启用前必须依次达到 SIMULATION_GO、SHADOW_GO 和 PHYSICAL_GO，步骤见[学习型策略工具](policy-tools.md)。
