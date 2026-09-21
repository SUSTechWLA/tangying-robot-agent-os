# 地图看不见：三个同样的「只找一层」假设，以及一次地图清理

**日期**：2026-09-21
**触发**：打开 `http://127.0.0.1:8897/#mapping` 只看到覆盖率 62% 的老地图，
看不到更好的地图；有的地图点开是 not found。

结论先说：**没有一张 99% 覆盖率的地图存在**。99.5% 是评测台对一个**模拟巡检**打分的结果
（`scripts/exploration_survey_benchmark.py --truth gazebo`，用世界文件当真值），
它从来没有跑在真栈上，也没有产出过地图包。本文件记录的是另外两件事：
**为什么最好的那几张地图在控制台里根本看不见**，以及**清理后留下了什么**。

---

## 1 三个各自独立的「只找一层」假设

地图包有两种摆放方式：

```
artifacts/maps/<id>/                     ← 普通巡检（新地图写在这里）
artifacts/maps/furnished-home/<id>/      ← 参考场景的巡检（furnished-home-demo.sh 的 map root）
```

两种是同一种包。但有**三处代码**都只找第一种，而且三处的表现各不相同——
所以这个缺陷不是"坏了一个地方"，而是"同一句话在三层各错一次"：

| 位置 | 原来的做法 | 表现 |
| --- | --- | --- |
| `console/maps.go` `listMaps` | `os.ReadDir(root)` 只看根的直接子目录 | 分组地图**不出现在列表里** |
| `console/maps.go` `mapDir` | `filepath.Join(root, id)` | 按 id 请求 → **`MAP_NOT_FOUND`**，而地图就在磁盘上 |
| `gateway/map_catalog.py` `MapCatalog.open` | `(root / map_id)`，且要求 `directory.parent == root` | **启用**分组地图失败：`manifest.json does not exist` |
| `gateway/robot_workflow.py` `available_maps` | 遍历 `root.iterdir()` 再 `catalog.open(目录名)` | 分组地图对"复用还是重新巡检"的决定**不可见** |

第四处的失败方式最值得记：它拿**目录名**去问 catalog，而一个分组目录的名字
（`furnished-home`）**不是里面任何地图的 id**，所以查询必然落空，包被静默跳过。
于是"这栋房子该复用哪张地图"的答案永远是"没有可用的地图，重新扫一遍吧"——
而想要的那张图就在磁盘上。

**四处修法一致**：按 `manifest.json` **发现**包，而不是把根和 id 拼起来；
允许一层分组（`GROUP_DEPTH = 1`，三处同名常量互相引用注释）。
`MapCatalog.locate()` 仍然先要求 id 是**单个安全路径段**，再在解析后的路径上
做一次 `self.root in directory.parents` 检查，所以遍历尝试到不了文件系统。

### 1.1 顺带收掉的一个：id 非法与地图不存在被合并成同一个回答

`mapDir` 原来返回 `(string, bool)`，所以"这个 id 不是一个合法路径段"（调用方的错，400）
和"没有这张地图"（404）走同一条分支。修分组查找时把 `mapDir` 改成先要求 manifest 存在，
于是 `/v1/maps/absent` 从 404 变成 400——**一个已有的测试立刻抓住了它**。
现在 `mapLookup` 是三态（`mapFound` / `mapIDUnsafe` / `mapMissing`），
每个调用点都必须分别回答。

---

## 2 依赖地图的那张图：活跃地图被删掉时说什么

清理时发现 `artifacts/maps/active-map.json` 指向 `scan-1c9694d78142`，
而那张图正是要删掉的老地图之一。`_restore_active()` 当时把**所有**失败都吞进
`self.active = None`，于是控制台显示的是：

```json
{"ready": false, "localizationState": "unavailable", "gridUnavailable": true}
```

**一个症状，没有原因。** 而三种"没有活跃地图"不是同一件事：

- 从来没有定位过 → 无事可做；
- 指针指向别的标定 → 也不是故障，标定换代了；
- 指针指向的地图加载不了 → **部署事实，操作员可以换一张图**。

第三种现在会写进 `active_map_error` 并在 `navigation_map()` 里作为
`localizationReason` 发布。报"unavailable"的调用方会重试；报"这张图没了"的调用方会换一张。

---

## 3 地图清单：删了什么，留了什么，凭什么

判据是**仓库里有没有引用**，以及**这张包能不能用**（有没有 `navigation_grid` 与 `slam_session`）。
清理前的完整清单（含每个包的 sha256）保存在 `artifacts/map-inventory/before.json`。

| 包 | 可用 | 仓库引用 | 处置 |
| --- | --- | --- | --- |
| `furnished-home/scan-af0ba496987e` | ✓ | 实验报告 §6.4.4 + `semantic_seed_sensitivity.py` | **保留**（对比实验） |
| `furnished-home/scan-6974b8f937e9` | ✓ | 三篇实验报告（作为复现输入）+ `replay_sensor_log.py` | **保留**（对比实验） |
| `furnished-home/scan-3b9d237aaef8` | ✓ | `development/2026-09-18-system-review…` 作为"已保存并启用"的证据 | 第二轮清理时删除（见 §4.1） |
| `scan-1c9694d78142` | ✓ | 无（34.9% 已知） | 删除 |
| `scan-7e4d7e808cd0` | ✓ | 无（34.6% 已知） | 删除 |
| `sim-home` | ✗ 无 grid/session | `exploration_coverage_benchmark.py` 的示例路径 | 删除 |
| `sim-home-hi` | ✗ 无 grid/session | `development/2026-09-13-system-audit.md` | 删除 |
| `loadtest-1m` | ✗，且机器人是 `loadtest` | 无；`scripts/build_map.py --synthetic` 可复现 | 删除 |
| `stable/`（空目录） | — | 无 | 删除 |

`artifacts/maps` 从 45 MB 降到 15 MB。被删的三张"不可导航"的包是
**点云加一个 manifest，不是机器人能导航的地图**：它们出现在选择器里，
点开之后没有栅格可画、没有覆盖率可报——读起来像地图坏了，
实际是这张包从来没建完。

清理还把两件事挪出了地图根：`active-map.json`（指向已删地图，见 §2）和
`artifacts/map-inventory/stale-active-map.json`（留档）。

### 3.1 让选择器自己说出这件事

`console/maps.go` 的列表现在带 `hasNavigationGrid` / `hasSemantics` / `hasSlamSession` /
`activatable` 四个字段，`web/app.js` 在选择器里给不可导航的包加 `· 不可导航`，
`web/map_cloud.js` 新增 `mapIsUsable()`：**自动选择时优先可导航的包，但绝不隐藏唯一的那一张**
——一张不可导航的包打开后页面的解释，比一个空选择器有用得多。
列表里**没有**这几个字段时按可用处理，否则旧版控制台会因为缺字段而把好地图藏起来。

---

## 3.2 第四个"这一层"问题：CLI 拿到的会话令牌是过期的

修完上面三处、重启控制台之后，`build_sim_map.py` 的第一次运行成功了，第二次却拿到
`CONSOLE_SESSION_REQUIRED`——**对着一个活得好好的控制台**。

查下来：控制台进程活着、`console-address` 文件写着 `127.0.0.1:8897` 与它相符，
**但旁边的 `console-session` 里是一个别的进程的令牌**：

```
console-address : 127.0.0.1:8897      ← 匹配
console-session : EcC-NGyI…           ← 401
控制台实际令牌  : ri5nY_b…            ← 200（从 Set-Cookie 读出来的）
```

`resolve_token` 的规则是"哪个控制台"而不是"哪个文件最新"，这是对的；
但它把**地址文件匹配**当成了"哪个控制台"的答案，而地址文件旁边那个令牌只是
**这个答案的一份拷贝**——拷贝会过期。失败信息是 `CONSOLE_SESSION_REQUIRED`，
读起来像守卫坏了，而不是像手里拿着上一把钥匙。

**修法**：控制台本来就把令牌作为 `SameSite=Strict` cookie 发给**每一个 GET**
（这样浏览器永远不用知道令牌存在）。所以问控制台本人就是了——它才是权威答案。
`console_session.resolve_token` 现在在知道 base URL 时先 `fetch_live_token()`（读那个 cookie），
**文件降级为兜底**（给一个问不到任何东西的调用方用）。
实测：修好后同一条命令 `ok: True`。

契约测试加了三条：`fetch_live_token` 存在、cookie 名与 `console/guard.go` 的
`SessionCookieName` 一致、以及在 `resolve_token` 里**先问控制台再查文件**
（顺序反了就是"过期拷贝胜出"，这正是发生的事）。

---

## 4 补一张覆盖率真的高的地图

既然 99% 的地图从来不存在，就真跑一次。用 `scripts/build_sim_map.py --mode explore
--max-travel-m 60 --max-legs 3` 在一套隔离的栈上（`50361`/`8899`，
`TANGYING_MAP_ROOT` 指回共用的 `artifacts/maps`），标定目录与既有地图一致
（`c93b4e4d…`），所以新图像既有地图一样可以启用。

标定一致这件事是必须检查的：`MapCatalog` 会拒绝标定不符的包，
而"新扫的图在控制台里也是 not found"正是这一轮要修的那类问题。

---

## 4.1 第二张、也是最终要留的那张地图

`survey` 模式（沿委托路线 25 个航点、27.6 m）**比既有地图更差**：
`scan-e891b18026aa` 只有 56.4% 已知 / 47.4 m²。**"走一遍委托路线"不等于"把房子测全"**——
路线是给任务用的，不是给建图用的。

`explore` 模式（朝未知空间走，90 m 预算、3 段）才有效。它在第 3 段以 `no_progress` 结束
（没有还能揭示未知的前沿了），最终：

| 地图 | 已知 | 自由面积 | 点数 | 怎么来的 |
| --- | --- | --- | --- | --- |
| **`scan-d3112395d94c`** | **79.2%** | **71.9 m²** | 105,169 | 本次 `explore` 最终图 ← **保留** |
| `scan-f867c97a5489` | 66.3% | 60.2 m² | 65,223 | 同一次 `explore` 的第 1 段；被最终图取代，删除 |
| `scan-af0ba496987e` | 63.5% | 53.4 m² | 100,748 | 实验报告 §6.4.4 引用 ← **保留** |
| `scan-6974b8f937e9` | 62.1% | 53.3 m² | 92,170 | 三篇实验报告引用 ← **保留** |
| `scan-e891b18026aa` | 56.4% | 47.4 m² | 53,766 | 本次 `survey` 模式；比既有地图差，删除 |
| `scan-3b9d237aaef8` | 43.2% | 35.6 m² | 54,332 | 只被一份开发计划引用为"跑通过"的证据，删除 |

**对比用户看到的那张 62%**：已知率 62.1% → **79.2%**（+17 个百分点），
自由面积 53.3 → 71.9 m²（+35%）。

> 关于 `scan-3b9d237aaef8`：`docs/development/2026-09-18-system-review-and-improvement-plan.md`
> 用它作为"`build_sim_map.py` 在真栈上跑完并保存"的证据。那张图删掉了，
> 那份文档的陈述保持原样（写下时是真的），删除这件事记在这里而不是改写历史记录。

清理后 `artifacts/maps` 只剩三张，全部可导航、全部被引用或就是最高覆盖的那张。
清单（含 sha256）在 `artifacts/map-inventory/`：`before.json`、`after-exploration.json`。

---

## 5 验证

- `go test ./...` 全通；`gofmt` 干净；`make test-web` 477 passed（含新增的选择器测试）
- `robot/gateway/tests` + `tests/tool_layer` + `tests/test_repository.py`：1058 passed, 4 skipped
- 新增测试：
  - `console/maps_test.go`：分组目录里的包**既被列出也能按 id 打开**；分组目录本身不被当成地图；
    重复 id 只保留一张；列表如实报告可导航性
  - `robot/gateway/tests/test_map_catalog.py`：同一个 catalog 同时打开根目录里的包与分组里的包；
    `../`、`a/b`、空串等 id 一次都不碰文件系统
  - `robot/gateway/tests/test_map_selection.py`：库存列表能看到分组地图（"复用还是重扫"因此才有正确答案）
  - `robot/gateway/tests/test_robot_services.py`：活跃地图被删时**报告原因**，且三种"没有活跃地图"
    互不混淆
  - `web/map_cloud_test.mjs`：不可导航的包被如实标记，缺字段的旧列表不被误读为拒绝
  - `tests/docs/console_session_contract_test.py`：活控制台优先于它的文件副本，cookie 名与 Go 侧一致

## 6 这一轮没做的

- **没有把 `scan-900dbcd3337b`（那次 81% 的巡检）找回来**：磁盘上只剩它的 `manifest.json`
  与 `summary.json`（`artifacts/slam-exploration-coverage/`），包体本身不在了。
  想复现它，只能重跑一次同配置的巡检（本文件 §4 做的就是这件事）。
- **没有改探索算法**。这一轮只动"地图能不能被看见、能不能被用"和地图清单。
  覆盖率本身要再提高，是探索层的事（见 `docs/experiments/2026-09-20-slam-exploration-experiment-report.md`）。
- **79.2% 是整张栅格的分母**（`1 - unknownFraction`，与用户看到的 62% 同一个口径）。
  实验报告里的 99.5% 用的是另一个分母（`mapped / coverable`，只算"可达位姿真正看得见的地板"），
  两个数不可直接比较——这条差异在 `docs/experiments/README.md` 的引用注意里已经写过一次。
- **`os.WriteFile` 写完 `console-session` 之后没有任何校验**。这一次的修法是让客户端
  优先问控制台本人，所以过期的文件不再有害；但**为什么那个文件会过期没有被查到**
  （进程没有重启，地址文件正确，内容却是另一个进程的令牌）。这是一个仍然存在的未知，
  值得单独查一次：一个写了却没人验证的凭据文件，下一次可能以别的方式失效。
- **没有动 `TANGYING_MAP_ROOT` 的默认值**。分组目录仍在，三处代码现在都能找到里面的包；
  把参考场景的巡检搬到根目录是另一种整理方式，但那会改变 `furnished-home-demo.sh` 的行为。
