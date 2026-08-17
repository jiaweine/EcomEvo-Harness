<div align="center">

# EcomEvo

### Autonomous Commerce Runtime

**自主决策 · 自我进化 · 多模态证据 · 可控执行**

把商品治理、商家审核、售后判责、风险核查和内容审核，从一次模型回答升级为能够持续规划、查证、复核、恢复、学习并受控执行的业务任务。

<p>
  <img alt="Runtime" src="https://img.shields.io/badge/Runtime-Autonomous-2B2926" />
  <img alt="Routing" src="https://img.shields.io/badge/Routing-Adaptive%20Posterior-C76535" />
  <img alt="Learning" src="https://img.shields.io/badge/Learning-Bayesian-3F765E" />
  <img alt="Input" src="https://img.shields.io/badge/Input-Multimodal-BF7B32" />
  <img alt="Authority" src="https://img.shields.io/badge/Authority-Deterministic-8F3D33" />
</p>

**[产品手册](docs/PRODUCT_MANUAL.md) · [算法技术报告](docs/ALGORITHM.md) · [技术手册](docs/TECHNICAL_MANUAL.md) · [架构](docs/ARCHITECTURE.md) · [设计系统](docs/DESIGN.md) · [验证报告](docs/VERIFICATION_REPORT.md)**

> **Give the Agent a goal, not a script.**

</div>

---

## 产品预览

![EcomEvo 商业决策工作台](docs/images/product-workbench.svg)

<p align="center">
  <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务工作台" width="360" />
</p>

### Demo / 视频

GitHub README 可以上传视频。仓库首屏建议继续使用轻量封面或短 GIF，完整演示使用 H.264 MP4 / WebM，避免大文件拖慢首次浏览。

---

## 一个任务，不是一轮聊天

真实电商业务很少是“一问一答”。同一个任务可能同时包含订单、物流、主体信息、资质、商品声明、图片、视频、录音、PDF、表格、历史风险和企业内部数据；随着核对推进，还会不断出现新的证据缺口、冲突和分支。

EcomEvo 把这些信息放进一个持续任务：

- 用户给目标，Runtime 决定下一步查什么；
- 文本、图片、视频、音频、文档、表格和日志进入统一证据空间；
- 工具调用可以并行、重排、停止和重规划；
- specialist 可以按当前任务动态派生，用于反证和专项复核；
- routing policy 和技能都能从真实结果中持续更新；
- 资料不足时明确停止并请求补证；
- 退款、下架、审核、冻结等高影响动作始终在模型权限之外。

```text
Goal
  ↓
Belief / Evidence State
  ↓
EvoLoop
  ├─ Observe
  ├─ Decide
  ├─ EvoGain-APR
  ├─ Parallel Read-only Tools
  ├─ Delegate Specialists
  ├─ Review
  ├─ Verify
  ├─ Counterfactual Credit
  ├─ Posterior Update
  ├─ Reflect / Replan
  └─ Stop
  ↓
Deterministic Authority
  ↓
Human Approval
  ↓
Business Action
```

---

## 核心算法

### EvoLoop：受控自主循环

Runtime 持续执行：

**Observe → Decide → Route → Act → Review → Verify → Replan / Stop**

固定 Planner 只保留业务域、硬约束和证据底线。语言模型负责产生候选认知动作，但候选没有业务执行权。

### EvoGain-APR：Adaptive Posterior Routing

旧式固定价值函数已经降级成 cold-start prior。

工具路由现在学习一个上下文参数后验：

\[
w\sim\mathcal N(\mu_0,\Lambda_0^{-1})
\]

每次真实工具结果产生 credit 后更新：

\[
A_t=A_0+\delta(A_{t-1}-A_0)+x_tx_t^T
\]

\[
b_t=b_0+\delta(b_{t-1}-b_0)+r_tx_t
\]

\[
\mu_t=A_t^{-1}b_t
\]

其中上下文包含 evidence coverage、source authority、skill posterior、novelty、counter-evidence value、tool reliability、cost pressure、same-round redundancy、evidence gap 和 recovery context。

### Global → Domain 层级迁移

全局经验和业务域经验分别维护 posterior。

\[
\tau_d=\frac{n_d}{n_d+\kappa}
\]

\[
\hat\mu_d(x)=
(1-\tau_d)\mu_g^Tx+
\tau_d\mu_d^Tx
\]

新业务域可以利用全局经验，但不会直接继承成熟业务域的全部偏好。

### Deterministic UCB

生产探索不依赖随机抽样：

\[
Q_t(x)=\hat\mu_t(x)+\beta_t\sqrt{x^TA_t^{-1}x}
\]

这样既能探索高不确定工具，又能保持同状态下的可复现排序。

### Shadow → Adaptive

样本不足时，posterior 只 shadow 学习，不直接改变生产排序。

成熟后：

\[
Score_t(x)=
(1-\eta_t)\mu_0^Tx+
\eta_tQ_t(x)
\]

\(\eta_t\) 随样本量和残差置信度上升，最高约 0.96。也就是说，冷启动先验不会成为永久认知天花板。

### Verifier Difference Credit

EcomEvo 不用另一组固定 reward 权重训练 routing policy。

对一次被选中的工具结果 \(r_i\)，Runtime 计算：

\[
\Phi(v)=VerifierScore(v)+EvidenceCompleteness(v)
\]

\[
D_i=
\Phi(V(R))-
\Phi(V(R\setminus\{r_i\}))
\]

\[
credit_i=\frac{D_i}{1+cost_i}
\]

也就是：**拿掉这个工具结果后，Verifier 的可验证状态到底下降了多少。**

这个差分 credit 再用于更新 posterior。

### Tool Reliability Posterior

工具是否“有价值”和工具是否“稳定”分开学习：

\[
p_{d,a}\sim Beta(\alpha_{d,a},\beta_{d,a})
\]

调用成功增加 \(\alpha\)，失败增加 \(\beta\)。可靠性 posterior 只是 routing feature，不是业务证据。

完整数学定义、复杂度和研究边界见 **[算法技术报告](docs/ALGORITHM.md)**。

---

## Dynamic Task Graph

任务路径不是提前写死的固定 DAG。

每一次初始计划、补证、specialist 复核、重规划、verification、停滞检测和恢复都进入 Task Graph。

Runtime 可以根据新证据改变认知路径，但所有动作仍留在事件链中。

---

## Cognitive Topology Mutation

当 verification fingerprint 连续不变，Runtime 不机械重试。

它可以动态增加：

- 反证审查；
- 授权链路复核；
- 时间线复核；
- 冲突证据检查；
- 特定业务域 specialist。

这些角色永远只有 read-only cognition authority。

---

## Bayesian Skill Evolution

EcomEvo 不把失败案例无限追加到 prompt，也不允许模型随意修改安全源码。

```text
Task trajectory
      ↓
Failure / Recovery diagnosis
      ↓
Candidate read-only skill
      ↓
Shadow replay
      ↓
Regression gate
      ↓
Quality-Diversity archive
      ↓
Live use
      ↓
Bayesian outcome update
      ↓
Promote / decay / retire
```

每个技能维护 Beta posterior：

\[
p_k\sim Beta(\alpha_k,\beta_k)
\]

真实任务成功/失败持续更新可信度；表现长期下降的技能可以自动退役。

---

## 多模态证据

支持：

**文字 · 图片 · 视频 · 音频 · PDF · Word · Excel · CSV · JSON · 日志**

多模态模型只负责提取可观察事实，不直接决定退款、下架、审核或冻结。

语义事实必须进入统一证据链，再由 Runtime 验证。

---

## 认知自治，权限确定

Agent 可以自主：

- 选择下一步只读工具；
- 并行查证；
- 重排工具；
- 委派 specialist；
- 主动找反证；
- 停止低价值探索；
- 学习 routing posterior；
- 学习、晋升和退役技能。

Agent 不可以：

- 把模型回答当独立证据；
- 降低 required evidence；
- 修改 Sandbox / Verifier 权限；
- 给自己新增生产工具；
- 自动批准高影响动作；
- 在 `uncertain` 状态下盲重试。

> **策略可以进化，权限不能自我扩张。**

---

## 模型与部署

模型是可替换认知引擎，不是业务权限中心。

EcomEvo 支持云端服务、企业兼容接口和 OpenAI-Compatible 的开源权重 / 自托管推理服务。

```bash
OPEN_MODEL_BASE_URL=http://your-runtime/compatible-endpoint
OPEN_MODEL_API_KEY=your-key
OPEN_MODEL_MODEL=your-current-model
OPEN_MODEL_MULTIMODAL=0
```

底层模型升级不要求改动 EvoGain、Verifier、Sandbox 或 BusinessAction 权限链。

---

## 企业工具

推荐把 MCP 能力严格分成两类：

```text
Read-only MCP
  → autonomous exploration
  → evidence

Side-effect MCP
  → BusinessAction proposal
  → human confirmation
  → execution
```

模型不能把 read-only cognition 自动升级成生产动作权限。

---

## 工程验证记录

本分支之前已执行过两组临时压力测试；脚本没有提交仓库。

### Shared SQLite Runtime

| 指标 | 结果 |
| --- | ---: |
| 并发任务 | 240 |
| Throughput | 37.2 runs/s |
| p50 | 3.74 s |
| p95 | 5.22 s |
| p99 | 5.25 s |
| Event-chain failures | 0 |
| Incomplete-case side-effect leaks | 0 |
| Duplicate semantic evolution patches | 0 |

### Adversarial Controller

| 指标 | 结果 |
| --- | ---: |
| 并发控制器 | 80 |
| Unsafe proposals rejected | 80 / 80 |
| Side-effect leaks | 0 |
| Cognitive delegation | 80 / 80 |
| Event-chain failures | 0 |

这些是本地工程记录，不是第三方 benchmark 排名，也不能单独证明整体智能能力优于其它系统。

新增 adaptive posterior routing 另外做了本地专项 smoke：

- posterior 从 shadow 进入 adaptive；
- domain/global posterior 持久化；
- tool reliability Beta posterior 更新；
- counterfactual verifier credit 为正时写入 routing outcome；
- `routing.policy.updated` 事件正常产生；
- learner error 不改变 live task 的安全执行路径。

---

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`。

### Docker

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

---

## 文档

| 文档 | 用途 |
| --- | --- |
| [产品手册](docs/PRODUCT_MANUAL.md) | 运营、审核、客服、风控使用 |
| [算法技术报告](docs/ALGORITHM.md) | 数学定义、posterior、credit、复杂度、研究方向 |
| [技术手册](docs/TECHNICAL_MANUAL.md) | 部署、二开、API、MCP、恢复、安全 |
| [架构](docs/ARCHITECTURE.md) | 系统分层与数据流 |
| [设计系统](docs/DESIGN.md) | UI、字体、动效、响应式、Agent UX |
| [验证报告](docs/VERIFICATION_REPORT.md) | 已执行验证与生产边界 |

---

## 项目边界

EcomEvo 追求的是更强的 **agent runtime architecture**，而不是靠 README 宣布“世界第一”。

真正的能力比较必须在相同任务、相同模型、相同工具、相同 token/cost budget 和一致评估标准下完成。

目前最重要的生产级后续工作仍包括：真实业务 gold set、CI eval gate、durable execution、tenant/SSO/RBAC、真实浏览器视觉回归和更严格的 off-policy evaluation。

<div align="center">

### EcomEvo

**目标交给 Agent，权限留给业务。**

</div>
