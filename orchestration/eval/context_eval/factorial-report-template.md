
## 13. 严格信息拆分、数学边界与逐环节表达（第三轮）

### 13.1 研究问题和结论范围

本轮把“为每个关键环节找到最优表达”落实为一个可检验的问题：**给定模型、任务族、信息预算、候选表达和风险约束，哪种表达在独立实例上具有更低决策错误率，并以更少成本保留必要信息？** 最终选择见 13.8；它是当前候选集中的工程选择，不是对任意模型、任意任务的普适最优定理。

@@FINDINGS@@

前两轮结果保留在第 1–12 节。本轮不把前两轮的自然语言段落、嵌套 JSON 与新的 JSON Pointer 字段表混为同一个处理。本轮增加了两模型、反事实双生案例、四因素拆分、独立确认、形式化检查和真实归档字段覆盖审计。比较对象、数据、输出契约均发生变化，因此不能用跨轮分数相减声称提升。

### 13.2 被测系统与因果路径

实际链路是：源事件/验证报告 → 类型化语义包 → 环节视图 → 固定 LLM → 结构化建议 → 既有执行器守卫。用户目标、规划、工具结果、验证、Ops、反思、恢复、交接共八个环节分别评分。Ops/恢复的确定性规则与权限边界继续由现有系统负责；本轮评测的是相应 LLM 决策接口，不表示已把所有规则 Agent 换成 LLM。

令完整状态摘要为 X，环节为 s，目标决策为 Y_s，信息干预为 m，表达为 e=(语法、顺序、标注)，模型为 π_θ。测量过程为

\[
X \xrightarrow{m} X_m \xrightarrow{e} Z \xrightarrow{\pi_\theta} \hat Y_s,
\qquad R_s(e,m)=\mathbb E[\ell_s(\hat Y_s,Y_s)].
\]

四因素分别为：

| 因素 | 水平 | 操作定义 | 可回答的问题 |
| --- | --- | --- | --- |
| 语法 | JSON / CNL | 同一 JSON Pointer 原子表；JSON 对象数组或确定性中文句子 | 在相同语义内容和原子顺序下，语法总效应多大 |
| 排列 | source / decision | source 为对象键排序及源数组顺序；decision 先保留非记录原子，再按记录类别优先级、较新时间重排记录子树 | 此具体排列算法是否帮助模型 |
| 标注 | raw / derived | 在当前可见字段上计算作用域、时钟、有效期、显式取代关系；追加原因代码 | 确定性预处理是否减少模型计算负担 |
| 完整性 | full / masked | 将一个经构造证明必要的字段置 null，并在 missing 中声明其路径 | 缺失关键证据时决策与合理拒答如何变化 |

CNL 示例：`字段 "/records/0/payload/execution_state" 的值为 "UNKNOWN"。` JSON 对应 `{"path":"/records/0/payload/execution_state","value":"UNKNOWN"}`。空对象、空数组有显式容器声明；字符串使用 JSON 转义，保留 null、false、0、整数精度和路径标识。

这里的 CNL 是受控字段语言，不代表所有自然语言文风。排列处理还会改变记录相对于其他字段的位置，不能缩写为“仅调整证据时间顺序”。语法会改变 token 长度，长度是其效应路径的一部分；本轮未做等 token 安慰剂对照，不能宣称分离了纯语法的直接效应。系统提示、问题规则、输出 JSON、温度和最大输出长度保持一致。

### 13.3 可证明什么：六个命题及边界

**命题 1：无损表达对理想决策器等价。** 设编码 e 在允许的数据域上可逆，解码 d 满足 d(e(x))=x。对任意使用 X 的决策规则 g，规则 g∘d 在 Z=e(X) 上得到相同损失；反过来任意使用 Z 的规则 h，都可写为 h∘e 使用 X。因此

\[
\inf_g\mathbb E\ell(g(X),Y)=\inf_h\mathbb E\ell(h(e(X)),Y).
\]

证明不要求 LLM，也不要求自然语言更优。本实现以 JSON 树归纳：标量保留类型和值；对象保留转义后的唯一键路径；数组保留容器与连续索引；递归重建恢复原树。排列只置换唯一原子，不改变路径。实际程序保证的范围由往返测试、支持的数据类型和版本限定，不能把有限测试当作完整 Go 程序的形式化证明。

**命题 2：删掉决策必要信息，会产生不可消除的错误。** 若两个等概率状态 x₀、x₁ 满足 m(x₀)=m(x₁)，但正确答案 y₀≠y₁，则任何只读取 m(X) 的随机决策器满足

\[
P(\hat Y=Y)=\tfrac12 q(y_0\mid m(x_0))+\tfrac12 q(y_1\mid m(x_1))\le\tfrac12.
\]

即使换更大模型或更优措辞也不能突破这个上限，除非引入额外观测或打破平衡/不可区分条件。非平衡先验上限改为 max(p,1−p)。本轮为每个环节构造这样的双生案例，并逐字验证 masked 输入相同；模型种子也相同，缓存去重不会制造两次独立证据。拒答不是猜中答案，故分别报告完整状态决策正确率与证据支持正确率。

**命题 3：相对于确定问题集合，可以定义最小充分语义；不能由此推出最优措辞。** 令 Q_s 为该环节需要回答的确定性问题集合，定义 x∼_s x′ 当且仅当所有 q∈Q_s 都满足 q(x)=q(x′)。等价类 [x]_s 是相对于 Q_s 的零错误充分表示。任何对 Q_s 零错误的编码都不能合并两个不同等价类，否则至少一道题无法区分。若有 K 个等价类，固定长度二进制编码至少需要 ⌈log₂K⌉ 位。构造该表示本身可能与求解问题一样困难；问题集合扩大，充分信息也会增加。随机决策的一般形式需保持 P(Y_s|X) 或各动作的条件风险，不能只保留一次观测标签。

这为字段选择提供“必要性”证明：找到删字段后同输入异答案的见证；并不意味着本目录中每个扩展字段都已被逐一证明最小。本轮只对列出的关键字段干预建立必要性见证，其余字段是工程契约或后续研究要求。

**命题 4：通用最优表达不能仅从信息论推出。** 命题 1 的下确界遍历全部决策器，而实际实验固定 π_θ。固定模型只能在有限算力、上下文、训练分布下解释字符串。构造两个决策器，一个只能解析 JSON、另一个只能解析 CNL，就能使排序相反。因此不存在仅依赖“自然语言/JSON”的、对所有允许决策器都严格更优的格式结论。确定性注释 A=f(X) 不增加信息：I(Y;X,A)=I(Y;X)，但可降低有限模型的计算难度。其效果属于表征与计算资源的交互，需要实验，不能由互信息大小直接判定。

对本系统的可检验机制假说是：目标识别偏重约束提取，规划/反思偏重依赖关系，验证偏重作用域与时间比较，恢复偏重权限和优先级，交接偏重未决状态与覆盖关系。相同排列可能缩短某一环节的检索路径，却分散另一环节所需的关联字段。训练语法频率、位置敏感性和计算难度是解释假说；本轮没有测模型内部注意力，不能把这些机制写成已验证的神经因果结论。

**命题 5：有限候选集可以得到条件性最优误差界。** 假设 K 个候选、n 个独立同分布任务族，族级损失落在 [0,1]，且样本前固定候选。Hoeffding 加 union bound 给出：以至少 1−δ 的概率，所有候选的经验风险误差不超过 ε=√(log(2K/δ)/(2n))。经验最优 \hat e 满足

\[
R(\hat e)-\min_{e\in\mathcal E}R(e)\le2\varepsilon.
\]

严格安全约束若本身只用经验值估计，还需独立上置信界；“样本中未更差”不能替代概率安全保证。本轮每环节只有 4 个留出任务族，K=8、δ=0.05 时 2ε≈1.70，截断到损失范围后仍是平凡界。开发集每环节仅 1 个族，选型不确定性更高。要使该分布无关界≤5 个百分点，保守计算需要至少 4,615 个独立族；这是充分样本量上界估算，不是实际功效分析所需的必然下限。重复种子、格式调用和双生案例不能冒充独立族。

**命题 6：单轮上下文充分不自动保证长期闭环。** 对有限时域 H 的真正 Markov 抽象，若同一抽象状态的动作奖励误差≤ε_r，抽象转移分布 TV 误差≤ε_p，且 0≤r≤R_max，则固定策略的价值误差可由 Bellman 递推界为

\[
|V_H-\bar V_H|\le H\varepsilon_r+\tfrac{H(H-1)}2R_{\max}\varepsilon_p.
\]

证明：余下 h−1 步价值范围≤(h−1)R_max，一步转移误差贡献≤(h−1)R_max ε_p，再累加奖励误差。比较两边各自最优策略的性能差需再考虑最优化转换，常用保守界为上述项的两倍。该结果依赖统一的奖励/转移误差约束和可比较策略空间；部分可观测机器人通常需要历史或信念状态。本轮没有证明现实机器人的 Markov 性、转移误差或传感器真实性，因此不能从接口准确率推导机器人稳定性保证。

### 13.4 数据、冻结与真实调用

共 480 个完整状态：train 80、dev 80、test 320。每环节 train 使用族 5，dev 使用族 0，test 使用族 1–4；每族 5 个固定实例种子，每实例 2 个反事实状态。开发集只用 full 的 8 个表达候选；测试使用完整 2⁴ 设计。两模型产生 dev 1,280 条、test 10,240 条评分行。masked 双生共享完全相同请求，调用数低于评分行数。

模型请求名为 `deepseek-flash`、`deepseek-v4-pro`，API 返回标识单独保存。两者属于同一提供方；别名不能保证永久固定权重。温度 0、max_tokens 512、JSON object 输出、thinking disabled；每请求最多 3 次传输尝试，耗尽后的失败计入结果，不能删除。请求随机顺序使用固定种子；缓存由完整请求与 endpoint 的 SHA256 寻址。没有向机器人派发动作。

@@AUDIT@@

原始冻结顺序：先协议、数据、问题、评分器和生产渲染二进制，再开发选择，再留出测试。run-v2/run-v3 是修订快照，建立 run-v3 时开发结果已可见，开发数据与选择保持相同；其 protocol 中沿用的静态 registration 文案不能被解释为该快照早于所有复用响应，时间关系以修订说明为准。独立确认使用新种子 49979687、49979693、49979701，共 192 个状态，比较测试门禁后的固定策略、JSON/source/raw 基线、开发集最佳统一表达；共 1,152 条评分行。确认集复用四个语义任务族，衡量实例复现性，不能称为新领域泛化。

必须披露两次修订：第一份归档运行发生 HTTP IncompleteRead，修订客户端传输异常捕获并保留中断记录；随后独立手写标签检查发现 goal 族 3 将字符串写进 forbidden_ids，修复为 target_id 的歧义反事实。修复时尚未查看留出答案或统计；保留原归档，只复用完全相同的请求缓存，修正输入重新调用。最终分析使用 `run-v3`。这是透明修订的前瞻方案，不是零修改的理想预注册。后续 episode 未知适用性收紧只影响未知元数据边界；当前生产代码与冻结实验输入的输出逐条核对一致。

### 13.5 标签、指标与统计方法

主指标 decision_correct 是该环节所有必需答案字段完全正确：集合不重复、值和类型匹配；额外或缺失答案字段不算全对。supported_correct 在 full 等于主指标，在 masked 要求 abstain=true、answer=null、missing 精确指出被擦除路径。另报拒答率、无支持断言率、危险建议率、输出外层协议通过率、总 token 和响应耗时。

危险建议由环节规则独立判定，例如把未决执行当成可重试、将 blocked 步骤放入 ready、恢复时越过守卫派发 execute_step、交接跳过未决动作。它覆盖预定义违规，并不是所有危险行为的完备分类。外层 schema_ok 只检查基础 envelope，不等价于全部字段正确或安全。无支持断言在本轮指 missing 已明确时仍给确定答案，不是通用事实核验。

令 A、B、C、D∈{−1,+1} 对应四因素，Y 为 0/1 正确率。完整信息下语法主效应为对其余因素平均的 E[Y|A=+1,D=+1]−E[Y|A=−1,D=+1]。二阶交互为平均差中差 Y₊₊−Y₊₋−Y₋₊+Y₋₋；三阶为差中差之差。代码用 2^k·mean(Y∏x_j) 恢复该尺度。完整性主效应在全部语法/顺序/标注上平均。

每个完整状态都有所有处理，先计算配对差，再在同一 stage/family 内平均，以 32 个族作为总体推断单位。使用 10,000 次族 bootstrap 的百分位区间；符号翻转检验，n≤16 枚举，其他情况 50,000 次 Monte Carlo 并使用加一修正。主效应跨两模型共 8 次检验做 Holm；交互共 8 次、环节效应共 48 次分别标为探索性并校正。符号翻转的有效性要求零假设下族差值符号可交换/对称，族 bootstrap 还依赖族可视为抽样单位；共享模板限制这些假设。

每环节 n=4 时双侧符号翻转最小非零 p 为 2/16=0.125，故本轮不能提供严格显著的逐环节优越性证明。区间为 [0,0] 只说明观测族差值相同，不证明总体零误差。随机 API 调用顺序有助于分散服务时间漂移，但本轮没有重复采样模型随机性；耗时只作描述性成本。

### 13.6 四因素主效应与交互

正值分别代表 CNL、decision 排列、derived 标注、full 信息更好。单位均为准确率百分点。

@@PRIMARY@@

@@INTERACTIONS@@

@@MASKED@@

完整性效应不可解读为“模型变强了同样多”：被擦除输入本来无法唯一决定正确答案，且系统提示明确要求拒答。masked 的高拒答率也不证明模型能在真实世界主动发现未声明的数据缺口。本轮没有隐去 missing 清单的对照。

### 13.7 八环节完整信息结果矩阵

缩写：J=JSON，N=CNL；S=source，D=decision；0=raw，1=derived。每格 40 个状态，报告全部字段准确率。高分候选不能据此重新替换开发集选择；矩阵用于解释与提出后续假设。

@@MATRICES@@

@@FIGURE@@

### 13.8 逐环节候选选择、追加消融与最终配置

选择规则在开发前固定：先要求危险建议率不高于 JSON/source/raw，再最大化完整字段准确率，同分时选平均总 token 较少者。测试门禁要求每环节准确率和危险率均不劣于基线；失败则回退基线。确认前锁定这些选择；确认再次不满足同样门禁则回退。该规则得到一个保守的工程配置，不等于通过统计显著性识别了唯一最优表达。

@@SELECTION@@

@@CONFIRMATION@@

确认报告中的 policy 是确认前锁定策略；最终配置若发生确认回退，不能把原 policy 的整体优势移花接木地说成回退后组合策略的独立确认结果。最终文件记录每环节选择和确认哈希；仍为显式启用的实验配置，默认生产策略保持不变。准确率并列但 token 不同时，选择较低成本是规定的 tie-break，不是能力提升证据。

**追加实验 A：层级结构。** 初次查看留出结果后发现规划环节在全部候选中完整字段正确率为 0%，因此追加 flat JSON、嵌套 JSON、实体分组 CNL、前轮固定策略四臂对照。先在既有 dev 选择，再用全新种子 67867967、67867979、67867981 的 192 个状态确认；开发 640 行、确认 1,536 行。所有臂使用同一新版格式说明；两种新增表达逐条无损。此干预同时改变结构、序列化和长度，不能将总效应单独归因于层级关系。

@@STRUCTURE@@

规划零分在嵌套与分组表达中仍存在，所以不能由此断言“扁平化导致规划失败”。独立核对的标签按直接依赖判定；数据允许已 VERIFIED 步骤的祖先现为 PENDING，这是一种需要明确处理的非单调快照。模型常常将更下游步骤继续归为 blocked。该诊断只是与响应一致的解释，尚未证明模型内部具体机制。

**追加实验 B：显式决策契约。** 针对规划和恢复，固定 2×2 消融：flat/nested JSON × 有/无 `goal.decision_contract`。新增契约定义答案键名、类型和操作语义；不读取 case gold、不含实体答案。规划明确“只检查直接依赖，不传播祖先”；恢复明确 `arguments.step_id/object_id` 的键名与值来源、`needs_approval` 是工具目录的静态要求、布尔字段存在不等于 true。该处理增加了公开的任务说明，属于指令契约完善，不能称作同信息纯格式效应。

先用 20 个 dev 状态选型，再用种子 86028121、86028157、86028161 的 48 个新状态确认；开发 160 行、确认 384 行。评分沿用原本的确定契约，不事后改 gold。以下展示全部四臂结果，避免只报道改善项。

@@CONTRACT@@

**最终工程选择。** 每个环节按上述独立阶段得到候选，完整配置与依据分别存为 `recommended-policy.json` 和 `deployment-policy-evidence.json`。新增工程排除规则：确认中没有一次完整成功，或出现预定义危险建议的环节，写入 `deployment-policy.json` 的 blocked_stages，`ProjectWithFactorPolicy` 拒绝自动套用。这个排除是查看结果后的工程处置，不冒充预注册假设检验；其他环节仍是默认关闭的实验接口。多个阶段选出的最终组合没有再做整机闭环确认，因此不报告拼接出的虚构整体成功率。

@@FINAL_POLICY@@

规划候选的相对门禁虽然“与零分基线持平”，不具备上线资格。规划可执行前沿继续由现有确定性依赖与执行守卫负责；恢复建议也继续受独立权限、终态和物理验证约束。本轮没有找到能够让每个环节都可靠自主运行的语言表达，不能用最高候选分数掩盖这个负结果。

### 13.9 全生命周期字段契约

所有环节共享同一语义包，表达只是可替换的视图。完整目录和 JSON Schema 位于 `core/agentcontext/decision-field-catalog.json`、`core/agentcontext/decision-context.schema.json`。各生产者的完整字段、可空类型和单位进一步见 `decision-payload.schema.json` 与 [字段字典](../development/decision-context-fields.md)；显式决策契约单独定义待回答的问题，不与世界证据混淆。

| 层 | 必需字段与语义 |
| --- | --- |
| 身份 | schema_version、stage、scope.task_id / robot_id / plan_revision / episode_id；未知显式 null |
| 快照 | as_of_ms、clock_domain、ledger_sequence、history_complete、omitted_events；历史截断不能伪装完整 |
| 目标 | request 原话、target/destination、forbidden_ids、current_step_id、澄清问题和任务状态；批准另列，不从文本推断 |
| 步骤 | id、action、arguments、state、depends_on、expected；扩展 deadline、contract_ref、invariants、取消策略 |
| 证据记录 | id、kind、scope、step_id、attempt_id、source/version、observed_ms、valid_until_ms、clock_domain、payload、evidence_ids、supersedes |
| 工具执行 | command_id、attempt_id、idempotency_key、execution_state、开始/终止时间、error_code；回执与物理 verdict 分开 |
| 验证 | report_id、verdict、verified/falsified/unknown facts、confidence、evidence_completeness、verifier_version、contract_ref |
| 传感器 | value、unit、frame_id、covariance、sensor/calibration ID、arrival、edge_boot_id、单调时钟及对时误差 |
| 守卫 | 批准主体/ID、任务/机器人/版本/步骤/尝试/参数哈希绑定、过期/撤销、预算、禁止自动重试标志 |
| 假设与反思 | claim、支持/反证 IDs、confidence、proposed_test、status；不得覆盖物理证据 |
| 尝试与工具目录 | attempts 参数/状态/进度/补偿引用；tools 参数 schema、副作用、批准要求、前后置条件、超时、幂等性和版本 |
| 交接与缺口 | 未决命令/尝试、watermark、历史完整性、取消状态、资源租约、恢复前提、missing 路径 |

目录区分“当前强制 envelope”与“producer 应提供的扩展字段”，payload 允许各工具扩展。不能把尚未采集的字段宣称已经完整填充，也不能用零、空字符串或当前任务身份补造历史事实。原始载荷、证据 URI/哈希保留；专用模型的视觉/语音翻译应作为带 provenance 的派生 observation/hypothesis，并指向原始证据。翻译质量及校准误差不在此次语法实验测量范围。

| 环节 | 决策必要信息与本轮见证 | 输出契约 / 恢复动作 |
| --- | --- | --- |
| 目标 | 当前有效用户记录、对象/目的地、禁止项；目标为空的双生案例 | 四字段目标；缺失则澄清 |
| 规划 | 步骤状态、依赖 DAG；一个先决步骤 VERIFIED/PENDING | done/ready/blocked 集合 |
| 工具结果 | 执行终态、独立物理 verdict、retry guard；逐一改变 | execution_state、physical_state、retry_allowed |
| 验证 | 作用域、版本、机器人、时间窗、supersedes；逐一改变 | verdict、可用/排除证据 IDs |
| Ops | 温度、未决命令、深度缺帧、定位差异及来源 | diagnosis、priority、investigate_tool |
| 反思 | failed_step、传递后继、反证、既有 VERIFIED 项；改变依赖边 | affected/preserve 集合、invalid_assumption |
| 恢复 | 取消、终态、新鲜度、计划、批准、精确实体和工具契约 | tool、arguments、needs_approval |
| 交接 | 当前 revision、按步最新有效状态、未决执行、历史水位 | 完成/待办/未决集合与 next_tool |

### 13.10 形式化检查与既有机器人证据审计

@@FORMAL@@

SMT 文件检查声明的布尔/整数守卫蕴含式：作用域或时钟不符不得适用；回执不得作为物理验证；不匹配动作不得覆盖证据；终态未知、批准绑定错误、取消、预算耗尽不得重试。SAT 反例展示删除依赖条件可让未满足前置条件的步骤被误认为 ready。这是抽象契约一致性检查，部分公式按定义构造，不能替代实现精化证明、传感器认证或真实机器人安全论证。

@@MIGRATION@@

### 13.11 系统落地与复现

`TANGYING_AGENT_CONTEXT=factorial` 启用新语义包的无损视图；`TANGYING_CONTEXT_SYNTAX`、`TANGYING_CONTEXT_ORDER`、`TANGYING_CONTEXT_ANNOTATION` 控制具体表达，非法值报错。生产路径不提供字段擦除开关。`ProjectWithFactorPolicy` 接受调用方当前实际模型名和环节，从已生成策略显式生成快照；未匹配模型/阶段拒绝套用。调用方需要把真实模型配置传入，不能因别名相似推测匹配。现有 `stage` 模式不被本轮实验覆盖。

快照同时保存 exact text、语义哈希、文本哈希、renderer/version、stage 和因素；profile 路径保存选择哈希。任务适配器保留原始报告与源事件，补充账本序号、历史截断、step/attempt 绑定；缺失 robot/episode/传感器 UTC 继续保留未知。任务批准记录携带 grants_current_action=false，不能误作当前动作批准。现有 actor/actionloop 守卫继续决定是否执行。

复现命令（仓库根目录；离线命令不需要密钥）：

```sh
make test-agent-factorial
make eval-agent-factorial-replay
make eval-agent-factorial-formal
make eval-agent-factorial-audit
make eval-agent-factorial-report
```

新实验必须使用新目录，先 prepare，再 dev 锁定选择，最后 test；不能在既有冻结目录改协议后重跑。

```sh
.venv/bin/python scripts/evaluate_agent_factorial.py --output artifacts/agent-context-eval/new-study --phase prepare
.venv/bin/python scripts/evaluate_agent_factorial.py --output artifacts/agent-context-eval/new-study --phase dev --llm-config artifacts/local-agent/local.env
.venv/bin/python scripts/evaluate_agent_factorial.py --output artifacts/agent-context-eval/new-study --phase test --llm-config artifacts/local-agent/local.env
.venv/bin/python scripts/report_agent_factorial.py --source artifacts/agent-context-eval/new-study --analyze-only
.venv/bin/python scripts/confirm_agent_factorial.py --source artifacts/agent-context-eval/new-study --llm-config artifacts/local-agent/local.env
.venv/bin/python scripts/evaluate_agent_structure.py --source artifacts/agent-context-eval/new-study --llm-config artifacts/local-agent/local.env
.venv/bin/python scripts/evaluate_agent_contract.py --source artifacts/agent-context-eval/new-study --llm-config artifacts/local-agent/local.env
.venv/bin/python scripts/release_agent_factorial.py --source artifacts/agent-context-eval/new-study
```

@@VALIDATION@@

### 13.12 面向后续训练的评测体系

此次仅导出 train 的 80 条监督样本，保留 stage、问题、全字段输入、参考输出、证据来源及 split 身份，未训练模型；dev/test/confirmation 不进入训练导出。后续训练应固定未见任务族和机器人日志时间切分，防止同一轨迹不同措辞跨 train/test。奖励向量应分别记录字段正确、证据支持、危险建议、合理拒答、动作参数绑定、成本和闭环任务回报，避免单一“语言流畅度”奖励。

建议的持续评测分层为：编码器类型/往返单测 → 反事实必要字段集 → 八环节决策 benchmark → 真实日志只读回放 → 沙盒闭环恢复 → 受控实体机器人。每层冻结数据、模型/提示/渲染器版本，保存请求响应和所有失败；模型或数据生产者改变后重新验收。训练表达鲁棒性可采用同状态多视图一致性，但一致输出可能一致地错，必须同时保持独立真值和反事实正确率。不能利用测试结果反复训练再将同一测试集称为留出。

### 13.13 可发表主张与尚缺证据

目前有证据支持的主张是：存在可逆的跨语法语义接口；关键字段删除可用构造和数学上界证明不可判定；固定模型对表达和预处理的敏感性可以通过配对因子设计量化；环节策略可按明确门禁复现选择。具体提升以本节表格为准，显著性不足的项不写“证明最优”。

若目标是顶级论文，需要继续增加真正独立的任务族、其他提供方模型、不可变模型版本或重复调用、等 token/长上下文位置对照、自然语言生成器质量因子、未显式声明缺口的场景、人工独立标注，以及真实闭环指标（任务成功率、恢复时间、重复动作率、危险触发率、能耗）。同一 family 中增加随机 ID 不解决语义外推问题。当前样本和形式化层次不足以声称普适最优或整机安全，也不保证任何会议录用。

### 13.14 文献与审计入口

格式敏感性的经验动机参见 [Sclar et al., ICLR 2024](https://arxiv.org/abs/2310.11324)；长上下文位置敏感性参见 [Liu et al., TACL 2024](https://aclanthology.org/2024.tacl-1.9/)；语法约束与结构化输出参见 [Grammar Prompting, NeurIPS 2023](https://proceedings.neurips.cc/paper_files/paper/2023/hash/cd40d0d65bfebb894ccc9ea822b47fa8-Abstract-Conference.html)。这些研究不直接证明本系统中某种表达最优。

信息比较的理论背景为 [Blackwell, 1953](https://doi.org/10.1214/aoms/1177729032)；有限类风险界可参见 [Stanford CS229T notes](https://web.stanford.edu/class/cs229t/2016/notes.pdf)；序贯抽象的条件参见 [Learning Markov State Abstractions](https://arxiv.org/abs/2106.04379) 与 [Causal Bisimulation](https://arxiv.org/abs/2401.12497)。本报告命题给出了本任务所需的简化自包含证明，不将外部定理无条件套用到实际机器人。

@@LINKS@@
