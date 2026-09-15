# 仓库整理：文档、证据、宣传与代码各归其位

**问题**：`docs/` 该不该按类型分组？`artifacts/marketing/` 放在 `artifacts/` 下合适吗？文档、模型、代码要不要分类管理，怎么让新人更快上手？

**做法**：先审计现状，再只做**可验证**的整理——本仓库自带文档链接检查（`tests/docs/test_production_docs.py`）与顶层区域归类检查
（`tests/install/test_start_all.py`），所以任何一次搬动都能当场验证。

---

## 一、整理前的问题（实测）

| 现象 | 证据 |
| --- | --- |
| `docs/` 根目录散着 16 篇文档 | 架构、协议、中间件、多机、部署、放行、安全、上手混在一起，与 `architecture/`、`operations/`、`guides/`、`install/` 四类子目录职责重叠 |
| 索引的"按任务阅读"表被逐轮升级记录挤满 | 25 行里 12 行是 `development/2026-09-xx-*` 的升级/审计记录，新人第一眼看不出主线 |
| `artifacts/` 的边界没人写清 | 21 个子目录里 7 个有入库文件，既有**脚本产出的证据**（基准、事故、验收），也有**人工撰写的宣传材料**（66 个文件） |
| 顶层新增代码区域没有归类要求 | `latency/`、`incidents/` 新增后，部署文档里没有它们的位置 |

---

## 二、`docs/` 归类：16 篇各归其位

| 新位置 | 从根目录搬入 |
| --- | --- |
| `docs/architecture/`（系统内部是怎么搭的） | architecture、protocols、middleware、agent-v1、orchestration、distributed-agentos、multi-robot、fleet-cloud、fleet-paper-loop |
| `docs/operations/`（怎么放行、怎么不出事） | deployment、production-readiness、safety-checklist、robocasa-handoff |
| `docs/guides/`（怎么用） | quickstart、user-console |
| `docs/install/`（怎么装） | xlerobot-setup |

`docs/` 现在只有 `README.md`（索引）留在根目录 ✓。**移动本身不值一提，值钱的是搬完仍然成立**：

- 116 处链接被重写（出向 + 入向 + 跨移动文件 + 非 markdown 目标如 `../proto/robot/v1/robot.proto`）；
- `tests/docs/test_production_docs.py::test_every_documentation_link_and_make_target_resolves` 通过；
- `tests/test_repository.py` 里对 `docs/architecture.md`、`docs/middleware.md` 的引用同步更新；
- 代码与脚本里的 4 处引用（`scripts/start-all.sh`、`tests/install/test_start_all.py`、`cmd/edge-worker/main.go`、`scripts/calibrate_xlerobot.py`）同步更新。

> 第一次尝试只改了"指向被移动文件"的链接，被链接检查器当场抓出**被移动文件自己指向旧兄弟目录**的那批 ✗，回退重做。这就是"有检查器"和"没有"的区别。

## 三、索引重排：新人一条路走到底

`docs/README.md` 现在是这个结构：

1. **当前主线**（一段话讲清机器人现在能做什么、怎么启动）；
2. **按任务阅读**（25 行，只留主线任务）；
3. **新人第一小时**（新增）：10 分钟看主张 → 20 分钟跑起来 → 10 分钟看一次完整任务的证据 → 15 分钟读架构与闭环契约 → 15 分钟改第一行代码；
4. **仓库地图**（新增）：每个目录放什么、谁维护、会不会入库；
5. **升级与审计记录（按日期）**（新增，12 行）：把逐轮优化记录从任务表里摘出来，新人在第 3 步之后再看；
6. 当前参考资料 / 对外宣传材料 / 其他路线 / 历史与设计档案（原有）。

根 `README.md` 也加了一行指路：第一次来先走"新人第一小时"。

## 四、`artifacts/marketing/` 放这里合适吗

**先回答"`artifacts/` 一般放什么"**：通行约定是**构建/运行产物**——可重新生成、通常不入版本库。本仓库沿用这个含义，但把范围写实了：

> `artifacts/` = **不参与产品构建与测试的非产品材料**。两类东西都放这里：**脚本产出的证据**（地图、基准、验收、事故记录）与**人工撰写的对外材料**（宣传）。共同点是"不进构建、不被服务读取"。

| 放进 `artifacts/` | 理由 |
| --- | --- |
| `maps/`、`maps-archive/`、`calibration/`、`sim-stack/`、`training/` | 运行时生成，已被 `.gitignore` 排除 |
| `acceptance/`、`robocasa-harness/round*`、`closure-verification/` | 验收证据：**证明曾经发生过什么**，按需入库 |
| `semantic-benchmark/`、`destination-policy/`、`slam-exploration-coverage/`、`incidents/` | 基准/对照/事故的**结论性小文件**入库，大的生成物排除 |

**营销材料确实是个边缘情况**：它是**人写的**，不是脚本生成的。所以严格按"artifacts=生成物"的口径，它该在顶层 `marketing/`。**本仓库选择保留在 `artifacts/marketing/`**，理由有三条，都是工程理由而不是习惯：

1. 它已经被写进规则：`docs/README.md` 明确"不是产品文档，也不参与构建与测试"，与 `artifacts/` 的定位一致；
2. 引用稳定：根 `README.md` 的系列目录、`docs/README.md`、各期之间互相引用（`../小红书长文-01-总体架构.md`）都指向这个路径，搬一次要改一串；
3. 它按"一期一目录"组织，与 `artifacts/` 下其他"一类一目录"的风格一致。

**如果你更想要物理分开**：顶层 `marketing/` 是等价的放法，`git mv artifacts/marketing marketing` 加上本文档提到的链接改写步骤即可——**代价就是那 116 处链接的同类重写**（链接检查器会告诉你漏了哪）。这条写在 `docs/README.md` 里，未来谁想改都能照着做。

## 五、模型相关的东西放在哪（此前没有一处写清）

| 东西 | 位置 | 入库 |
| --- | --- | --- |
| 训练产物 / 检查点 | `artifacts/training/` | 否（`.gitignore` 排除） |
| 策略契约与推理服务 | `policy/sidecar/`（HTTP 服务 + 版本化 manifest + 权重哈希） | 是（代码） |
| 策略引用 | 任务参数 `policy_execution`：`framework`/`artifact_sha256`/`manifest_revision`/`inference_id` | 随任务记录 |
| 仿真场景与机器人资产 | `sim/mujoco/assets/`、`web/assets/scenes/` | 是（代码与测试直接依赖） |
| 标定结果 | `artifacts/calibration/`（运行产物）+ 地图产物里的 `calibrationRevision` | 否/随地图 |

一句话：**权重不进仓库，进仓库的是"用哪个权重"的契约与哈希**——这样才能复现"这次任务用的是哪个模型"。

## 六、顺手修掉的一个真 bug（仓库检查自身）

`tests/install/test_start_all.py::test_every_top_level_source_area_is_classified` 会枚举顶层目录并要求每个都在部署文档里归类。它用 `git ls-files` **按行**解析，而 git 默认给含非 ASCII 的路径加引号（`core.quotePath`），于是中文目录名解析出伪顶层目录 `"artifacts` ✗——一个**任何文档都无法归类**的区域。改用 `git ls-files -z` 按 NUL 切分修掉。这个 bug 在营销目录改成中文命名后就会让检查失败，属于"检查器本身也需要被检查"的例子。

## 七、验证

| 检查 | 结果 |
| --- | --- |
| 文档链接与 make 目标 | `tests/docs/` 5 通过 |
| 顶层区域归类 / 发布树 | `tests/install/` 154 通过 1 跳过 |
| 仓库不变量 | `tests/test_repository.py` 通过 |
| 全量 Python（除安装类） | 1478 通过 / 28 跳过 |
| Go / Web / lint | 全包通过 / 375 通过 / `make lint` 干净 |

