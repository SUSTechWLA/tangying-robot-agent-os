# XLeRobot 实验前检查与首次动作

此文档用于第一次在实体 XLeRobot 上运行前执行。所有命令都在树莓派上，除非另有说明。任何一步失败都要先断开 12 V 执行器电源，再排查。

## 1. 实验前固定项

- [ ] 完成[物理硬件安全检查表](../safety-checklist.md)，实体急停已在断电和上电状态下各测试一次。
- [ ] 两个控制板使用稳定串口别名，`tangying-robot` 用户属于 `dialout`。
- [ ] 标定文件存在且可解析：
  ```bash
  sudo -u tangying-robot /opt/tangying-robot-agent-os/.venv/bin/python \
    /opt/tangying-robot-agent-os/scripts/xlerobot_preflight.py \
    /etc/tangying-robot-agent-os/robot-pi.env
  ```
- [ ] mTLS 已配对，笔记本 `robot-agent doctor local` 通过。
- [ ] 确认桌面安全配置未改宽：
  ```bash
  sudo grep -E 'XLEROBOT_MAX_(RELATIVE_TARGET|ACTION_CHUNK_LENGTH)' \
    /etc/tangying-robot-agent-os/robot-pi.env
  ```
  首次实验保持默认 `8.0` 与 `64`，不要在实验中途调大。

## 2. 启动顺序

```bash
# 树莓派：无动作预检
sudo robot-agent doctor robot-pi

# 树莓派：启动后先看 readiness 日志，不应看到 READY 才继续动作
sudo robot-agent start robot-pi
sudo journalctl -u tangying-robot-edge.service -n 50 --no-pager
```

直接后端启动会打印：

```text
xlerobot direct edge readiness: READY
```

或列出具体 blockers。缺少感知、策略或 verifier provider 时，服务可以启动，但对应 capability 会显示不可用；Local Agent 在执行前会失败关闭。

## 3. 第一次最小动作

第一次动作必须使用最轻、最软、无液体、非尖锐物体，并保持 12 V 急停触手可及。

1. 先用云端创建任务并审批（见 README“第一条桌面任务”）。
2. 在 Local Agent 日志中确认出现 capability snapshot 通过后再靠近机器人。
3. 实体急停测试必须安排在首次自动动作之前：由另一人在安全距离发出任务，操作员手放在急停上，验证急停立即断电、服务进入 `EMERGENCY_STOPPED`。
4. 任何 `SAFETY_STOPPED`、`CANCELLED`、`BACKEND_STOP_FAILED` 或 provider 失败后：
   ```bash
   sudo robot-agent restart robot-pi
   sudo robot-agent doctor robot-pi
   ```
   不要在未检查机械状态前重新自动执行同一任务。

## 4. 策略与感知 Provider 合约

- `ROBOT_ENTITY_PROVIDER=module:function` 返回 scene entity dict 列表。
- `ROBOT_POLICY_PROVIDER=module:function` 返回 `action_chunk`，每个 action 只允许 `left_arm_*`、`right_arm_*`、`head_*` 的 `.pos` 键；值必须有限且在 `[-100, 100]`，夹爪在 `[0, 100]`。chunk 长度不得超过 `XLEROBOT_MAX_ACTION_CHUNK_LENGTH`，并且整段动作必须在命令 lease（默认 15 秒）内完成；策略提供方负责按自身控制频率生成可在一个 lease 内执行完的 chunk。
- `ROBOT_VERIFIER_PROVIDER=module:function` 返回 `BackendResult`，失败不得返回 `success=True`。
- provider 抛异常会映射为 `ENTITY_PROVIDER_FAILED`、`POLICY_PROVIDER_FAILED` 或 `VERIFIER_FAILED`，不会制造物理成功。

## 5. 日志与证据

每次实验保存：

```bash
sudo journalctl -u tangying-robot-edge.service -u tangying-xlerobot.service -n 500 --no-pager \
  > "xlerobot-$(date -u +%Y%m%dT%H%M%SZ).log"
```

记录：任务 ID、动作 chunk 长度、最大相对目标、是否出现安全事件、急停测试次数、物体与工作区照片。30 次硬件验收通过前不要改变 safety profile 或默认限制。
