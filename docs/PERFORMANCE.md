# EcomEvo 性能工程手册

> 本文只记录已经测量的数据、当前瓶颈和可复现的工程原则。不同 benchmark 层级严格分开，避免把微基准、Hosted CI runtime 或真实业务 QPS 混为一谈。

## 1. 性能目标

EcomEvo 的性能不是单一 QPS，而是同时关注：

- Time to Verifiable Decision；
- Tool Calls per Completed Task；
- Cost per Completed Task；
- p50 / p95 / p99 latency；
- Verified Decisions per Operator Hour；
- unauthorized side effects = 0；
- evidence-gate bypass = 0；
- policy learning overhead；
- recovery / resume efficiency。

对 Agent Runtime 来说，少一次无价值远程 Tool / Model 调用通常比把 12×12 本地矩阵再优化几十微秒更有意义。

---

## 2. Current-head 1→240 Runtime Gate

当前 CI 对 adaptive runtime 自动运行以下并发层级：

```text
1 / 8 / 32 / 64 / 120 / 240 concurrent tasks
```

2026-08-21 当前融合工作树在本地容器的一次可复现运行：

| 并发 | Throughput | p50 | p95 | p99 | Safety failures |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 15.605 tasks/s | 0.0640 s | 0.0640 s | 0.0640 s | 0 |
| 8 | 27.953 tasks/s | 0.2169 s | 0.2326 s | 0.2326 s | 0 |
| 32 | 27.643 tasks/s | 0.8551 s | 0.8885 s | 0.8942 s | 0 |
| 64 | 25.535 tasks/s | 1.8733 s | 2.0178 s | 2.0245 s | 0 |
| 120 | 29.891 tasks/s | 2.8340 s | 2.8697 s | 2.8997 s | 0 |
| 240 | 31.160 tasks/s | 5.6841 s | 6.1374 s | 6.1649 s | 0 |

每个 level 硬检查：

- session id 唯一；
- event chain 有效；
- stop reason 非空；
- tool cost 不越预算；
- status 合法；
- evidence-incomplete 不产生 action；
- side-effect action 必须 requires_confirmation；
- runtime 不得自主把 action 推进成 executed/approved。

这些是**当前代码的本地 runtime 压力**，不是 GitHub-hosted runner 或生产数据。它们不包含真实外部模型、MCP 网络、复杂媒体解析或企业数据库，不能作为真实业务 QPS 宣传。

---

## 3. 历史 Runtime 基线

Adaptive Posterior Routing 加入前，历史 240 task 底层 Runtime 压力记录：

| Metric | Result |
| --- | ---: |
| Throughput | 37.2 runs/s |
| p50 | 3.74 s |
| p95 | 5.22 s |
| p99 | 5.25 s |
| Event-chain failures | 0 |
| Incomplete-case side-effect leaks | 0 |
| Valid-case failures | 0 |
| Duplicate semantic evolution patches | 0 |

历史 80 adversarial controllers：

| Metric | Result |
| --- | ---: |
| Throughput | 29.3 runs/s |
| p50 | 1.51 s |
| p95 | 2.20 s |
| Event-chain failures | 0 |
| Side-effect leaks | 0 |
| Unsafe proposals rejected | 80 / 80 |
| Cognitive delegation | 80 / 80 |

这些历史数字只作为底层工程演进背景，不能拿来冒充当前 adaptive head 的结果。

---

## 4. Adaptive Routing Store 微基准

为了定位 learner persistence 开销，曾使用隔离 routing-store 压测：

- 240 routing rounds；
- 每轮 4 selected-tool credits；
- 共享 SQLite routing store；
- 不包含外部认知引擎、MCP、媒体处理。

旧逐工具写法在 64 workers 下约：

- 59 rounds/s；
- p95 2.60 s；
- p99 3.57 s。

当前 batch writer + inverse-outside-writer-lock 隔离结果：

| Workers | Throughput | p50 | p95 | p99 |
| ---: | ---: | ---: | ---: | ---: |
| 16 | ~256.5 rounds/s | 0.061 s | 0.116 s | 0.160 s |
| 64 | ~299.5 rounds/s | 0.202 s | 0.421 s | 0.449 s |
| 120 | ~351.5 rounds/s | 0.306 s | 0.549 s | 0.611 s |

64-worker 隔离 throughput 约从 59 → 299.5 rounds/s，约 5.1×。这是 persistence microbenchmark，不是完整 Runtime QPS。

主要变化：

- 一轮 posterior/outcome/reliability 使用一次 writer transaction；
- non-stationary decay 每 parallel batch 只发生一次；
- exact posterior inverse 在 read/scoring path，不占 SQLite writer lock；
- candidate reliability 批量读取，避免 N+1。

---

## 5. Read-path / Counterfactual Plumbing

成熟 routing store 上，1000 次并发 `prepare_context()` 历史隔离结果约：

- 315.8 reads/s；
- p50 0.193 s；
- p95 0.304 s；
- p99 0.486 s。

80 concurrent minimal counterfactual-controller plumbing：

- ~238 runs/s；
- p50 ~1.66 ms；
- p95 ~18.1 ms；
- p99 ~24.2 ms；
- errors 0；
- domain posterior samples 80。

两类数据都只证明本地 routing/credit/persistence plumbing，不包含真实远程依赖。

---

## 6. Durable Execution 的性能语义

当前消息路径：

```text
request
→ asset snapshot integrity check
→ turn lease
→ atomic user/message.accepted/durable-job commit
→ low-latency BackgroundTasks claim OR polling worker claim
→ cognitive runtime
→ atomic assistant/action/answer.ready/job-success commit
```

性能设计重点：

- `BackgroundTasks` 只是低延迟触发器，不决定可靠性；
- durable worker poll 默认 1s，代码 clamp 0.25..10s；
- job lease 默认 120s，代码 clamp 60..600s；
- job crash 后可被其他 worker reclaim；
- asset SHA snapshot 防止 resume 时证据被静默替换；
- active job/turn 阻止当前 evidence snapshot 被新上传资料改变。

真正生产多节点时，SQLite WAL 的单 writer 仍是容量边界；不要把跨进程 reclaim 误解成无限水平扩展。

---

## 7. WebSocket / Frontend 性能

WebSocket 的权威顺序来自 SQLite `task_events`：

- process-local queue 只做低延迟 wake hint；
- `after_id` 支持 reconnect 增量续传；
- queue/poll 唤醒后都按 SQLite id drain；
- backlog 超过一批时立即继续 drain，不人为等待下一个 poll；
- 跨 worker 写入不会被本进程更快的 queue event 跳过。

前端性能硬化包括：

- MutationObserver 工作合并并通过 `requestAnimationFrame` 批处理；
- Runtime Pulse signature guard；
- 不观察 attribute mutation；
- 长对话 `content-visibility:auto`；
- 动效以 opacity/transform 为主；
- `prefers-reduced-motion` 硬降级；
- WebSocket cursor 按 conversation 有界缓存；
- 多标签页只在需要时 reconcile 当前 conversation。

---

## 8. Browser Performance / Interaction Gate

CI 使用真实 Chromium + Playwright 启动 Uvicorn，覆盖：

- 首屏；
- provider UI；
- scene switching；
- durable message round；
- Runtime Pulse；
- keyboard command palette；
- 390×844 drawer；
- same-conversation two-tab reconciliation；
- page/console errors。

这个 gate 的目标不是给浏览器 FPS 做微基准，而是防止性能/状态优化引入用户可见交互回归。

---

## 9. 成本模型

一个任务近似墙钟时间：

\[
T_{task}\approx
\sum_t\max_{a\in S_t}T_{tool}(a)
+\sum_t T_{controller,t}
+\sum_t T_{review,t}
+T_{local}
\]

其中并行工具组主要由最慢工具决定。

`T_local` 包括：

- posterior/context preparation；
- contextual ranking；
- deterministic verifier；
- leave-one-out verifier；
- event/skill/routing/job persistence。

实际企业部署中，秒级延迟通常更多来自真实 provider、MCP、媒体语义提取和业务 API，而不是 posterior 数学。

---

## 10. SQLite 的明确边界

当前 WAL 路线：

```text
减少事务数量
→ 缩短 writer lock
→ batch sufficient statistics
→ batch reliability
→ durable job lease
→ read-only snapshot scoring
```

SQLite 仍是单 writer。如果目标部署需要多节点、高频并发 learner/job writes，应迁移 routing/event/skill/job state 到合适的生产 transactional store / durable stream，并重新跑现有 Gold Set、authority、pressure 和 browser gates。

---

## 11. CI Performance / Release Gates

当前 `.github/workflows/ci.yml` 有三条独立 job：

### regression

- editable install；
- Python compile；
- full pytest；
- Gold Set fresh + persisted replay；
- adversarial authority；
- all production JS syntax；
- loader/HTML/DOM/CSS static checks。

### pressure

- 1 / 8 / 32 / 64 / 120 / 240 current-head runtime；
- correctness / budget / authority invariants；
- 输出 throughput + p50/p95/p99；
- 不用 hosted-runner QPS 阈值制造 flaky gate。

### browser-e2e

- real Chromium；
- durable UI round；
- mobile + keyboard + multi-tab；
- page/console error gate；
- 失败时上传 trace/screenshot。

---

## 12. 仍需真实环境测量

仓库内 current-head gate 已完成；下一层必须来自目标部署环境：

- real provider latency / timeout / 429 / cost；
- real MCP auth/schema/idempotency/downstream reconciliation；
- large/hostile PDF/image/video/audio；
- Safari / Edge target devices；
- enterprise proxy/SSO；
- multi-node production DB/queue；
- real business Gold Set 的 decision-quality frontier。

最终优化目标应同时看：

\[
\frac{Verified\ Task\ Success}{Latency\times Tool\ Cost}
\]

不能为了吞吐牺牲 evidence completeness 或 authority safety。

---

## 13. 当前性能结论

1. adaptive posterior 的小矩阵数学不是主要 CPU 瓶颈；
2. writer transaction amplification 曾是明确瓶颈，batching/lock shortening 已改善；
3. current-head 1→240 runtime gate 已自动化并成功运行；
4. 240 并发在本轮记录中为 31.160 tasks/s、p95 6.1374s、0 safety failures；
5. 真 Chromium 交互 E2E 已成为持续 gate；
6. 真实生产性能仍必须在真实 provider/MCP/media/DB 环境重新测量。
