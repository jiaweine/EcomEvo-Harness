# EcomEvo Algorithm Technical Report

## 摘要

EcomEvo 将企业 Agent 建模为一个**受硬安全约束的序贯证据获取过程**。语言模型负责提出候选认知动作，但没有工具执行权、证据裁决权或业务动作授权权。Runtime 负责合法动作过滤、证据路由、预算约束、验证、反事实 credit assignment、在线后验更新和安全停止。

当前核心算法由五个互相独立但闭环协作的部分组成：

1. **EvoLoop**：Observe → Decide → Route → Act → Review → Verify → Replan / Stop；
2. **EvoGain-APR**：Adaptive Posterior Routing，基于层级 Bayesian posterior 的可学习工具路由；
3. **Verifier Difference Credit**：使用 leave-one-out verifier 反事实估计工具的边际证据贡献；
4. **Bayesian Skill Evolution**：成功/失败轨迹持续更新可复用技能后验；
5. **Deterministic Authority**：Sandbox、Verifier、BusinessAction 和人工确认位于学习系统之外。

这意味着 EcomEvo 可以学习“下一步查什么”，但不能学习“如何绕过权限”。

---

## 1. 问题定义

设业务任务为

\[
\mathcal T=(g,d,\mathcal C,\mathcal E^{req},B)
\]

其中：

- \(g\)：用户目标；
- \(d\)：业务域；
- \(\mathcal C\)：不可学习、不可降低的硬约束；
- \(\mathcal E^{req}\)：完成任务需要的证据类别；
- \(B\)：当前任务可使用的工具预算。

第 \(t\) 步 Runtime 维护状态

\[
s_t=(F_t,R_t,U_t,M_t,q_t,H_t)
\]

其中：

- \(F_t\)：已确认事实；
- \(R_t\)：风险；
- \(U_t\)：不确定性；
- \(M_t\)：当前缺失证据集合；
- \(q_t\in[0,1]\)：Verifier score；
- \(H_t\)：已执行工具、specialist、技能和任务图历史。

模型输出、memory、skill、历史回答和 specialist opinion 都属于**认知状态**，不自动成为独立业务证据。

---

## 2. 合法动作空间

语言模型或确定性 Planner 可以提出候选工具集合 \(\hat{\mathcal A}_t\)。真正可以进入排序器的动作必须满足：

\[
\mathcal A_t=
\hat{\mathcal A}_t
\cap \mathcal A^{registered}
\cap \mathcal A^{read\text{-}only}
\cap \mathcal A^{budget}
\cap \mathcal A^{sandbox}
\]

任何 unknown tool、side-effect tool、requires-confirmation tool、超预算调用和禁止的参数都会在进入学习策略之前被拒绝。

因此在线学习只能改变

\[
\pi(a\mid s),\quad a\in\mathcal A_t
\]

而不能改变 \(\mathcal A_t\) 本身的安全边界。

---

## 3. EvoGain-APR：Adaptive Posterior Routing

### 3.1 为什么不再使用固定价值函数

旧实现使用固定线性系数计算工具价值。它适合作为冷启动，但不能适应：

- 不同业务域的证据结构；
- 新增 MCP 工具；
- 工具质量变化；
- 模型能力变化；
- 历史技能质量变化；
- 任务从初始查证进入 recovery 后的策略变化。

因此当前实现把人工系数降级成**先验均值** \(\mu_0\)，真实任务不断更新 posterior。随着样本增多，先验影响自然衰减。

### 3.2 Context feature

对候选工具 \(a_i\)，当前实现构造 12 维上下文特征：

\[
x_i=[
1,
C_i,
A_i,
S_i,
N_i,
X_i,
P_i,
L_i,
K_i,
D_i,
G_i,
R_i
]^T
\]

分别表示：

- \(C_i\)：missing evidence coverage；
- \(A_i\)：authority，企业 MCP read-only 且有 evidence tags 时更高；
- \(S_i\)：相关已验证 skill 的 posterior support；
- \(N_i\)：novelty，重复成功调用会降低；
- \(X_i\)：counter-evidence / contradiction value；
- \(P_i\)：工具 purpose/evidence channel specificity；
- \(L_i\)：tool reliability posterior；
- \(K_i\)：cost pressure；
- \(D_i\)：与本轮已选工具的 evidence-channel redundancy；
- \(G_i\)：当前 evidence gap pressure；
- \(R_i\)：是否处于 recovery context。

这些特征来自 Runtime 可审计状态，不来自模型隐藏推理。

---

## 4. Bayesian 线性后验

### 4.1 冷启动先验

定义参数

\[
w\sim\mathcal N(\mu_0,\Lambda_0^{-1})
\]

实现中 \(\Lambda_0=\lambda I\)，初始统计量为

\[
A_0=\Lambda_0
\]

\[
b_0=\Lambda_0\mu_0
\]

\(\mu_0\) 只承担 cold-start prior 的作用，不是永久价值函数。

### 4.2 在线更新

若观察到一个工具上下文 \(x_t\) 和 credit \(r_t\)，标准线性 Bayesian sufficient statistics 为

\[
A_t=A_{t-1}+x_tx_t^T
\]

\[
b_t=b_{t-1}+r_tx_t
\]

posterior mean 为

\[
\mu_t=A_t^{-1}b_t
\]

预测不确定性为

\[
\sigma_t^2(x)=x^TA_t^{-1}x
\]

EcomEvo 不依赖额外 ML runtime；当前 12 维矩阵在 Runtime 内完成确定性求逆和更新。

---

## 5. Non-stationary adaptation

生产工具生态并非平稳分布。一个 MCP 服务可能变慢、数据质量可能下降，新模型可能改变候选工具分布。

因此实现使用保留先验质量的指数遗忘：

\[
A_t=A_0+\delta(A_{t-1}-A_0)+x_tx_t^T
\]

\[
b_t=b_0+\delta(b_{t-1}-b_0)+r_tx_t
\]

当前 \(\delta=0.997\)。

旧轨迹会缓慢衰减，但不会把系统退化成无先验状态。

同时维护 reward residual EWMA：

\[
e_t=|r_t-\hat r_t|
\]

\[
\bar e_t=0.9\bar e_{t-1}+0.1e_t
\]

残差升高意味着当前 posterior 与新环境不一致。Runtime 会降低已学习策略的激活强度并提高 epistemic exploration，而不是盲目相信旧策略。

---

## 6. Global → Domain 层级迁移

每次工具 credit 同时更新：

- 全局 posterior \(p(w_g\mid D)\)；
- 当前业务域 posterior \(p(w_d\mid D_d)\)。

对业务域样本数 \(n_d\)，定义收缩系数

\[
\tau_d=\frac{n_d}{n_d+\kappa}
\]

当前 \(\kappa=24\)。

组合预测均值：

\[
\hat\mu_d(x)=
(1-\tau_d)\mu_g^Tx+
\tau_d\mu_d^Tx
\]

新业务域不会从零开始，但也不会直接继承成熟业务域的全部策略。

当本域仍在 shadow、但全局样本已经充足时，只允许很小比例的 global transfer。

---

## 7. Deterministic UCB，而不是随机在线探索

为了兼顾探索与可复现性，生产路由不使用随机 Thompson Sampling，而使用 deterministic upper confidence bound：

\[
Q_t(x)=\hat\mu_t(x)+\beta_t\hat\sigma_t(x)
\]

其中 \(\beta_t\) 随领域 exploration policy 与 residual drift 调整。

这样：

- 高均值工具倾向被利用；
- 高不确定但合法的工具仍有机会被探索；
- 相同 posterior 与相同任务状态得到相同排序；
- 审计可以解释“因为 posterior uncertainty 而探索”，而不是依赖不可复现随机数。

---

## 8. Shadow → Adaptive activation

学习系统不能在只有少量轨迹时立即接管路由。

设本域 posterior 样本数为 \(n\)。当前生产策略：

\[
\eta=0,\quad n<n_0
\]

其中 \(n_0=12\)。

进入 adaptive 后：

\[
\eta_t=
\min\left(
\eta_{max},
\frac{n-n_0+1}{n+c}\cdot Conf_t
\right)
\]

其中：

\[
Conf_t=
clip\left(
\frac{1}{1+1.8\bar e_t},
0.30,
1
\right)
\]

当前 \(\eta_{max}=0.96\)。

最终工具评分为

\[
Score_t(x)=
(1-\eta_t)\mu_0^Tx+
\eta_tQ_t(x)
\]

因此固定系数只在冷启动阶段占主导；成熟后最多约 96% 由后验策略决定。

为避免年轻 posterior 突然大幅偏离，在较早样本阶段还会限制单次 learned score 相对 prior 的最大跳变。

---

## 9. Verifier Difference Credit

### 9.1 不使用手工 reward 权重拼接

如果用

\[
r=\alpha\Delta score+\beta\Delta gap-\gamma cost+\cdots
\]

那么只是把“固定价值函数”问题转移到“固定奖励函数”。

生产 EcomEvo 因此使用 verifier leave-one-out difference reward。

### 9.2 Verification potential

对 VerificationResult \(v\)，定义：

\[
\Phi(v)=q(v)+Completeness(v)
\]

其中

\[
Completeness(v)=
clip\left(
1-\frac{|M(v)|}{\max(1,|\mathcal E^{req}|)},
0,
1
\right)
\]

Verifier score 与 evidence completeness 本身都已归一到 \([0,1]\)，因此这里不额外引入人工混合权重。

### 9.3 Leave-one-out marginal contribution

设当前所有工具结果为 \(R\)，本轮自适应策略选中的结果为 \(r_i\)。

Runtime 额外执行一次只读 deterministic verifier：

\[
D_i=
\Phi(V(R))-\Phi(V(R\setminus\{r_i\}))
\]

再按实际工具成本归一化：

\[
credit_i=\frac{D_i}{1+cost_i}
\]

这个 credit 用于更新 routing posterior。

它不是严格的因果识别，也不是完整 Shapley value；它是一个**有界、可解释、低成本的 difference reward**。因为每轮自主工具数有硬上限，额外 verifier 计算量同样有界。

Specialist 自然语言不会进入这次 counterfactual credit，避免“模型说得更像真的”被误当成证据贡献。

---

## 10. Tool Reliability Posterior

证据价值和工具稳定性是两件不同的事。

每个 domain/tool 维护：

\[
p_{d,a}\sim Beta(\alpha_{d,a},\beta_{d,a})
\]

成功调用：

\[
\alpha\leftarrow\alpha+1
\]

失败调用：

\[
\beta\leftarrow\beta+1
\]

工具可靠性使用全局与领域 posterior 的收缩组合：

\[
L_{d,a}=0.35\,E[p_{global,a}]+0.65\,E[p_{d,a}]
\]

它作为 routing context feature，而不是业务证据。

因此一个工具可以“业务上很相关，但运行不稳定”，也可以“非常稳定，但对当前证据缺口没价值”。两者不会被一个混合 reward 混为一谈。

---

## 11. Budget-aware set selection

单工具高分并不代表一组工具组合最优。

每选择一个工具后，Runtime 重新计算剩余候选与已选 evidence channels 的 overlap：

\[
D_i=
\frac{|Channel_i\cap Channel_{selected}|}
{\max(1,|Channel_i|)}
\]

该值进入 posterior feature `redundancy`，因此“多样性惩罚”本身也可以被真实 outcome 学习，而不再是永久固定减分项。

选择过程满足：

\[
\sum_{a_i\in S_t}cost_i\le B_t
\]

并受每步最大工具数硬限制。

---

## 12. Dynamic Task Graph 与 Stagnation

如果连续 verification fingerprint 没有改变，Runtime 认为当前认知拓扑可能停滞。

fingerprint 由：

- missing evidence；
- 已成功工具类别；
- 企业 evidence tags

构成。

连续停滞时可以新增只读反证 specialist；再次无进展则停止，而不是无限 retry。

拓扑可以改变，但 specialist 仍然只有 cognition 权限。

---

## 13. Bayesian Skill Evolution

Routing posterior 学“下一步工具策略”，Skill Library 学“可复用业务认知模式”。两者分开更新。

每个 skill：

\[
p_k\sim Beta(\alpha_k,\beta_k)
\]

其 posterior mean：

\[
\mu_k=\frac{\alpha_k}{\alpha_k+\beta_k}
\]

Skill ranking 综合 posterior、shadow replay 和 trigger relevance；同一 pathology niche 只保留更强代表，避免 archive 无限膨胀。

Skill 可以改变信息获取和 specialist 方向，但不能成为 evidence，也不能给自己增加 side-effect 权限。

---

## 14. Deterministic Authority

整个学习系统位于硬权限边界上方：

```text
LLM / Open-weight Controller
        ↓
Candidate cognition
        ↓
EvoGain-APR posterior routing
        ↓
Read-only tools / specialists
        ↓
Verifier
════════════════════════════════
Deterministic authority boundary
        ↓
BusinessAction proposal
        ↓
Human confirmation
        ↓
Business executor
```

下面这些内容不参与 posterior 学习：

- side-effect prohibition；
- tool registration；
- credential scope；
- budget hard ceiling；
- required evidence gate；
- tenant / approval authority；
- confirmation requirement。

即使 routing posterior 学坏，也只能导致“查证策略变差”，不能变成“生产权限扩大”。

---

## 15. 复杂度

设 feature dimension 为 \(d=12\)，本轮候选工具数为 \(m\)，最终选择 \(k\)。

Posterior preparation 主要成本为两个 \(d\times d\) 矩阵求逆：

\[
O(d^3)
\]

因为 \(d\) 固定且很小，这一成本相对外部模型和 MCP 网络调用可以忽略。

候选重排约为：

\[
O(kmd^2)
\]

Counterfactual credit 最多额外执行 \(k\) 次 deterministic verifier：

\[
O(k\cdot V)
\]

其中 \(k\) 被 Runtime 每步工具上限严格限制。

---

## 16. 与前沿 Agent 学习方向的关系

当前设计吸收但没有照搬以下研究方向：

- **Agent Lightning**：长轨迹需要 credit assignment，训练/执行应解耦；
- **AutoTool**：工具选择应成为可优化的 ranking policy，而不是固定 inventory 上的 prompt heuristic；
- **ToRL / ReTool**：工具使用时机和策略可以从 outcome 学习；
- **language world model / agent simulator**：可用于未来 shadow replay 和大规模离线策略评估。

EcomEvo 的不同点是：当前系统不要求在线更新 LLM 参数，而是先学习**Runtime 级 read-only routing policy**。这样可以更快更新、更容易审计，也更容易保持企业权限边界。

---

## 17. 当前实现与研究扩展

### 已实现

- cold-start prior → online posterior；
- global/domain hierarchical transfer；
- deterministic UCB；
- shadow activation；
- non-stationary decay；
- residual drift；
- tool reliability Beta posterior；
- verifier leave-one-out difference credit；
- persistent routing outcomes；
- budget/diversity set selection；
- routing audit trace；
- learner failure 与业务执行隔离。

### 下一阶段值得研究

1. **Doubly-robust off-policy evaluation**：在不扩大线上 exploration 风险的情况下评估候选 policy；
2. **World-model shadow replay**：用可控 agent environment simulator 产生 counterfactual trajectory，再由真实 verifier 筛选；
3. **Non-linear posterior head**：数据规模足够后，将线性 posterior 升级为低秩或 neural contextual bandit，但仍保留可解释 safety features；
4. **Tool-set generalization**：对动态 MCP schema 构造 learned semantic embedding，同时保留 registration/sandbox hard gate；
5. **Policy promotion gate**：用真实 gold set 和离线 replay 比较 posterior policy 与 cold-start prior，达到统计门槛后再提高 activation ceiling。

这些研究方向属于 cognition policy，不改变 deterministic authority。

---

## 18. 参考方向

- Luo et al., *Agent Lightning: Train ANY AI Agents with Reinforcement Learning*, arXiv:2508.03680.
- Zou et al., *AutoTool: Dynamic Tool Selection and Integration for Agentic Reasoning*, arXiv:2512.13278.
- Li et al., *ToRL: Scaling Tool-Integrated RL*, arXiv:2503.23383.
- Feng et al., *ReTool: Reinforcement Learning for Strategic Tool Use in LLMs*, arXiv:2504.11536.

这些论文用于说明相关研究方向；EcomEvo 的 posterior routing、deterministic UCB、verifier difference credit、企业 authority separation 是当前 Runtime 自己的系统组合与实现选择。
