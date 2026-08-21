# EcomEvo Harness Self-Evolution

> **Cognitive autonomy, deterministic authority.** 让 Harness 学会怎样更好地观察、查证、调用工具、复核与恢复，但不允许学习层改写证据门槛或业务权限。

EcomEvo 面向商品治理、商家审核、售后判责、风险核查与内容审核等复杂业务决策。它不是把一个模型包进聊天页面，而是构建一个 **model-agnostic Agent Harness Runtime**：Model、Tool、Skill、Memory、Sandbox 与 Verifier 都是可替换插件，Event-Sourced Runtime 用统一事件流维护 Goal、Belief State、Task State、工具结果、验证状态、checkpoint 与执行轨迹。

## 1. 两个时间尺度的学习

EcomEvo 把“本轮该做什么”和“以后 Harness 应该怎样做得更好”分开优化。

**Within-turn：EvoGain-APR** 在一次任务内部选择 read-only Tool / Skill / Sub-Agent，利用持久化 Bayesian posterior、deterministic UCB、contextual abstention 与 verifier difference credit 学习路由。

**Across-turn：EvoHarness-VCO** 在多个已验证任务之间优化 Harness 本身。它采用 verifier-grounded block-coordinate evolution：每次只编辑一个认知组件，候选先经过无副作用 Sandbox Replay 与逐案例 Regression Gate，再进入 shadow；真实任务 outcome 更新后验，只有 posterior superiority 足够高才晋升，否则回滚。

两层学习都位于业务权限之外：

\[
\max_{\pi_\theta,\,H}\; \mathbb E[R_{\text{evidence}}]
\quad\text{s.t.}\quad
 a_t \in \mathcal A^{\text{safe}}(s_t)
\]

其中 \(\mathcal A^{\text{safe}}\) 由 Registry + Sandbox + Verifier + Governance 决定，而不是由模型、Skill、Memory 或 Harness optimizer 学出来。

## 2. 可进化的 Harness，不可进化的权限

生产实现中的可学习坐标是：

\[
\mathcal H_{\text{learn}}
=
\{H_{prompt}, H_{tool}, H_{memory}, H_{delegation}\}
\]

它们分别对应：控制器指导策略、只读工具偏好/规避、长期记忆检索策略、认知 specialist 委派策略。Skill 还有独立的 `AdaptiveSkillLibrary` 后验与质量多样性 archive。

以下组件不进入 optimizer 的类型系统：

\[
\mathcal H_{\text{authority}}
=
\{Registry, Sandbox, Verifier, RBAC, Approval, BusinessAction\}
\]

因此即使 optimizer model 输出“跳过证据”“直接退款”“自动下架”，它也没有表示这种修改的合法字段；Tool 编辑还会再次经过 Registry/Sandbox，只允许 `read-only` / `mcp-read` 工具。

## 3. 2026 方法谱系与 EcomEvo 适配

EvoHarness-VCO 不是把某篇论文原封不动复制到电商环境，而是复现其核心优化机制，并针对有显式证据与权限约束的垂直业务重新组合。

### AHE — observability-driven harness evolution

AHE（Agentic Harness Engineering, 2026）强调三种 observability：组件可观察、经验可观察、决策可观察。EcomEvo 对应实现为：Harness component 全量持久化；每次 candidate 保存 parent、generation、hypothesis；任务 outcome、session、verifier reward 和 transition 都写入 SQLite，并通过 Event Store 暴露 `harness.profile.bound`、`harness.evolution.candidate`、`harness.evolution.transition`。

### Microsoft SkillOpt — bounded text-space optimization

SkillOpt（Microsoft, 2026）把自然语言 Skill 当成 frozen agent 的外部可训练参数，通过 add/delete/replace、edit budget、rejected-edit buffer 和 validation gate 做稳定更新。EcomEvo 将同样的 bounded edit 机制扩展到 Prompt / Tool Strategy / Memory / Delegation；被拒绝的近似编辑进入 durable rejected buffer，供后续 optimizer 避免重复失败。

### HarnessCompass — component-wise constrained evolution

HarnessCompass（2026）指出联合修改多个 Harness 组件容易产生干扰和过拟合。EcomEvo 因此执行严格 block-coordinate constraint：一个 domain 同时最多存在一个 shadow coordinate；当前 candidate 未被验证前，不允许另一个组件同时变异。

### SBCO — verifier-grounded block coordinate optimization

SBCO（2026）针对具有显式约束的 planning agent 提出 verifier-grounded block-coordinate harness optimization。EcomEvo 直接采用这一方向：candidate 的生存不是由 optimizer model 自评分决定，而是由真实 `DecisionVerifier` outcome 的 posterior evidence 决定。

## 4. Candidate 不是自由改代码

EcomEvo 不允许 optimizer 生成任意 Python 并在生产 Runtime 内执行。候选是 typed declarative component edit：

```text
prompt      -> guidance

tool        -> preferred_tools / avoid_tools

memory      -> retrieval_terms / guidance

delegation  -> roles / guidance
```

模型侧候选只允许：

```text
add | delete | replace
```

并受 `ECOMEVO_HARNESS_EDIT_BUDGET` 限制。没有 reasoner 时，Tool coordinate 仍可依据 **当前 verifier gap + 当前注册工具 metadata/evidence tags** 自动提出候选；这里没有“商家缺授权就固定调用 X”之类的业务工具映射表。

## 5. Block-coordinate search

设 Harness 为多个坐标的组合：

\[
H_t=(H_t^{(1)},H_t^{(2)},\ldots,H_t^{(K)})
\]

每轮只选择一个坐标 \(k_t\)：

\[
k_t
=
\arg\max_k\; U(H_t^{(k)}\mid\mathcal D_t)
\]

实际实现优先搜索 generation 较低、posterior uncertainty 较高的组件；其它坐标保持不变：

\[
H_{t+1}^{(j)}=H_t^{(j)},\qquad j\neq k_t
\]

这使 attribution 更清晰，也避免一次失败同时污染 Prompt、Tool、Memory 与 Delegation。

## 6. Evidence-aware verifier reward

Harness 不能靠“模型觉得自己做得更好”来学习。EcomEvo 使用 verifier quality \(q\) 与 evidence completeness \(c\) 的 harmonic potential：

\[
\Phi(v)=
\frac{2q(v)c(v)}{q(v)+c(v)+\epsilon}
\]

当任一项低时，潜力都会被压低；高质量语言输出不能补偿证据缺失。该 reward 只衡量 cognition quality，**不承担 safety/admissibility**，因为不合法动作在 optimizer action space 之前就被 Sandbox 排除。

每个 component 使用 fractional Beta posterior：

\[
\alpha_h \leftarrow \alpha_h + \Phi(v)
\]

\[
\beta_h \leftarrow \beta_h + 1-\Phi(v)
\]

\[
\mathbb E[\theta_h]
=
\frac{\alpha_h}{\alpha_h+\beta_h}
\]

## 7. Post-candidate cohort，而不是历史污染

一个新 shadow candidate 只能与**candidate 创建之后**的 incumbent outcome 比较。对于 candidate \(h'\) 与 parent \(h\)，定义实验起点 \(t_0\) 为 candidate 创建时刻：

\[
\mathcal D_{h'}=\{v_i\mid component=h',\;t_i\ge t_0\}
\]

\[
\mathcal D_h=\{v_i\mid component=h,\;t_i\ge t_0\}
\]

只有两个 cohort 都出现真实 exposure 后，才允许做 sequential transition；这不是固定 sample-count gate，而是“比较问题必须有两臂证据”的统计可识别性约束。

## 8. Pre-shadow Sandbox Replay / Regression Gate

在分配任何真实 shadow traffic 之前，`HarnessReplayGate` 会对 durable failure/recovery trajectory 做确定性重放：

- Replay 不调用业务工具，只重放 evidence gap、历史 tool sequence 与当前注册工具 metadata；
- `ActionSandbox` 与 Tool catalog mode 同时检查候选，只允许无确认要求的 `read-only` / `mcp-read` 工具；
- Tool coordinate 对每个历史案例比较语义覆盖与成本，任一案例超过容忍度的退化都会拒绝候选；
- Prompt / Memory / Delegation 无法在不执行模型的前提下诚实估算性能，因此离线门禁只验证安全与可表示性，性能仍由在线 verifier cohort 决定。

Replay gate 是 **pre-shadow admissibility gate**，不是用合成分数替代真实业务验证。

## 9. Posterior-derived shadow allocation

EcomEvo 不设置“10% shadow traffic”这种固定 rollout 常数。shadow allocation 直接来自后验优越概率：

\[
p_{shadow}
=
P(\theta_{h'}>\theta_h\mid\mathcal D)
\]

在两臂尚无可比较证据时，使用无偏 \(0.5\)；之后 allocation 会随 outcome posterior 自动变化。为保证同一 session 可审计、可复现，实际 cohort assignment 使用 `hash(session_id, candidate_id)` 的 deterministic draw，而不是运行时随机数。

## 10. Sequential promote / reject / keep-shadow

设风险容忍度为 \(\rho\)，默认配置来自 `ECOMEVO_HARNESS_ACCEPT_RISK`，它是统计决策风险，不是业务价值阈值：

\[
P(\theta_{h'}>\theta_h\mid\mathcal D)\ge1-\rho
\Rightarrow promote
\]

\[
P(\theta_{h'}>\theta_h\mid\mathcal D)\le\rho
\Rightarrow reject\;\&\;rollback
\]

其余状态继续 shadow。没有“运行 10 次自动晋升”，也没有固定业务收益权重。

## 11. Event-Sourced recovery and Failure-Driven evolution

一次业务任务的主路径是：

```text
Goal
  -> Belief State
  -> Adaptive Planner
  -> Tool / Skill / Sub-Agent
  -> PTC parallel execution
  -> Recursive review
  -> Verifier
       | pass -> controlled action proposal
       | fail -> checkpoint / rollback / replan
                    -> failure trajectory
                    -> Harness / Skill candidate
                    -> shadow / regression evidence
                    -> promote or rollback
```

`EventStore` 维护 append-only hash-chain event stream 与 checkpoint；每个 checkpoint 同时绑定 `state_hash` 与对应 event 的 `event_hash`。Autonomous Controller 只有在完整性检查通过后才恢复 Belief State，并把 `checkpoint_seq`、恢复结果和 replan 轨迹写回事件流。Failure-Driven Evolver 从失败轨迹提取证据缺口和成功恢复经验，而跨任务 Harness optimizer 将可泛化经验沉淀到组件 posterior。

这意味着“失败恢复”和“自进化”不是同一件事：当前任务先安全恢复；候选策略必须在后续真实 verifier outcome 中证明更优，才会成为未来任务的 active Harness。

## 12. 面向电商垂直领域的适配

EcomEvo 不把通用 coding-agent 的“测试通过率”硬套成电商 reward，而把领域知识放在三个可审计位置：

1. **Goal / required evidence schema**：商品、商家、订单、风险与内容场景定义各自必须具备的证据类型；
2. **Tool metadata / evidence tags**：企业 MCP 或本地工具声明它能提供什么可验证事实；
3. **Verifier**：统一判断 evidence completeness、约束满足、source admissibility 与 side-effect boundary。

学习器只优化“怎样更高效地把这些证据找齐”，而不学习“什么证据可以被绕过”。

## 13. 与 EvoGain-APR 的关系

EvoGain-APR 优化的是单次任务内部候选 read-only tool 的相对信息价值：

\[
Score(x)=\mu(x)+\beta\sqrt{x^\top A^{-1}x}
\]

并通过 contextual abstention 判断是否继续探索：

\[
Adv(a_i\mid s_t)=Score(x_i)-Score(x_{\varnothing}(s_t))
\]

EvoHarness-VCO 则优化长期 Harness component。两者形成 nested learning loop：

```text
Across tasks:   EvoHarness-VCO evolves the cognition substrate
                    |
Per task:       EvoGain-APR adapts read-only routing
                    |
Outcome:        Verifier produces auditable evidence reward
                    |
                posterior updates both learning planes
```

## 14. 研究来源

- Microsoft SkillOpt — official open source: https://github.com/microsoft/SkillOpt
- SkillOpt paper — arXiv:2605.23904
- Agentic Harness Engineering (AHE) — arXiv:2604.25850
- AHE official code: https://github.com/china-qijizhifeng/agentic-harness-engineering
- HarnessCompass — arXiv:2608.01918
- SBCO — arXiv:2608.10157

HarnessCompass 与 SBCO 截至 2026-08 属于最新公开预印本；README / 技术文档不会把它们虚构成已经正式录用的顶会论文。EcomEvo 的实现是面向 evidence-governed commerce runtime 的工程化复现与再设计，不宣称与原项目逐行同构。
