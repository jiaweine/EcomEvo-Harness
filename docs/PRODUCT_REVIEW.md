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

已落地租户隔离的 Procedure / Skill Studio 与 maker-checker Policy Center。Studio 评估通过不等于生产启用；evaluated-pass 可生成只读 Release Candidate，且新评估会把完整 durable evaluation result 的确定性 hash 绑定进评估事件与导出，导出时会校验 immutable version、candidate provenance、summary 与 result hash，一致性失败即拒绝导出；历史未绑定 hash 的评估仍可读，但会明确标记未在评估时绑定。Policy draft/review 在 checker approval + publish 前不进入 runtime resolution。两者都不新增 BusinessAction、MCP tool 或 deterministic authority 旁路。

### Business Gold Set Expansion

把工程 fixture 扩展到由业务专家裁决的真实 case，覆盖不同市场、规则版本、媒体质量和异常分布。任何 adaptive activation 放大都继续走同一 promotion gate。

---

## 下一阶段 P2：质量控制与多节点扩展

### Operator Active Time / North Star Denominator

v1 已落地服务端计时的 Operator active-time telemetry：客户端只能发送前台活跃 heartbeat，不能提交 duration 或 timestamp；服务端按 15 秒 bucket 计量并以 tenant + user + bucket 去重，多标签页不会重复放大。同一 bucket 的重复 heartbeat 会跳过 SQLite UPDATE，降低多标签页/WAL 写放大；遥测采用服务端固定 90 天 rolling retention，客户端不能修改保留期；retention cleanup 由同一 SQLite 数据库中的 durable maintenance timestamp 原子协调，同节点多个 worker/process 共享一天一次的清理窗口，不再每个进程各跑一次全局 prune。遥测启用时间由服务端持久化；只有当 measurement start 覆盖所选统计窗口起点时，Observability 才允许用同一窗口内的精确 active seconds 计算 Verified Decisions per Operator Hour。若遥测在窗口中途才启用，仍展示已观测 Operator Hours 与 coverage rate，但整窗 VDPH 保持 unavailable，避免完整分子除以部分分母；零工时同样不产生 VDPH。该遥测仅用于运营效率观测，不是考勤/薪资证据，也不改变 routing、Policy、approval、BusinessAction 或 tool authority。

### Routing Quality Control Tower

v1 已落地 tenant-safe、admin-only、read-only 控制塔，直接消费 durable assistant/runtime snapshots 与 task events，持续观测 posterior samples、reward/residual EWMA、adaptive activation、tool reliability、diversity overlap、failed-call rate、evidence-tag yield、stagnation 与 tool-cost/completed run。Residual delta 只作为描述性趋势，不自动宣称 drift；控制塔不能改 routing、Policy、Runtime Skill、BusinessAction，也不能执行 tool。

### Off-policy Evaluation

v0 readiness audit 已落地到 Routing Quality Control Tower：它只读 tenant-scoped durable `autonomy.decided` 与 `routing.policy.updated`，量化完整 candidate feature trace、verifier-derived reward linkage 与显式 behavior propensity coverage。只有明确的非负整数 `step` 才允许按 conversation + step 配对，缺失、负数或小数 step 不会被默认为 0。当前生产 routing 是 deterministic UCB，没有随机行为 propensity，也没有经独立验证的 candidate-action outcome model，因此系统只允许展示 logged current-behavior 的描述性 replay；只有事件窗口未截断、每个 decision 都有有效 reward linkage、且窗口内不存在 orphan/duplicate 或 step 无效的 reward update 时，才标记 exact logged-behavior replay。candidate counterfactual、direct-method value 与 doubly-robust estimate 都明确保持 unavailable。系统不会把 utility / rank / activation 伪装成 propensity，也不会因为离线分析结果自动修改 routing、Policy、Runtime Skill、BusinessAction 或 tool authority。

下一步若要真正给候选 policy 做离线价值评估，必须先引入可审计且满足 support/positivity 的行为日志，或单独验证 direct outcome model；任何 adaptive activation 放大仍继续走既有 deterministic authority / promotion gate。

### Production Multi-node Control Plane

当前 durable control plane 基于 SQLite WAL，已经能跨进程 reclaim，但仍有单节点/single-writer 边界。v0 部署拓扑前置闸门已落地：Release Readiness 要求部署方显式声明 `ECOMEVO_DEPLOYMENT_NODES`，当前只有 `1` 能通过；未声明、非法值或多节点声明都会 fail closed，并明确说明这不是实际副本自动发现。Runtime 进一步 fail fast：完全未声明时仅允许兼容启动且保持 release-unattested；一旦显式声明为空、非法或 `>1`，会在打开 runtime 数据库和启动 durable worker 前拒绝启动。

v1 已新增机器可读的 multi-node migration readiness contract，但仍不宣称多节点支持。它把迁移拆成 5 个当前明确未满足的前置条件：共享 product transaction/CAS + monotonic event domain、跨节点 authoritative lease clock + fencing、共享 immutable asset storage、共享 runtime authority state、共享 admin/release evidence state；同时要求未来目标后端重新通过 cross-node job lease handoff、BusinessAction CAS、event reconnect、asset snapshot integrity、authority consistency 与 failure recovery 六类认证 gate。当前 lease 仍依赖 application wall clock，durable asset 仍通过 node-local filesystem path 重开，因此仅替换 database URL 不能被当成多节点完成证据。该 readiness 只读、不能改拓扑或存储后端，也不接受 deployment 自报“已共享”作为解锁条件。真正完成多节点仍需要先实现这些共享能力，再在真实多节点拓扑上重跑 correctness / authority / side-effect uncertainty gates。

### Shadow Environment

v0 已落地 deterministic Shadow Enterprise Simulator，覆盖 MCP / browser / terminal / structured-data 四类 surface 的受限 failure / schema mutation 场景。模拟器只生成 tenant-scoped、内容哈希稳定的 offline replay / training candidate，不连接真实 MCP、Browser、Terminal、Provider 或业务系统，也不持久化 runtime state。对于 governed action 的 post-dispatch timeout / reset / 5xx / malformed response 等不确定故障，候选明确要求 `uncertain`、禁止自动重试并要求先核对业务状态；显式 permission rejection 不会被误标成 uncertain。Schema mutation 必须提供真实发生变化的 before / after fingerprint，否则 fail closed。

v1 进一步增加 fail-closed corpus fixture import：只接受 bounded、预脱敏的结构化故障元数据，以及由上游提供的 source-record SHA-256；API 明确拒绝 raw payload、headers、body 与自由文本 incident 内容。生成的 fixture hash 绑定 tenant、Shadow 场景语义、sanitized observation、source system/event/time 与上游 digest，并保持 deterministic。系统不会伪装成已经独立验证该上游 digest：`source_digest_verified_by_shadow=false`，真实性仍需由真实 provider/MCP/browser/terminal 采集链负责。

Shadow 输出和 corpus fixture 都不是生产证据，不能替代真实集成测试、Verifier、Governance 或业务 Approval，也不能修改 routing、Policy、Runtime Skill、BusinessAction 或 tool authority。真实 failure corpus 接入仍应在外部采集层完成脱敏、digest 生成与保留策略，并通过这一窄接口导入，而不是把原始生产日志复制进 EcomEvo。

---

## Product Gate

当前仓库已经满足“可自动化工程门禁”的主要条件：Gold Set、adversarial authority、durable execution、tenant/RBAC、approval audit、1→240 pressure、Chromium E2E。

成熟企业生产定位仍要求真实部署继续满足：

- Operator active-hours 已有服务端 bucket 计量、tenant/user 去重、window coverage 防误读、重复 bucket 写抑制、固定 90 天 rolling retention 与数据库级跨 worker prune 协调；生产部署仍应验证真实工作台覆盖率和前台交互信号质量，且不得把该遥测当作考勤/薪资证据；
- 企业 IdP / Gateway 接入；
- 真实 provider/MCP auth/rate-limit/idempotency/failure matrix；
- Safari/Edge 与目标设备；
- 真实大媒体和真实业务 Gold Set；
- 目标规模下的生产数据库/队列拓扑；当前 Release Readiness 会阻断未声明或 >1 节点的 SQLite 部署意图，但不会把声明值误称为实际副本发现。

始终不变的产品原则：**routing / skill 可以学习，deterministic authority 不由学习系统修改。**
