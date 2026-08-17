# Autonomous & Self-Evolving Runtime

EcomEvo 的 Runtime 目标不是让模型拥有无限权限，而是让模型在明确的业务安全边界内拥有足够强的自主性：自己观察任务状态、选择信息源、组合工具、派生复核角色、发现停滞、改变探索策略，并把成功与失败沉淀成可复用技能。

## 自主循环

每个任务都会维护一个动态 Task Graph。控制器反复执行：

1. **Observe**：读取当前目标、业务域、强制证据、剩余预算、已完成工具、当前证据缺口、技能后验和元进化策略。
2. **Decide**：选择最能减少不确定性的下一批只读工具，并决定是否派生 specialist。
3. **Act**：只执行 Tool Registry 中实际存在、Sandbox 允许、成本预算内的工具；远程 MCP 参数仍由服务端模板控制。
4. **Review**：保留确定性规则/证据/风险/业务复核，同时允许模型派生额外的反证或专项 specialist。
5. **Verify**：用服务器端 Verifier 检查证据完整性、约束和副作用安全。
6. **Reflect / Replan**：如果缺口仍存在，基于最新观察重新选择路径；连续没有新增证据时触发反证角色和停滞检测。
7. **Stop**：证据足够则进入 BusinessAction 生成；继续探索已无价值则明确请求补证，而不是用模型置信度补齐事实。

Planner 不再负责写死整个执行路线。它提供必须覆盖的确定性安全底线和无模型时的 fallback；真正的运行路线可以按任务动态变化。

## 动态团队拓扑

固定复核角色仍然存在，因为它们提供稳定的业务基线。自主控制器还能在运行时增加只读 specialist，例如反证审查、时间线复核或授权链路复核。

这些动态角色的权限被硬限制：

- 不执行业务副作用；
- 不产生独立业务证据；
- 不能改变 Verifier 规则；
- 只能基于已经核对的工具结果提出发现、风险和下一步核对方向。

当连续两轮 verification fingerprint 没有变化时，Runtime 会判定探索停滞并停止机械循环。

## 自进化技能

自进化不是直接让模型修改 Python 源码。EcomEvo 采用受控 Harness/Skill Evolution：

- 失败任务会把真实 `missing_evidence`、工具轨迹、停滞状态和已有技能作为进化输入；
- 成功但经历恢复的任务也可以把有效轨迹蒸馏为技能；
- 模型可提出技能名、只读策略、偏好工具和触发条件；
- 服务端过滤不存在的工具以及任何试图降低证据/确认要求的策略；
- 候选先执行 deterministic shadow replay；
- 只有回归分数没有下降且达到业务域当前 promotion threshold 时才晋升；
- 相同 pathology niche 只保留更强代表，形成质量多样性 archive。

## Bayesian skill posterior

技能被真实任务使用后会持续更新 Beta 后验：成功增加 `alpha`，失败增加 `beta`。选择技能时综合：

- 后验成功概率；
- shadow replay 分数；
- 当前目标/证据缺口与 trigger terms 的匹配。

连续真实失败会降低技能后验并最终触发退役，因此一次离线 replay 通过并不意味着永久可信。

## Meta-evolution

每个业务域还维护自身的进化策略：

- `promotion_threshold`：候选技能进入活跃库需要达到的 shadow 分数；
- `retirement_threshold`：活跃技能长期表现过低时的退役阈值；
- `exploration`：控制每轮自主工具/委派容量的探索强度。

如果使用技能后失败，系统会略微提高晋升要求并增加探索；如果技能持续成功，则逐步减少不必要探索。所有参数都有硬边界，不允许元进化关闭证据门槛或人工确认。

## 不可变安全不变量

以下能力不属于可进化范围：

- Verifier 的证据完整性硬门槛；
- Sandbox 的副作用限制；
- BusinessAction 的人工确认要求；
- 上传文件和证据完整性校验；
- 事件 hash chain；
- 下游结果不确定时的 `uncertain` 状态；
- “模型/历史/技能不是独立证据”的原则。

这使 EcomEvo 可以持续提高自主性和策略质量，同时避免把“自进化”变成自我授权。

## Runtime observability

Runtime 事件会额外记录：

- `autonomy.decided`
- `autonomy.decision_rejected`
- `agent.delegated`
- `topology.mutated`
- `autonomy.stagnated`
- `evolution.patch`
- `evolution.distilled`
- `evolution.reused`

`RuntimeSummary` 同时提供 autonomy steps、delegations、skills used、evolution events 和 task graph，方便后续做运行审计、可视化与线上策略评估。

## 配置

自主循环使用三项可选环境变量：

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

代码会再次施加硬上限，环境变量不能把自主循环放大为无限循环。
