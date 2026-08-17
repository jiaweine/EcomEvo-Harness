# EcomEvo 产品体检与下一阶段路线

> 角色：高级产品经理 / Agent 产品负责人
>
> 目标：让 EcomEvo 从“技术上很强的自主运行时”变成“用户能稳定理解、信任、纠正并持续使用的生产产品”。

## 结论

EcomEvo 的核心差异化已经成立：多模态证据、受控自主循环、确定性权限边界、可追溯执行和技能进化是一套完整骨架。当前最大的风险不再是“Agent 能不能自主”，而是三个产品化缺口：

1. **质量是否可持续证明**：自主重构后缺统一 gold set 与 CI eval release gate。
2. **任务是否真的可长期运行**：当前服务内后台任务不是跨进程/跨故障域的 durable execution。
3. **用户是否能形成信任闭环**：有证据与审批，但“纠错、反证、反馈、为什么停止”还不够产品化。

## North Star

建议北极星指标：

**Verified Decisions per Operator Hour（每人时完成的可验证决策数）**

必须同时满足两个护栏：

- Unauthorized side effects = **0**
- Evidence-gate bypass = **0**

配套指标：

- Median time to verifiable decision
- Evidence-complete rate
- Needs-evidence recovery rate
- Replan success rate
- Human correction rate
- Correction-to-resolution time
- Proposed → executed conversion
- `uncertain` action rate
- Cost per completed task
- Resume-after-interruption success rate
- Multimodal extraction failure rate

如果没有这些指标，团队会很容易优化“Agent 看起来更聪明”，而不是优化真实业务结果。

## P0：合并/生产前必须解决

### 1. 建立 Agent Gold Set + CI Eval Gate

当前压力测试能证明并发与权限安全，但还不能回答：

- 同一真实业务案例改模型后，结论质量是否退化？
- 多模态抽取变化后，证据召回是否下降？
- EvoGain 调权后，工具成本降低的同时有没有漏掉关键证据？
- 新技能进入 archive 后，是否改善正确率而不是只增加调用？

建议建立 50–100 个高价值标注任务，覆盖五个业务域和典型失败模式。拆成：

- Evidence extraction eval
- Tool-routing eval
- Verification eval
- Final decision eval
- Side-effect safety eval
- Recovery / stagnation eval

每次 Runtime、Verifier、模型配置、技能策略变化都必须跑。

### 2. 把长任务从进程内后台执行升级为 Durable Execution

当前任务 lease / recovery 很有价值，但如果任务仍依赖单个 API 进程执行，进程重启、滚动发布、节点故障仍会中断真实 Agent 工作。

生产目标应是：

- API 接受任务后只负责持久化与入队；
- Worker 可跨进程恢复；
- 每一步有 idempotency key；
- side-effect action 与 read-only reasoning 分离队列；
- checkpoint 后任一节点可继续；
- 部署不应等价于“杀掉正在运行的 Agent”。

### 3. 完成自主重构后的全量回归

历史基线不能替代当前结果。PR 在进入 ready-for-review 前需要完整测试矩阵重新跑一遍，并对新增自主路径补回正式测试，而不是长期依赖临时专项脚本。

### 4. 明确生产身份、租户和角色权限

“人工确认”只有在确认者身份可靠时才有意义。生产前需要明确：

- tenant isolation
- user / reviewer / approver roles
- per-tool permission
- approval actor audit
- credential isolation
- SSO / gateway integration

这是产品权限模型的一部分，不只是部署文档。

## P1：决定产品是否好用、可信、可规模化

### 5. 把纠错变成一等交互

AI 产品不能只有“复制答案”。本轮 UI 已补：

- **继续追证**：要求继续寻找能改变判断的关键证据；
- **检查反证**：主动尝试推翻当前结论。

下一步应把用户纠错结构化：

- 证据错误
- 证据缺失
- 规则不适用
- 结论过度推断
- 动作不合适

这些信号应进入 eval 数据和 skill learning，而不只是成为下一轮自然语言。

### 6. 重新设计首次任务的“场景选择”

当前左侧先让用户选业务场景，容易把内部 taxonomy 暴露给用户。长期更好的入口是：

**先给目标 → Runtime 自动识别场景 → 用户可修正**

场景应该是系统对任务的解释，而不是用户必须先知道的系统字段。

### 7. 右侧控制面需要回答三个问题

当前轨迹已经优于普通聊天 UI，但还应该始终回答：

1. **现在在做什么？**
2. **为什么做这一步？**
3. **为什么停止 / 还缺什么？**

建议逐步增加：

- evidence completeness
- remaining uncertainty / missing evidence
- cost budget used / remaining
- current strategy / recovery reason
- stop reason

不要展示隐藏思维，只展示可审计状态摘要。

### 8. MCP / 企业数据连接不能只停留在环境变量

企业用户需要一个连接控制面：

- 数据源名称
- 权限范围
- 可调用只读能力
- 最近健康检查
- 最近失败
- evidence tags
- 写操作是否配置 idempotency

否则“平台能力”仍然需要工程师手工配置，产品无法自助落地。

### 9. 增加协作对象，而不是只分享 URL

真实治理/售后/风控任务通常多人参与。建议增加：

- owner
- watcher
- reviewer / approver
- comments / @mention
- decision summary export
- audit timeline export
- handoff state

这样“持续任务”才真正成为业务工作对象，而不只是持久聊天。

### 10. 设计正式的证据争议流程

用户应该能：

- 标记某条证据不可靠；
- 排除附件；
- 替换过期材料；
- 解释某字段为什么不适用于本案；
- 要求 Runtime 重新验证而不是从头问一次。

## P2：形成长期护城河

### 11. 建立产品质量控制塔

对业务域持续看：

- 完成率
- 补证率
- 平均重规划次数
- 工具成本
- 延迟
- 人工纠错率
- action uncertainty
- skill promotion / retirement
- 模型路由分布

不要把“模型调用次数”当产品成功指标。

### 12. 任务模板和组织知识

成熟后可以提供：

- 组织级任务模板
- 审核规则包
- 证据清单模板
- 业务域自定义 verifier profile
- 团队级只读 skill

但这些配置都不能降低系统硬安全门槛。

## UI 本轮修复原则

### 字体

- 去掉 Aptos / Display 与 CJK fallback 混搭；
- 中文标题和正文统一同一 UI family；
- 字重只使用 400 / 500 / 600 / 700；
- 中文 metadata 不再套 Latin mono；
- 产品可见文字原则上不低于 10.5px，正文/支持说明尽量 >= 11.5px；
- 标题降低过度负字距，避免中文被压扁。

### 动效

只保留能解释状态的动效：

- 首次进入：层级式 reveal，帮助读取顺序；
- 新消息：短距离进入，说明新增内容；
- 当前进度：轻呼吸，说明“正在做”；
- 工作进度条：低强度移动高光，说明仍在运行；
- 面板 / modal：短淡入和轻位移，保持空间连续性；
- `prefers-reduced-motion`：全部降级。

禁止：循环浮动卡片、无意义粒子、弹簧 bounce、长时间背景动画。

## 建议的 4 个迭代节奏

### Sprint A — Release Confidence

- 全量回归
- Gold set v1
- CI eval
- durable worker 方案与最小实现
- auth / approval actor 明确化

### Sprint B — Trust & Correction

- structured correction
- evidence dispute
- why / stop reason
- evidence completeness
- 真实用户可用性测试

### Sprint C — Enterprise Operation

- MCP connection control plane
- owner / reviewer / approver
- audit export
- task handoff

### Sprint D — Learning Flywheel

- correction → eval dataset
- production trace sampling
- skill promotion quality dashboard
- per-domain model / routing experiments

## Product Gate

当以下条件同时成立，EcomEvo 才应该从“先进 Agent Runtime”升级为“生产级 Agent 产品”定位：

1. 真实业务 gold set 持续通过；
2. 跨进程任务可恢复；
3. 用户能明确纠正证据与结论；
4. 身份与审批链完整；
5. 每个业务域有可观测的结果指标；
6. 不需要工程师修改环境变量才能完成主要企业接入；
7. 权限安全仍保持 deterministic authority。
