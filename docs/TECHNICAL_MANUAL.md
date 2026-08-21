# EcomEvo 技术手册

> 面向部署、二次开发、平台工程、安全和 Agent Runtime 工程团队。

## 1. 系统目标

EcomEvo 不是一个无限 Tool Calling 的聊天代理。工程目标是构建一个满足以下属性的业务 Runtime：

- 多模态输入进入统一证据空间；
- 只读认知可以自主规划、重规划和学习 routing policy；
- 模型只能提出候选，Runtime 决定合法执行集合；
- 工具调用受预算、可靠性和证据价值约束；
- 高影响业务动作与认知层分离；
- 任务过程可追溯、可回放、可恢复；
- routing policy 与 skill 都能从真实 outcome 更新；
- 学习系统永远不能自我扩权。

---

## 2. 代码结构

```text
ecomevo/
├── api/                         API / WebSocket / assets / actions
├── product/                     多模态事实提取与产品编排
├── providers/                   可替换认知引擎与能力路由
└── runtime/
    ├── autonomy.py              EvoLoop / Dynamic Task Graph / stagnation
    ├── control_policy.py        candidate sanitize / registry / sandbox / budget
    ├── adaptive_routing.py      posterior / UCB / reliability / routing persistence
    ├── counterfactual_routing.py verifier leave-one-out credit / production controller
    ├── delegation.py            只读 cognitive specialist
    ├── skills.py                Bayesian skill evolution
    ├── evolver.py               shadow replay / trajectory distillation
    ├── verifier.py              证据与安全硬门槛
    ├── governance.py            BusinessAction 权限边界
    ├── sandbox.py               side-effect policy
    ├── event_store.py           event sourcing / hash chain / patch dedupe
    └── tools.py                 本地与 MCP 工具
frontend/                        Agent-native 操作台
scripts/                         smoke / E2E 辅助
```

生产 `EcomEvoEngine` 直接实例化：

```text
CounterfactualAdaptiveAutonomousController
  → CounterfactualAdaptiveDecisionPolicy
  → AdaptiveRoutingStore
```

基础 `AutonomousController` 仍保留，便于确定性 fallback 和聚焦回归。

---

## 3. 请求生命周期

```text
Browser
  ↓
Conversation API
  ↓
ProductAnalyzer
  ├─ validate assets
  ├─ local parsing
  ├─ multimodal fact extraction
  └─ provider capability routing
  ↓
EcomEvoEngine
  ↓
CounterfactualAdaptiveAutonomousController
  ├─ Planner safety floor
  ├─ model candidate generation
  ├─ sanitizer
  ├─ EvoGain-APR
  ├─ parallel read-only tools
  ├─ specialist review
  ├─ verifier
  ├─ counterfactual credit
  └─ posterior update / bounded recovery
  ↓
GovernanceBoundary
  ↓
BusinessAction proposal
  ↓
Human confirmation
  ↓
Business executor
```

---

## 4. Product state

产品层持久化：

- conversations；
- messages；
- assets；
- task events；
- actions；
- turn leases。

附件只通过任务归属和安全路径读取。文件下载 / preview 前验证路径、元数据和内容完整性。

---

## 5. Runtime persistence

### Event Store

`EventStore` 使用 append-only hash chain 保存 Runtime 事件、snapshot、replay 和 evolution patch。

Evolution patch 使用 semantic fingerprint 做并发去重。

### Skill Store

`AdaptiveSkillLibrary` 保存：

- runtime skills；
- Beta posterior；
- skill outcomes；
- domain evolution policy；
- quality-diversity niche。

domain evolution policy 的读取、派生与更新位于同一个 `BEGIN IMMEDIATE` 事务；不同 Engine / worker 实例不能用旧快照覆盖彼此的策略增量。

### Routing Store

`AdaptiveRoutingStore` 在同一 SQLite 数据库新增：

```text
routing_policy
routing_outcomes
routing_tool_stats
```

#### `routing_policy`

保存 global / domain posterior sufficient statistics：

- `A` matrix；
- `b` vector；
- samples；
- reward EWMA；
- residual EWMA；
- updated time。

不保存模型 chain-of-thought。

#### `routing_outcomes`

保存每个 adaptive-selected tool 的：

- domain；
- phase；
- tool key；
- verifier marginal credit；
- feature vector；
- credit method；
- created time。

#### `routing_tool_stats`

对 domain/tool 保存 Beta reliability posterior，与 evidence value 分开学习。

SQLite 使用 WAL + busy timeout。当前 single-writer 仍是单节点横向扩展上限。

---

## 6. 多模态附件管线

支持：

- raster image；
- video；
- audio；
- PDF；
- DOCX；
- XLSX / XLSM；
- text / log / JSON / CSV / YAML / XML。

上传阶段包括：

- extension / MIME 归一化；
- executable / script block；
- capacity limit；
- image dimension / decompression-bomb 防护；
- Office ZIP structure / expansion-ratio 检查；
- PDF encrypted / corrupted 检查；
- audio/video 内容检查；
- SHA-256 fingerprint。

多模态模型只提取可观察事实，不直接产生退款、下架、审核或冻结处置。

---

## 7. Provider routing

ProviderRegistry 负责：

- 配置检测；
- text / image / audio / document capability match；
- auto route；
- explicit local-controlled mode；
- task-local current provider。

可通过：

```bash
OPEN_MODEL_BASE_URL=
OPEN_MODEL_API_KEY=
OPEN_MODEL_MODEL=
OPEN_MODEL_MULTIMODAL=0
```

接入兼容的开源权重 / 自托管推理服务。

模型名不写死在 Runtime，升级认知引擎不需要重写 EvoGain、Verifier 或权限链。

---

## 8. AutonomousController hard caps

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

代码再次 clamp，环境配置不能创建无限自主循环。

典型 recovery：

1. Verifier 返回 missing evidence；
2. 计算 remaining tool budget；
3. 召回 relevant skill；
4. model / deterministic fallback 产生候选；
5. sanitizer 构造 legal read-only candidate set；
6. EvoGain-APR 排序；
7. execute；
8. review；
9. verify；
10. counterfactual credit；
11. posterior update；
12. 继续 / topology mutation / stop。

---

## 9. Tool policy hard boundary

模型 candidate 必须经过：

- unknown tool rejection；
- sandbox side-effect rejection；
- confirmation-required rejection；
- server-owned arguments；
- budget limit；
- duplicate call handling；
- per-step call cap。

除定向 evidence search 外，模型不能自由生成企业 MCP 参数。

学习策略只能在合法 action set 内排序，不能扩大 action set。

---

## 10. EvoGain-APR

### Feature vector

当前 12 维 feature：

```text
bias
coverage
authority
skill_support
novelty
contradiction
specificity
tool_reliability
cost_pressure
redundancy
gap_pressure
recovery_context
```

### Bayesian posterior

Cold-start 配置进入 prior mean，而不是永久 weights。

```text
A0 = prior precision
b0 = A0 × prior mean

A = A0 + decay × (A - A0) + x xᵀ
b = b0 + decay × (b - b0) + reward × x
mean = inverse(A) × b
variance = xᵀ inverse(A) x
```

### Global / domain hierarchy

同一 credit 同时更新 global 和 domain posterior。

新 domain 只允许小比例 global transfer；本域样本增加后逐渐转向 domain posterior。

### Deterministic UCB

生产探索使用 posterior mean + uncertainty bonus，不使用随机 online sampling。

### Shadow activation

样本不足时只 shadow update。

达到最小样本后，adaptive activation 根据 sample count 和 prediction residual 增长。

Residual drift 增大时 activation 降低，防止环境变化后继续盲信旧策略。

---

## 11. Verifier counterfactual credit

对本轮 adaptive-selected tool：

```text
full = verifier(all_results, specialists=[])
without_i = verifier(all_results - result_i, specialists=[])

credit_i
 = potential(full) - potential(without_i)
   --------------------------------------
                 1 + cost_i
```

`potential` 使用 verifier score 与 evidence completeness。

只重新运行 deterministic verifier，不重新调用模型。

因此额外成本受本轮 adaptive-selected tool 数量硬限制。

注意：这是 difference reward / marginal evidence proxy，不是严格因果 Shapley attribution。

---

## 12. Tool reliability

每个 domain/tool：

```text
Beta(alpha, beta)
```

- successful execution → `alpha + 1`；
- failed execution → `beta + 1`。

Reliability 进入 routing feature，但不是业务证据。

---

## 13. Specialist delegation

`CognitiveDelegator` 支持：

- deterministic domain reviewers；
- optional model specialists；
- stagnation-triggered counter-evidence role。

模型 specialist 只接收压缩后的已核对 tool results。

输出属于 review opinion，不是独立 evidence。

---

## 14. Verification

`DecisionVerifier` 是 cognition 与 BusinessAction 之间的硬边界。

检查至少包括：

- required evidence；
- constraints；
- side-effect safety；
- missing evidence；
- recommendation；
- score。

最终自然语言回答不能把 evidence-incomplete case 改写成通过。

---

## 15. BusinessAction 与权限

高影响动作保持：

```text
side_effect = true
requires_confirmation = true
status = proposed
```

确认使用 compare-and-set，避免并发重复批准。

下游网络异常区分：

- `failed`：明确失败；
- `uncertain`：无法确认是否真实执行。

`uncertain` 禁止自动盲重试。

---

## 16. MCP integration

推荐严格分离：

```text
Read-only MCP
  → autonomous cognition
  → evidence

Side-effect MCP
  → BusinessAction proposal
  → human approval
  → execution
```

生产 MCP 建议配置：

- minimum-privilege credentials；
- domain；
- purpose；
- evidence tags；
- request timeout；
- idempotency key；
- independent audit log。

---

## 17. Skill evolution

Failure / successful recovery trajectory 可以产生候选 read-only skill。

候选需要经过：

```text
safety filter
→ shadow replay
→ regression gate
→ quality-diversity archive
→ live outcome
→ Bayesian update
```

模型可以帮助生成 skill guidance，但不能修改 Verifier、安全源码和生产权限。

---

## 18. Runtime events

重要事件包括：

```text
goal.parsed
belief.updated
plan.created
autonomy.decided
autonomy.decision_rejected
tools.completed
verification.checked
routing.policy.updated
routing.policy.learning_error
plan.replanned
tools.recovery_completed
verification.rechecked
topology.mutated
autonomy.stagnated
action.proposed
evolution.patch
evolution.distilled
run.completed
```

`routing.policy.updated` 只描述 read-only policy learning，不表示业务动作已经获得批准。

---

## 19. Failure recovery

### UI connection

WebSocket 断开后按指数退避重连，并刷新任务状态。

### Turn lease

同一 conversation 同时只允许一个处理 lease。

进程异常后过期 lease 会进入恢复逻辑，避免 UI 永久卡在 processing。Durable worker 的 progress append 同时校验当前 job owner 与未过期 lease；续租失败或 ownership handoff 会取消旧 analyzer，旧 worker 不写 terminal、不释放新 owner 正在使用的 lease token。

### Learner failure

Routing learner exception 被转换成：

```text
routing.policy.learning_error
```

live task 继续使用当次已经通过 sandbox 的执行路径；learner error 不能改变 Verifier / Governance 结果。

### Action recovery

长时间停留在 approved 且 worker 中断的动作应进入 uncertain，而不是重新回到可点击 proposed。

---

## 20. Frontend design system

加载顺序：

```text
app.css
→ visual.css
→ product-polish.css
```

`product-polish.css` 从 HTML 首帧静态加载，负责：

- CJK-stable typography；
- standard 400/500/600/700 weights；
- 16px-class main body；
- restrained state motion；
- reduced-motion；
- keyboard focus；
- medium-screen task-path preservation。

不依赖公网 font CDN。

---

## 21. Deployment

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

Docker：

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

镜像以 UID/GID `10001:10001` 非 root 运行，`/healthz` 只返回最小存活状态。详细运行信息继续由受身份保护的 `/api/health` 提供。wheel 会携带 `frontend` 静态资源，默认数据目录从当前工作目录解析；部署时仍应显式设置 `ECOMEVO_DATA`。

---

## 22. Production gaps

仓库内已经具备回归、Gold Set、240 并发压力、durable cross-process lease、tenant/RBAC、真实 Chromium、wheel smoke、依赖审计与容器启动门禁。仍需目标环境完成：

- 具体企业 IdP / SSO / Gateway 集成与角色映射；
- 真实 Provider / MCP 凭证、区域网络、限流、schema drift 与数据合规；
- 下游业务幂等、结果查询、权限复核和 `uncertain` 人工处置流程；
- 多节点集中式数据库、队列、备份恢复与灾难演练；
- Safari、Edge、企业终端和真实大媒体样本验证；
- 使用真实业务 Gold Set 持续校准 Adaptive Routing 与 Harness promotion。

这些问题属于系统工程和验证问题，不应该依赖“换更强模型”来掩盖。

更完整的数学说明见 [`ALGORITHM.md`](ALGORITHM.md)，已执行验证见 [`VERIFICATION_REPORT.md`](VERIFICATION_REPORT.md)。
