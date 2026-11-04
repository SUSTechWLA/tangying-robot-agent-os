# 首帧预算的迟到返回补充修复

本补充发生在原 bootstrap 归档之后；原压缩包、17/189项测试及其SHA清单保持原样。

独立审查发现：provider 的 capture 调用如果迟到，但返回的图像本身新鲜，原 `_sample(deadline=...)` 会在新鲜度检查后集成它，越过首帧阶段的8秒预算。整个mapping的操作lease可能仍有效，不能用它替代这一阶段预算。

现在在新鲜度检查之后、校准与SLAM.add之前再次检查显式deadline；迟到返回标为 `capture_timeout`，结束补采，不集成该曝光、不假报accepted。它仍不能抢占阻塞的第三方provider；能保证的是调用返回后不会把超预算曝光准入当前阶段。续段如仍有授权和安全预算，可再进入原本独立计数的安全环视。

新增一个虚拟时钟负例：capture在8.25秒才返回新鲜图像，整体lease还有余量；验证只捕获一次、SLAM保持0帧、深度拒绝不增加、结果是timeout。全部18个bootstrap测试通过（0.56秒），Ruff通过。无网络、ROS或物理动作。

`report.json`保存完整文件快照哈希和两个函数的精确源码哈希；`_sample.py.txt`、`_await_first_frame.py.txt`为函数原文，测试全文与JUnit/控制台输出一并保留。其他agent同期拥有同文件的operational-radius区域，故完整文件hash只是采集时点身份，不能冒充整个部署包已冻结。最终部署应另对完整source cohort取hash。

```sh
(cd docs/experiments/2026-10-28-user-workbench-e2e/exploration-audit/deadline-addendum && shasum -a 256 -c SHA256SUMS)
.venv/bin/python -m pytest -q robot/gateway/tests/test_survey_continuation_bootstrap.py
```
