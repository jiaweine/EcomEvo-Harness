# EcomEvo 性能工程手册

> 本文只记录已经测量的性能数据、当前瓶颈与可复现的工程原则。不同层级的 benchmark 明确分开，避免把微基准当成整机吞吐。

## 1. 性能目标

EcomEvo 的性能不是单一 QPS。生产目标同时包含：

- **Time to Verifiable Decision**：从目标提交到可验证结论的时间；
- **Tool Calls per Completed Task**：完成任务需要多少外部调用；
- **Cost per Completed Task**：模型、MCP、媒体处理与业务 API 成本；
- **p50 / p95 / p99 latency**；
- **Verified Decisions per Operator Hour**；
- **Side-effect safety = 0 unauthorized actions**；
- **Policy learning overhead**；
- **Recovery efficiency**：补证能否减少缺口而不是形成无效循环。

对 Agent Runtime 来说，少一次无价值远程 Tool / Model 调用，通常比把一个 12×12 矩阵计算再优化几十微秒更有价值。

---

## 2. 已有端到端历史压力记录

以下测试发生在 Adaptive Posterior Routing 加入之前，因此只能作为底层 Event Store / Sandbox / Tool Runtime 的工程基线。

### 240 个并发 Runtime tasks

| Metric | Result |
| --- | ---: |
| Throughput | **37.2 runs/s** |
| p50 | **3.74 s** |
| p95 | **5.22 s** |
| p99 | **5.25 s** |
| Event-chain failures | **0** |
| Incomplete-case side-effect leaks | **0** |
| Valid-case failures | **0** |
| Duplicate semantic evolution patches | **0** |

### 80 个 adversarial controllers

| Metric | Result |
| --- | ---: |
| Throughput | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event-chain failures | **0** |
| Side-effect leaks | **0** |
| Unsafe proposals rejected | **80 / 80** |
| Cognitive delegation | **80 / 80** |

这些数字不能直接代表当前 adaptive head。

---

## 3. Adaptive Routing Store 压力实验

为了定位新学习层的数据库开销，使用临时隔离脚本模拟：

- 240 个 routing rounds；
- 每轮 4 个 selected tool credits；
- 共享一个 SQLite routing store；
- 不包含外部 LLM、MCP 网络和多模态解析。

### 3.1 旧逐工具写入

旧实现每个工具分别：

1. 写 global/domain posterior；
2. 再写 global/domain reliability。

近似产生 \(2k\) writer transactions / round。

64 workers：

- throughput 约 **59 rounds/s**；
- p95 约 **2.60 s**；
- p99 约 **3.57 s**。

### 3.2 单轮单事务

现在一轮合并：

```text
BEGIN IMMEDIATE
  global posterior batch update
  domain posterior batch update
  all routing outcomes
  all reliability updates
COMMIT
```

并且 non-stationary decay 每轮只发生一次。

### 3.3 把 posterior inverse 移出 writer lock

第一版 batch writer 仍然在持写锁时执行矩阵 inverse 来计算 residual。压测显示在高 worker 数下仍有明显 lock amplification。

当前实现把 exact posterior inverse / uncertainty 完全放在只读 scoring 路径；writer-hot-path 只维护 bounded outcome-surprise EWMA。

最新隔离结果：

| Workers | Throughput | p50 | p95 | p99 |
| ---: | ---: | ---: | ---: | ---: |
| 16 | **~256.5 rounds/s** | 0.061 s | 0.116 s | 0.160 s |
| 64 | **~299.5 rounds/s** | 0.202 s | 0.421 s | 0.449 s |
| 120 | **~351.5 rounds/s** | 0.306 s | 0.549 s | 0.611 s |

与旧 64-worker 写法相比，隔离 routing-store throughput 约从 **59 → 299.5 rounds/s**，约 **5.1×**；p95 从约 **2.60s → 0.421s**。

这是 routing persistence 微基准，不是端到端业务吞吐。

---

## 4. Read-path 压力

对成熟 store 做 1000 次并发 `prepare_context()` 读取时，隔离结果约：

- throughput **315.8 reads/s**；
- p50 **0.193 s**；
- p95 **0.304 s**；
- p99 **0.486 s**。

`prepare_context()` 一次读取：

- global posterior；
- domain posterior；
- 本轮所有候选工具 reliability。

避免了旧的 per-candidate reliability N+1 query。

---

## 5. Counterfactual learner plumbing

最小 stub Runtime 对当前 production controller 的并发 plumbing 测试：

- 80 concurrent controller runs；
- throughput 约 **238 runs/s**；
- p50 约 **1.66 ms**；
- p95 约 **18.1 ms**；
- p99 约 **24.2 ms**；
- errors **0**；
- domain posterior samples **80**。

该测试只证明 counterfactual credit → batch persist → posterior update 的轻量路径，不包含真实模型、真实工具或媒体解析。

---

## 6. 当前主要成本模型

一个任务的近似墙钟时间：

\[
T_{task}\approx
\sum_t
\max_{a\in S_t}T_{tool}(a)
+
\sum_t T_{controller,t}
+
\sum_t T_{review,t}
+
T_{local}
\]

其中并行工具组主要由最慢工具决定，而不是工具时间简单求和。

当前 `T_local` 包括：

- 12×12 posterior preparation；
- greedy contextual ranking；
- deterministic verifier；
- leave-one-out verifier；
- SQLite event / skill / routing persistence。

真正可能达到秒级的通常是外部认知引擎、企业 MCP、媒体语义提取和业务 API，而不是 posterior 数学本身。

---

## 7. Counterfactual credit 开销上界

每轮最多选 \(k\) 个 adaptive tools。

Difference credit 最多增加：

\[
O(kV)
\]

次 deterministic verifier，其中 \(V\) 是一次本地 verifier 成本。

默认每步工具上限 4，因此单轮 counterfactual 额外 verifier 数量有硬上限。

当前 UI 会显示 `counterfactual_ms`，用于真实任务中观察这部分是否开始成为热点。

---

## 8. Runtime Pulse

右侧任务控制面增加“运行质量”状态，但不展示隐藏思维：

- 任务用时；
- routing policy samples；
- API RTT EWMA；
- routing drift；
- counterfactual verifier time。

这些值用于判断：

- 慢在网络还是本地；
- policy 是否仍处于 cold start；
- 数据分布是否变化；
- counterfactual learning 是否产生异常开销。

---

## 9. Frontend performance

前端性能优化包括：

1. 多个 MutationObserver 合并为一个；
2. DOM 维护通过 `requestAnimationFrame` 合并；
3. Runtime Pulse 有 signature guard，避免自己更新 DOM 再触发无限 observer loop；
4. observer 不监听 attribute mutation；
5. 长任务消息使用 `content-visibility:auto`，减少离屏消息 paint；
6. motion 主要使用 opacity / transform；
7. `prefers-reduced-motion` 下关闭非必要动画。

性能目标是让“任务越来越长”时，UI 不因为历史消息数线性增加重绘压力。

---

## 10. SQLite 的明确边界

WAL 可以让 reader 与 writer 并发，但 SQLite 仍是单 writer。

因此当前优化路线是：

```text
减少事务数量
→ 缩短 writer lock
→ 批量 sufficient statistics
→ 批量 reliability
→ 只读 snapshot scoring
```

如果未来 deployment 需要多节点、高频并发 learner writes，应把 routing/event/skill state 迁移到真正的 multi-writer transactional store 或 durable stream，而不是继续把单节点 SQLite 调参当水平扩展方案。

---

## 11. CI release gate

当前分支加入 `.github/workflows/ci.yml`，自动执行：

- editable packaging install；
- Python compile；
- full `pytest -q`；
- `app.js` / `enhancements.js` syntax；
- HTML parse / DOM id uniqueness；
- CSS brace checks。

CI 不等于 performance benchmark，但它保证后续性能优化不能靠破坏正确性换取数字。

---

## 12. 下一轮完整 performance gate

在生产候选合并前，应重新运行当前 adaptive head 的完整压力矩阵：

### Runtime concurrency

```text
1 / 8 / 32 / 64 / 120 / 240 concurrent tasks
```

记录：

- throughput；
- p50 / p95 / p99；
- SQLite lock wait；
- routing prepare/write/counterfactual time；
- event-chain failures；
- unauthorized side effects；
- tool calls/task；
- cost/task。

### Quality-performance frontier

性能不能只追求更快。还要在同一 gold set 上比较：

\[
\frac{Verified\ Task\ Success}
{Latency\times Tool\ Cost}
\]

以及 posterior policy 相对 cold-start prior 是否减少无效工具调用和 recovery rounds。

### Real dependency matrix

还需要：

- slow / failing MCP；
- model timeout / 429；
- large PDF / image / video；
- WebSocket reconnect；
- long conversation history；
- downstream action `uncertain`；
- process interruption / resume。

---

## 13. 当前性能结论

当前数据支持以下判断：

1. **Adaptive posterior 的 12×12 数学不是主要 CPU 瓶颈。**
2. **旧 adaptive writer 的事务放大曾是明确瓶颈，batching + lock-shortening 已在隔离压测中显著改善。**
3. **真实端到端性能仍主要受外部模型、MCP、媒体处理和 SQLite 单 writer 共同决定。**
4. **最新 adaptive head 仍需要完整 current-head 240 concurrency 重跑，旧 37.2 runs/s 不能直接继承。**
5. **决策性能最终必须和业务 gold set 一起测；减少一次错误远程调用通常比局部 CPU 微优化更重要。**
