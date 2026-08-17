# Autonomous & Self-Evolving Runtime

EcomEvo 的自主性建立在一个明确边界上：**模型负责认知候选，Runtime 负责工具政策，Verifier 负责业务事实门槛，人工保留高影响动作授权。**

这不是“让模型拥有更多权限”，而是让系统在安全边界内拥有更强的任务自主性。

## EvoLoop

每个任务维护动态 Task Graph，并循环执行：

1. **Observe**：读取目标、业务域、强制证据、当前缺口、工具结果、剩余预算、已验证技能；
2. **Decide**：模型提出少量只读候选工具和可选 cognitive delegation；
3. **EvoGain Route**：Runtime 对合法候选重新计算预期证据信息增益；
4. **Act**：并行执行被选中的只读工具；
5. **Review**：规则、证据、风险、业务 specialist 与可选动态 specialist 做交叉复核；
6. **Verify**：deterministic verifier 检查真实证据硬门槛与副作用安全；
7. **Reflect / Replan**：证据不足时基于最新状态重新规划；
8. **Stop**：通过验证，或继续探索已经没有足够信息价值时停止。

Planner 不再写死完整路线。它只保留业务域、强制证据、成本约束和无模型时的安全 fallback。

## EvoGain

### 为什么需要第二层路由

模型很擅长提出可能的下一步，但模型输出顺序并不天然代表最优工具策略。尤其在更小、开源权重或成本更低的控制器上，如果 Runtime 直接照模型列表执行，容易出现：

- 先调用与当前 missing evidence 无关的工具；
- 同一轮多次查相似证据通道；
- 在已经得到足够信息后仍继续调用；
- 高成本工具抢占低成本、高价值工具预算；
- skill 推荐与当前真实缺口脱节。

因此 EcomEvo 把“候选生成”和“执行选择”拆开。

### 可审计评分维度

EvoGain 对每个合法候选计算：

- **coverage**：与当前 missing evidence 的语义覆盖；
- **authority**：可信企业 read tool 是否提供明确 evidence tags；
- **novelty**：该工具此前已经成功使用过多少次；
- **skill support**：当前活跃技能对该工具的 posterior 支持；
- **contradiction value**：是否有能力发现反证、规则冲突或风险信号；
- **cost**：工具成本；
- **diversity overlap**：和本轮已选工具是否覆盖同一证据通道。

简化表示：

```text
utility(tool)
  = evidence coverage
  + source authority
  + novelty
  + posterior skill support
  + contradiction value
  - same-round overlap
  --------------------------------
             execution cost
```

这些分数只用于 **routing**，绝不进入 Business Verifier，也绝不变成业务置信度。

### Greedy diversity selection

不是简单按单工具分数 Top-K。

每选中一个工具，EvoGain 会对后续候选计算证据通道重叠惩罚，使同一轮更倾向于覆盖不同信息来源。例如主体核验和规则核验可以一起被选中，而无关风险扫描会因为信息增益低被放弃。

### Utility floor

候选低于最低预期信息增益时不会执行。

这是停止机制的一部分：**没有值得查的新东西，就不为了“Agent 看起来很忙”继续 Tool Calling。**

### Concurrency rule

当前 missing evidence 每次显式作为函数参数传入 EvoGain。不存在共享的临时 routing state，因此多个任务共用同一个 Runtime 时不会因为证据缺口串线改变彼此的工具排序。

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

## Dynamic cognitive delegation

固定 specialist 提供稳定业务基线，自主控制器还可以增加只读复核角色。

动态角色必须满足：

- 不执行任何副作用；
- 不产生独立业务证据；
- 不能改变 verifier；
- 只能使用已经核对的工具结果；
- 只输出发现、风险和下一步只读核对方向。

## Stagnation-driven topology mutation

连续 verification fingerprint 不变化，意味着当前路线没有减少缺口。

Runtime 会：

1. 标记 stagnation；
2. 可增加反证审查角色；
3. 尝试不同证据通道；
4. 连续无增益后停止；
5. 把失败轨迹送入 skill evolution。

## Skill evolution

### Candidate generation

进化输入来自真实运行轨迹：

- missing evidence；
- 成功/失败工具；
- 是否 stagnated；
- 当前技能；
- verifier 结果。

模型可以提出候选技能，但只允许包含：

- 只读策略；
- 偏好工具；
- trigger terms；
- 任务核对建议。

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

posterior 参与 routing，但不是业务证据。

### Quality-diversity archive

技能按 domain + pathology niche 竞争。

同一 niche 只保留质量更强代表，防止 prompt/skill 库无限膨胀。

### Retirement

活跃技能真实使用次数足够后，如果 posterior 长期低于 retirement threshold，会进入 retired 状态，不再参与在线路由。

## Meta-evolution

每个业务域独立维护：

- `promotion_threshold`；
- `retirement_threshold`；
- `exploration`。

使用技能失败时，系统会略微提高晋升要求并增加探索；技能持续成功时，逐步降低无意义探索。

所有元参数都有硬边界。

## Open-weight controllers

EcomEvo 支持把当前开源权重 / 自托管 OpenAI-Compatible 服务作为认知控制器。

Runtime 不把“开源模型更便宜”理解成“可以降低安全要求”。相反，EvoGain 的作用之一，就是把候选生成与最终工具政策分离，让较小控制器仍然工作在 deterministic policy envelope 内。

自动路由可以优先使用部署方提供的开源文本引擎处理常规规划与工具协作；真正需要图片、音频或扫描文档时再选择具备相应能力的引擎。

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

## Auditable events

Runtime 记录：

- `autonomy.decided`；
- `autonomy.decision_rejected`；
- `agent.delegated`；
- `topology.mutated`；
- `autonomy.stagnated`；
- `evolution.patch`；
- `evolution.distilled`；
- `evolution.reused`。

`autonomy.decided` 还会记录 compact EvoGain selection trace：工具、是否选中、routing utility、coverage、authority、novelty、skill support、cost。

这是可审计的工具政策摘要，不是隐藏 chain-of-thought。

## Configuration

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

代码会再次施加硬上限，环境变量不能创建无限循环。
