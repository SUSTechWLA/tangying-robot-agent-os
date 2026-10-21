# 本轮研究证据与复验

本目录的可读副本对应开发 140、留出 210 个未知探索 episode，以及最终四个预测计划和两个理想软件 Runner 闭环。各组信息条件不同，不能合并成功率；不含新的 Gazebo、ROS 2、实机、物理测量或真实网络验收。

## 直接读取

- `study/development` 和 `study/holdout`：完整配置、summary、逐次 CSV、环境、预登记和 manifest。`study/selection-protocol.json`、`selection-freeze.json` 保存开发集选型规则和留出执行前的本地冻结；没有外部盲测认证。
- `analysis/development` 和 `analysis/holdout`：最终独立分析、逐对差、按几何聚类的描述性区间、方法与源码/provenance。旧字段名不改变实际通信计量范围。
- `coverage-plans`：四个最终已知地图计划、请求、源码和各自 manifest。完整预测不是执行证据。
- `fixture/normal` 和 `fixture/recovery`：最终 003/004 理想 Runner 的 capture、命令、停止回执、核心账本、源码和 summary。实际模拟观测覆盖均为 315/315；全部 physical 标志为 false。
- `validation`：工程命令、原始失败与修复回归日志。主清单收齐后固定各文件 SHA-256，不丢弃初次失败。

## 原始归档

`raw-evidence.tar.gz` 是 200,401,338 字节原始记录的压缩副本（1998 个文件）。`archive-members.json` 固定每个成员的字节数与 SHA-256；创建归档后已逐成员重新读取验证。压缩不是删除原始证据。归档根目录包括：

- `study/`：两分区全部逐步事件、真值、输入/实现快照、配对指标、选择记录，保留早期与最终分析及最终预测计划。
- `fixture-001/002/003/004/`：修正最终格式前后 Runner 运行。003/004 与001/002数值一致，重复不能扩大独立样本数。
- `planning-smoke/`：早期已知地图 smoke，保留其当时源码，不能混作新的独立任务。

解压到新目录后复验，勿覆盖原始实验：

```bash
mkdir -p artifacts/reproduce-spatial-coverage-20261018
tar -xzf docs/experiments/evidence/2026-10-18-spatial-coverage-upgrade/raw-evidence.tar.gz \
  -C artifacts/reproduce-spatial-coverage-20261018
.venv/bin/python scripts/spatial_exploration_benchmark.py \
  --replay artifacts/reproduce-spatial-coverage-20261018/study/holdout
.venv/bin/python scripts/plan_spatial_coverage.py \
  --verify artifacts/reproduce-spatial-coverage-20261018/study/coverage-plans-final/full-room-coverage
.venv/bin/python scripts/run_spatial_coverage_fixture.py \
  --verify artifacts/reproduce-spatial-coverage-20261018/fixture-004
```

benchmark 回放核对存档字节与事件指标，不重新执行传感器或路径；计划 verify 在匹配源码下重新规划；fixture verify 检查采集/账本绑定并重算覆盖，不执行物理控制。当前工作区可验证最终计划及003/004；较早计划和001/002的源绑定复验要求先在独立checkout恢复对应source字节。哈希链只证明本次存档的一致性，不是外部数据真实性签名。

350个探索样本与最终工作区仅有一次 scope 条件 if 的格式合并差异，冻结原实现未被改写；最终计划与003/004已在最终源码重跑，详情见 validation/frozen-vs-final-source.json。主清单自身不自哈希，其内容可随提交 Git 身份核对；真实设备的安全、动态与定位误差仍需独立实验。
