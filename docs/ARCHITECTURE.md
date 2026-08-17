# EcomEvo Architecture

## System thesis

EcomEvo 把一个商业 Agent 拆成六个相互制约的平面：

```text
Experience Plane   → 任务、对话、多模态输入、证据、执行控制
Cognitive Plane    → EvoLoop / EvoGain / Dynamic Task Graph / Delegation
Evidence Plane     → 本地解析 / 多模态事实 / MCP read tools / Event Store
Authority Plane    → Verifier / Sandbox / Governance Boundary
Action Plane       → BusinessAction / Approval / Idempotent downstream execution
Evolution Plane    → Trajectory distillation / Shadow Gate / Bayesian Skills
```

核心原则：**认知层可以越来越自主，权限层保持 deterministic。**

## Product plane

### `frontend/`

三块工作面：

- 左侧：业务场景与历史任务；
- 中间：目标、多模态输入、任务对话与结果；
- 右侧：轨迹、证据、执行控制与资料。

UI 不展示底层服务品牌。用户看到的是 EcomEvo 的任务能力，而不是供应商切换器。

### `ecomevo/api/`

负责 FastAPI、WebSocket、conversation/message/asset、upload validation、task lease、action confirmation、interrupted-turn recovery、安全响应头和静态工作台。

### `ecomevo/product/`

负责文本/PDF/Office/sheet 解析、多模态事实提取、semantic cache、历史用户上下文和面向业务用户的结果编排。多模态模型只做事实读取，不拥有业务处置权。

## Cognitive plane

### `runtime/autonomy.py`

实现 EvoLoop：

```text
Observe → Decide → EvoGain Route → Act → Review → Verify → Reflect / Replan → Stop
```

每个任务创建 Dynamic Task Graph。Graph 随真实证据与失败状态增长，不是提前写死的流程。

### `runtime/control_policy.py`

模型输出只被视为候选。实际执行前必须经过：Tool Registry 存在性检查、Sandbox 权限检查、参数收敛、重复调用检查、成本预算、EvoGain ranking 和 low-gain stop。

### EvoGain

路由维度：missing evidence coverage、evidence source authority、novelty、Bayesian skill support、contradiction value、execution cost、same-round evidence-channel diversity。模型不控制最终工具排序。

### `runtime/delegation.py`

固定 specialist 之外允许动态 cognitive delegation。动态 specialist 只能 read-only、evidence-bound，不能拥有 BusinessAction authority，也不能修改 verifier。

## Evidence plane

### `runtime/tools.py`

本地只读工具包含媒体状态整理、证据搜索、规则查询、商品核验、商家核验、订单核验和风险扫描。企业 MCP read tools 可以声明可信 `evidence_tags`，但参数模板仍由服务器配置控制。

大文本采用 bounded index + truncated raw stream search。图片、视频关键帧、音频与扫描文档通过语义通道转成带来源、置信度和 asset id 的事实，再进入统一 verifier 路径。

### `runtime/event_store.py`

SQLite WAL append-only event store：session、hash chain、JSON checkpoint、fork/replay、evolution patch 与 semantic deduplication。单节点高并发正确性已做压力验证；SQLite single-writer 仍是横向扩展上限。

## Authority plane

### `runtime/verifier.py`

Verifier 检查 domain required evidence、当前问题特定 evidence、attachment-derived/trusted enterprise facts、tool cost、side-effect safety、missing evidence 与 finish/replan/rollback recommendation。

模型、memory、skill、历史回复都不能降低这里的条件。

### `runtime/governance.py`

证据不完整时不生成 BusinessAction。证据完整时，高影响动作仍必须：

```text
proposed → human confirmation → approved → executed / failed / uncertain
```

## Action plane

真实 MCP action mapping 与 autonomous read tools 分离。批准使用数据库 compare-and-set 避免重复确认；下游通信结果不确定时进入 `uncertain`，不做危险自动重放。

## Evolution plane

### `runtime/skills.py`

持久化 skill domain、pathology niche、trigger terms、preferred tools、shadow score、Beta posterior、status、outcome history 与 per-domain evolution policy。

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

自进化不直接编辑源代码，也不能修改 authority plane。

## Provider plane

`providers/` 支持可替换云端、企业兼容和开源权重/自托管 OpenAI-Compatible 认知引擎。产品层做一次 request-local provider selection，Runtime 在同一 async context 中复用该引擎，避免并发任务串 provider。

自动路由可以让常规文本规划优先使用部署方配置的开源/自托管引擎；图片、音频和扫描 PDF 只路由到真正具备对应能力的引擎；显式本地受控模式清空外部 provider context。

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
Relevant verified skills + evolution policy
        ↓
EvoLoop creates Dynamic Task Graph
        ↓
Planner safety coverage + model candidate proposals
        ↓
EvoGain deterministic routing
        ↓
Parallel read-only tools / MCP reads
        ↓
Deterministic review + dynamic cognitive specialists
        ↓
Verifier hard evidence gate
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
Result event + trajectory outcome
        ↓
Shadow-gated skill evolution
```

## Invariants

永远保持：模型不能直接执行 side-effect tool；模型不能把自己输出变成证据；memory/skill 只影响 planning；上传资料保持任务归属与完整性；action approval 必须原子；`uncertain` 不自动重试；evolution 不能修改 evidence gate；产品 UI 不把底层模型品牌当产品价值。

## Scaling path

进一步横向扩展时优先迁移 Event Store 到 multi-writer transactional store、live events 到 durable stream、skills/policy state 到共享事务存储、action execution 到 dedicated idempotent worker layer。认知层、证据层和权限层接口保持不变，因此存储升级不需要推翻 Agent 核心。
