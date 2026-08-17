<div align="center">

# EcomEvo

### Autonomous Commerce Runtime

**把复杂电商任务，从一次回答，变成持续查证、可恢复、可学习、权限受控的业务运行时。**

<p>
  <img alt="CI" src="https://github.com/jiaweine/EcomEvo-Harness/actions/workflows/ci.yml/badge.svg?branch=agent%2Fautonomous-self-evolving-runtime" />
  <img alt="Runtime" src="https://img.shields.io/badge/Runtime-Autonomous-202733" />
  <img alt="Routing" src="https://img.shields.io/badge/Routing-Adaptive%20Posterior-C76535" />
  <img alt="Evidence" src="https://img.shields.io/badge/Evidence-Multimodal-BF7B32" />
  <img alt="Authority" src="https://img.shields.io/badge/Authority-Deterministic-3F765E" />
</p>

**[产品手册](docs/PRODUCT_MANUAL.md) · [算法技术报告](docs/ALGORITHM.md) · [技术手册](docs/TECHNICAL_MANUAL.md) · [性能](docs/PERFORMANCE.md) · [设计系统](docs/DESIGN.md) · [验证报告](docs/VERIFICATION_REPORT.md)**

> **Give the Agent a goal, not a script.**

</div>

---

## 一个商业任务，而不是一轮聊天

真实电商决策很少只依赖一段文本。

一次商品治理可能同时涉及主图、详情、资质、声明和历史风险；一次售后判责可能需要订单、物流、聊天记录、图片、录音和规则；一次商家审核还会不断遇到新的主体、授权链和证据缺口。

**EcomEvo 把这些材料放进一个持续任务，让 Runtime 自己决定下一步查什么、什么时候补证、什么时候反证、什么时候停止。**

它不是把更多工具塞进一个聊天框，而是把 Agent 变成一个有状态、有预算、有验证器、有恢复能力、也有明确权限边界的业务运行时。

<p align="center">
  <img src="docs/images/product-workbench.svg" alt="EcomEvo 商业决策工作台" width="100%" />
</p>

<table>
  <tr>
    <td width="58%" valign="top">
      <img src="docs/images/product-evidence-wall.svg" alt="EcomEvo 商品证据空间" width="100%" />
      <br />
      <sub><b>商品证据空间</b> · 主图、详情、资质、声明与企业证据进入同一验证链。</sub>
    </td>
    <td width="42%" valign="top">
      <img src="docs/images/product-runtime-control.svg" alt="EcomEvo 运行质量与权限控制面" width="100%" />
      <br />
      <sub><b>运行质量与权限控制面</b> · 证据完整度、停止原因、预算和权限边界直接可见。</sub>
    </td>
  </tr>
</table>

<table>
  <tr>
    <td width="70%" valign="top">
      <img src="docs/images/product-scenes.svg" alt="EcomEvo 五类业务场景" width="100%" />
      <br />
      <sub><b>五类高价值场景</b> · 商品治理、商家审核、售后判责、风险核查、内容审核。</sub>
    </td>
    <td width="30%" valign="top" align="center">
      <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务工作台" width="92%" />
      <br />
      <sub><b>移动端控制面</b> · 任务、证据与审批状态在窄屏保持同一语义。</sub>
    </td>
  </tr>
</table>

---

## EcomEvo 在做什么

### 01 · 商品治理

把商品主图、详情页、功效声明、品牌资料、资质文件与历史治理记录放进同一个证据空间。Runtime 会主动发现冲突和缺口，而不是只总结附件。

### 02 · 商家审核

围绕主体、品牌授权、经营范围、风险记录和关联企业持续核验。授权链没有闭合时，系统会明确停在“缺证”，而不是把模型推测包装成完成。

### 03 · 售后判责

根据订单、物流、聊天、图片、录音和规则重建事实时间线；必要时补查物流或历史记录。退款建议可以自主生成，真实退款执行仍然需要明确权限。

### 04 · 风险核查

把弱线索、相关性和独立证据分开。Runtime 会主动寻找反证，避免把“看起来可疑”直接升级成业务事实。

### 05 · 内容审核

图片、视频、音频、文案和结构化资料进入同一验证过程。多模态模型负责提取可观察事实，但不能单独决定处罚或下架。

---

## 为什么它不是普通 Agent

| 普通工具型 Agent 容易遇到的问题 | EcomEvo 的 Runtime 设计 |
| --- | --- |
| 模型直接决定下一个工具 | **EvoGain-APR** 在合法动作空间内学习工具路由 |
| 工具越多，调用越容易失控 | 预算、并行上限、超时、stagnation 与 contextual abstention 同时约束 |
| 模型自评成为“奖励” | **Verifier Difference Credit** 用反事实边际证据贡献学习 |
| 失败后无限重试 | Dynamic Task Graph + verification fingerprint 触发重规划或停止 |
| 历史经验无限塞进 prompt | Bayesian Skill Library 通过 posterior、shadow gate 与 retirement 管理 |
| 模型既判断又执行 | Verifier、Governance、RBAC 与 Human Approval 独立掌握真实业务权限 |
| 进程一挂任务就丢 | Durable Job + immutable evidence snapshot + cross-process lease 恢复 |

> **认知自治，权限确定。** 允许系统持续学习“下一步查什么”，但不允许它学习“如何获得更多权限”。

---

## 核心运行闭环

```text
Goal
  ↓
Evidence / Belief State
  ↓
Observe → Decide → Route → Act → Review → Verify
                         ↓                 ↓
                    Read-only Tools   Counterfactual Credit
                         ↓                 ↓
                     Task Graph      Posterior Update
                         └─────── Replan / Stop ───────┘
                                      ↓
                         Deterministic Authority
                                      ↓
                              Human Approval
                                      ↓
                              Business Action
```

语言模型可以生成候选认知动作，但候选先经过 Registry、Read-only Policy、Budget 和 Sandbox 过滤：

$$
\mathcal A_t=
\hat{\mathcal A}_t
\cap\mathcal A^{registered}
\cap\mathcal A^{read\text{-}only}
\cap\mathcal A^{budget}
\cap\mathcal A^{sandbox}
$$

学习发生在合法动作空间之内，而不是用 reward 去“学会绕过权限”。

---

# EvoGain-APR

## 可学习的上下文路由，而不是永久固定权重

EcomEvo 对候选只读工具构造可审计上下文特征，包括 evidence coverage、authority、skill support、novelty、counter-evidence value、specificity、tool reliability、cost pressure、redundancy、evidence gap 和 recovery context。

冷启动使用保守 Gaussian prior：

$$
w\sim\mathcal N(\mu_0,\Lambda_0^{-1})$$

真实任务持续更新后验 sufficient statistics：

$$
A_t=A_0+\delta(A_{t-1}-A_0)+\sum_{i=1}^{k}x_ix_i^T
$$

$$
b_t=b_0+\delta(b_{t-1}-b_0)+\sum_{i=1}^{k}r_ix_i$$

$$
\mu_t=A_t^{-1}b_t
$$

因此人工数字只决定冷启动方向，长期策略可以被真实 outcome 覆盖。

### 1. Deterministic UCB

生产环境不依赖随机采样来决定工具顺序：

$$
Q_t(x)=\hat\mu_t(x)+\beta_t\hat\sigma_t(x)
$$

高 posterior mean 的工具被优先利用；合法但高不确定的工具仍然保留有限探索机会。相同 state + posterior 会得到可复现排序。

### 2. Contextual Abstention

系统不仅学习“哪个工具更好”，也学习“现在是否还值得继续调用工具”。

$$
Adv_t(a_i\mid s_t)=Score_t(x_i)-Score_t(x_{\varnothing}(s_t))
$$

只有：

$$
Adv_t(a_i\mid s_t)>0
$$

才继续执行只读工具。

这让停止边界和 posterior 使用同一标尺，不再依赖一个永久固定的绝对 utility cutoff。

### 3. Verifier Difference Credit

EcomEvo 不让模型给自己的工具调用打分，也不使用另一组固定 reward 权重拼接学习信号。

先定义同时受 Verifier score 与 evidence completeness 限制的调和验证势能：

$$
\Phi(v)=
\begin{cases}
\frac{2q(v)c(v)}{q(v)+c(v)}, & q(v)+c(v)>0\\
0, & \text{otherwise}
\end{cases}
$$

然后对本轮工具结果做 deterministic leave-one-out：

$$
D_i=\Phi(V(R))-\Phi\left(V(R\setminus\{r_i\})\right)
$$

$$
credit_i=\frac{D_i}{1+cost_i}
$$

直观上就是：**拿掉这个工具结果之后，可验证状态到底下降了多少。**

### 4. Tool Reliability Posterior

一个工具“证据价值高”和“运行稳定”不是同一件事，因此 reliability 单独维护 Bayesian posterior：

$$
p_{d,a}\sim \operatorname{Beta}(\alpha_{d,a},\beta_{d,a})
$$

成功和失败持续更新稳定性；reliability 只作为 routing feature，不自动变成业务证据。

> 完整数学定义、层级迁移、shadow activation、复杂度与研究边界见 **[算法技术报告](docs/ALGORITHM.md)**。

---

## 三种持续学习

<table>
  <tr>
    <td width="33%" valign="top"><b>Routing Learning</b><br/><br/>学习当前状态下下一步查什么。<br/><br/><code>context → posterior → UCB → tool set</code></td>
    <td width="33%" valign="top"><b>Tool Reliability</b><br/><br/>学习一个数据源或工具在不同业务域到底有多稳定。<br/><br/><code>success / failure → Beta posterior</code></td>
    <td width="34%" valign="top"><b>Skill Evolution</b><br/><br/>学习哪些长期认知策略值得复用、晋升或退休。<br/><br/><code>shadow → replay → QD archive → promote/retire</code></td>
  </tr>
</table>

这三种学习都只改变认知层策略，不能改变 registered tool、required evidence、credential scope、hard budget、confirmation requirement 或审批身份。

---

## Durable Execution

任务不是依赖某个进程内的临时回调。

```text
User message
   ↓
Atomic message + accepted event + durable job
   ↓
Immutable input / asset snapshot
   ↓
Cross-process worker lease
   ↓
Runtime execution
   ↓
Atomic assistant message + proposals + terminal event
```

worker 中断后，lease 到期可以由其他 worker reclaim；运行中的任务不能悄悄加入新资料，因此一个自主轮次始终面对封闭的 evidence snapshot。

WebSocket 的进程内 queue 只用于低延迟唤醒，SQLite `task_events` 才是跨 worker 的事件顺序真相。断线重连使用 `after_id` 增量续传，不需要重复回放整个任务历史。

---

## 真实业务动作永远在学习层之外

```text
Adaptive Cognition
      ↓
Read-only Tools / Specialists
      ↓
Verifier
════════════════════════════════
Deterministic Authority Boundary
      ↓
BusinessAction Proposal
      ↓
Approver Identity + Human Confirmation
      ↓
Business Executor
```

Agent 可以自主查证、并行工具、重规划、反证、停止和学习；但不能自行：

- 降低 required evidence；
- 把模型回答当独立证据；
- 修改 Sandbox / Verifier；
- 注册新的生产 side-effect tool；
- 自动批准退款、下架、冻结等高影响动作；
- 在 `uncertain` 状态下盲目重放副作用请求。

MCP / 企业工具的 timeout、连接中断、HTTP 5xx/408、损坏响应和执行结果不确定会进入 `uncertain`，而不是被伪装成“明确失败后安全重试”。

---

## 多租户、RBAC 与审批审计

生产身份边界支持 trusted-proxy / gateway 签入的 tenant、user 和 role。角色层级：

```text
viewer < operator < approver < admin
```

- tenant-scoped conversation / asset / action 不泄漏其他租户资源存在性；
- action decision 需要 `approver`；
- runtime / evolution 全局控制面需要 `admin`；
- 审批 CAS 自动记录 actor tenant / user / role / auth mode；
- 真实浏览器永远不持有服务端签名 secret。

具体企业 IdP / SSO 属于部署环境集成，不在仓库里伪造“已完成”。

---

## 当前自动化验证

当前分支把三类 release gate 分开运行：

### Regression

- 完整 `pytest -q`；
- 9-case Gold Set × fresh / persisted replay；
- malicious-controller authority gate；
- durable job / crash reclaim / WebSocket ordering；
- tenant / RBAC / approval audit；
- MCP uncertain / no-blind-replay；
- 所有生产 JS 的 `node --check`；
- HTML / DOM / CSS 基础结构检查。

### Current-head pressure

GitHub-hosted Ubuntu runner 自动跑：

| 并发任务 | Throughput | p50 | p95 | p99 | Safety failures |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 26.604 tasks/s | 0.0375 s | 0.0375 s | 0.0375 s | 0 |
| 64 | 38.417 tasks/s | 1.2793 s | 1.3820 s | 1.3899 s | 0 |
| 120 | 39.508 tasks/s | 2.3034 s | 2.5656 s | 2.5849 s | 0 |
| 240 | **35.402 tasks/s** | **4.6831 s** | **5.7819 s** | **5.8138 s** | **0** |

这些是本地 Runtime / SQLite / policy 路径的 hosted-runner 测量，**不是带真实外部模型和企业 MCP 的业务 QPS**。

### Real Chromium E2E

Playwright 会真正启动 Uvicorn + Chromium，并覆盖：

- 首屏和输入框；
- 场景切换；
- durable message → assistant result；
- Runtime Pulse；
- 命令面板与键盘 focus；
- 390×844 移动端任务抽屉；
- 双标签页同一任务实时同步；
- busy 状态释放；
- page error / console error。

完整记录见 **[验证报告](docs/VERIFICATION_REPORT.md)**。

---

## 快速开始

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

打开：

```text
http://localhost:8000
```

### Docker

```bash
docker build -t ecomevo .
docker run --rm \
  -p 8000:8000 \
  --env-file .env \
  -v ecomevo-data:/app/outputs \
  ecomevo
```

### 自托管认知引擎

认知引擎是可替换组件，不拥有业务执行权。通过兼容接口接入当前可用的云端或开源权重推理服务：

```bash
OPEN_MODEL_BASE_URL=http://your-runtime/compatible-endpoint
OPEN_MODEL_API_KEY=your-key
OPEN_MODEL_MODEL=your-current-model
OPEN_MODEL_MULTIMODAL=0
```

底层模型升级不要求改动 EvoGain-APR、Verifier、Sandbox 或 BusinessAction 权限链。

---

## 文档地图

| 文档 | 适合谁 | 内容 |
| --- | --- | --- |
| **[产品手册](docs/PRODUCT_MANUAL.md)** | 运营 / 审核 / 客服 / 风控 | 怎么发起任务、看证据、补证、确认动作 |
| **[算法技术报告](docs/ALGORITHM.md)** | Agent / ML / Runtime 工程 | EvoGain-APR、posterior、credit、复杂度 |
| **[技术手册](docs/TECHNICAL_MANUAL.md)** | 平台 / 后端 / 安全 | API、durable job、MCP、RBAC、恢复与部署 |
| **[性能手册](docs/PERFORMANCE.md)** | 性能 / 架构 | 压测口径、锁竞争、routing-store 与 current-head gate |
| **[架构](docs/ARCHITECTURE.md)** | 系统设计 | 组件分层、数据流和权限边界 |
| **[设计系统](docs/DESIGN.md)** | 前端 / 产品设计 | 中文排版、动效、响应式和任务工作台规则 |
| **[验证报告](docs/VERIFICATION_REPORT.md)** | Reviewer / 发布负责人 | 已完成 CI、Gold Set、压力和浏览器证据 |

---

## 当前边界

仓库内可以自动化的核心门禁已经闭环，但以下内容必须在真实部署环境验证，不能靠 README 宣布完成：

- 企业 IdP / SSO / Gateway 的真实接入；
- 真实 provider / MCP 凭证、区域、rate limit、schema drift 与下游幂等；
- Safari / Edge 实机验证；
- 大型或畸形 PDF、Office、图片、视频和音频业务语料；
- 超过 SQLite WAL 单 writer 边界后的生产多节点数据库 / 队列拓扑；
- 由真实业务团队持续扩展和裁决的 Gold Set。

真正的智能与产品能力比较，也必须在**相同任务、相同模型、相同工具、相同 token / cost budget 和一致评估标准**下进行。

---

<div align="center">

## EcomEvo

**目标交给 Runtime。证据交给 Verifier。权限留给业务。**

*Adaptive cognition. Deterministic authority.*

</div>
