# EcomEvo Algorithm Technical Report

## 摘要

EcomEvo 将企业 Agent 建模为一个**受硬安全约束的序贯证据获取与受控决策过程**。语言模型负责提出候选认知动作，但不拥有工具执行权、证据裁决权或业务动作授权权；Runtime 负责合法动作过滤、上下文路由、预算控制、验证、反事实 credit assignment、在线后验更新和停止。

当前核心算法由六个闭环组件构成：

1. **EvoLoop**：Observe → Decide → Route → Act → Review → Verify → Learn / Replan / Stop；
2. **EvoGain-APR**：Adaptive Posterior Routing，以层级 Bayesian contextual posterior 学习只读工具选择；
3. **Contextual Abstention**：不再使用固定 absolute utility floor，而是比较工具与同状态 no-op 的后验优势；
4. **Verifier Difference Credit**：用 leave-one-out deterministic verifier 估计工具的边际证据贡献；
5. **Bayesian Reliability + Skill Evolution**：分别学习工具运行稳定性与长期可复用认知策略；
6. **Deterministic Authority**：Registry、Sandbox、Verifier、Governance 与人工确认完全位于学习参数空间之外。

核心原则是：**系统可以学习“下一步查什么”，但不能学习“如何获得更多权限”。**

---

## 1. 问题定义

设一个业务任务为

\[
\mathcal T=(g,d,\mathcal C,\mathcal E^{req},B)
\]

其中：

- \(g\)：用户目标；
- \(d\)：业务域；
- \(\mathcal C\)：不可学习、不可降低的硬约束；
- \(\mathcal E^{req}\)：完成任务需要的证据类别；
- \(B\)：工具成本预算。

第 \(t\) 步 Runtime 维护状态

\[
s_t=(F_t,R_t,U_t,M_t,q_t,H_t)
\]

其中 \(F_t\) 为已核验事实，\(R_t\) 为风险，\(U_t\) 为不确定性，\(M_t\) 为缺失证据，\(q_t\in[0,1]\) 为 Verifier score，\(H_t\) 为工具、任务图、技能和 recovery 历史。

模型回复、memory、skill 与 specialist opinion 都属于认知状态，不自动成为独立业务证据。

---

## 2. 合法动作空间先于学习

模型或 Planner 可以提出候选集合 \(\hat{\mathcal A}_t\)，但进入 adaptive routing 前必须先经过确定性过滤：

\[
\mathcal A_t=
\hat{\mathcal A}_t
\cap\mathcal A^{registered}
\cap\mathcal A^{read-only}
\cap\mathcal A^{budget}
\cap\mathcal A^{sandbox}
\]

因此学习系统只允许改变

\[
\pi(a\mid s),\quad a\in\mathcal A_t
\]

而不能扩大 \(\mathcal A_t\)。unknown tool、side-effect tool、requires-confirmation tool、越权参数和超预算动作在学习器之前就被拒绝。

---

## 3. EvoGain-APR 上下文表示

对候选工具 \(a_i\)，Runtime 构造 12 维可审计 feature：

\[
x_i=[1,C_i,A_i,S_i,N_i,X_i,P_i,L_i,K_i,D_i,G_i,R_i]^T
\]

其中：

- \(C_i\)：当前 missing evidence coverage；
- \(A_i\)：可信企业 read tool / evidence tags 的 authority；
- \(S_i\)：相关已验证 skill posterior support；
- \(N_i\)：novelty；
- \(X_i\)：counter-evidence / contradiction potential；
- \(P_i\)：purpose 与 evidence channel specificity；
- \(L_i\)：tool reliability posterior；
- \(K_i\)：cost pressure；
- \(D_i\)：与本轮已选工具的 evidence-channel redundancy；
- \(G_i\)：evidence gap pressure；
- \(R_i\)：recovery context。

这些 feature 来自 Runtime 结构化状态，不来自隐藏 chain-of-thought。

---

## 4. 冷启动 prior，而不是固定价值函数

参数使用 Gaussian prior：

\[
w\sim\mathcal N(\mu_0,\Lambda_0^{-1})
\]

实现维护 sufficient statistics：

\[
A_0=\Lambda_0,\qquad b_0=\Lambda_0\mu_0
\]

当前 \(\mu_0\) 是保守 cold-start engineering prior，只在数据不足时提供方向。它不是业务置信度，也不是永久价值函数。

posterior mean：

\[
\mu_t=A_t^{-1}b_t
\]

预测 epistemic uncertainty：

\[
\sigma_t^2(x)=x^TA_t^{-1}x
\]

固定 feature dimension 为 \(d=12\)，因此无需引入大型 ML runtime。

---

## 5. Parallel-batch Bayesian update

Agent 一轮通常并行选择多个只读工具。若同一批次包含 \(k\) 个 credit 样本 \((x_i,r_i)\)，当前实现**每轮只执行一次 non-stationary decay**，再批量吸收该轮信息：

\[
A_t=A_0+\delta(A_{t-1}-A_0)+\sum_{i=1}^{k}x_ix_i^T
\]

\[
b_t=b_0+\delta(b_{t-1}-b_0)+\sum_{i=1}^{k}r_ix_i
\]

而不是对同一个并行批次重复 \(k\) 次 decay。

当前 \(\delta=0.997\)。旧证据缓慢衰减，使策略能适应工具质量、MCP 数据源、模型候选分布和任务分布的变化。

### 5.1 Writer-hot-path drift signal

SQLite writer lock 内不执行矩阵求逆。为了避免昂贵 posterior inference 延长单 writer 持锁时间，热写路径维护 outcome-surprise EWMA：

\[
e_t=|r_t-\bar r_{t-1}|
\]

\[
\bar r_t=0.92\bar r_{t-1}+0.08r_t
\]

\[
\bar e_t=0.90\bar e_{t-1}+0.10e_t
\]

完整 posterior mean / covariance / UCB 仍在只读 scoring 路径精确计算。换言之，**写路径优化不牺牲 ranking posterior 的数学形式**。

---

## 6. Global → Domain hierarchical transfer

同时维护全局 posterior 与业务域 posterior：

\[
p(w_g\mid D),\qquad p(w_d\mid D_d)
\]

业务域样本数为 \(n_d\) 时：

\[
\tau_d=\frac{n_d}{n_d+\kappa}
\]

当前 \(\kappa=24\)。组合预测均值：

\[
\hat\mu_d(x)=(1-\tau_d)\mu_g^Tx+\tau_d\mu_d^Tx
\]

因此新业务域可以使用少量全局经验，但不会直接继承成熟领域的完整策略。

---

## 7. Deterministic UCB

生产路由使用确定性 UCB，而非随机 Thompson Sampling：

\[
Q_t(x)=\hat\mu_t(x)+\beta_t\hat\sigma_t(x)
\]

\(\beta_t\) 由领域 exploration policy 与当前 drift 调节。

这样同时满足：

- exploitation：高 posterior mean 工具有优先权；
- exploration：合法但不确定的工具可以获得机会；
- reproducibility：相同 posterior + state 得到相同分数；
- auditability：可以记录 posterior、uncertainty 与 activation，而不记录隐藏推理。

---

## 8. Shadow → Adaptive activation

年轻 posterior 不能立即接管生产路由。

样本不足时：

\[
\eta=0
\]

进入 adaptive 后：

\[
\eta_t=
\min\left(
\eta_{max},
\frac{n-n_0+1}{n+c}\cdot Conf_t
\right)
\]

其中当前 \(n_0=12\)，\(\eta_{max}=0.96\)。

最终 score：

\[
Score_t(x)=(1-\eta_t)\mu_0^Tx+\eta_tQ_t(x)
\]

在本域尚未成熟、全局样本已经足够时，只允许有界 global transfer。

---

## 9. Contextual Abstention：学习“什么时候不调用”

旧实现使用固定 absolute utility floor。这会产生尺度问题：posterior 可以改变 ranking，却仍可能被旧阈值截断。

当前实现定义一个与任务状态一致的 **no-op / abstain context** \(x_{\varnothing}(s_t)\)。它保留当前 gap pressure、recovery context 和中性的未观察工具先验，但不携带具体工具 evidence feature。

对候选动作定义：

\[
Adv_t(a_i\mid s_t)=Score_t(x_i)-Score_t(x_{\varnothing}(s_t))
\]

只在

\[
Adv_t(a_i\mid s_t)>0
\]

时选择该工具。

因此停止边界与 posterior 使用同一标尺，不再依赖一个永久的 `0.42` 常数。这里的 abstention 只影响只读认知动作，不能跳过业务 required evidence gate。

---

## 10. Verifier Difference Credit

### 10.1 为什么不用手工 reward 拼接

若使用

\[
r=\alpha\Delta score+\beta\Delta gap-\gamma cost+\cdots
\]

只是把固定价值函数问题搬到奖励函数。

当前生产路径使用 deterministic leave-one-out verifier difference reward。

### 10.2 Harmonic verification potential

令 evidence completeness：

\[
c(v)=clip\left(1-\frac{|M(v)|}{\max(1,|\mathcal E^{req}|)},0,1\right)
\]

Verifier score 为 \(q(v)\in[0,1]\)。验证势能采用调和形式：

\[
\Phi(v)=
\begin{cases}
\frac{2q(v)c(v)}{q(v)+c(v)}, & q(v)+c(v)>0\\
0, & otherwise
\end{cases}
\]

它具有关键性质：**高 score 无法补偿很差的 evidence completeness，反之亦然。**

### 10.3 Leave-one-out marginal contribution

当前所有工具结果为 \(R\)，本轮被 adaptive router 选择的结果为 \(r_i\)：

\[
D_i=\Phi(V(R))-\Phi(V(R\setminus\{r_i\}))
\]

按工具成本归一化：

\[
credit_i=\frac{D_i}{1+cost_i}
\]

\(\Phi\in[0,1]\)，因此 difference credit 天然有界。

这不是严格因果识别，也不是完整 Shapley value；它是有界、确定性、可解释且计算成本受每轮工具上限约束的 counterfactual credit。

Specialist 自然语言不进入该 credit verifier，避免模型表达风格成为奖励来源。

---

## 11. Tool Reliability Posterior

证据价值与工具运行稳定性分开建模。

每个 domain/tool：

\[
p_{d,a}\sim Beta(\alpha_{d,a},\beta_{d,a})
\]

成功：

\[
\alpha\leftarrow\alpha+1
\]

失败：

\[
\beta\leftarrow\beta+1
\]

全局与本域 reliability 用样本数驱动 shrinkage，而不是永久固定 35/65 混合：

\[
\tau^{rel}_{d,a}=\frac{n_{d,a}}{n_{d,a}+\kappa_{rel}}
\]

\[
L_{d,a}=(1-\tau^{rel}_{d,a})E[p_{g,a}]+\tau^{rel}_{d,a}E[p_{d,a}]
\]

当前 \(\kappa_{rel}=12\)。新工具先借用全局稳定性，随后由本域真实使用覆盖。

---

## 12. Budget-aware diverse set selection

对已经选择的 evidence channels \(C_S\)，候选工具通道重叠：

\[
D_i=\frac{|C_i\cap C_S|}{\max(1,|C_i|)}
\]

`redundancy` 作为 posterior feature，因此同轮多样性影响也可以从真实 outcome 学习，而不是永久固定减分项。

选择始终满足：

\[
\sum_{a_i\in S_t}cost_i\le B_t
\]

并受每步最大工具数硬上限约束。

---

## 13. 单轮一致性与数据库事务

同一 routing round 使用一个 immutable scoring snapshot：

```text
read global posterior
read domain posterior
read all candidate tool reliability
        ↓
compute posterior + UCB once
        ↓
rank whole candidate set
```

因此同一轮不会因为前一个候选的读取副作用改变后一个候选的 posterior。

学习写入则合并为一个事务：

```text
BEGIN IMMEDIATE
  update global sufficient statistics
  update domain sufficient statistics
  insert all routing outcomes
  update all tool reliability posteriors
COMMIT
```

事务数量从旧设计近似 \(O(2k)\) / round 降为 \(O(1)\) / round。

矩阵求逆不发生在 SQLite writer lock 内。

---

## 14. Dynamic Task Graph 与 Stagnation

如果连续 verification fingerprint 没有变化，Runtime 认为当前认知拓扑可能停滞。

fingerprint 包含：

- missing evidence；
- 已成功工具类别；
- enterprise evidence tags。

停滞时可以增加只读 counter-evidence specialist、切换证据通道，连续无进展后停止，而不是无限 retry。

---

## 15. Bayesian Skill Evolution

Routing posterior 学“当前状态下一步查什么”；Skill Library 学“长期可复用的业务认知模式”。

每个 skill：

\[
p_k\sim Beta(\alpha_k,\beta_k)
\]

posterior mean：

\[
\mu_k=\frac{\alpha_k}{\alpha_k+\beta_k}
\]

Skill 经过 shadow replay / regression gate、Quality-Diversity niche 竞争、promotion / retirement。Skill 可以改变认知策略，但不能成为业务证据，也不能获得 side-effect authority。

---

## 16. Deterministic Authority

```text
LLM / Open-weight Controller
        ↓
Candidate cognition
        ↓
EvoGain-APR
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

不可学习内容包括：

- registered tool set；
- side-effect prohibition；
- credential / tenant scope；
- hard budget ceiling；
- required evidence gate；
- human approval identity；
- confirmation requirement。

即使 routing policy 学坏，最坏结果应被限制在“查证策略变差”，而不是“权限变大”。

---

## 17. 复杂度

设 feature dimension \(d=12\)，候选工具数 \(m\)，本轮最多选择 \(k\)。

两个 posterior inverse：

\[
O(d^3)
\]

在固定 \(d=12\) 下为小型本地 CPU 成本。

Greedy set ranking：

\[
O(kmd^2)
\]

Counterfactual credit 最多额外运行 \(k\) 次 deterministic verifier：

\[
O(kV)
\]

数据库学习事务：

\[
O(1)\ \text{transactions / round}
\]

而不是按工具产生多个独立 writer transactions。

---

## 18. 已执行的专项性能实验

以下为**隔离 routing-learning-store 压测**，不是完整 Runtime / 模型 / MCP 的端到端 QPS。

240 个并发 routing rounds、每轮 4 个 learning samples 的旧逐工具双事务实现，在 64 workers 下约：

- 59 rounds/s；
- p95 约 2.60 s。

改成单轮单事务，但 writer lock 内仍做 posterior inverse 后，吞吐显著提高；进一步把矩阵求逆完全移出 writer lock 后，隔离结果为：

| Workers | Throughput | p50 | p95 | p99 |
| ---: | ---: | ---: | ---: | ---: |
| 16 | ~256.5 rounds/s | 0.061 s | 0.116 s | 0.160 s |
| 64 | ~299.5 rounds/s | 0.202 s | 0.421 s | 0.449 s |
| 120 | ~351.5 rounds/s | 0.306 s | 0.549 s | 0.611 s |

这说明 adaptive 数学本身不是主要瓶颈；SQLite writer transaction 粒度与持锁时间才是当前单节点 learning state 的主要性能变量。

这些脚本只在临时环境执行，不提交仓库。

---

## 19. 当前研究边界与下一阶段

已经实现：

- fixed heuristic → cold-start prior → online posterior；
- global/domain hierarchical transfer；
- deterministic UCB；
- contextual abstention advantage；
- shadow → adaptive activation；
- non-stationary decay；
- batched writer path；
- outcome-surprise drift EWMA；
- sample-shrunk tool reliability posterior；
- harmonic verifier leave-one-out credit；
- budget/diversity set selection；
- learner failure 与业务 authority 隔离；
- routing/runtime telemetry。

下一阶段值得研究：

1. **Doubly-robust off-policy evaluation**：在不扩大线上 exploration 风险时比较候选 policy；
2. **Gold-set policy promotion gate**：posterior 相对 prior 的收益达到统计门槛后才提高 activation；
3. **World-model shadow replay**：只用于认知策略评估，不拥有真实业务 authority；
4. **Incremental verifier sufficient statistics**：长轨迹中降低 leave-one-out 复核成本；
5. **Shared transactional policy store**：多节点高写入场景迁出 SQLite；
6. **Non-linear posterior head**：真实 gold set 足够大后再研究低秩或 neural contextual bandit，并保留可解释 safety features。

这些扩展仍只作用于 cognition policy。

---

## 20. 相关前沿方向

当前设计与以下研究方向相关，但并非直接复制：

- Agent trajectory / turn-level credit assignment；
- learned tool routing / tool-use reinforcement learning；
- contextual bandit routing；
- agent world model / simulator for shadow evaluation。

EcomEvo 的系统选择是：**先学习一个小型、可审计、read-only 的 Runtime policy，再把大模型能力当作可替换候选生成器。**这样模型升级不会抹掉 Runtime 已积累的路由经验，也不会把业务权限交给学习器。
