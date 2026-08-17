<div align="center">

# EcomEvo

### 自主决策 · 自我进化 · 证据驱动 · 可控执行

**面向真实电商业务的 Autonomous & Self-Evolving Agent Runtime。**

EcomEvo 不把大模型当成一个更聪明的聊天框，也不把 Agent 简化成“模型 + Tool Calling + Retry”。  
它把**动态任务图、自主工具选择、认知委派、反思重规划、Bayesian 技能进化、Shadow Gate、事件溯源和确定性业务权限**放进同一个运行时，让 Agent 能真正持续推进复杂任务，同时不把生产权限交给模型。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-1f6feb?logo=python&logoColor=white" />
  <img alt="Autonomy" src="https://img.shields.io/badge/Autonomy-Dynamic%20Task%20Graph-245EDB" />
  <img alt="Evolution" src="https://img.shields.io/badge/Evolution-Bayesian%20Skills-14845D" />
  <img alt="Safety" src="https://img.shields.io/badge/Authority-Deterministic-B42318" />
  <img alt="Pressure" src="https://img.shields.io/badge/Pressure-240%20Concurrent-A45C00" />
</p>

**[为什么 EcomEvo](#为什么-ecomevo) · [算法栈](#核心算法栈) · [自进化](#真正的自进化) · [安全边界](#认知自治权限确定) · [业务场景](#业务场景) · [快速启动](#快速启动)**

> **Give the Agent a goal, not a script.**

</div>

## 产品预览

![EcomEvo 商业决策工作台](docs/images/product-workbench.svg)

<p align="center">
  <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务工作台" width="360" />
</p>

## 为什么 EcomEvo

大模型越来越强，但企业真正难的问题从来不是“模型会不会回答”，而是：

- 信息散落在订单、商品、商家、截图、视频、文档、日志和内部系统里；
- 一个任务要经过多轮查询、交叉核对、补证、反证、重规划；
- 证据不足时必须停，不能靠模型置信度把事实补出来；
- 退款、下架、冻结、审核等动作会改变真实业务状态；
- 任务可能长时间运行、失败、恢复、重试，还必须完整留痕；
- 模型、数据源和业务系统会变化，但企业不能每次都重写一套 Agent。

**EcomEvo 把这些问题统一收进 Runtime。**

用户只需要描述目标并持续补充资料。系统会围绕目标自主决定下一步查什么、先调用哪些只读工具、是否并行核对、是否派生专项复核角色、什么时候改变策略、什么时候停止，并把每次成功和失败沉淀成可复用能力。

最终得到的不是一段“像答案的文本”，而是一条可以持续演进、可以审计、可以执行的业务轨迹：

```text
Goal
 ↓
Evidence / Belief State
 ↓
Autonomous Controller
 ↓
Dynamic Task Graph
 ├─ Observe
 ├─ Decide
 ├─ Select Tools
 ├─ Parallel Execute
 ├─ Delegate Specialists
 ├─ Review
 ├─ Verify
 ├─ Reflect / Replan
 └─ Stop
 ↓
Deterministic Authority
 ↓
Human Approval
 ↓
Business Action
 ↓
Experience → Skill Evolution
```

> **EcomEvo 的核心不是“让模型多想几步”，而是让整个 Agent Runtime 会自主工作、会从结果中成长，同时始终知道自己没有什么权限。**

## 不只是一个普通 Agent Harness

很多 Agent Harness 解决的是“怎样让模型反复调用工具”。EcomEvo 解决的是更完整的问题：**怎样让一个企业 Agent 长期自主工作，并且越用越会做，而不是越用权限越大。**

| 能力 | 常见单循环 Agent | EcomEvo |
| --- | --- | --- |
| 执行路径 | 模型循环 / 固定流程 | 动态 Task Graph，按证据状态实时变化 |
| 工具选择 | Prompt 中决定 | 模型决策 + Registry + Sandbox + Cost Gate |
| 并行执行 | 可选 | 原生并行工具组合 |
| 专项 Agent | 预设角色 | 固定基线 + 运行时动态认知委派 |
| 失败恢复 | Retry | Verification-driven replan + stagnation detection |
| 记忆 | 对话历史 / 向量召回 | 已验证业务记忆 + 运行轨迹 + 技能后验 |
| 自我改进 | Prompt 累积 | Shadow-gated Skill Evolution |
| 技能选择 | 静态规则 | Bayesian posterior + relevance + replay score |
| 技能淘汰 | 很少 | 在线结果驱动晋升 / 退役 |
| 策略进化 | 固定 | Domain-level Meta-Evolution |
| 业务权限 | 常与 Agent 混在一起 | Deterministic Verifier + Sandbox + Human Approval |
| 审计 | 日志 | Append-only event sourcing + hash chain |

## 核心算法栈

EcomEvo 把一组原本分散在研究型 Agent 系统里的能力组合成一个面向生产业务的执行栈。

### 1. EvoLoop — Observe → Decide → Act → Review → Verify

每个任务不是只规划一次，而是在每一轮工具结果回来之后重新观察状态。

控制器会持续读取：

- 当前业务目标；
- 强制证据要求；
- 已完成工具与结果；
- 当前 `missing_evidence`；
- 剩余工具预算；
- 已验证历史经验；
- 当前可用技能及其后验可信度；
- 当前业务域的进化策略。

然后重新决定下一批最有价值的只读动作。

**Planner 不再是执行路线本身，而是安全底线与无模型 fallback。**

### 2. Dynamic Task Graph — 运行时任务拓扑

任务图会随证据和失败动态增长，而不是启动时就把 DAG 写死。

节点可以代表：

- 初始证据计划；
- 自主补证；
- MCP 企业数据查询；
- 交叉复核；
- 反证审查；
- 重新规划；
- 停滞检测；
- 最终验证。

这让 EcomEvo 能处理“开始时根本不知道后面需要查什么”的真实业务问题。

### 3. Evidence-Gain Planning — 以减少不确定性为目标

下一步工具不是为了“多调用几个工具”，而是为了减少当前证据缺口。

控制器同时考虑：

**Evidence Gap × Tool Capability × Cost Budget × Historical Skill Utility**

模型可以提出候选动作，但只有 Tool Registry 中真实存在、Sandbox 允许、成本预算内的只读工具才会被执行。

### 4. Cognitive Topology Mutation — 动态认知团队

系统保留规则、证据、风险、业务判定等稳定基线角色，同时允许 Runtime 在需要时增加专项 specialist。

例如：

- 反证审查；
- 授权链路复核；
- 订单时间线复核；
- 风险信号交叉检查；
- 证据冲突检查。

如果连续 verification fingerprint 没有变化，Runtime 会识别探索停滞，改变认知拓扑，而不是机械 Retry。

### 5. Bayesian Skill Evolution — 用真实结果更新技能

每个技能都有 Beta posterior：

```text
success → alpha + 1
failure → beta + 1
posterior_mean = alpha / (alpha + beta)
```

技能选择不是简单“命中关键词就使用”，而是综合：

**Posterior Utility + Shadow Replay Score + Trigger Relevance**

所以一个技能即使曾经表现很好，如果真实任务里持续失败，也会逐渐降权并最终退役。

### 6. Quality-Diversity Skill Archive — 不是无限堆经验

同一种失败模式不会无限生成重复 Prompt。

EcomEvo 为技能建立 pathology niche，同一个 niche 只保留更强的代表。新候选必须在 replay/后验综合质量上显著优于 incumbent，才能替换现有技能。

结果是一个**持续压缩、持续竞争、持续更新**的技能库，而不是越来越长的提示词垃圾场。

### 7. Shadow Gate — 先影子验证，再进入活跃策略

失败轨迹或恢复成功轨迹可以产生候选技能，但候选不会直接影响线上决策。

它必须先通过：

- 工具存在性检查；
- 副作用权限检查；
- 安全不变量检查；
- deterministic shadow replay；
- regression gate；
- 当前业务域 promotion threshold。

只有没有降低回归表现并达到门槛，才有资格进入 active skill archive。

### 8. Meta-Evolution — 进化“如何进化”

每个业务域维护自己的：

- `promotion_threshold`
- `retirement_threshold`
- `exploration`

如果某个领域使用技能后持续失败，系统会提高晋升要求并增加探索；如果技能持续稳定成功，则会减少不必要探索。

**技能在进化，技能选择策略也在进化。**

### 9. Event-Sourced Agent State — 让长任务可恢复、可复盘

Runtime 使用 append-only Event Store：

- 连续序号；
- SHA-256 hash chain；
- JSON checkpoint；
- rollback；
- fork；
- replay；
- evolution patch fingerprint；
- 并发语义去重。

Agent 不只是“记住聊天”，而是拥有可以验证完整性的执行历史。

## 真正的自进化

EcomEvo 的“自进化”不是让模型偷偷修改 Python，也不是把失败原因继续塞进 system prompt。

它的进化链路是：

```text
Task Experience
 ↓
Failure / Recovery Diagnosis
 ↓
Candidate Skill
 ↓
Safety Filter
 ↓
Shadow Replay
 ↓
Regression Gate
 ↓
Quality-Diversity Archive
 ↓
Live Usage
 ↓
Bayesian Outcome Update
 ↓
Promote / Decay / Retire
 ↓
Meta-Policy Adaptation
```

系统同时从两类轨迹学习：

**失败轨迹**  
告诉 Runtime 哪类证据缺口、错误工具路径或停止条件需要被修正。

**恢复成功轨迹**  
告诉 Runtime 哪种补证顺序、工具组合和认知委派真正有效。

这使系统不是“失败后打补丁”，而是在积累一个可竞争、可淘汰、可复用的行为能力库。

详细机制见 [`docs/AUTONOMY.md`](docs/AUTONOMY.md)。

## 认知自治，权限确定

这是 EcomEvo 最重要的设计原则。

### Agent 可以自主决定

- 下一步查什么；
- 调哪个只读工具；
- 哪些工具并行；
- 是否派生 specialist；
- 是否做反证检查；
- 是否重新规划；
- 是否停止探索；
- 哪个已验证技能更适合当前任务。

### Agent 永远不能自主决定

- 降低证据门槛；
- 把模型输出当成业务事实；
- 把历史回复当成独立证据；
- 自己批准退款；
- 自己下架商品；
- 自己冻结账户；
- 自己通过/拒绝商家；
- 绕过人工确认；
- 修改 Verifier / Sandbox 的权限边界。

**Agent 可以改策略，但不能给自己扩权。**

这让自主性和生产安全不再是二选一。

## 多模型不是架构核心

EcomEvo 支持：

- OpenAI
- DeepSeek
- 通义千问
- 豆包
- Claude
- Gemini
- 企业 OpenAI-Compatible Endpoint

模型在这里是**可替换认知引擎**。

同一个 Runtime 可以根据附件能力和企业策略选择不同模型；显式选择本地演示时不会隐式出站。产品层选中的 provider 通过 task-local Context 进入 Runtime，避免并发任务串模型。

换模型，不需要重写 Agent 的安全规则、事件状态、自进化机制和业务执行层。

## 企业系统：MCP + 受控动作

EcomEvo 可以把企业 MCP 只读工具直接纳入自主任务图，例如：

- 订单中心；
- 商品中心；
- 商家中心；
- 风控系统；
- 物流系统；
- 内容治理平台。

模型可以决定“什么时候值得查询”，但远程工具和参数模板仍由服务端配置控制。

退款、下架、审核、风险升级等真实业务动作走另一条链路：

```text
Agent Recommendation
 ↓
Verifier
 ↓
BusinessAction
 ↓
Human Confirmation
 ↓
MCP / Business System
 ↓
Executed / Failed / Uncertain
```

当下游连接中断导致结果无法确认时，状态进入 `uncertain`，不会自动盲重试。

## 压力验证

为了验证“自主性打开之后，安全边界会不会在并发下失效”，升级过程中运行了两组**临时压力测试**。压力测试脚本没有写入仓库。

### 240 个并发 Runtime 任务，共享同一个 SQLite

| 指标 | 结果 |
| --- | ---: |
| 吞吐 | **37.2 runs/s** |
| p50 | **3.74 s** |
| p95 | **5.22 s** |
| p99 | **5.25 s** |
| Event chain failure | **0** |
| 缺证据副作用泄漏 | **0** |
| 有效案例误失败 | **0** |
| 重复语义 evolution patch | **0** |

### 80 个并发恶意 Model Controller

测试控制器每轮都故意尝试非法 `refund.issue`，同时申请合法只读检索和认知委派。

| 指标 | 结果 |
| --- | ---: |
| 吞吐 | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event chain failure | **0** |
| Side-effect leak | **0** |
| 非法动作拦截 | **80 / 80** |
| 成功认知委派 | **80 / 80** |
| Model-controller runs | **80 / 80** |

这些结果说明在本次测试范围内，模型自主决策没有穿透 deterministic authority boundary。

> 压力数据是当前本地测试环境的工程结果，不等同于第三方 benchmark，也不宣称跨硬件的绝对性能。SQLite 单写仍是当前单节点横向扩展的主要上限。

## 业务场景

| 场景 | Agent 会做什么 |
| --- | --- |
| 商品治理 | 自主组合商品、素材、声明和资质核对，追踪声明证据缺口，形成治理建议 |
| 商家审核 | 核对主体、经营范围、授权链路、历史风险和企业数据，缺证时自主补查 |
| 售后判责 | 围绕订单、物流、沟通和用户举证重建事实链，形成可解释判责建议 |
| 风险核查 | 区分用户描述、弱线索和独立强证据，必要时动态增加反证与交叉复核 |
| 内容审核 | 组合图片、视频、文案和商品事实，未读到媒体内容时 fail closed |

## 产品能力

- **Autonomous Control Loop**：不是固定脚本，按真实观察持续决定下一步。
- **Dynamic Task Graph**：任务拓扑随证据变化实时增长。
- **Parallel Tool Composition**：支持并行只读工具与企业 MCP。
- **Dynamic Cognitive Delegation**：按任务需要派生专项复核角色。
- **Verification-driven Replan**：根据证据硬验证结果重规划，不靠模型自评。
- **Persistent Self-Evolving Skills**：失败和成功轨迹都能蒸馏为候选技能。
- **Bayesian Skill Ranking**：用真实任务成功/失败持续更新技能可信度。
- **Quality-Diversity Archive**：同类技能竞争，避免经验无限膨胀。
- **Meta-Evolution**：不同业务域会形成不同探索和晋升策略。
- **Multimodal Evidence**：图片、视频、音频、PDF、Word、Excel、CSV/JSON、日志与文本。
- **Event Sourcing**：hash chain、checkpoint、rollback、fork、replay。
- **Human-in-the-loop Authority**：所有高影响业务操作仍需明确确认。
- **Provider Independence**：模型可替换，Runtime 不绑定单一厂商。
- **Enterprise MCP**：企业数据和工具可以进入同一个证据与执行闭环。

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`。

没有配置外部模型时可以使用**本地演示**跑通文本和结构化资料流程。图片、音视频或扫描文档需要配置支持相应能力的模型服务。

### Docker

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

### 自主 Runtime 参数

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

这些参数只能在代码允许的硬范围内调节探索强度，不能关闭证据门槛、Sandbox 或人工确认。

## 工程结构

```text
.
├── ecomevo/
│   ├── runtime/
│   │   ├── autonomy.py        # 自主循环与动态任务图
│   │   ├── control_policy.py  # 模型决策解析、过滤与只读策略
│   │   ├── delegation.py      # 动态认知 specialist
│   │   ├── skills.py          # Bayesian 自进化技能库
│   │   ├── evolver.py         # 轨迹蒸馏、shadow replay、regression gate
│   │   ├── governance.py      # 确定性权限边界
│   │   ├── verifier.py        # 证据与动作硬验证
│   │   └── event_store.py     # event sourcing / hash chain / replay
│   ├── product/               # 多模态事实提取与产品编排
│   ├── providers/             # 多模型 provider 路由
│   └── api/                   # FastAPI / WebSocket / Actions
├── frontend/                  # 商业 Agent 工作台
├── docs/                      # 架构、自主运行时、设计、部署与验证说明
├── scripts/                   # E2E / live smoke
└── tests/                     # 原有工程回归体系
```

更多技术细节：

- [`docs/AUTONOMY.md`](docs/AUTONOMY.md) — 自主决策、自进化、技能后验与安全不变量
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 整体架构与数据流
- [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md) — 已执行的工程验证与边界说明
- [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md) — 部署建议

## 生产部署

当前默认存储以 SQLite 为核心，适合单节点/单企业工作区和产品验证。压力测试中并发正确性保持稳定，但 SQLite 单写会逐步成为吞吐上限。

面向大规模生产部署，建议将事件、技能、任务状态迁移到支持多写者和高可用的基础设施，并继续在企业网关侧配置：

- SSO / IAM；
- 最小权限；
- 下游幂等键；
- 审计策略；
- 网络隔离；
- 数据合规策略。

Runtime 的自治与进化设计不依赖 SQLite，本地存储可以替换。

## 项目定位

EcomEvo 想做的不是一个更会聊天的电商 AI。

它想做的是：

> **一个会自己规划、会自己查证、会自己反思、会从结果中学习，但不会自己扩权的企业 Agent Runtime。**

模型能力会继续变化，真正值得企业长期持有的是模型之外的东西：

**任务状态、证据体系、业务权限、工具网络、执行轨迹、经验技能和进化机制。**

EcomEvo 把这一层变成产品。

---

<p align="center">
  <b>EcomEvo — 让 Agent 自己想办法，让业务始终掌握最终权力。</b>
</p>
