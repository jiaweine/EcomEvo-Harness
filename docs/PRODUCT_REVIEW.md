# EcomEvo 产品体检与下一阶段路线

> 角色：高级产品经理 / Agent 产品负责人
>
> 目标：让 EcomEvo 从“技术上很强的自主运行时”变成“用户能稳定理解、信任、纠正并持续使用的生产产品”。

## 当前判断

EcomEvo 已经形成几条明确产品能力：

- 多模态业务任务，而不是一次聊天；
- EvoLoop + Dynamic Task Graph；
- EvoGain-APR Adaptive Posterior Routing；
- verifier leave-one-out counterfactual credit；
- Bayesian skill evolution；
- deterministic authority；
- Evidence & Authority 操作台；
- 人工确认高影响动作。

当前最大问题不再是“Agent 是否能自主”，而是**这种自主性是否能被持续证明、跨故障运行、被组织治理并形成真实业务效率**。

## North Star

建议北极星指标：

**Verified Decisions per Operator Hour（每人时完成的可验证决策数）**

硬护栏：

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
- Routing posterior regret / baseline delta
- Adaptive routing activation by domain
- Tool reliability posterior drift
- Skill promotion / retirement quality

不要把“模型调用次数”“自主 step 数”当最终产品成功指标。

---

## P0：进入生产定位前必须解决

### 1. Agent Gold Set + CI Eval Gate

Adaptive routing 现在可以在线学习，因此 gold set 比静态 routing 时代更重要。

至少建立 50–100 个高价值标注任务，覆盖：

- Evidence extraction
- Tool routing
- Verification
- Final decision
- Side-effect safety
- Recovery / stagnation
- Counterfactual routing credit sanity

每次 Runtime、Verifier、模型配置、posterior strategy 或 skill policy 变化都必须评估。

需要同时比较：

```text
cold-start prior policy
vs
candidate posterior policy
```

而不是只看 candidate 自己的绝对分数。

### 2. Posterior Policy Promotion Gate

当前 Adaptive Posterior Routing 已有 shadow → adaptive 机制，但正式生产还应增加离线 promotion gate：

- replay gold set；
- same budget；
- compare completion / evidence coverage / tool cost / safety；
- bootstrap confidence interval；
- 只有统计上不退化，才允许提高 adaptive activation ceiling。

这样在线学习不是“自动越学越敢”，而是 safe policy improvement。

### 3. Durable Execution

当前任务 lease / recovery 很有价值，但 API 进程仍不等于 durable workflow engine。

生产目标：

```text
API
 ↓
Durable Queue
 ↓
Read-only Cognitive Worker
 ↓
Checkpoint
 ↓
Resume on any worker
```

Side-effect worker 与 read-only cognition worker 分离。

每一步具备 idempotency key。

### 4. Tenant / Identity / Approval Chain

生产前必须明确：

- tenant isolation；
- user / reviewer / approver；
- per-tool permission；
- approval actor audit；
- credential isolation；
- SSO / gateway integration。

“人工确认”只有在“谁确认”可靠时才成立。

### 5. 最新 adaptive head 完整回归

当前 counterfactual adaptive routing 已完成专项算法验证，但不能拿旧压力结果代替最新 head 完整回归。

Release gate 应包含：

- full pytest；
- latest-head concurrency pressure；
- malicious-controller pressure；
- browser visual regression；
- provider/MCP failure injection。

---

## P1：决定产品是否真的好用

### 6. Intent-first

长期入口应该是：

```text
用户给目标
   ↓
Runtime 识别业务场景
   ↓
用户必要时修正
```

业务 taxonomy 应成为系统解释，而不是用户开始任务前必须掌握的字段。

### 7. Structured correction

当前已有“继续追证 / 检查反证”。下一步把纠错结构化：

- 证据错误；
- 证据缺失；
- 规则不适用；
- 结论过度推断；
- 动作不合适。

这些信号应进入 eval dataset，而不是只成为下一条自然语言消息。

### 8. Evidence dispute

用户需要能够：

- 标记证据不可靠；
- 排除附件；
- 替换过期资料；
- 解释某字段为何不适用于当前案件；
- 对指定证据重新验证。

### 9. Explainable runtime state

右侧控制面最终需要始终回答：

1. 现在在做什么？
2. 为什么调用这个工具？
3. 当前缺什么证据？
4. 为什么停止？
5. posterior 处于 shadow / transfer / adaptive 哪种状态？
6. 当前 routing uncertainty 是否异常升高？

展示的是**可审计策略摘要**，不是隐藏 chain-of-thought。

### 10. MCP Connection Control Plane

企业用户需要 UI 管理：

- 数据源名称；
- read/write scope；
- evidence tags；
- health；
- latency；
- recent failure；
- idempotency；
- credential owner。

不应长期依赖工程师手改 JSON 环境变量完成主要企业接入。

### 11. Task Collaboration

持续任务需要真正的业务协作对象：

- owner；
- watcher；
- reviewer；
- approver；
- comment / mention；
- handoff；
- decision export；
- audit export。

---

## P2：形成长期护城河

### 12. Routing Quality Control Tower

按业务域持续观察：

- posterior samples；
- posterior residual；
- adaptive activation；
- baseline-vs-adaptive regret；
- tool reliability drift；
- evidence gain / tool call；
- cost / completed task；
- stagnation rate；
- tool-set diversity；
- failed-call rate。

如果某个 domain residual 突然上升，应该能自动提示：可能出现新业务分布、工具 schema 变化或 provider candidate distribution 变化。

### 13. Off-policy Evaluation

收集 routing log 后，可以增加 doubly-robust / replay evaluation，先在历史轨迹上评估候选 posterior policy，再决定是否扩大线上 activation。

重点不是追求“纯 RL”，而是做到：

> 学得更快，但比直接在线 exploration 更安全。

### 14. World-model Shadow Environment

可以研究用 open-weight agent world model / simulator 构造 shadow enterprise environments：

- MCP-like tool response；
- browser / terminal / structured data interactions；
- failure injection；
- tool schema mutation。

但 simulator 只能产生训练/回放候选，不能替代真实 verifier 或业务 approval。

### 15. Dynamic Tool Embedding

当前 routing context 仍包含人工定义的 evidence channel 语义。

未来 MCP 工具规模非常大时，可以研究：

```text
Tool schema / description
   ↓
semantic tool embedding
   ↓
learned contextual router
```

但 registry、sandbox、credential scope 和 confirmation gate 保持硬编码。

### 16. Organization Knowledge

成熟后增加：

- 组织级任务模板；
- 审核规则包；
- evidence checklist；
- domain verifier profile；
- 团队级 read-only skill；
- approved routing policy snapshot。

这些组织配置都不能降低平台硬安全门槛。

---

## Product Gate

当以下条件同时成立，EcomEvo 才适合从“先进 Agent Runtime”进一步定位为成熟生产平台：

1. 真实业务 gold set 持续通过；
2. adaptive policy 有 baseline promotion gate；
3. 跨进程任务可恢复；
4. 用户能明确纠正证据与结论；
5. 身份与审批链完整；
6. 每个业务域有结果指标和 routing quality 指标；
7. 企业接入不依赖工程师手改主要配置；
8. routing / skill 可以学习，但 deterministic authority 从未被学习系统修改。
