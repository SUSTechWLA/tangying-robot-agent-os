# XLeRobot 生产就绪判定

## 当前结论

**不要**在没有完成本页全部检查前让 XLeRobot 执行真实 fetch/place 任务。

项目当前已验证：

- MuJoCo 仿真闭环；
- 30 轮仿真验收与 18 组对象/目标矩阵；
- 本地 Agent、SQLite 任务运行态、Robot Runtime、Safety Supervisor、mTLS、直连 XLeRobot 驱动和标定/预检链路。

尚未满足的实体条件：

- 本地实体感知 provider；
- 本地动作策略 provider；
- 本地抓取/放置 verifier provider；
- 实体急停、断网停机和重复命令试验证据；
- 至少 30 次真实硬件验收。

这些条件未满足时，系统会失败关闭，返回 `ENTITY_PROVIDER_REQUIRED`、`POLICY_ACTION_CHUNK_REQUIRED` 或 `VERIFICATION_UNAVAILABLE`，不会假装成功。

## 一键 go/no-go

树莓派执行：

```bash
sudo robot-agent production-check robot-pi
# 或
make production-check
```

命令依次检查：

1. no-motion XLeRobot preflight；
2. 树莓派的 `ROBOT_ENTITY_PROVIDER`、`ROBOT_VERIFIER_PROVIDER` 已配置且可导入；
3. 笔记本策略集成能够为物理技能生成经过验证的有界 `action_chunk`；
4. `/var/lib/tangying-robot-agent-os/evidence/hardware-trials.json` 记录：
   - `completed_trials >= 30`
   - `emergency_stop_tested = true`
   - `network_interruption_tested = true`
   - `duplicate_command_tested = true`
5. `/var/lib/tangying-robot-agent-os/evidence/safety-checklist.json` 记录：
   - `physical_estop_installed = true`
   - `physical_estop_tested = true`
   - `operator_present_during_trials = true`

全部通过才会输出：

```text
READY xlerobot is cleared for physical fetch/place experiments
```

## 证据文件模板

```bash
sudo mkdir -p /var/lib/tangying-robot-agent-os/evidence
sudo tee /var/lib/tangying-robot-agent-os/evidence/safety-checklist.json >/dev/null <<'EOF'
{
  "physical_estop_installed": true,
  "physical_estop_tested": true,
  "operator_present_during_trials": true
}
EOF
```

硬件验收由操作员逐次记录；全部 30 次成功后才能填写：

```json
{
  "completed_trials": 30,
  "emergency_stop_tested": true,
  "network_interruption_tested": true,
  "duplicate_command_tested": true
}
```

不要提前填写。生产门槛是安全证据，不是配置项。

## Provider 配置示例

```bash
sudo robot-agent configure robot-pi \
  ROBOT_ENTITY_PROVIDER=my_perception.providers:scene_entities \
  ROBOT_VERIFIER_PROVIDER=my_perception.providers:verify
```

## 建议的生产上线顺序

1. 完成 `docs/install/xlerobot-experiment.md`。
2. 空载/轻物试验，记录每次结果。
3. 断网、急停、重复命令试验。
4. 完成 30 次硬件验收并填写证据。
5. `sudo robot-agent production-check robot-pi` 通过。
6. 才允许普通用户下发真实任务。
