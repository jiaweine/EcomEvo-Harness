# EcomEvo 工程验证与压力记录

## 当前结论

截至 2026-08-17，EcomEvo 当前 adaptive runtime 已把仓库内可自动化的核心发布门槛接入 GitHub Actions，并在 head `aa968aa904fabcd1f4947b81fe4a76dcb0e3222c` 的 CI run #191 全部通过：

- Ubuntu 24.04 / Python 3.11 editable install；
- ffmpeg / ffprobe 媒体依赖；
- `python -m compileall -q ecomevo`；
- 完整 `pytest -q`；
- 9 个业务 Gold Set × fresh / persisted replay 两阶段 promotion gate；
- adversarial-controller authority gate；
- 所有 `frontend/*.js` 的 `node --check`；
- loader 模块存在性、HTML parser、DOM id 唯一性、CSS brace 检查；
- current-head 1 / 8 / 32 / 64 / 120 / 240 并发 runtime pressure gate；
- 真 Chromium Playwright E2E，包括场景切换、发送/收取结果、Runtime Pulse、命令面板、移动端控制抽屉、双标签页同步，以及 page/console error 监测。

该结论只代表上述代码、CI 与托管 runner 环境。它**不等同于真实企业生产环境已经验收**；真实外部 provider/MCP、企业 IdP/反向代理、Safari/Edge 实机和真实大媒体数据仍需要部署环境验证。

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

### 已知非阻塞依赖警告

当前仍有依赖升级类 warning：

- Starlette TestClient 当前 httpx 兼容路径 deprecated；
- FastAPI `on_event` lifespan API deprecated。

二者没有导致当前回归失败。后续依赖升级应独立清理，避免把 release 行为变更和框架生命周期迁移混在同一批次。

---

## 2. Durable Execution 验证

对话处理不再只依赖进程内 `BackgroundTasks`：

- 用户消息、`message.accepted` 与 durable job 在同一 SQLite 事务写入；
- job 持久化 immutable input / asset SHA snapshot；
- worker 使用 cross-process job lease；
- worker/process 崩溃后，lease 到期可由另一 worker reclaim；
- 运行期间不允许把新资料悄悄插入当前 evidence snapshot；
- assistant message、action proposals、`answer.ready` 与 job success 原子提交；
- 失败路径写入 `answer.error` 与 durable failed 状态；
- WebSocket 的进程内 queue 仅是低延迟 wake hint，SQLite `task_events` 才是跨 worker 顺序真相。

回归覆盖 job reclaim、active-job upload/turn blocking、原子 terminal event、跨 worker event catch-up 与 event-id ordering。

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

## 5. Current-head 1→240 Runtime Pressure

GitHub Actions Ubuntu 24.04 上，head `aa968aa...` 的 current-head pressure gate 实测如下：

| 并发任务 | Throughput | p50 | p95 | p99 | Safety failures |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 26.604 tasks/s | 0.0375 s | 0.0375 s | 0.0375 s | 0 |
| 8 | 34.602 tasks/s | 0.1838 s | 0.2006 s | 0.2006 s | 0 |
| 32 | 34.896 tasks/s | 0.6956 s | 0.7376 s | 0.7442 s | 0 |
| 64 | 38.417 tasks/s | 1.2793 s | 1.3820 s | 1.3899 s | 0 |
| 120 | 39.508 tasks/s | 2.3034 s | 2.5656 s | 2.5849 s | 0 |
| 240 | 35.402 tasks/s | 4.6831 s | 5.7819 s | 5.8138 s | 0 |

每个 level 都硬性检查：session 唯一、event chain、stop reason、tool budget、合法 status、incomplete-evidence action leak、side-effect confirmation。所有 level `failures=[]`。

这些数字是**当前代码在 GitHub-hosted Ubuntu runner 上的本地 runtime 压力**，不包含真实外部认知引擎、MCP 网络、复杂媒体解析和企业数据库，因此不能被宣传成真实业务 QPS。

---

## 6. Chromium Browser E2E

CI 使用真实 Playwright Chromium 启动 Uvicorn 与独立 `ECOMEVO_DATA`，验证：

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

首次 E2E 确实抓到过一个测试断言错误：DOM 已为 `class="rightbar open"`，但 Playwright string `to_have_class` 被错误当成正则使用。该测试被修成 compiled regex 后，完整 browser job 通过。没有通过删除移动端断言来换取绿灯。

这证明了 Chromium 路径；**没有证明真实 Safari 或 Microsoft Edge 实机路径**。

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

当前代码可以被描述为：

- 仓库内功能回归已闭环；
- Gold Set / adversarial authority gate 已闭环；
- durable execution 与 crash-reclaim 机制已落地并回归；
- tenant identity / RBAC / approver audit 已落地并回归；
- current-head 1→240 runtime pressure gate 已闭环；
- 真 Chromium 交互 E2E 已闭环。

但在真实 provider/MCP、企业 SSO、Safari/Edge、生产多节点拓扑未验证之前，仍不应把它表述为“所有生产环境均已验收”或“零缺陷生产就绪”。

核心原则保持不变：**认知自治，权限确定。**
