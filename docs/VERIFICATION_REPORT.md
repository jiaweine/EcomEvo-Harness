# EcomEvo 工程验证与压力记录

## 当前结论

当前自主运行时、Adaptive Posterior Routing 与产品 UI 已完成多轮专项检查。

在已经实际执行的范围内，没有观察到：

- event chain 被破坏；
- evidence-incomplete case 泄漏高影响 side effect；
- 模型候选绕过 sandbox / verifier；
- routing learner 获得业务动作权限；
- adaptive posterior 因并发共享临时状态而串任务。

本报告只记录实际执行过的检查。**最新 adaptive routing head 尚未通过仓库级 CI / 完整 pytest 矩阵，因此这里不把旧的回归结果冒充当前 head 的完整验收。**

---

## Adaptive Posterior Routing 专项验证

### 1. Posterior 能覆盖 cold-start prior

临时测试构造两个候选：

- 工具 A 在 cold-start prior 下 coverage / authority 更强；
- 工具 B 初始更弱，但模拟环境持续给 B 高 verifier marginal credit，A 无边际贡献。

初始排序：

| 工具 | Cold-start score |
| --- | ---: |
| A | **2.4180** |
| B | **1.1430** |

经过 360 个在线 routing sample 后：

| 工具 | Learned score |
| --- | ---: |
| A | **0.4766** |
| B | **0.9997** |

此时 domain adaptive activation 为 **0.8257**。

结论：posterior 可以把排序从 A>B 学成 B>A，cold-start 系数不是永久价值函数。

### 2. Global → Domain transfer 有界

在全局 posterior 已有足够样本、但 `risk_review` 还是新业务域时：

```text
mode       = global_transfer
activation = 0.18
```

新业务域可以利用少量全局经验，但不会直接继承成熟领域的完整策略。

### 3. Tool reliability posterior

对两个工具分别模拟 20 次连续失败 / 成功：

| 工具 | Reliability posterior |
| --- | ---: |
| flaky | **0.0833** |
| stable | **0.9167** |

业务证据价值和工具运行稳定性被分开建模。

### 4. Deterministic UCB

相同 posterior、相同 context、相同 exploration 参数重复计算，routing score 与 uncertainty 完全一致。

生产探索不是随机 Thompson Sampling，因此可复现、可审计。

---

## Verifier Difference Credit 专项验证

使用隔离 stub Runtime 模拟：

```text
AutonomousController
  → autonomy.decided
  → tools.completed
  → verification.checked
  → routing.policy.updated
```

一个真实有贡献的只读工具在 leave-one-out verifier 中：

```text
full verifier potential      > without-tool potential
marginal credit              = 0.9
routing.policy.updated       = emitted
credit_method                = verifier_leave_one_out
```

并确认：

- credit 写入 SQLite routing outcome；
- domain posterior sample +1；
- tool reliability 同步更新；
- learner exception 被隔离为 `routing.policy.learning_error`，不改变 live task 的 verifier / sandbox / authority 路径。

这些专项脚本只在临时目录运行，没有提交到仓库。

---

## Runtime 路由与安全检查

此前专项验证覆盖：

- EvoGain 能把主体/规则缺口对应工具排在无关风险扫描之前；
- missing evidence 通过函数参数显式传递，不保存在共享 policy 临时字段；
- 模型提出 unknown / side-effect / requires-confirmation 工具仍由 deterministic sanitizer 拒绝；
- 低预期信息增益候选不会因为模型排在前面就自动执行；
- fallback 保持同一 sandbox / verifier 边界；
- specialist 输出不是独立证据；
- routing posterior 只影响 read-only cognition。

当前 adaptive 生产绑定为：

```text
EcomEvoEngine
  → CounterfactualAdaptiveAutonomousController
  → CounterfactualAdaptiveDecisionPolicy
  → AdaptiveRoutingStore
```

`Sandbox`、`DecisionVerifier`、`GovernanceBoundary` 和 `BusinessAction` 不在 learner 参数空间中。

---

## UI 静态检查

产品 UI 此前已执行：

- HTML parser 完整解析；
- 关键 DOM id 唯一性检查；
- JS 语法检查；
- CSS rule brace 配对；
- 桌面 / 中屏 / 手机响应式检查；
- `prefers-reduced-motion` 降级；
- 产品 HTML 不展示底层模型/服务品牌；
- 多模态输入在首屏和 composer 中均为一级能力；
- CJK typography layer 现在从 HTML 首帧静态加载，不再等待增强脚本动态注入。

当前执行环境没有完成可靠的真实 Chromium screenshot E2E，因此正式生产合并前仍应在 Chrome / Safari / Edge 跑视觉回归。

---

## UI bug audit

已处理：

1. approval 请求返回 `approved / failed / uncertain` 时不再统一提示“已完成”；
2. provider/model 品牌不再直接泄漏到产品 UI；
3. 网络和任务创建异常有可见反馈；
4. 首屏由等尺寸卡片墙改为任务导向结构；
5. 图片、视频、音频、文档、表格成为 composer 一级输入；
6. 右栏明确区分轨迹、证据、执行和资料；
7. CJK 非标准 700+ 合成字重、7–9px 微型状态字和字体延迟加载问题已收口。

---

## Shared SQLite Runtime 压力记录

历史同分支工程压力测试：240 个 Runtime 任务并发共享同一个 SQLite event/evolution store。

| 指标 | 结果 |
| --- | ---: |
| Throughput | **37.2 runs/s** |
| p50 | **3.74 s** |
| p95 | **5.22 s** |
| p99 | **5.25 s** |
| Event-chain failures | **0** |
| Side-effect leaks on incomplete cases | **0** |
| Valid-case failures | **0** |
| Duplicate semantic evolution patches | **0** |

这些数据是在 adaptive posterior routing 加入之前记录的，**不能直接当成新算法的最终吞吐量 benchmark**。新 routing 每个 rank batch 增加小型 12×12 posterior 计算，并对本轮 adaptive-selected 工具增加有界 verifier leave-one-out，因此应在最终环境重新测延迟。

---

## Adversarial Controller 压力记录

历史同分支测试：80 个并发控制器持续提出非法高影响动作，同时提出合法只读核对与 cognitive delegation。

| 指标 | 结果 |
| --- | ---: |
| Throughput | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event-chain failures | **0** |
| Side-effect leaks | **0** |
| Unsafe proposals rejected | **80 / 80** |
| Cognitive delegation | **80 / 80** |

这些同样属于 adaptive posterior 之前的安全压力记录；安全边界代码仍然保留，但最新 head 仍应重新做同规模压力复测。

---

## 当前没有完成的验证

最新 adaptive routing head 仍需要：

- 完整 `pytest -q`；
- 当前 head 的 240 并发复测；
- 当前 head 的 80 malicious-controller 复测；
- 真实 Chrome / Safari / Edge screenshot + keyboard 回归；
- 真实云端 / 自托管认知引擎网络和配额验证；
- 真实企业 MCP 鉴权、schema、最小权限和幂等验证；
- 真实业务 gold set；
- CI eval release gate；
- 相同模型 / 工具 / token-cost budget 的外部 benchmark；
- posterior policy 的 off-policy / replay promotion gate。

仓库当前没有可直接触发的 `.github/workflows` 路径，因此本轮没有声称 GitHub Actions 已通过。

---

## 生产原则

- 公网入口放在企业 SSO / API Gateway / 反向代理之后；
- 高影响工具使用最小权限与下游幂等键；
- `uncertain` 状态必须先查真实业务系统再决定是否重试；
- 模型、memory、skill、routing posterior 与历史回复都不能绕过 evidence gate；
- learner 可以改变 read-only ranking，但不能扩大自己的 action space；
- 新 posterior policy 在真实生产放大 activation 前，应通过 gold-set / replay gate。
