# EcomEvo Architecture

## System thesis

EcomEvo 把商业 Agent 拆成七个相互制约的平面：

```text
Experience Plane   → 任务、对话、多模态输入、证据、执行控制
Cognitive Plane    → EvoLoop / Dynamic Task Graph / Delegation
Routing Plane      → EvoGain-APR / posterior / UCB / counterfactual credit
Evidence Plane     → 本地解析 / 多模态事实 / MCP read tools / Event Store
Authority Plane    → Verifier / Sandbox / Governance Boundary
Action Plane       → BusinessAction / Approval / Idempotent downstream execution
Evolution Plane    → Bayesian Skills / Shadow Gate / trajectory distillation
```

核心原则：**认知策略可以学习，权限边界保持 deterministic。**

---

## Experience Plane

### `frontend/`

三块工作面：

- 左侧：业务场景与历史任务；
- 中间：目标、多模态输入、任务对话与结果；
- 右侧：轨迹、证据、执行控制与资料。

产品 UI 只展示 EcomEvo 自己的能力，不把底层 provider/model 品牌作为产品结构。

### `ecomevo/api/`

负责 FastAPI、WebSocket、conversation/message/asset、upload validation、task lease、action confirmation、interrupted-turn recovery、安全响应头和静态工作台。

### `ecomevo/product/`

负责文本/PDF/Office/sheet 解析、多模态事实提取、semantic cache、历史用户上下文和面向业务用户的结果编排。

多模态模型只做事实读取，不拥有业务处置权。

---

## Cognitive Plane

### `runtime/autonomy.py`

实现基础 EvoLoop：

```text
Observe
  → Decide
  → Route
  → Act
  → Review
  → Verify
  → Reflect / Replan
  → Stop
```

每个任务创建 Dynamic Task Graph。Graph 随真实证据与失败状态增长，不是提前写死的流程。

### `runtime/control_policy.py`

定义模型候选的安全清洗边界：

- Tool Registry 存在性；
- Sandbox 权限；
- server-owned args；
- duplicate call；
- remaining budget；
- per-step call cap；
- low-gain stop。

语言模型输出只被视为 candidate cognition。

### `runtime/delegation.py`

固定 specialist 之外允许动态 cognitive delegation。

动态 specialist 只能 read-only、evidence-bound，不能拥有 BusinessAction authority，也不能修改 verifier。

---

## Routing Plane

### `runtime/adaptive_routing.py`

实现 EvoGain-APR 的核心 posterior state：

- 12 维可审计 routing context；
- cold-start Gaussian prior；
- global posterior；
- per-domain posterior；
- deterministic UCB；
- shadow → adaptive activation；
- non-stationary decay；
- residual drift；
- tool reliability Beta posterior；
- routing outcome persistence。

当前主要 routing context：

```text
coverage
source authority
skill posterior support
novelty
counter-evidence value
purpose specificity
tool reliability
cost pressure
same-round redundancy
evidence gap pressure
recovery context
```

这些只影响 read-only routing，不进入 Business Verifier 的证据分数。

### `runtime/counterfactual_routing.py`

生产 `EcomEvoEngine` 直接使用 `CounterfactualAdaptiveAutonomousController`。

它在每次 verification 后，对本轮 adaptive-selected 工具做 deterministic verifier leave-one-out：

```text
all results
   ↓ verifier
full potential

all results - selected result i
   ↓ verifier
counterfactual potential

marginal difference / (1 + cost)
   ↓
routing posterior update
```

这样 credit 来自工具对“可验证状态”的边际贡献，而不是模型自评或手工 reward 权重拼接。

Specialist prose 被排除在这次 counterfactual credit 之外。

### Routing persistence

同一个 Runtime SQLite 中新增：

```text
routing_policy
routing_outcomes
routing_tool_stats
```

`routing_policy` 保存 global/domain sufficient statistics、sample count、reward/residual EWMA。

`routing_outcomes` 保存工具、feature vector、credit method 和 outcome audit。

`routing_tool_stats` 用 Beta posterior 单独建模工具运行稳定性。

---

## Evidence Plane

### `runtime/tools.py`

本地只读工具包含媒体状态整理、证据搜索、规则查询、商品核验、商家核验、订单核验和风险扫描。

企业 MCP read tools 可以声明可信 `evidence_tags`，但参数模板仍由服务器配置控制。

大文本采用 bounded index + truncated raw stream search。图片、视频关键帧、音频与扫描文档通过语义通道转成带来源、置信度和 asset id 的事实，再进入统一 verifier 路径。

### `runtime/event_store.py`

SQLite WAL append-only event store：session、hash chain、JSON checkpoint、fork/replay、evolution patch 与 semantic deduplication。

当前单节点横向扩展上限仍主要来自 SQLite single-writer。

---

## Authority Plane

### `runtime/verifier.py`

Verifier 检查：

- domain required evidence；
- 当前问题特定 evidence；
- attachment-derived / trusted enterprise facts；
- tool cost；
- side-effect safety；
- missing evidence；
- finish / replan / rollback recommendation。

模型、memory、skill、routing posterior、counterfactual learner 都不能降低这里的条件。

### `runtime/sandbox.py`

决定工具是否允许 autonomous read-only execution，或者必须进入 human-confirmed action path。

### `runtime/governance.py`

证据不完整时不生成 BusinessAction。

证据完整时，高影响动作仍必须：

```text
proposed
  → human confirmation
  → approved
  → executed / failed / uncertain
```

---

## Action Plane

真实 side-effect action mapping 与 autonomous read tools 分离。

批准使用数据库 compare-and-set 避免重复确认；下游通信结果不确定时进入 `uncertain`，不做危险自动重放。

---

## Evolution Plane

### `runtime/skills.py`

持久化：

- skill domain；
- pathology niche；
- trigger terms；
- preferred tools；
- shadow score；
- Beta posterior；
- status；
- outcome history；
- per-domain evolution policy。

### `runtime/evolver.py`

```text
trajectory
→ diagnosis / distillation
→ candidate skill
→ safety filter
→ shadow replay
→ regression gate
→ quality-diversity archive
→ active use
→ Bayesian outcome update
→ promotion / retirement
```

Routing posterior 与 skill posterior 是两个不同学习层：

- routing posterior 学“当前状态下一步查什么”；
- skill posterior 学“哪些可复用业务策略长期有效”。

两者都不能修改 Authority Plane。

---

## Provider Plane

`providers/` 支持可替换云端、企业兼容和开源权重 / 自托管 OpenAI-Compatible 认知引擎。

产品层做 request-local provider selection，Runtime 在同一 async context 中复用该引擎，避免并发任务串 provider。

更强模型可以提升 candidate generation；Routing Plane 负责继续学习这些候选在真实业务中的边际价值。

---

## End-to-end data flow

```text
User Goal + Multimodal Assets
        ↓
Upload validation + SHA integrity
        ↓
Local parsing / semantic evidence extraction
        ↓
Goal + Belief + Required Evidence
        ↓
Relevant verified skills
        ↓
EvoLoop / Dynamic Task Graph
        ↓
Planner safety coverage + model candidates
        ↓
Sandbox / Registry / Budget filtering
        ↓
EvoGain-APR
  ├─ global/domain posterior
  ├─ deterministic UCB
  ├─ tool reliability
  └─ budget-aware set selection
        ↓
Parallel read-only tools / MCP reads
        ↓
Deterministic review + cognitive specialists
        ↓
Verifier hard evidence gate
        ↓
Counterfactual verifier difference credit
        ↓
Routing posterior update
        ↓
incomplete → replan / topology mutation / stop for evidence
        ↓ complete
Controlled result
        ↓
BusinessAction proposal
        ↓
Human approval
        ↓
Downstream execution
        ↓
Task outcome
        ↓
Shadow-gated skill evolution
```

---

## Invariants

永远保持：

- 模型不能直接执行 side-effect tool；
- 模型不能把自己输出变成证据；
- memory / skill / routing posterior 只影响 cognition；
- learner 不能扩大合法 action space；
- action approval 必须原子；
- `uncertain` 不自动重试；
- evolution 不能修改 evidence gate；
- counterfactual credit 不使用 specialist rhetoric；
- learner error 不能使 live task 失败或绕过 authority；
- 产品 UI 不把底层模型品牌当产品价值。

---

## Scaling path

进一步横向扩展时优先迁移：

- Event Store → multi-writer transactional store；
- live events → durable stream；
- routing posterior / skills → shared transactional policy store；
- action execution → dedicated idempotent worker layer；
- long-running cognition → durable worker / resume protocol。

认知、路由、证据和权限接口保持分离，因此存储升级不需要推翻 Agent 核心。
