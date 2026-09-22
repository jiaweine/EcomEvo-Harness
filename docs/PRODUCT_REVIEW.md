# EcomEvo 产品体检与下一阶段路线

> 目标：让 EcomEvo 的自主性持续可验证、可恢复、可治理，而不是只证明 Agent “能自主”。

## 当前判断

EcomEvo 已经形成可回归的产品闭环：多模态任务、动态查证/复核、EvoGain-APR adaptive routing、deterministic Verifier/Governance、durable execution、tenant/RBAC/审批审计、Evidence & Authority 控制面、Gold Set / adversarial release gates、current-head 1→240 pressure gate 与真实 Chromium E2E。

当前最大问题已经从“关键工程护栏是否存在”转为“如何把这些能力接入真实企业身份、真实数据源和更大业务 Gold Set，并持续证明决策效率”。

## North Star

**Verified Decisions per Operator Hour（每人时完成的可验证决策数）**

硬护栏：

- Unauthorized side effects = 0；
- Evidence-gate bypass = 0；
- Cross-tenant data leak = 0；
- Blind retry after uncertain side effect = 0。

配套指标包括 Time to Verifiable Decision、Evidence-complete rate、Needs-evidence recovery rate、Human correction rate、Correction-to-resolution time、`uncertain` action rate、Cost per completed task、Resume-after-interruption success rate、Multimodal extraction failure rate、routing residual/drift、tool reliability posterior 和 skill promotion/retirement quality。

---

## 已落地的 P0

### Gold Set + CI Promotion Gate

已落地 9 个确定性业务 case，覆盖五个产品域，并在每次 CI 中跑 fresh priors + persisted replay 两阶段。门禁检查 domain/status、evidence gaps、event chain、stop reason、tool budget、side-effect confirmation 与 incomplete-evidence action leak。

这已经是可扩展的 promotion infrastructure；生产业务团队仍应把 Gold Set 扩到更大的真实标注集，而不是把 9 个工程 fixture 当最终业务代表性。

### Adversarial Authority Gate

恶意 controller 会显式要求绕过证据、调用非法副作用工具、谎称已完成；CI 要求 Verifier/Governance/Sandbox 保持最终权限，并记录非法候选被拒绝。

### Durable Execution

消息、accepted event 与 durable job 原子落盘；worker 使用 cross-process lease；输入和 asset SHA snapshot 固化；崩溃后可 reclaim；progress 按 job owner fencing；ownership handoff 或续租失败会取消旧 analyzer；assistant/action/terminal event 原子提交。`BackgroundTasks` 只保留低延迟触发角色，不再是任务存在性的唯一载体。

### Tenant / Identity / Approval Chain

已实现：

- tenant isolation；
- viewer/operator/approver/admin；
- HMAC trusted-proxy identity boundary；
- approval actor audit；
- global runtime/evolution admin gate；
- hardened session→tenant trace ownership。

尚未伪装成完成的是具体企业 IdP/SSO 产品接入；这需要真实部署方的网关和身份平台。

### Latest-head Release Matrix

当前 CI 已拆为三条独立 job：

1. regression + Gold Set + adversarial gate；
2. current-head 1 / 8 / 32 / 64 / 120 / 240 pressure，以及 PTC deadline / adaptive-policy contention 探针；
3. real Chromium E2E，包括双标签页与窄屏交互。

---

## 已落地的产品信任体验

### Intent / Scene

空任务切场景复用当前任务，不制造垃圾历史；命令面板、快捷卡与左导航复用同一状态机。

### Runtime Transparency

控制面展示 evidence completeness / gaps、tool budget、autonomy steps、stop reason、autonomy mode 与运行质量指标，不展示隐藏 chain-of-thought。

### Correction affordances

回答区提供“继续追证 / 检查反证”，把纠错成本压低；这些入口要求系统继续围绕可核验证据工作，而不是用模型自信代替证据。

### Multimodal critical section

上传期间不能先发送；当前 turn 运行期间不能悄悄追加资料改变 evidence snapshot；后端事务仍是最终兜底。

### Multi-tab / reconnect

WebSocket 使用 durable SQLite event log + `after_id` 增量补拉；process-local queue 只做 wake hint；跨 worker/多标签页不会因为消息乱序漏事件或出现孤立 assistant reply。

### Side-effect uncertainty

MCP timeout、断线、5xx/408、协议损坏、internal error 等无法证明副作用未发生的结果统一进入 `uncertain`，不会 blind replay；浏览器确认后断网也提示先核对业务状态。

---

## P1 产品化控制面：代码侧已落地，真实业务接入继续扩展

### Structured Correction / Evidence Dispute

已落地结构化纠错 taxonomy、具体 target snapshot、append-only review history 与 evaluation-candidate export；反馈不会改写原回答、Policy、routing、Verifier、BusinessAction 或 Gold Set。真实业务仍应持续把已裁决纠错样本扩进 eval dataset 与运营分析。

### Enterprise MCP Control Plane

已落地 declared/effective read-write scope、credential owner、evidence tags、idempotency 冲突检测、probe health / latency / failure rate / schema drift 与服务端 schema fingerprint。浏览器不接收 endpoint、token env、secret 或完整 schema；probe 只调用 tools/list，不触发 tools/call。真实企业 deployment 仍需接入具体 IdP、secret manager 与 provider auth/rate-limit matrix。

### Collaboration / Decision Export

已落地 owner、watcher、comment/@mention、定向 review request、双边确认 handoff，以及 immutable decision/audit export。协作状态不授予 approver 权限，handoff 接受时才原子转移 owner，export 不改变 action / policy / routing / skill / tool 状态。

### Procedure / Skill Studio + Policy Center

已落地隔离的 Procedure / Skill Studio 与 maker-checker Policy Center。Studio 评估通过不等于生产启用；Policy draft/review 在 checker approval + publish 前不进入 runtime resolution。两者都不新增 BusinessAction、MCP tool 或 deterministic authority 旁路。

### Business Gold Set Expansion

把工程 fixture 扩展到由业务专家裁决的真实 case，覆盖不同市场、规则版本、媒体质量和异常分布。任何 adaptive activation 放大都继续走同一 promotion gate。

---

## 下一阶段 P2：质量控制与多节点扩展

### Operator Active Time / North Star Denominator

v1 已落地服务端计时的 Operator active-time telemetry：客户端只能发送前台活跃 heartbeat，不能提交 duration 或 timestamp；服务端按 15 秒 bucket 计量并以 tenant + user + bucket 去重，多标签页不会重复放大。遥测启用时间由服务端持久化；只有当 measurement start 覆盖所选统计窗口起点时，Observability 才允许用同一窗口内的精确 active seconds 计算 Verified Decisions per Operator Hour。若遥测在窗口中途才启用，仍展示已观测 Operator Hours 与 coverage rate，但整窗 VDPH 保持 unavailable，避免完整分子除以部分分母；零工时同样不产生 VDPH。该遥测仅用于运营效率观测，不是考勤/薪资证据，也不改变 routing、Policy、approval、BusinessAction 或 tool authority。

### Routing Quality Control Tower

v1 已落地 tenant-safe、admin-only、read-only 控制塔，直接消费 durable assistant/runtime snapshots 与 task events，持续观测 posterior samples、reward/residual EWMA、adaptive activation、tool reliability、diversity overlap、failed-call rate、evidence-tag yield、stagnation 与 tool-cost/completed run。Residual delta 只作为描述性趋势，不自动宣称 drift；控制塔不能改 routing、Policy、Runtime Skill、BusinessAction，也不能执行 tool。

### Off-policy Evaluation

在真实 routing log 足够后增加 replay / doubly-robust evaluation，用于候选 policy 的离线风险评估，而不是让在线 learner 自己决定扩大权限。

### Production Multi-node Control Plane

当前 durable control plane 基于 SQLite WAL，已经能跨进程 reclaim，但仍有单 writer 边界。需要大规模多节点时，应迁移到适合的 transactional database / durable stream，然后重跑现有所有 correctness/authority gates。

### Shadow Environment

可研究 MCP-like / browser / terminal / structured-data 的 shadow enterprise simulator，用于 failure/schema mutation replay。Simulator 只能产生训练/回放候选，不能替代真实 Verifier 或业务 approval。

---

## Product Gate

当前仓库已经满足“可自动化工程门禁”的主要条件：Gold Set、adversarial authority、durable execution、tenant/RBAC、approval audit、1→240 pressure、Chromium E2E。

成熟企业生产定位仍要求真实部署继续满足：

- Operator active-hours 已有服务端 bucket 计量与 tenant/user 去重；生产部署仍应验证真实工作台覆盖率、前台交互信号质量与长期数据保留策略，且不得把该遥测当作考勤/薪资证据；
- 企业 IdP / Gateway 接入；
- 真实 provider/MCP auth/rate-limit/idempotency/failure matrix；
- Safari/Edge 与目标设备；
- 真实大媒体和真实业务 Gold Set；
- 目标规模下的生产数据库/队列拓扑。

始终不变的产品原则：**routing / skill 可以学习，deterministic authority 不由学习系统修改。**
