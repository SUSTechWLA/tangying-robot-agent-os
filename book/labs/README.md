# 无硬件课堂实验

这两个程序只使用 Python 标准库，没有 ROS、模型服务或机器人连接。先预测结果，再运行，再解释为什么与直觉不同。

## 丢响应实验

```bash
python3 book/labs/uncertain_outcome.py --output /tmp/agentos-class-01
```

输出目录必须未使用。预期 naive 的 effects=2，guarded 为 effects=1/UNKNOWN，reconcile 为 effects=1/COMPLETED。打开每组 trace.json，解释两次数据库连接之间发生什么。

设备计数器只是模拟物理效果，第三组假设存在可信的匹配回执。真实系统还检查完整 binding、当前权限、目录与动作后证据；这个小程序不能替代生产恢复器。对应第 8 章，以及 `internal/edgeagent/recovery.go`。

修改练习：把第三组 receipt 改成其他 command；在设备效果发生前中断；把预算写入放在执行之后并推演窗口。每次用新的输出目录，保留原始结果。

## 上下文实验

```bash
python3 book/labs/context_projection.py
```

比较 naiveTail 与 protectedView。前者没有目标、禁止区和未决命令，后者完整保留这些对象，并能从正确 scope 恢复原事实。跨机器人读取与过小预算被拒绝。

修改练习：加入大量 guard，观察为何不能继续裁剪；修改冷归档字节，检查哈希；设计一个“只看最后三项”会重复执行早期动作的例子。该程序是教学简化模型；生产源码见第 18 章的 ProjectManaged 与 Host 两条路径。
