# EcomEvo 产品体检与下一阶段路线

> 角色：高级产品经理 / Agent 产品负责人
>
> 目标：让 EcomEvo 从“技术上很强的自主运行时”变成“用户能稳定理解、信任、纠正并持续使用的生产产品”。

## 当前判断

EcomEvo 已经形成几条明确产品能力：多模态业务任务、EvoLoop + Dynamic Task Graph、EvoGain-APR Adaptive Posterior Routing、verifier leave-one-out counterfactual credit、Bayesian skill evolution、deterministic authority、Evidence & Authority 操作台和人工确认高影响动作。

当前最大问题不再是“Agent 是否能自主”，而是这种自主性是否能被持续证明、跨故障运行、被组织治理并形成真实业务效率。

## North Star

**Verified Decisions per Operator Hour（每人时完成的可验证决策数）**

硬护栏：Unauthorized side effects = 0；Evidence-gate bypass = 0。

配套指标包括 Median time to verifiable decision、Evidence-complete rate、Needs-evidence recovery rate、Replan success rate、Human correction rate、Correction-to-resolution time、Proposed → executed conversion、`uncertain` action rate、Cost per completed task、Resume-after-interruption success rate、Multimodal extraction failure rate、Routing posterior regret / baseline delta、Adaptive routing activation by domain、Tool reliability posterior drift 和 Skill promotion / retirement quality。

## P0

### Agent Gold Set + CI Eval Gate

建立 50–100 个高价值标注任务，覆盖 Evidence extraction、Tool routing、Verification、Final decision、Side-effect safety、Recovery / stagnation 和 Counterfactual routing credit sanity。每次 Runtime、Verifier、模型配置、posterior strategy 或 skill policy 变化都必须评估，并比较 cold-start prior policy 与 candidate posterior policy。

### Posterior Policy Promotion Gate

正式生产增加离线 promotion gate：同一 gold set、同一预算下比较 completion、evidence coverage、tool cost 与 safety，并用 bootstrap confidence interval 判断是否允许提高 adaptive activation ceiling。在线学习不能等价于“自动越学越敢”。

### Durable Execution

API → Durable Queue → Read-only Cognitive Worker → Checkpoint → Resume on any worker。Side-effect worker 与 read-only cognition worker 分离，每一步具备 idempotency key。

### Tenant / Identity / Approval Chain

明确 tenant isolation、user / reviewer / approver、per-tool permission、approval actor audit、credential isolation 和 SSO / gateway integration。

### 最新 adaptive head 完整回归

Release gate 包含 full pytest、latest-head concurrency pressure、malicious-controller pressure、browser visual regression 和 provider/MCP failure injection。

## P1

### Intent-first

用户先给目标，Runtime 识别业务场景，用户必要时修正。业务 taxonomy 应成为系统解释，而不是开始任务前必须掌握的字段。

### Structured correction / Evidence dispute

把“继续追证 / 检查反证”继续结构化为证据错误、证据缺失、规则不适用、结论过度推断和动作不合适，并支持标记证据不可靠、排除附件、替换过期资料和指定证据重验证。这些信号进入 eval dataset。

### Explainable runtime state

右侧控制面回答：现在在做什么、为什么调用这个工具、还缺什么、为什么停止、posterior 处于 shadow / transfer / adaptive 哪种状态，以及 routing uncertainty 是否异常升高。展示可审计策略摘要，不展示隐藏 chain-of-thought。

### MCP Connection Control Plane / Task Collaboration

企业用户需要管理数据源、read/write scope、evidence tags、health、latency、recent failure、idempotency 和 credential owner；任务需要 owner、watcher、reviewer、approver、comment / mention、handoff、decision export 和 audit export。

## P2

### Routing Quality Control Tower

按业务域持续观察 posterior samples、posterior residual、adaptive activation、baseline-vs-adaptive regret、tool reliability drift、evidence gain / tool call、cost / completed task、stagnation rate、tool-set diversity 和 failed-call rate。

### Off-policy Evaluation

收集 routing log 后增加 doubly-robust / replay evaluation，先在历史轨迹上评估候选 posterior policy，再决定是否扩大线上 activation。

### World-model Shadow Environment

研究用 open-weight agent world model / simulator 构造 MCP-like、browser / terminal / structured data 的 shadow enterprise environment，并注入 failure 和 tool schema mutation。Simulator 只产生训练/回放候选，不能替代真实 verifier 或业务 approval。

### Dynamic Tool Embedding

未来 MCP 工具规模变大时可从 Tool schema / description 生成 semantic tool embedding，再进入 learned contextual router；registry、sandbox、credential scope 和 confirmation gate 继续保持硬边界。

## Product Gate

EcomEvo 进入成熟生产平台定位前至少满足：真实业务 gold set 持续通过；adaptive policy 有 baseline promotion gate；跨进程任务可恢复；用户能纠正证据与结论；身份与审批链完整；每个业务域有结果指标与 routing quality 指标；主要企业接入不依赖工程师手改配置；routing / skill 可以学习，但 deterministic authority 从未被学习系统修改。
