# scan002 续段首帧缺失审计

本目录沿用 2026-10-28 实验计划日期；定位、修复及离线回归实际在 2026-10-29（Asia/Shanghai）完成。这里只记录代码修复及其有界证据，**不是修复后真实扫描成功的证据**。旧 scan002 的 `failed` 保持不变。

## 真实失败与确定原因

操作 `1ba72d23a2f74a679aec11285775f2d4` 首段达到 40 帧、56,210 点、约 0.565684 m 的实测行程，停止原因为 `no_reachable_frontier`；仍有 **712 个前沿单元**，`frontiersExhausted=false`。37 条受控动作记录中 36 条 `STEP_COMPLETE`、末条 `NAV_ENVELOPE`。不能解释成前沿耗尽、全场景覆盖完成，也没有放宽控制器包络来绕过拒绝。

续段 `_open_leg` 清零连续深度拒绝计数，仅调用一次 `_sample`。保存的最终结果为 0 帧且 `consecutiveInsufficientDepth=1`，因此这次首采明确走了 `InsufficientDepthError` 分支；之后续段跳过初始扫描、空栅格立即触发 `no_frames`。仅凭“source stamp 重复”无法解释这个计数，不把猜测写成确定根因。

`before-after.json` 使用保存的旧源码和真实 DenseSlamProvider，由进程内合成 RGB-D 夹具复现：旧代码只读第一个无效深度曝光即 `no_frames`；修复代码读到第二个有效曝光，得到 1 帧，未发运动命令。真实场景下一帧是否有效仍须新编号仿真重测。

## 实现与边界

- 续段静止补采最多 3 个不同曝光，等待预算 8 秒；不改写传感器时间、不降低深度或配准阈值。底层 capture/add 仍依赖已有 provider 的有界调用合同，不宣称能强制中断阻塞的第三方 provider。
- source ID + sensor stamp 去重；无源时间戳的适配器退回 source ID + 实际采集墙钟。每次读取新分配的 observation ID 不会把同一曝光反复记成深度拒绝。
- 仍无实测栅格时，续段最多花一次现有 look 次数预算执行已有安全环视。原 120 秒环视上限、命令次数、每次 <=0.5 rad、取消/租约/帧/行程/深度限制都保留。已有有效帧的续段不重复初始扫描；持续缺测仍失败。
- `scanEvidence.bootstraps` 最多保存 6 段补采记录；`continuationScanAttempted/Status` 标记恢复环视。继承地图不能冒充本段新测量，标注的房间名不能当作实测可通行空间。

## 验证与追溯

17 个新回归单独运行全部通过（0.71 秒）；连同已有 survey/workflow/provider/continuation 共 189 项通过（1.65 秒），Ruff 通过。原始文本及 JUnit XML 均保留；首轮开发运行 5 失败、12 通过的日志也原样保留，避免把调试失败覆盖掉。详见 `bootstrap-final-tests.txt`、`regressions-01.txt`、`commands.json`。

`source-before.json`、`source-final.json` 给出原始证据及修复源文件 SHA256。压缩包保存原始 `/tmp/scan002-bootstrap-fix` 证据、旧源码、修复后四个文件、负例脚本和变化补丁；原始脚本中的绝对路径保持原貌。`evidence-manifest.json` 记录原路径、大小和逐成员哈希，共 24 个成员，约 154 KB。没有复制数据库、密钥、环境文件或大型原始传感器数据。仅对明确 allowlist 进行常见私钥/provider/AWS/bearer 模式检查，零命中；这不等于任意秘密格式的普适检测。

在仓库根校验或复跑：

```sh
python3 docs/experiments/2026-10-28-user-workbench-e2e/exploration-audit/verify.py
(cd docs/experiments/2026-10-28-user-workbench-e2e/exploration-audit && shasum -a 256 -c SHA256SUMS)
.venv/bin/python -m pytest -q robot/gateway/tests/test_survey_continuation_bootstrap.py robot/gateway/tests/test_survey.py robot/gateway/tests/test_robot_services.py robot/gateway/tests/test_slam_provider.py robot/gateway/tests/test_slam_provider_continuation.py
```

校验逐成员读取压缩包但不解压、不执行内容。源码可能随后因其他修复更新，复现本次结果须先对照保存的源码哈希；本归档不声称后续版本与本次完全相同。
