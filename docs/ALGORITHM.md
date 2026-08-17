# EcomEvo 算法说明

## 摘要

EcomEvo 将自主 Agent 运行时建模为一个**受约束的序贯证据获取与决策过程**。大模型负责提出候选认知动作，但候选顺序不拥有执行权；Runtime 使用确定性策略在安全约束、证据缺口、信息增益、工具成本、技能后验与同轮多样性之间进行选择。真实业务结果进一步更新技能后验和领域级进化策略，从而形成有界的在线自适应。

本说明给出当前实现对应的数学抽象。公式用于解释运行时目标和设计原则；若某个公式比当前代码更一般，会明确标注为“系统目标”而不是伪装成已完成的理论证明。

---

## 1. 问题定义

设业务任务为

\[
\mathcal{T}=(g,d,\mathcal{C},\mathcal{E}^{req},B,\rho)
\]

其中：

- \(g\)：用户目标；
- \(d\)：业务域；
- \(\mathcal{C}\)：硬约束集合；
- \(\mathcal{E}^{req}\)：完成任务所需证据类型；
- \(B\)：工具成本预算；
- \(\rho\)：风险容忍参数。

在第 \(t\) 步，Runtime 维护 belief state：

\[
b_t=(F_t,R_t,U_t,M_t,q_t)
\]

其中 \(F_t\) 为已确认事实，\(R_t\) 为风险，\(U_t\) 为不确定性，\(M_t\) 为缺失证据集合，\(q_t\) 为验证分数。

关键约束是：**belief 不是证据本身**。模型回复、memory、skill 和历史文本只能改变搜索策略，不能直接充当独立业务证据。

---

## 2. EvoLoop：受控自主循环

运行过程可表示为：

\[
\text{Observe}\rightarrow\text{Decide}\rightarrow\text{Route}\rightarrow\text{Act}\rightarrow\text{Review}\rightarrow\text{Verify}\rightarrow\text{Replan/Stop}
\]

定义可执行只读动作集合为 \(\mathcal{A}^{ro}_t\)。模型控制器生成候选集合 \(\hat{\mathcal{A}}_t\)，Runtime 实际允许的候选为：

\[
\mathcal{A}_t = \hat{\mathcal{A}}_t \cap \mathcal{A}^{ro}_t \cap \mathcal{A}^{budget}_t \cap \mathcal{A}^{sandbox}_t
\]

因此模型输出不是动作授权，而只是候选生成。

### 2.1 停机条件

Runtime 在满足任一条件时停止本轮自主探索：

\[
\text{Stop}_t = V_t \lor (B_t\le \epsilon) \lor S_t \lor (|\mathcal{A}_t|=0)
\]

其中：

- \(V_t\)：Verifier 已通过；
- \(B_t\)：剩余预算；
- \(S_t\)：连续停滞条件成立；
- \(|\mathcal{A}_t|=0\)：没有合法且有价值的下一步动作。

当前实现另外受最大自主步数、每步工具数和每步委派数的硬上限约束。

---

## 3. EvoGain：确定性证据信息增益路由

### 3.1 候选特征

对候选工具 \(a_i\)，定义：

- \(C_i\)：missing-evidence coverage；
- \(A_i\)：source authority；
- \(S_i\)：learned-skill support；
- \(N_i\)：novelty；
- \(X_i\)：contradiction value；
- \(P_i\)：specificity；
- \(c_i\)：执行成本。

当前实现的基础效用对应：

\[
R_i = 1.70C_i+0.58A_i+0.48S_i+0.36N_i+0.20X_i+0.10P_i
\]

成本归一化后：

\[
U_i=\frac{R_i}{0.72+\max(0.15,c_i)^{0.68}}
\]

这些系数是**路由权重**，不是业务置信度，也不会进入 Verifier 的证据判断。

### 3.2 缺失证据覆盖

设当前缺失证据目标为 \(M_t=\{m_1,\ldots,m_k\}\)，工具的证据通道词集合为 \(H_i\)。对每个目标 \(m_j\) 提取归一化 term set \(T(m_j)\)。当前实现使用离散覆盖近似：

\[
c_{ij}=\begin{cases}
\min(1,0.42+0.18|T(m_j)\cap H_i|), & |T(m_j)\cap H_i|>0\\
0.34, & a_i=\text{evidence.search}\\
0, & \text{otherwise}
\end{cases}
\]

随后：

\[
C_i=\frac{1}{|M_t|}\sum_j c_{ij}
\]

若当前没有显式缺口，则使用目标定义中的 required evidence 作为目标集合。

### 3.3 信息新颖度

若同一工具此前成功调用次数为 \(n_i\)，当前实现使用：

\[
N_i=\frac{1}{1+0.72n_i}
\]

这使重复调用的边际价值递减，但并不绝对禁止带有新参数的重新检索。

### 3.4 Skill support

对当前召回的技能集合 \(\mathcal{K}_t\)，若技能 \(k\) 推荐工具 \(a_i\)，其后验均值为 \(\mu_k\)，则：

\[
S_i=\max_{k\in\mathcal{K}_t:a_i\in tools(k)}\mu_k
\]

技能只是路由先验，不构成业务证据。

### 3.5 同轮多样性

设已选工具证据通道并集为 \(H_S\)。候选工具与已选通道的重叠率为：

\[
O_i=\frac{|H_i\cap H_S|}{\max(1,|H_i|)}
\]

当前 greedy surrogate 使用：

\[
\tilde U_i=U_i-0.24O_i-0.002p_i
\]

其中 \(p_i\) 是模型候选中的原始位置。位置项只作为极小的稳定 tie-breaker，因此模型排序不能支配 Runtime 路由。

### 3.6 系统级集合目标

更一般地，EvoGain 的设计目标可以写成受约束集合选择：

\[
S_t^*=\arg\max_{S\subseteq\mathcal{A}_t}
\left[
\sum_{i\in S}U_i
-\lambda\sum_{i\ne j\in S}\operatorname{overlap}(i,j)
\right]
\]

约束：

\[
\sum_{i\in S}c_i\le B_t,\qquad |S|\le K,\qquad sideEffect(i)=0
\]

当前代码采用确定性 greedy 近似，而不是声称已经求解全局组合最优化。

---

## 4. Stagnation Detection 与 Cognitive Topology Mutation

对验证结果和工具轨迹构造状态指纹：

\[
f_t=H(\operatorname{sort}(M_t),\operatorname{sort}(Tools_t^{ok}),\operatorname{sort}(Tags_t))
\]

其中 \(H\) 为 SHA-256。

若连续多轮：

\[
f_t=f_{t-1}
\]

则认为当前策略没有带来新的可验证状态变化。第一次停滞时 Runtime 可以增加只读“反证审查”等 specialist；连续停滞达到硬阈值后停止继续机械探索。

这相当于把“再试一次”改成“改变认知拓扑，若仍无信息增益则停机”。

---

## 5. Bayesian Skill Evolution

### 5.1 后验

每个技能维护 Beta 分布：

\[
p_k\sim \operatorname{Beta}(\alpha_k,\beta_k)
\]

后验均值：

\[
\mu_k=\frac{\alpha_k}{\alpha_k+\beta_k}
\]

当前实现的保守下界近似：

\[
LCB_k=\max\left(0,\mu_k-1.64\sqrt{\frac{\mu_k(1-\mu_k)}{\alpha_k+\beta_k+1}}\right)
\]

它只用于技能质量判断，不进入业务事实层。

### 5.2 Shadow prior

候选技能的 replay score 为 \(s_k\in[0,1]\)。当前先验初始化为：

\[
\alpha_k=1.5+4s_k
\]

\[
\beta_k=1.5+4(1-s_k)
\]

因此离线 replay 只影响初始先验，不会把候选技能永久“认证”为正确。

### 5.3 在线更新

对一次真实任务结果 \(y\in\{0,1\}\)：

\[
\alpha_k\leftarrow\alpha_k+y
\]

\[
\beta_k\leftarrow\beta_k+(1-y)
\]

技能使用次数、胜负次数同时持久化。

### 5.4 技能召回排名

当前 active skills 的召回分数为：

\[
Rank(k)=0.46\mu_k+0.34s_k+\min(0.20,0.05h_k)
\]

其中 \(h_k\) 是 trigger term 命中数。

---

## 6. Quality-Diversity Archive

技能 niche 由业务域、trigger terms 和 preferred tools 的标准化表示哈希得到。

对同一 niche 的 incumbent \(k\) 与 candidate \(k'\)，定义：

\[
Q(k)=0.55s_k+0.45\mu_k
\]

新候选只有满足：

\[
Q(k')>Q(k)+0.015
\]

才替换当前 live representative。

目的不是保存“所有学到的东西”，而是防止技能库被大量同质 prompt 变体污染。

---

## 7. Meta-Evolution

每个业务域维护：

\[
\theta_d=(\tau_p,\tau_r,e)
\]

其中：

- \(\tau_p\)：promotion threshold；
- \(\tau_r\)：retirement threshold；
- \(e\)：exploration strength。

真实成功和失败以小步长调整这些参数，但参数被硬范围夹紧。例如技能失败会提高晋升门槛并增加探索；技能成功会轻微降低探索并逐步放宽晋升。

这是一种**有界策略自适应**，而不是让 Agent 重写安全边界。

---

## 8. Verification 与 Authority Separation

令业务动作集合为 \(\mathcal{B}\)。一个动作进入 proposal 层至少需要：

\[
V(g,b_t,Results_t,Reviews_t)=1
\]

并满足 evidence complete、constraints satisfied 和 side-effect safe。

即使满足上述条件，若 \(b\in\mathcal{B}\) 会改变真实业务状态：

\[
Execute(b)=1 \Rightarrow HumanConfirm(b)=1
\]

模型没有路径把这个条件改写为 false。

因此系统的核心结构不是“更聪明的 LLM”，而是：

\[
\text{Autonomous Cognition}\;\perp\;\text{Deterministic Authority}
\]

两者在系统架构上解耦。

---

## 9. 计算复杂度

设候选工具数为 \(n\)，每步最多选择 \(K\) 个工具。当前 greedy EvoGain 每次选择都重算剩余候选的 diversity-adjusted utility，时间复杂度约为：

\[
O(Kn)
\]

由于 \(K\) 和每步候选上限都被硬限制，该部分开销相对于远程模型和工具调用通常很小。

技能召回在当前 SQLite 实现中对单域最多读取有界候选集合后排序，近似为：

\[
O(m\log m)
\]

其中 \(m\) 是当前业务域 active skill 数。

---

## 10. 不变量

无论模型、技能和进化策略如何变化，以下约束应保持：

1. 用户陈述不是独立企业证据；
2. 历史系统回答不是独立证据；
3. Skill / memory 不能替代 Verifier；
4. 自主工具必须经过 registry + sandbox；
5. 高影响动作必须经过确定性权限边界；
6. 不确定的下游执行结果不能被当成成功；
7. 进化不能关闭证据门槛；
8. 进化不能给 Agent 新增生产权限。

---

## 11. 研究问题与局限

当前实现没有声称解决所有 Agent research 问题。仍值得继续研究：

- EvoGain 权重能否通过离线 counterfactual replay 学习，而不破坏安全可解释性；
- set-level evidence gain 是否可以形成严格的 submodular 近似保证；
- 不同业务域如何学习独立的 cost-quality frontier；
- specialist topology mutation 如何在质量与额外调用成本之间优化；
- 如何用真实 gold set 衡量技能进化是否产生长期净收益；
- 如何在分布式 durable execution 下保持 event-sourced evolution 的一致性。

真正的模型或 Harness 对比必须控制相同 base model、工具集合、预算、任务数据和评估规则，否则无法把收益归因到 Runtime 算法本身。
