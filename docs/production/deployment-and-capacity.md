# 部署拓扑、容量与可观测性

本页区分当前可运行拓扑和未来生产要求。V1 的实际验证与未完成项见[当前状态](v1-release-status.md)。单机仿真通过不证明容量、实机安全或 HA。

## 当前拓扑

- Local Brain：笔记本单进程、SQLite 与有界内存队列，Runtime 为另一受控进程/设备。
- Fleet Compose：一个控制面进程、MySQL、Redis 与 nginx；每机器人一个 Edge 和 Runtime。
- RoboCasa 开发栈：共享世界与两个 Runtime/Edge，用于限定任务、恢复与视觉验收。

当前 Fleet 不是可直接复制多个副本的自动 HA 服务。Coordinator、WorldHub 以及物理资源所有权必须维持同一单写关系。即使数据库或 Redis 部署为 HA，也不自动补齐业务提交 fencing 和资源转移 saga。

## 单主世界持久化

直接启动 `cmd/fleet-control-plane` 时，`FLEET_WORLD_SNAPSHOT_PATH` 未设置便使用内存世界；设置后启用单机 checkpoint。Compose 固定为 `/var/lib/tangying-fleet/world.json`，由 `fleet-world` named volume 保存。不要将该环境变量误当作任意共享文件系统的多写协调机制。

- 文件保存世界身份、投影和源序列，用于进程重启后恢复。
- 独占文件锁防止同机第二个 writer；损坏、世界身份不符或写入失败时关闭推进能力，不自动退回空世界。
- 重启不恢复 delta 环形缓存；浏览器应 REST resync。恢复快照不是当前现场真值，需要新观测重新证明 freshness 和后置条件。
- 备份/恢复时冻结新派发、保存 MySQL/Redis/世界快照与 Runtime journal 的关联版本，确认现场物体归属后再继续。
- 跨主机 HA、共享盘多写、全局原子事务、长期故障容忍和自动切主仍需独立工程与验收。

## 环境分层

开发使用 loopback、仿真和隔离的演示凭据。预生产复制 TLS、数据库、网络与机器人配置，先用仿真/受监督硬件；生产限定 world、现场与操作员。不要把开发 `admin/admin123`、dev-insecure、测试 CA 或 capture bearer 带入生产。

入口遵循 HTTPS 443、机器人 mTLS 8444；内部 8080/8443、MySQL、Redis 不公开。细粒度 RBAC、组织/租户隔离和审计留存需要按[安全配置](configuration-and-security.md)补齐。

## 容量维度与建议 SLO

按机器人心跳/观测频率、实体数、帧大小、并发任务、WebSocket 客户端、delta retention 和事件保留期评估。图像使用受限帧通道或 FrameReference，不进入低频世界事件作为大 blob。尚无适用于任意硬件数量的认证容量。

压力与稳定性验证分别记录 p50/p95/p99、Outbox lag、队列 pending、世界投影延迟、source stale 比例、Harness 延迟和浏览器 resync 次数。控制面可用率、任务延迟、观测新鲜度、停止响应与任务成功率分开统计；成功率不能掩盖安全失败。

## 升级与回滚

检查代码版本与数据库迁移，冻结派发，完成安全释放并备份。兼容读先行，按迁移要求更新 MySQL，再升级 Fleet/Edge/Runtime；每批设备重注册目录并跑 dry-run。旧新版本不兼容时使用维护窗口，不依赖滚动升级自动安全。回滚不降低 fencing token、不重写 TaskRevision，也不能删除 journal 或 world 文件来消除冲突。

未来按 world/tenant 分片、读取副本、对象存储、双 ingress 和数据库 HA 的方案均需另立设计及容量/恢复证据，不能作为当前已经实现的功能列入交付。
