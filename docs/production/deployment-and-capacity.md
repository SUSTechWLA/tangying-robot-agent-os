# 部署拓扑、容量与可观测性

这是用户要求之外建议补充的生产文档：接口能调用并不等于系统可容量规划、监控和升级。

## 环境分层

开发环境使用 loopback、确定性凭据和单实例；预生产复制生产 TLS、MySQL、Redis、CIDR、地图与机器人数量，但使用仿真/安全硬件；生产按 tenant/world 隔离。禁止把开发 `admin/admin123`、dev-insecure、测试 CA 或 RoboCasa bearer 带入生产。

## 最小生产拓扑

- HTTPS ingress/WAF：至少 2 实例；WebSocket sticky 不是一致性前提。
- Fleet API/投影读取：可横向扩展。
- Coordinator：每 world 单活，备用实例用 leader fencing 接管。
- MySQL：主备 + PITR；Redis：高可用 Stream；帧放对象存储。
- 每机器人独立 Edge；Runtime 与硬件同局域网；安全回路本地化。

## 容量维度

按机器人心跳/遥测/观测 Hz、实体数、帧大小、并发 Task、WS 客户端、World delta retention、事件保留期分别计算。高频图像不进入 World delta；使用 FrameReference。压力测试必须同时观察 p50/p95/p99、Outbox lag、consumer pending、World projection lag、source stale 比例、Harness latency 和浏览器 resync 次数。

## 建议 SLO

控制面可用率、任务创建/审批延迟、Edge 在线率、World freshness、Harness 判决延迟和安全停止成功率分开定义。任何安全 SLO 都以“失败关闭是否生效”为主，不以任务成功率掩盖。告警至少覆盖：leader/lease、DB/Redis、Outbox、设备离线、观测 stale/conflict、fencing reject、Harness timeout、WS gap、磁盘、证书到期和急停。

## 滚动升级

先升级读兼容组件，再数据库迁移，再 Fleet/Coordinator，再 Edge，最后 Runtime；catalog/protocol 变化在预生产验证。每批机器人升级后重新注册并跑 dry-run。若旧/新版本不能双向兼容，冻结任务、完成安全释放、切换维护窗口。回滚不能降低 fencing token 或重写 Revision。
