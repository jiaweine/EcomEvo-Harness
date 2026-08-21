# Autonomous & Self-Evolving Runtime

EcomEvo 的自主性建立在一个明确边界上：**模型负责提出认知候选，Runtime 负责学习和选择只读工具策略，Verifier 负责业务事实门槛，人工保留高影响动作授权。**

这不是让模型拥有更多权限，而是让系统在硬边界内拥有更强的任务自主性和长期适应能力。

## EvoLoop

每个任务维护动态 Task Graph，并循环执行：

1. **Observe**：读取目标、业务域、强制证据、当前缺口、工具结果、剩余预算、已验证技能；
2. **Decide**：模型提出少量只读候选工具和可选 cognitive delegation；
3. **EvoGain-APR Route**：Runtime 用 adaptive posterior 对合法候选重新排序；
4. **Act**：并行执行被选中的只读工具；
5. **Review**：规则、证据、风险、固定 specialist 与动态 specialist 做交叉复核；
6. **Verify**：deterministic verifier 检查真实证据硬门槛与副作用安全；
7. **Counterfactual Credit**：对本轮自适应选中的工具做 verifier leave-one-out 差分贡献；
8. **Posterior Update**：更新 routing posterior 与 tool reliability posterior；
9. **Reflect / Replan**：证据不足时基于最新状态重新规划；
10. **Stop**：通过验证，或继续探索已经没有足够信息价值时停止。

Planner 不再写死完整路线。它只保留业务域、强制证据、成本约束和无模型时的安全 fallback。

---

## EvoGain-APR

APR 表示 **Adaptive Posterior Routing**。

### 为什么需要第二层路由

语言模型可以提出下一步候选，但候选顺序本身不是执行权。尤其在较小、自托管或成本更低的控制器上，直接照模型输出执行容易出现：

- 先调用与当前 missing evidence 无关的工具；
- 同一轮多次查相似证据通道；
- 已经得到足够信息后仍继续调用；
- 高成本工具抢占预算；
- skill 推荐与当前真实缺口脱节；
- 新工具上线后长期停留在旧 prompt 偏好。

因此 EcomEvo 把“候选生成”和“执行选择”拆开。

### 可学习 context

对每个合法工具，Runtime 构造可审计的上下文特征：

- evidence coverage；
- source authority；
- skill posterior support；
- novelty；
- contradiction value；
- purpose / evidence-channel specificity；
- tool reliability posterior；
- cost pressure；
- same-round redundancy；
- evidence gap pressure；
- recovery context。

这些都来自 Runtime 状态，不来自模型隐藏思维。

### Cold-start prior，不是固定价值函数

生产路由维护 Bayesian linear posterior：

```text
A0 = prior precision
b0 = A0 × prior mean

A ← prior + decay × (A - prior) + x xᵀ
b ← prior_b + decay × (b - prior_b) + reward × x
posterior_mean = A⁻¹ b
```

人工系数只承担冷启动 prior 的作用。

当真实任务数据积累后，posterior 会逐渐替代 cold-start prior，不存在一组永远不变的最终 routing weights。

### Global → Domain transfer

每个 credit 同时更新：

- 全局 routing posterior；
- 当前业务域 routing posterior。

新业务域可以使用少量全局经验，但不会直接继承成熟业务域的全部偏好。

本域样本增多后，domain posterior 权重自动提高。

### Deterministic UCB

生产探索使用 deterministic UCB，而不是随机抽样：

```text
score = posterior_mean + exploration × posterior_uncertainty
```

因此高不确定、但合法且可能有价值的工具仍能得到探索机会，同时保持相同状态下的可复现排序。

### Shadow before adaptive

领域样本不足时，posterior 只记录、不接管生产排序。

达到最小样本后才逐步提高 learned policy activation。若 residual drift 增大，activation 会下降、探索不确定性会提高。

这样可以避免少量早期错误轨迹直接把生产 routing 带偏。

---

## Verifier Difference Credit

EcomEvo 不再用一组固定 reward 权重训练另一组“可学习权重”。

对本轮被 adaptive router 选中的工具结果，Runtime 使用 deterministic verifier 做 leave-one-out：

```text
full       = verifier(all_results)
without_i  = verifier(all_results - tool_result_i)

marginal_credit
  = potential(full) - potential(without_i)
  -------------------------------------------
                  1 + tool_cost
```

其中 verification potential 由：

- verifier score；
- evidence completeness

组成，两者本身都已经归一到 `[0, 1]`。

这个 credit 表示：**拿掉这个工具结果后，可验证状态到底下降了多少。**

它不是完整 Shapley value，也不是严格因果识别，但比手工 reward 权重更直接、更可解释，而且额外 verifier 次数受每步工具上限严格限制。

Specialist 自然语言不参与这次 counterfactual credit，避免“模型表达更像真的”被误学习成证据价值。

---

## Tool Reliability Posterior

工具的业务价值和运行稳定性分开学习。

每个 domain/tool 维护 Beta posterior：

```text
success → alpha + 1
failure → beta + 1
reliability = alpha / (alpha + beta)
```

同时保留全局与领域级 reliability，用于新业务域收缩估计。

这个 reliability 只是 routing feature，不是业务证据。

一个工具可以“非常相关但经常失败”，也可以“稳定但对当前证据缺口没有价值”。两者不会被混成同一个分数。

---

## Non-stationary adaptation

工具生态和模型分布会变化，因此 routing posterior 使用缓慢 decay。

旧经验逐渐衰减，但 cold-start prior 保留。

系统还维护 prediction residual EWMA：

- residual 低：当前 posterior 与环境一致；
- residual 高：可能出现工具质量变化、数据分布变化或模型候选分布变化。

高 residual 时，系统会降低 learned policy activation，并提高 uncertainty exploration。

---

## Budget-aware set selection

不是简单按单工具分数 Top-K。

每选中一个工具，后续候选会重新计算与已选 evidence channels 的 overlap。

`redundancy` 本身也是 posterior feature，因此多样性偏好可以从真实 outcome 学习，而不是永久固定一个减分常数。

同时保持：

- 当前剩余工具预算；
- 每步最大工具数；
- sandbox gate；
- unknown tool rejection；
- side-effect prohibition。

---

## Dynamic Task Graph

Task Graph 记录：

- goal；
- initial plan；
- replan；
- tool batches；
- specialist delegation；
- verification state；
- recovery path。

Graph 是运行轨迹，不是预先固定 DAG。节点由真实任务状态动态增长。

---

## Dynamic cognitive delegation

固定 specialist 提供稳定业务基线，自主控制器还可以增加只读复核角色。

动态角色必须满足：

- 不执行任何副作用；
- 不产生独立业务证据；
- 不能改变 verifier；
- 只能使用已经核对的工具结果；
- 只输出发现、风险和下一步只读核对方向。

---

## Stagnation-driven topology mutation

连续 verification fingerprint 不变化，意味着当前路线没有减少缺口。

Runtime 会：

1. 标记 stagnation；
2. 可增加反证审查角色；
3. 尝试不同证据通道；
4. 连续无增益后停止；
5. 把失败轨迹送入 skill evolution。

---

## Skill evolution

Routing posterior 学“下一步工具策略”；Skill Library 学“可复用业务认知模式”。两者分开更新。

### Candidate generation

进化输入来自真实运行轨迹：

- missing evidence；
- 成功/失败工具；
- 是否 stagnated；
- 当前技能；
- verifier 结果。

模型可以提出候选技能，但只允许包含只读策略、偏好工具、trigger terms 和任务核对建议。

### Shadow before live

候选必须先经过 deterministic replay / regression gate。

只有在不降低回归结果并达到当前业务域 promotion threshold 时，才允许进入活跃技能库。

### Bayesian posterior

活跃技能每次真实使用都会更新 Beta posterior：

```text
success → alpha + 1
failure → beta + 1
posterior mean = alpha / (alpha + beta)
```

posterior 可以影响 routing，但不是业务证据。

### Quality-diversity archive

技能按 domain + pathology niche 竞争。

同一 niche 只保留质量更强代表，防止 prompt / skill 库无限膨胀。

### Retirement

活跃技能真实使用次数足够后，如果 posterior 长期低于 retirement threshold，会进入 retired 状态，不再参与在线路由。

---

## Meta-evolution

每个业务域独立维护：

- `promotion_threshold`；
- `retirement_threshold`；
- `exploration`。

这些元参数会根据真实技能 outcome 缓慢变化，但始终有硬范围约束。

---

## Open-weight controllers

EcomEvo 支持把开源权重 / 自托管 OpenAI-Compatible 服务作为认知控制器。

更强的控制器可以提出更好的候选，但 Runtime 不把模型能力当成业务权限。

Adaptive Posterior Routing 的目的之一，就是减少整个系统对某一个模型“天生会不会排工具”的依赖。

模型升级时：

- Candidate generation 可以变强；
- routing posterior 可以重新适应新候选分布；
- residual drift 可以暴露分布变化；
- Verifier / Sandbox / Authority 不需要跟模型一起重写。

---

## Immutable authority

以下能力不属于可进化范围：

- Verifier 证据硬门槛；
- Sandbox 工具权限；
- BusinessAction 人工确认；
- 文件类型、结构和哈希完整性校验；
- Event Store hash chain；
- 下游不确定状态的 `uncertain` 处理；
- 模型 / 历史 / memory / skill 不是独立证据；
- side-effect 工具不能由 autonomous controller 直接调用。

**Agent 可以改变策略，但不能给自己更多权限。**

---

## Auditable events

Runtime 记录：

- `autonomy.decided`；
- `autonomy.decision_rejected`；
- `routing.policy.updated`；
- `routing.policy.learning_error`；
- `agent.delegated`；
- `topology.mutated`；
- `autonomy.stagnated`；
- `evolution.patch`；
- `evolution.distilled`；
- `evolution.reused`。

`autonomy.decided` 的 EvoGain trace 会记录：

- 是否选中；
- coverage / authority / novelty / skill support；
- policy mode；
- posterior samples；
- prior score；
- posterior mean；
- uncertainty；
- adaptive activation；
- final routing utility。

这是一份可审计策略摘要，不是隐藏 chain-of-thought。

---

## Configuration

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

代码会再次施加硬上限，环境变量不能创建无限循环。

更完整的数学定义见 [`ALGORITHM.md`](ALGORITHM.md)。
