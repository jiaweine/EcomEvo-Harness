# EcomEvo 工程验证与压力记录

## 当前结论

当前 adaptive runtime、EvoGain-APR、counterfactual learner 与产品 UI 已进入自动回归门禁。

最新代码 head 已在 GitHub Actions 的 Ubuntu 24.04 / Python 3.11 环境完成：

- 系统媒体依赖安装；
- editable package build/install；
- `python -m compileall -q ecomevo`；
- 完整 `pytest -q`；
- `frontend/app.js` 与 `frontend/enhancements.js` 语法检查；
- HTML parser + DOM id 唯一性；
- `visual.css` 与 `product-polish.css` brace 检查。

该 CI run 已完成且 conclusion=`success`。当前仍有一个非阻塞依赖警告：Starlette TestClient 提示其当前 `httpx` 兼容路径已 deprecated，后续依赖升级时应单独清理。

在已经执行的范围内，没有观察到：

- event chain 被破坏；
- evidence-incomplete case 泄漏高影响 side effect；
- 模型候选绕过 Sandbox / Verifier；
- routing learner 获得业务动作权限；
- adaptive posterior 因共享临时状态而串任务；
- learner exception 改变业务执行结果。

---

## 1. CI 真实暴露并修复的问题

新增 release gate 后，不只是“证明代码能跑”，还真实发现了此前人工检查遗漏的问题：

1. **Python packaging 不可重复**：setuptools 自动发现把 `outputs` / `frontend` 当成顶层 package candidate，导致 `pip install -e .[dev]` 失败。现在 package discovery 显式只包含 `ecomevo*`。
2. **CI 与生产媒体环境不一致**：合法 WAV 测试因 runner 缺 `ffprobe` 失败，而 Docker 生产镜像本来就依赖 ffmpeg。CI 现在显式安装 ffmpeg。
3. **客户产品文案契约漂移**：产品页曾被技术语言侵入，测试要求的“商业决策工作台”“待确认操作”和外部服务数据发送披露被恢复。
4. **客户表面算法词泄漏**：产品页不展示内部算法/模型术语，复杂算法只保留在技术文档和运行审计层。

这些问题均通过修改产品/工程实现解决，没有通过弱化测试换取绿灯。

---

## 2. Adaptive Posterior Routing 学习验证

### Posterior 可以覆盖 cold-start prior

构造两个候选：A 的初始 prior 更强，B 的真实 verifier marginal contribution 长期更高。

初始：

| Tool | Cold-start score |
| --- | ---: |
| A | **2.4180** |
| B | **1.1430** |

360 个在线 routing samples 后：

| Tool | Learned score |
| --- | ---: |
| A | **0.4766** |
| B | **0.9997** |

测试环境的 domain adaptive activation 为 **0.8257**。

这验证了 cold-start prior 不是永久价值函数。

### Global → Domain transfer 有界

新业务域在全局 posterior 已成熟时测试得到：

```text
mode       = global_transfer
activation = 0.18
```

新域可以借用少量全局经验，但不会直接继承成熟领域的完整策略。

### Tool reliability posterior

对两个工具分别模拟 20 次连续失败 / 成功：

| Tool | Reliability posterior |
| --- | ---: |
| flaky | **0.0833** |
| stable | **0.9167** |

业务证据价值与运行稳定性分开建模。

### Deterministic UCB

相同 posterior、context、exploration 参数重复计算时，score 与 uncertainty 一致；生产探索不是随机采样。

---

## 3. Contextual Abstention

Adaptive 路由已经不再使用固定 `0.42` absolute utility floor。

当前做法比较候选工具与**同一任务状态下的 contextual no-op**：

```text
advantage(tool) = score(tool) - score(abstain | same state)
```

只有 `advantage > 0` 才执行工具。

这样 posterior 不仅学习“工具之间谁更好”，也参与学习“什么时候继续调用已经不值得”。业务 required-evidence gate 仍由 Verifier 独立拥有，不会被 abstention 绕过。

---

## 4. Verifier Difference Credit

生产 credit 使用 deterministic leave-one-out verifier。

验证势能同时要求 verifier score 与 evidence completeness，使用调和形式，使任一维度很低时整体势能都受限。

对本轮被 adaptive router 选中的结果：

```text
full verifier potential
  - verifier potential without this result
  = marginal contribution
```

再按工具成本归一化后更新 routing posterior。

专项 stub Runtime 已确认：

- `routing.policy.updated` 会实际产生；
- credit 持久化到 SQLite；
- domain posterior sample 增加；
- tool reliability 同步更新；
- learner exception 被隔离为 `routing.policy.learning_error`；
- specialist 自然语言不进入 counterfactual verifier。

临时专项脚本未提交仓库。

---

## 5. Routing Store 写入压力

为定位 adaptive learner 的本地瓶颈，使用隔离 store 压测：

- 240 个 routing rounds；
- 每轮 4 个 learning samples；
- 共享 SQLite store；
- 不包含外部模型 / MCP / 多模态网络开销。

### 旧逐工具双事务

64 workers：

- throughput 约 **59 rounds/s**；
- p95 约 **2.60 s**；
- p99 约 **3.57 s**。

### 当前 batch writer + inverse-outside-lock

| Workers | Throughput | p50 | p95 | p99 |
| ---: | ---: | ---: | ---: | ---: |
| 16 | **~256.5 rounds/s** | 0.061 s | 0.116 s | 0.160 s |
| 64 | **~299.5 rounds/s** | 0.202 s | 0.421 s | 0.449 s |
| 120 | **~351.5 rounds/s** | 0.306 s | 0.549 s | 0.611 s |

64-worker 隔离吞吐约从 **59 → 299.5 rounds/s**，约 **5.1×**。

主要变化：

- 一轮 posterior + outcome + reliability 只提交一次 writer transaction；
- parallel batch 只执行一次 non-stationary decay；
- posterior matrix inverse 不发生在 SQLite writer lock 内；
- tool reliability 在 scoring round 开始时一次批量读取。

这些是 routing persistence 微基准，不是完整业务 Runtime QPS。

---

## 6. Routing Read Path

成熟 store 上 1000 次并发 `prepare_context()` 隔离测试约：

- throughput **315.8 reads/s**；
- p50 **0.193 s**；
- p95 **0.304 s**；
- p99 **0.486 s**。

一次 snapshot 同时读取 global/domain posterior 与当前候选工具 reliability，避免 per-candidate N+1 query。

---

## 7. Counterfactual Controller Plumbing

最小 stub production controller 的 80 concurrent runs：

- throughput 约 **238 runs/s**；
- p50 约 **1.66 ms**；
- p95 约 **18.1 ms**；
- p99 约 **24.2 ms**；
- errors **0**；
- domain posterior samples **80**。

该数据只验证 counterfactual credit → batch persist → posterior update 的本地 plumbing，不包含真实认知引擎、MCP、媒体解析。

---

## 8. 历史完整 Runtime 压力基线

Adaptive posterior 加入前，240 个 Runtime tasks 共享 SQLite event/evolution store：

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

80 个 adversarial controllers：

| Metric | Result |
| --- | ---: |
| Throughput | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event-chain failures | **0** |
| Side-effect leaks | **0** |
| Unsafe proposals rejected | **80 / 80** |
| Cognitive delegation | **80 / 80** |

这些是 adaptive 之前的底层 Runtime / safety 工程基线，**不能直接作为当前 adaptive head 的端到端吞吐成绩**。

---

## 9. Frontend / UI 性能验证

当前前端性能硬化包括：

- 多个维护型 MutationObserver 合并为一个；
- observer 的工作通过 `requestAnimationFrame` 合并；
- 不观察 attribute mutation，减少自身样式变化造成的反馈；
- Runtime Pulse 有 signature guard，避免 render → mutation → render 回环；
- 长对话消息使用 `content-visibility:auto`，减少离屏 paint；
- 动效以 opacity / transform 为主；
- `prefers-reduced-motion` 硬降级；
- Runtime Pulse 显示 task elapsed、policy samples、API RTT、drift、counterfactual time，不展示隐藏思维。

CI 对两个 JS 文件做语法检查，并检查 HTML/DOM/CSS 基础结构。

---

## 10. 仍需完成的生产性能验证

最新 adaptive head 已有完整 pytest/CI，但仍需要独立的**当前-head 端到端性能矩阵**：

- 1 / 8 / 32 / 64 / 120 / 240 concurrent business tasks；
- current-head malicious-controller 压测；
- SQLite lock wait / transaction time；
- tool calls per task；
- cost per completed task；
- routing prepare / write / counterfactual latency；
- slow / failing MCP；
- model timeout / 429；
- large PDF / image / video；
- process interruption / resume；
- 真实 Chrome / Safari / Edge visual + keyboard regression；
- real provider / MCP auth / schema / idempotency；
- gold-set decision quality；
- posterior-vs-prior off-policy / replay promotion gate。

完整性能设计见 `docs/PERFORMANCE.md`。

---

## 11. 当前性能结论

当前数据支持：

1. 12×12 posterior 数学不是主要 CPU 瓶颈；
2. adaptive writer 的事务放大曾是明确瓶颈，batching + lock shortening 在隔离压力中已显著改善；
3. 当前端到端延迟仍主要由外部模型、MCP、媒体处理以及 SQLite 单 writer 共同决定；
4. 最新 head 已通过仓库完整自动回归，但尚未取得新的 current-head 240-task 端到端吞吐数字；
5. 决策效率必须和 gold set 一起测，不能只追求 QPS。

---

## 12. 生产原则

- 公网入口放在企业 SSO / API Gateway / 反向代理之后；
- 高影响工具使用最小权限与下游幂等键；
- `uncertain` 状态先查真实业务系统再决定是否重试；
- model、memory、skill、routing posterior 与历史回复不能绕过 evidence gate；
- learner 只能改变 read-only ranking，不能扩大 action space；
- 新 posterior policy 在真实生产放大 activation 前，应通过 gold-set / replay promotion gate。
