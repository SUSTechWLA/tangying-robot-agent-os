# 从零开始快速上手

## 1. 准备环境

支持 macOS 13+、Ubuntu 22.04/24.04，建议 Python 3.11、Go、Git、Docker、Conda。RoboCasa 使用独立 Conda 环境，避免污染主 Python 环境。

```bash
git clone https://github.com/SUSTechWLA/tangying-robot-agent-os.git
cd tangying-robot-agent-os
make build
make test
```

## 2. 最快跑通 RoboCasa 双机器人

```bash
make robocasa-install
make robocasa-smoke
make robocasa-web-assets
bash scripts/robocasa-fleet.sh start
```

打开 `http://127.0.0.1:18080/`。开发/验收栈默认账号是 `admin`，密码是 `admin123`；它只用于 loopback 演示，生产不得使用。页面显示 `WORLD LIVE` 和 `VISUAL LIVE` 后，输入：

```text
让1号机器人把红色方块放到交接区，然后让2号机器人把红色方块从交接区放到右侧目标区
```

批准任务。运行期间在“更新任务”输入：

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

## 3. 服务器端使用：云端 Fleet

```bash
./scripts/fleet-up.sh up
./scripts/fleet-up.sh status
./scripts/fleet-up.sh credentials   # 只在受控终端查看生成的账号/密码
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

1. 输入“希望机器人做什么”；页面先复述机器人理解，不直接执行。
2. 核对编号步骤、负责机器人和人话能力，例如“移动到方块”“夹取方块”“放到蓝色垫子”。
3. 批准；执行中查看“机器人正在做什么、为什么等待、环境是否确认”。专业事件和 evidence ID 默认折叠。
4. 需要改变结果时输入更新，查看“保留/修改/取消”预览，再确认。系统会在安全点切换。

数字孪生操作：左键拖动平移；右键拖动旋转；滚轮按鼠标位置缩放；双击实体聚焦；`F` 复位；“总览/俯视/R1/R2/跟随”切换视角；“模型/构件边界/标签/任务路径”可独立开关。刷新后任务、版本、更新轨道和权威世界会恢复。

如果 `WORLD LIVE / VISUAL DEGRADED`，表示数据仍可信但 3D 资产失败，页面会显示语义 Canvas；不要把视觉降级误判为机器人离线。

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

树莓派：

```bash
./install.sh robot-pi --dry-run --yes
./install.sh robot-pi --yes
sudo robot-agent doctor robot-pi
sudo bash scripts/robot-pi-preflight.sh
sudo robot-agent start robot-pi
```

控制端首次配对：

```bash
ssh ubuntu@xlerobot.local        # 人工核对主机指纹
robot-agent pair xlerobot.local --ssh-user ubuntu
robot-agent doctor local
```

动作前必须核对实体急停、电源、舵机 ID、稳定串口、工作区、地图与 transform revision、相机外参、夹爪零点和安全速度。先 dry-run，再空载 bench，再受限工作区单机器人，最后双机器人。详细步骤见[仿真到实机](sim-to-real.md)。

## 8. 常用验证

```bash
make test
make test-policy-sidecar
make policy-handoff
make policy-faults
make test-robocasa-faults
make robocasa-acceptance
go test ./...
(cd web && npm ci && npm run build && npm test)
```

`make robocasa-acceptance` 只离线重验证仓库固定的签名证据，不启动新任务。重新采集必须使用专门 candidate 工作流，详见[测试与验收](testing-and-acceptance.md)。

## 9. 学习策略快速接入

仿真默认使用只允许仿真 adapter 的确定性策略，方便不安装机器学习框架也能跑通完整协议。接入实际 VLA/模仿学习/强化学习服务时，将模型包装为 Python sidecar 的 CallableProvider，先验证 `GET /v1/manifest` 和 `POST /v1/infer`，再配置 `EDGE_POLICY_MODE=http`、`EDGE_POLICY_ENDPOINT`、robot model、地图和标定 revision。实机启用前必须依次达到 SIMULATION_GO、SHADOW_GO 和 PHYSICAL_GO，步骤见[学习型策略工具](policy-tools.md)。
