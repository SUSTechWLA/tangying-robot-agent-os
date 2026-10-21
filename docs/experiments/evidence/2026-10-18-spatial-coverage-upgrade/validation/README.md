# 工程验证原始结果与修复

完整状态见 `resolved-checks.json`，原始退出码保留。`python-all.log` 初轮为3713 passed/1 failed/40 skipped/20warnings，唯一失败是README前2500字缺快速启动命令。研究段落移至启动例子之后，`readme-regression-attempt02.log` 中整文件15项通过；早期单项修复探针失败也保留。不得将原始exit1改写成0。

`runtime-boundary.log` 为独立2项通过；`final-focused.log` 为128项通过，包括全量收集后新增的范围拒绝测试。各回归集合有重叠，不能将次数直接相加成独立测试数。

`lint.log` 记录scope条件SIM102初次失败，`lint-attempt02.log` 为最终通过。`frozen-vs-final-source.json` 记录格式变更前后源码身份及本机Python step字节码/名称表一致；冻结研究数据保留，最终plan/fixture另行重跑。

Go维护包、build、generate-check分别退出0。`reproduction-status.json` 保存压缩原件临时解压后的8次校验：开发140、留出210、4个预测计划和2个Runner fixture均通过，临时解压目录已清除；记录中的/tmp路径仅是已完成验证的临时位置。

所有测试和实验均与新增硬件/ROS2/Gazebo运动无关。已有跳过项、警告、原始失败和修复历史均保留，不用重写旧结果制造全量首轮通过。
