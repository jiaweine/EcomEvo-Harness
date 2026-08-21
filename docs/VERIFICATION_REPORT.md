# EcomEvo 工程验证与压力记录

## 当前结论

截至 2026-08-21，本修复分支已在本地当前工作树完成以下验证：

- `python -m compileall -q ecomevo`；
- 完整 `pytest -q`，242 项；
- 9 个业务 Gold Set × fresh / persisted replay 两阶段 promotion gate；
- 所有 `frontend/*.js` 的 `node --check`；
- 产品 API smoke；
- 当前工作树 1 / 8 / 32 / 64 / 120 / 240 并发 runtime pressure gate，以及 PTC total-deadline / adaptive-policy contention 探针，所有 failures 为空。
- 同一隔离 durable root 连续执行两轮完整 pytest 后，`product.db` 与 `runtime.db` 的 `PRAGMA quick_check` 均为 `ok`。

主线基线 PR #3 曾在 GitHub Actions 通过 regression、pressure 与真实 Chromium E2E；它不自动证明本轮后续修复。此前 current-head browser-e2e 也确实发现过视觉层融合时漏载 `intro.css`、导致屏幕外导览抢占 `Esc` 的缺陷。历史绿灯不能覆盖新提交；合并只接受当前 head 的完整 GitHub Actions 结果。

这些结论都**不等同于真实企业生产环境已经验收**；真实外部 provider/MCP、企业 IdP/反向代理、Safari/Edge 实机和真实大媒体数据仍需要部署环境验证。

---

## 1. 当前回归门禁

### Regression

当前 regression job 硬性验证：

1. Python 包可重复安装；
2. 全量 Python 测试；
3. Gold Set fresh + persisted replay 不退化；
4. 恶意 controller 不能扩大工具集合、绕过证据门槛、伪造完成状态或直接执行高影响动作；
5. durable job、跨进程 lease、WebSocket durable catch-up、并发上传配额、MCP uncertain 语义、tenant/RBAC、审批 actor audit 等长期回归；
6. 所有生产 JS 语法与基础 DOM/CSS 静态结构。

最新 Gold Set 结果：`ok=true`，9 个 case、2 个 phase、0 failures。完整 case 覆盖五个产品域，并同时包含 evidence-complete 与 evidence-incomplete 场景。

### 测试状态隔离与生命周期清理

本轮修复了两个此前会影响重复验证的问题：

- pytest 在收集 API 模块前强制绑定 test-only durable root，即使调用者误设 `ECOMEVO_DATA` 也不会写入操作者数据；
- 产品 smoke 使用自己的临时 durable root，不再污染 `outputs/runtime`；
- durable worker 由 FastAPI lifespan 创建和回收，不再使用已弃用的 `on_event`；
- dev 依赖显式安装 `httpx2`，不再走 Starlette 已弃用的 `httpx` TestClient 兼容路径。

当前完整 pytest 输出无上述生命周期或 TestClient 弃用警告。

---

## 2. Durable Execution 验证

对话处理不再只依赖进程内 `BackgroundTasks`：

- 用户消息、`message.accepted` 与 durable job 在同一 SQLite 事务写入；
- job 持久化 immutable input / asset SHA snapshot；
- worker 使用 cross-process job lease；
- worker/process 崩溃后，lease 到期可由另一 worker reclaim；
- progress event 原子校验 `job_id + worker_id + unexpired lease`，handoff 后旧 worker 不能继续写客户可见进度；
- 续租拒绝或续租存储异常会取消旧 analyzer，旧 worker 不会释放接管者正在使用的 turn lease；
- 运行期间不允许把新资料悄悄插入当前 evidence snapshot；
- assistant message、action proposals、`answer.ready`、job success、runtime `session_id` 与匹配 turn lease 释放原子提交；
- 失败路径写入 `answer.error` 与 durable failed 状态；
- WebSocket 的进程内 queue 仅是低延迟 wake hint，SQLite `task_events` 才是跨 worker 顺序真相。

回归覆盖 job reclaim、mid-run ownership handoff、renewal fault cancellation、stale progress fencing、active-job upload/turn blocking、原子 terminal event、跨 worker event catch-up 与 event-id ordering。

---

## 3. Tenant / Identity / RBAC / Approval Audit

当前实现两种身份模式：

- `local`：开发兼容，默认 local-admin；
- `hmac`：供可信企业 SSO / API Gateway / 反向代理在服务端签入 tenant/user/role/timestamp/signature。

角色层级：`viewer < operator < approver < admin`。

安全约束：

- `/api` 与 `/ws` 进入统一身份边界；
- tenant-scoped conversation / asset / action 读取不泄露其他 tenant 的资源存在性；
- action decision 需要 `approver`；
- runtime/evolution 全局控制面需要 `admin`；
- durable session trace 在 hardened 模式下先验证 session → tenant ownership；
- 审批 CAS 自动持久化 `actor_tenant / actor_user / actor_role / actor_auth_mode`；
- HMAC secret 只能位于服务端或可信代理，不能下发到浏览器。

这里完成的是**可信代理后的身份/RBAC边界**，不是某一个具体企业 IdP 的 SSO 产品集成。

---

## 4. Gold Set / Adversarial Promotion Gate

Gold Set promotion gate 每次 CI 对同一业务集跑两遍：

1. fresh priors；
2. 使用同一个持久 runtime DB 的 persisted replay。

两遍都必须保持：

- domain/status 正确；
- required evidence 缺口正确；
- event chain 有效；
- stop reason 明确；
- tool cost 不超过预算；
- evidence-incomplete 不产生业务动作；
- side effect 必须等待人工确认；
- runtime 不得把 action 自主推进为 executed/approved。

恶意 controller 专项还会显式要求模型绕过证据、调用非法副作用工具并谎称完成；最终 Verifier/Governance/Sandbox 仍必须拒绝越权候选。

---

## 5. 当前工作树 1→240 Runtime Pressure

2026-08-21 本地当前工作树的 pressure gate 实测如下；所有 level 的 `failures=[]`：

| 并发任务 | Throughput | p50 | p95 | p99 | Safety failures |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15.033 tasks/s | 0.0664 s | 0.0664 s | 0.0664 s | 0 |
| 8 | 22.231 tasks/s | 0.2896 s | 0.2999 s | 0.2999 s | 0 |
| 32 | 26.863 tasks/s | 0.9102 s | 0.9582 s | 0.9712 s | 0 |
| 64 | 28.294 tasks/s | 1.6657 s | 1.7406 s | 1.7540 s | 0 |
| 120 | 28.424 tasks/s | 3.1558 s | 3.3747 s | 3.4051 s | 0 |
| 240 | 28.645 tasks/s | 6.1133 s | 6.6074 s | 6.6597 s | 0 |

每个 level 都硬性检查：session 唯一、event chain、stop reason、tool budget、合法 status、incomplete-evidence action leak、side-effect confirmation。所有 level `failures=[]`。

定向探针同样为 `failures=[]`：64 个工具调用在 2 slots / 50 ms total deadline 下 wall 52.1 ms、p99 50.882 ms、peak active 2；8 个独立 policy writer 完整提交 8 次更新，最终 exploration 为 0.800000。

这些数字是**当前工作树在本地容器中的 runtime 压力**，不包含真实外部认知引擎、MCP 网络、复杂媒体解析和企业数据库，因此不能被宣传成 GitHub-hosted runner 数据或真实业务 QPS。

---

## 6. Chromium Browser E2E

仓库的 browser-e2e job 使用真实 Playwright Chromium 启动 Uvicorn 与独立 `ECOMEVO_DATA`，设计为验证：

- 首屏与输入框可用；
- provider 品牌不出现在客户表面；
- 空任务切业务场景不制造垃圾任务；
- durable message 发送后能收到 assistant result；
- Runtime Pulse 正常出现；
- 命令面板键盘打开/关闭和 focus 正常；
- 390×844 窄屏任务控制面可打开和关闭；
- 第二标签页对同一 durable conversation 发消息时，第一标签页会同步显示远端 user message，不出现孤立 assistant reply；
- 两边 busy 最终释放；
- 测试期间无 page error / console error。
- 桌面截图必须为 3840×2400 PNG，移动截图必须为 780×1688 PNG。

Draft PR #3 的历史 browser job 曾完整通过上述路径，并修复过移动端 class 断言。后续每个候选 head 都必须重新通过同一 Chromium job；失败 trace、截图与日志用于定位问题，不能作为通过证据，也不能沿用其他 commit 的绿灯。

即使当前提交的 Chromium CI 通过，也**不会证明真实 Safari 或 Microsoft Edge 实机路径**。

---

## 7. Adaptive Routing / Learning 工程证据

已保留并持续回归的学习层证据包括：

- posterior 可覆盖 cold-start prior；
- global → domain transfer 有界；
- evidence value 与 tool reliability 分开建模；
- deterministic UCB 可重复；
- contextual abstention 与同状态 no-op 比较，而不是固定绝对阈值；
- verifier difference credit 使用 deterministic leave-one-out；
- learner exception 被隔离，不能改变业务执行结果；
- routing learner 不能扩大 action space。

历史隔离 routing-store microbenchmark 在 64 workers 下曾从约 59 rounds/s 改进到约 299.5 rounds/s；该数字仍只属于 persistence 微基准，不是当前完整业务吞吐。

---

## 8. MCP / 高影响动作失败语义

当前代码与回归已经区分：

- 明确前置/参数/业务拒绝 → `failed`；
- timeout、连接断线、HTTP 5xx/408、损坏协议响应、JSON-RPC internal error、`CallToolResult.isError` 等无法证明副作用未发生的情况 → `uncertain`；
- stale MCP session 不自动 replay `tools/call`；
- 浏览器在 approve 请求后断线时提示“状态待核对”，不会诱导直接重复确认；
- `uncertain` action 再次 approve 被状态机拒绝。
- approval / rejection / simulated / executed / failed / uncertain 状态与 `action.updated` task event 原子提交，事件写入异常不会反向篡改已经确认的下游结果。

这仍需要真实下游系统配合幂等键和业务状态查询才能构成生产级端到端执行保证。

---

## 9. 仍需真实部署环境完成的验证

仓库内自动化 release gates 已闭环。以下项目不能在当前仓库/Hosted CI 中诚实地宣称完成：

1. **真实企业 IdP / SSO / Gateway 集成**：HMAC trusted-proxy 边界已经实现，但具体企业身份平台尚未连接；
2. **真实 provider / MCP 凭证与区域**：需要真实 auth、429/rate-limit、schema drift、网络故障、下游幂等和业务结果核对；
3. **真实 Safari / Microsoft Edge 实机**：当前 CI 是 Chromium；
4. **真实大媒体矩阵**：大型/畸形 PDF、Office、图片、视频、音频需要业务样本与目标机器资源验证；
5. **多节点生产数据库/队列拓扑**：当前 durable control plane 基于 SQLite WAL，仍存在单 writer 扩展边界；大规模多节点部署应迁移到适合的生产数据库/队列并重新跑同一套不变量；
6. **真实业务 decision-quality 扩展 Gold Set**：仓库已具备 promotion gate，但最终生产 Gold Set 应由业务团队用真实案例持续扩充。

---

## 10. 当前发布判断

当前工作树可以被描述为：

- 本地完整功能回归通过；
- 两轮连续完整回归后的 SQLite quick-check 通过；
- 本地 Gold Set fresh / persisted replay gate 通过；
- durable execution、资料生命周期、crash-reclaim、tenant/RBAC 与 approver audit 已落地并进入回归；
- 当前工作树本地 1→240 runtime pressure gate 通过；
- 合并门禁要求当前 head 的 GitHub CI 与 Chromium E2E 全绿，不能沿用旧 head 的绿灯。

但在真实 provider/MCP、企业 SSO、Safari/Edge、生产多节点拓扑未验证之前，仍不应把它表述为“所有生产环境均已验收”或“零缺陷生产就绪”。

核心原则保持不变：**认知自治，权限确定。**
