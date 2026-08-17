# EcomEvo Architecture

EcomEvo 是一个面向真实电商业务的 **Autonomous & Self-Evolving Agent Runtime**。

它不是“给模型套一个工具循环”，而是把**认知自主、证据状态、任务拓扑、技能进化、业务权限、事件恢复和企业系统执行**拆成不同层。

核心架构原则：

> **认知自治，权限确定。**

模型可以越来越聪明、策略可以持续进化，但业务证据门槛、真实副作用和人工确认永远掌握在 deterministic authority plane。

---

## Architecture at a Glance

```text
┌─────────────────────────────────────────────────────────┐
│                    Product Workspace                    │
│ Conversation · Assets · Evidence · Actions · Progress   │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    Product Orchestration                │
│ Multimodal Fact Extraction · Provider Routing · Context │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                  Autonomous Cognitive Plane             │
│                                                         │
│  Goal / Belief                                          │
│      ↓                                                  │
│  Autonomous Controller                                  │
│      ↓                                                  │
│  Dynamic Task Graph                                     │
│  ├─ Tool Selection                                      │
│  ├─ Parallel Composition                                │
│  ├─ Cognitive Delegation                                │
│  ├─ Reflection / Replan                                 │
│  └─ Stagnation Detection                                │
└──────────────────────────┬──────────────────────────────┘
                           │ read-only evidence
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    Evidence Plane                       │
│ Local Tools · MCP Read Tools · Assets · Policy · Risk   │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                  Deterministic Authority Plane          │
│ Verifier · Governance Boundary · Sandbox · Cost Gate    │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                       Action Plane                      │
│ BusinessAction → Human Approval → MCP / Business System │
└──────────────────────────┬──────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│                    Experience Plane                     │
│ Event Store · Skill Distillation · Shadow Gate · Bayes  │
│ Quality-Diversity Archive · Meta-Evolution              │
└─────────────────────────────────────────────────────────┘
```

---

# Product Layer

## `frontend/`

三栏业务工作台：

- 左侧：业务入口与任务；
- 中间：持续对话、多模态资料和处理进度；
- 右侧：关键依据、任务状态、资料和待确认操作。

产品文案刻意强调“给目标，而不是给脚本”。用户不需要理解内部 Agent 拓扑，但需要清楚知道：

1. 系统现在知道什么；
2. 当前结论依据是什么；
3. 哪些信息还缺；
4. 下一步系统会做什么；
5. 哪些真实操作需要自己确认。

## `ecomevo/api/`

FastAPI / WebSocket API：

- conversations
- messages
- assets
- runtime events
- actions
- provider state
- MCP state
- realtime progress

API 层同时负责：

- 同任务 turn lease；
- stale turn recovery；
- stale action recovery；
- asset ownership；
- upload validation；
- SHA-256 integrity；
- safe action transition。

## `ecomevo/product/`

产品编排层：

- 文本/文档解析；
- 图片、视频、音频、扫描件事实提取；
- 多媒体事实缓存；
- 历史用户上下文；
- Provider 选择；
- 面向业务用户的回答编排。

多媒体模型先做**事实提取**，不会直接拥有业务结论权限。

产品层选中的 Provider 通过 task-local `ContextVar` 进入 Runtime：

- 并发任务不会串模型；
- 显式 `demo` 不会隐式出站；
- Runtime 和最终表达使用一致的本轮 provider context。

## `ecomevo/providers/`

模型适配层支持：

- OpenAI
- DeepSeek
- Qwen
- Doubao
- Anthropic
- Gemini
- Custom OpenAI-Compatible

Provider 是可替换 cognitive engine，不是业务 authority。

---

# Autonomous Cognitive Plane

## `runtime/autonomy.py`

`AutonomousController` 是核心自主循环。

职责：

- 创建并维护 Dynamic Task Graph；
- 读取 Goal / Belief / Verification；
- 召回 relevant skills；
- 调用 model controller；
- 组织确定性 fallback；
- 触发 replan；
- 跟踪剩余 budget；
- 识别 stagnation；
- 记录 autonomy events。

运行模式：

```text
Observe
→ Decide
→ Sanitize
→ Act
→ Review
→ Verify
→ Reflect
→ Replan / Stop
```

模型不可用时，系统仍然运行 deterministic fallback。

## `runtime/control_policy.py`

控制器策略边界。

模型可以提出候选：

- tool calls
- delegations
- reflection
- objective
- stop

但 `DecisionPolicy` 会重新检查：

- tool 是否真实注册；
- Sandbox 是否允许；
- cost 是否超预算；
- call 是否重复；
- 参数是否符合允许形态；
- delegation 是否超上限；
- 是否试图发起 side effect。

因此 model output 是**proposal**，不是 executable authority。

## `runtime/delegation.py`

动态认知委派。

系统拥有固定业务 baseline specialist，同时可以运行时增加：

- counter-evidence reviewer
- authorization reviewer
- timeline reviewer
- risk cross-check reviewer
- evidence contradiction reviewer

动态 specialist：

- 只读；
- 不产生独立业务证据；
- 不拥有 Tool Registry 权限；
- 不产生 BusinessAction；
- 不修改 Verifier。

这让团队拓扑可以变化，但权限拓扑不会变化。

---

# Evidence Plane

## `runtime/planner.py`

Planner 不再负责写死完整路线。

它负责：

- domain parsing；
- deterministic required evidence；
- budget baseline；
- evolution checks；
- 无模型 fallback plan。

即使 controller/provider 不可用，Planner 仍然提供最小安全能力。

## `runtime/tools.py`

工具层：

- media summarize
- evidence search
- policy lookup
- catalog inspect
- merchant inspect
- order inspect
- risk scan
- configured MCP read tools

`PTCExecutor` 支持 parallel-group execution。

模型只能选择 Tool Registry 中存在的工具。

企业 MCP 工具：

- 由服务端配置；
- 参数模板由服务端掌握；
- evidence tags 只有可信内部工具能提供；
- side-effect action 与 read tool 分离。

## Evidence Separation

系统明确区分：

- 用户陈述；
- 历史助手回复；
- 模型观察；
- 上传附件；
- 企业 MCP 数据；
- skill guidance；
- deterministic tool result。

只有满足 Verifier 规则的来源可以解除业务证据缺口。

---

# Deterministic Authority Plane

## `runtime/verifier.py`

Verifier 是最终证据 authority。

它检查：

- evidence completeness；
- current-question-specific requirements；
- domain constraints；
- risk evidence independence；
- side-effect safety；
- action consistency。

模型置信度不能覆盖 Verifier。

## `runtime/governance.py`

Governance Boundary 负责将验证结果转换为可以对外输出的业务边界。

业务动作只有在：

```text
verification.passed
AND evidence_complete
AND constraints_satisfied
```

后才允许被构造。

## `runtime/sandbox.py`

Sandbox 区分：

- read-only tools；
- side-effect tools；
- unknown tools。

未知或副作用工具不能因为模型提出就被执行。

---

# Action Plane

## BusinessAction

高影响动作不会由 Agent 直接执行，而是转换成明确的 `BusinessAction`：

```text
proposed
→ approved
→ executed
```

同时支持：

```text
rejected
failed
uncertain
```

## Human Approval

退款、下架、商家审核、风险升级等真实动作仍必须确认。

批准采用数据库 atomic compare-and-set，避免并发双击重复执行。

## Uncertain Outcome

如果下游网络在动作执行过程中中断，系统无法确认业务系统是否已经处理时：

```text
status = uncertain
```

并要求人工核对真实下游状态。

系统不会自动重放可能已经执行的副作用。

---

# Experience & Self-Evolution Plane

## `runtime/skills.py`

Persistent Adaptive Skill Library。

每个 skill 保存：

- domain
- niche
- name
- guidance
- preferred tools
- trigger terms
- shadow score
- alpha / beta
- uses
- wins / losses
- status
- source patch

Skill 生命周期：

```text
shadow
→ active
→ retired
```

## Bayesian Outcome Model

技能真实使用结果更新 Beta posterior：

```text
success → alpha + 1
failure → beta + 1
```

Skill retrieval 综合：

- posterior mean；
- shadow score；
- trigger relevance。

## Quality-Diversity Archive

Skill niche 基于：

```text
domain + trigger terms + preferred tools
```

同 niche 只保留更高质量代表。

这样避免：

- 重复失败不断制造重复技能；
- Prompt/guidance 无限增长；
- 历史坏经验永久存在。

## `runtime/evolver.py`

Evolution Pipeline：

```text
Experience
→ Diagnosis
→ Candidate Strategy
→ Safety Filter
→ Shadow Replay
→ Regression Gate
→ Archive
```

来源包括：

- failed traces；
- recovered successful traces。

模型可以帮助提出 candidate，但晋升条件由 deterministic code 决定。

## Meta-Evolution

每个 domain 独立维护：

- promotion threshold；
- retirement threshold；
- exploration。

真实任务表现会缓慢调整这些参数。

Meta-evolution 只能改变**探索策略**，不能改变**业务 authority**。

---

# Persistence Plane

## `runtime/event_store.py`

Event Store 提供：

- append-only events；
- WAL；
- transactional append；
- per-session sequence；
- SHA-256 hash chain；
- JSON snapshots；
- rollback；
- fork；
- replay；
- evolution patches；
- semantic fingerprint；
- concurrent evolution deduplication。

Runtime 因此可以：

- 恢复；
- 复盘；
- 审计；
- 蒸馏 experience；
- 验证事件链完整性。

---

# Data Flow

```text
User Goal + Assets
    ↓
Upload Integrity / Parsing
    ↓
Multimodal Fact Extraction
    ↓
Goal + Belief
    ↓
Verified Memory Recall
    ↓
Skill Recall + Evolution Policy
    ↓
Autonomous Controller
    ↓
Dynamic Task Graph
    ↓
Read-only Tools / MCP
    ↓
Deterministic Review + Dynamic Cognitive Delegation
    ↓
Verifier
    ├─ pass ────────────────┐
    └─ gap → Reflect/Replan │
                            ↓
                    Governance Boundary
                            ↓
                     BusinessAction
                            ↓
                      Human Approval
                            ↓
                     Business System
                            ↓
                      Runtime Events
                            ↓
                       Experience
                            ↓
                  Shadow-gated Evolution
```

---

# Safety Invariants

这些能力不属于进化空间：

- evidence completeness hard gate；
- upload integrity；
- Sandbox side-effect rules；
- human approval；
- action CAS；
- uncertain outcome protection；
- event hash chain；
- “model is not evidence”；
- “memory is not evidence”；
- “skill is not evidence”。

这使 EcomEvo 可以持续增加认知能力，同时保持生产权限可预测。

---

# Scaling Boundary

当前默认使用 SQLite：

优点：

- 部署简单；
- WAL；
- transactional append；
- 单机一致性清晰；
- 便于产品验证和单企业工作区。

临时压力测试中：

- 240 concurrent runtime runs；
- event chain failure = 0；
- side-effect leak = 0；
- duplicate semantic evolution patch = 0。

但 SQLite single-writer 仍然是横向扩展上限。

面向大规模生产，应把：

- events；
- runtime skills；
- task state；
- evolution state

迁移到支持多 writer、HA 和分布式协调的基础设施。

自主控制与自进化层本身不依赖 SQLite。

---

# Architectural Positioning

EcomEvo 的价值不在“绑定了哪个最强模型”。

它把真正应该沉淀在企业内部的东西从模型里拿出来：

- goal state；
- evidence state；
- task graph；
- tool network；
- permission boundary；
- business action state；
- runtime experience；
- skill archive；
- evolution policy；
- audit trail。

因此模型可以升级、替换甚至混用，而企业 Agent 的业务能力不会从零开始。

> **模型是认知引擎，Runtime 才是长期资产。**
