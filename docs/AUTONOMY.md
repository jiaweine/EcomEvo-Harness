# EcomEvo Autonomous & Self-Evolving Runtime

EcomEvo 的目标不是做一个“可以不断 Tool Calling 的聊天机器人”，而是构建一个面向真实业务的 **Autonomous & Self-Evolving Agent Runtime**：

> **Agent 可以自主寻找完成目标的方法，但不能自主扩大自己的业务权限。**

这套 Runtime 同时解决四个问题：

1. **Autonomy**：任务路径不写死，系统根据实时证据状态自主决定下一步；
2. **Adaptation**：同一个任务失败后可以反思、换工具、换复核角色、换路径；
3. **Evolution**：跨任务把失败和恢复成功轨迹蒸馏为技能，并根据真实结果持续淘汰；
4. **Authority**：证据门槛、副作用权限和人工确认始终由确定性代码掌握。

---

## Algorithm Stack

EcomEvo 把自主控制、动态任务图、技能进化和业务权限分成不同层，避免把“更聪明”与“权限更大”混为一件事。

| 能力 | 机制 | 作用 |
| --- | --- | --- |
| Autonomous Loop | Observe → Decide → Act → Review → Verify | 每轮工具结果回来后重新决定下一步 |
| Dynamic Task Graph | Runtime topology | 任务路线按证据状态动态增长 |
| Evidence-Gain Planning | Gap × Tool × Budget × Skill | 优先执行最可能减少不确定性的只读动作 |
| Cognitive Delegation | Dynamic specialist | 运行时增加反证、授权链、时间线等专项复核 |
| Stagnation Detection | Verification fingerprint | 连续没有新证据时停止机械 Retry |
| Skill Distillation | Failure + Recovery traces | 把真实轨迹转成候选策略 |
| Shadow Gate | Replay + regression gate | 候选技能先影子验证再上线 |
| Bayesian Skill Posterior | Beta(alpha, beta) | 用线上成败持续更新技能可信度 |
| Quality-Diversity Archive | Pathology niche | 同类技能竞争，防止经验无限膨胀 |
| Meta-Evolution | Domain policy adaptation | 不同业务域形成不同探索/晋升策略 |
| Deterministic Authority | Verifier + Sandbox + Approval | 模型永远不能自己授权高影响动作 |

---

## EvoLoop：持续自主决策

每个任务都会维护一个动态 Task Graph。Runtime 的核心循环不是“Plan once”，而是：

```text
Observe
  ↓
Decide
  ↓
Act
  ↓
Review
  ↓
Verify
  ↓
Reflect / Replan
  └──────────────→ Observe
```

### Observe

控制器会读取当前任务的：

- `GoalState`
- 业务域
- 强制证据要求
- 当前 `BeliefState`
- 已完成工具及其结果
- `missing_evidence`
- 剩余工具预算
- 已验证历史任务经验
- 当前可用技能
- 技能 Bayesian posterior
- 当前业务域的 evolution policy

这意味着下一步决策建立在**外部可验证状态**上，而不是只建立在模型自己上一轮的自然语言上。

### Decide

有可用模型时，模型可以提出：

- 下一批只读工具；
- 工具参数；
- 并行组；
- 本轮目标；
- specialist 委派；
- 是否停止；
- 对当前失败路径的 reflection。

没有模型时，Runtime 自动退回 deterministic fallback，产品不会因为某一家模型服务不可用就失去基本业务能力。

### Sanitize

模型提出的动作不会直接执行。

`DecisionPolicy` 会把候选逐项过滤：

- Tool Registry 是否真实存在；
- Sandbox 是否允许；
- 是否属于当前业务域；
- 是否超过剩余预算；
- 是否重复调用；
- 是否试图调用 side-effect 工具；
- 是否试图从动态 specialist 获得业务动作权限。

因此模型只能在**服务器预先定义的可行动空间**里自主决策。

### Act

通过过滤的只读动作交给 `PTCExecutor`。

同一个 parallel group 可以并发执行，适合：

- 同时查规则与证据；
- 同时查订单与风险；
- 同时查企业 MCP 与本地附件；
- 同时进行多个互不依赖的证据补全。

### Review

EcomEvo 保留稳定的 deterministic review baseline：

- 规则复核；
- 证据复核；
- 风险复核；
- 业务判定；
- 条件触发的交叉复核。

自主控制器还可以临时增加 cognitive specialist，例如：

- 反证审查；
- 时间线复核；
- 授权链路复核；
- 证据冲突检查；
- 风险信号独立性检查。

这些 specialist **只产生认知意见，不产生独立业务证据**。

### Verify

最终是否“信息够了”，由服务器端 `DecisionVerifier` 决定。

模型不能：

- 用自己的高置信度替代附件/企业数据；
- 把用户描述当成独立强证据；
- 把历史助手回复升级成事实；
- 把 skill guidance 当成证据；
- 因为“已经想了很多轮”就强制结束。

### Reflect / Replan

Verification 未通过时，控制器会重新读取缺口并选择新路径。

如果连续多轮 verification fingerprint 没有变化，Runtime 会认为当前探索正在停滞：

1. 增加反证视角；
2. 尝试不同的只读工具组合；
3. 如果仍然无法增加真实证据，则停止并请求补证。

这比无限 Retry 更适合业务系统，因为**没有新的信息源时，继续推理本身不会产生新的业务事实。**

---

## Dynamic Task Graph

任务图的节点在运行时生成。

一个典型任务可能从：

```text
Goal
 └─ Initial Evidence Plan
```

逐渐生长成：

```text
Goal
 └─ Initial Evidence Plan
     ├─ Attachment Search
     ├─ Policy Lookup
     ├─ Merchant Inspect
     ├─ MCP Merchant Profile
     └─ Review
         └─ Verification Gap
             ├─ Authorization Re-check
             ├─ Counter-evidence Specialist
             └─ Reverification
```

这让系统不需要在任务开始之前预知完整路线。

Task Graph 同时进入运行时事件和 `RuntimeSummary`，便于：

- 调试；
- 审计；
- 可视化；
- 失败分析；
- 轨迹蒸馏；
- 后续 benchmark。

---

## Evidence-Gain Planning

EcomEvo 不鼓励“调用更多工具 = 更智能”。

一个工具是否值得调用，本质上取决于：

```text
expected_value
≈ evidence_gap_reduction
× tool_relevance
× skill_prior
÷ cost
```

实际实现会综合：

- 当前 `missing_evidence`
- 当前任务目标
- 业务域
- 工具描述与能力
- 已执行工具
- 剩余成本预算
- relevant skills
- 模型提出的优先级

这使 Agent 的自主性更接近“主动信息获取”，而不是“随机试工具”。

---

## Cognitive Topology Mutation

传统多 Agent 系统常见两种极端：

1. 角色完全固定，任务变化但团队不变；
2. 模型随意 spawn agent，成本和权限快速失控。

EcomEvo 采用中间路线：

- 保留 deterministic specialist 作为稳定基线；
- 允许自主控制器在明确上限内派生额外 cognitive role；
- 动态角色只读取已核对工具结果；
- 动态角色不进入 Tool Registry；
- 动态角色不直接产生 BusinessAction；
- 动态角色不能修改 Verifier。

因此可以让“团队结构”随任务改变，而不让“权限结构”随任务改变。

---

# Self-Evolution

## 进化什么，不进化什么

EcomEvo **允许进化**：

- 只读工具优先级；
- 证据补全策略；
- specialist 使用策略；
- 失败模式对应的处理 guidance；
- 触发条件；
- 不同业务域的探索强度；
- 技能晋升与退役门槛。

EcomEvo **不允许进化**：

- Verifier 的业务证据硬门槛；
- Sandbox 的 side-effect policy；
- BusinessAction 的人工确认；
- 文件完整性验证；
- hash chain；
- `uncertain` 防盲重试机制；
- “模型/技能/历史不是独立业务证据”的原则。

这就是系统的核心安全不变量。

---

## Experience Distillation

进化输入来自真实任务轨迹，而不是凭空生成“更好的 Prompt”。

### Failure Experience

失败任务提供：

- `missing_evidence`
- 已尝试工具
- 无效重复路径
- stagnation 状态
- verification issues
- 当前使用技能
- 当前业务域

### Recovery Success

成功但经历补证/重规划的任务同样重要。

它提供：

- 哪个工具真正补到了缺失证据；
- 哪种调用顺序减少了无效探索；
- 哪种 specialist 帮助发现了遗漏；
- 哪个停止条件是有效的。

这类轨迹可以被蒸馏成“成功恢复技能”。

---

## Candidate Skill

候选技能包含：

- domain
- name
- guidance
- preferred tools
- trigger terms
- pathology niche
- source patch
- shadow score

模型可以帮助提出候选，但候选会被服务端再次过滤。

任何包含下列倾向的策略都不应该被允许进入活跃技能：

- 绕过证据要求；
- 降低人工确认；
- 直接调用副作用工具；
- 把模型输出升级成业务证据；
- 使用不存在的工具。

---

## Shadow Gate

候选技能不会因为“听起来合理”就直接上线。

首先进行 deterministic shadow replay。

Replay cases 用来检查：

- 缺证据任务是否仍然会 replan；
- 证据完整任务是否能正常 finish；
- 风险任务是否仍要求独立信号；
- 高影响处置是否仍受证据完整性控制；
- 新策略有没有让既有场景退化。

只有：

```text
regression_after >= regression_before
AND shadow_score >= promotion_threshold
```

候选才有资格晋升。

这使“进化”从文本生成问题变成一个**候选策略 + 评估 + 晋升**问题。

---

## Bayesian Skill Posterior

每个技能都维护 Beta posterior。

初始 posterior 由 shadow replay 作为弱先验种子，真实线上任务持续更新它。

```text
success:
    alpha += 1

failure:
    beta += 1

mean:
    alpha / (alpha + beta)
```

选择技能时综合：

```text
score =
    posterior utility
  + shadow replay quality
  + trigger relevance
```

因此：

- replay 好但线上差的技能会下降；
- replay 中等但真实表现持续稳定的技能会逐渐可信；
- 长期失败的 active skill 会退役。

这是 EcomEvo 区别于“静态经验库”的关键点：**经验不是永久真理。**

---

## Quality-Diversity Skill Archive

如果每次失败都产生一个新技能，系统最终会变成巨大的经验垃圾场。

EcomEvo 使用 pathology niche 对技能做质量多样性管理。

Niche 由：

- domain
- trigger terms
- preferred tools

共同形成。

同一个 niche 中：

- incumbent 保持活跃；
- candidate 必须显著更好才能替代；
- 被替代技能进入 retired；
- 不同 pathology 保留不同代表。

系统追求的不是“技能数量最大”，而是：

> **覆盖更多有价值的失败模式，同时每种模式只保留当前最强策略。**

---

## Meta-Evolution

每个业务域独立维护：

### promotion_threshold

候选技能进入 active archive 的最低 shadow 要求。

### retirement_threshold

active skill 在线表现下降到什么程度时应该退役。

### exploration

当前领域应该为每轮自主决策保留多大的探索容量。

真实结果会缓慢影响这些参数：

- 技能失败 → promotion 更严格、exploration 增加；
- 技能成功 → 减少无效 exploration；
- 无技能任务持续失败 → 鼓励探索新策略。

所有参数都有硬范围，因此 meta-evolution 不能“学会关闭安全”。

---

# Deterministic Authority

## 认知自治，权限确定

这是整个 Runtime 最重要的一条线。

### Cognitive plane

模型、技能、memory、dynamic specialist 可以影响：

- 下一步信息获取；
- 工具优先级；
- 并行策略；
- 复核角度；
- 停止/继续建议。

### Authority plane

只有 deterministic code 可以决定：

- 证据是否满足业务硬条件；
- 工具是否允许；
- action 是否属于副作用；
- 是否需要人工确认；
- 下游动作是否可以执行；
- 结果中断后是否进入 `uncertain`。

两个 plane 有意分离。

这使“更聪明的模型”不会自动变成“更高权限的模型”。

---

## Adversarial Control Validation

升级期间使用临时测试控制器故意在每轮请求非法：

```text
refund.issue
```

同时申请正常只读检索和 specialist delegation。

80 个并发恶意 Model Controller 的结果：

- unsafe proposals rejected: **80 / 80**
- side-effect leaks: **0**
- event-chain failures: **0**
- cognitive delegation: **80 / 80**

说明模型层即使持续提出越权动作，执行层仍会在 deterministic allowlist / sandbox 处拒绝。

测试脚本为临时压力验证，不进入仓库。

---

# Persistence & Observability

Runtime 记录的关键事件包括：

- `goal.parsed`
- `belief.updated`
- `plan.created`
- `autonomy.decided`
- `autonomy.decision_rejected`
- `agent.delegated`
- `topology.mutated`
- `tools.completed`
- `plan.replanned`
- `tools.recovery_completed`
- `verification.checked`
- `verification.rechecked`
- `autonomy.stagnated`
- `evolution.patch`
- `evolution.distilled`
- `evolution.reused`
- `action.proposed`
- `run.completed`

Event Store 提供：

- append-only history
- sequence ordering
- SHA-256 hash chain
- JSON checkpoint
- restore
- rollback
- fork
- replay
- semantic evolution deduplication

因此自治行为本身也是可观察、可追责的。

---

# Runtime Configuration

```bash
ECOMEVO_AUTONOMY_STEPS=6
ECOMEVO_AUTONOMY_CALLS_PER_STEP=4
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP=3
```

配置只在代码硬范围内调整探索容量。

它们不能：

- 创建无限循环；
- 放大到无限工具调用；
- 关闭 Sandbox；
- 关闭 Verifier；
- 关闭人工确认。

---

# Design North Star

EcomEvo 希望做到的不是：

> “让模型拥有无限自由。”

而是：

> **让 Agent 拥有足够强的认知自由，同时让企业拥有绝对清晰的权限边界。**

最终目标是一个能够长期运行的企业 Agent：

**会自主规划，会主动查证，会动态组队，会识别失败，会积累技能，会淘汰坏经验，会调整自己的探索策略，同时不会因为越来越聪明而获得越来越大的生产权限。**
