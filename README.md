<div align="center">

# EcomEvo

### Adaptive Autonomous Commerce Runtime

**把复杂电商任务，从“一次回答”，变成持续查证、可恢复、会学习、权限受控的业务运行时。**

<p>
  <img alt="CI" src="https://github.com/jiaweine/EcomEvo-Harness/actions/workflows/ci.yml/badge.svg?branch=agent%2Fautonomous-self-evolving-runtime" />
  <img alt="Adaptive Routing" src="https://img.shields.io/badge/Routing-Adaptive%20Posterior-C76535" />
  <img alt="Multimodal Evidence" src="https://img.shields.io/badge/Evidence-Multimodal-BF7B32" />
  <img alt="Durable Runtime" src="https://img.shields.io/badge/Runtime-Durable-315EA8" />
  <img alt="Deterministic Authority" src="https://img.shields.io/badge/Authority-Deterministic-3F765E" />
</p>

**[产品手册](docs/PRODUCT_MANUAL.md) · [算法技术报告](docs/ALGORITHM.md) · [技术手册](docs/TECHNICAL_MANUAL.md) · [性能](docs/PERFORMANCE.md) · [设计系统](docs/DESIGN.md) · [验证报告](docs/VERIFICATION_REPORT.md)**

> **Give the Agent a goal, not a script.**

</div>

<br />

<p align="center">
  <img src="docs/images/product-workbench.svg" alt="EcomEvo 商业决策工作台" width="100%" />
</p>

<p align="center">
  <sub><b>EcomEvo 商业决策工作台</b> · 一个任务里同时组织目标、消息、多模态证据、运行状态、验证结果与待确认业务动作。</sub>
</p>

<br />

<table>
  <tr>
    <td width="25%" align="center" valign="top">
      <img src="docs/images/product-evidence-wall.svg" alt="证据空间" width="100%" />
      <br /><sub><b>Evidence Space</b><br/>多模态证据与缺口</sub>
    </td>
    <td width="25%" align="center" valign="top">
      <img src="docs/images/product-runtime-control.svg" alt="运行控制面" width="100%" />
      <br /><sub><b>Runtime Control</b><br/>预算、停止与权限</sub>
    </td>
    <td width="25%" align="center" valign="top">
      <img src="docs/images/product-scenes.svg" alt="业务场景" width="100%" />
      <br /><sub><b>Business Scenes</b><br/>五类高价值任务</sub>
    </td>
    <td width="25%" align="center" valign="top">
      <img src="docs/images/product-mobile.svg" alt="移动端任务控制面" width="76%" />
      <br /><sub><b>Mobile Control</b><br/>窄屏保持同一语义</sub>
    </td>
  </tr>
</table>

---

## 不是聊天机器人，而是一条可验证的业务决策链

真实电商任务很少只依赖一段文本。

一次商品治理可能同时涉及主图、详情页、功效声明、资质、品牌授权和历史治理；一次售后判责可能需要订单、物流、聊天记录、图片、录音与规则；一次商家审核则经常在主体、授权链、经营范围和风险记录之间不断补证。

EcomEvo 把这些材料放进一个**持续任务**。Runtime 自己决定下一步查什么、是否需要反证、什么时候补证、什么时候停止；Verifier 判断证据是否真的闭合；真实高影响动作始终留在确定性的权限链和人工确认之后。

<table>
  <tr>
    <td width="25%" valign="top"><b>自主查证</b><br/><br/>根据证据缺口与不确定性选择下一步只读工具，而不是要求用户手写固定流程。</td>
    <td width="25%" valign="top"><b>多模态证据</b><br/><br/>图片、视频、音频、PDF、Office、表格、日志和业务 API 结果进入同一验证链。</td>
    <td width="25%" valign="top"><b>持续学习</b><br/><br/>Routing、工具可靠性与长期 Skill 分开学习；经验可以改变认知策略，但不能改变权限。</td>
    <td width="25%" valign="top"><b>确定性权限</b><br/><br/>Verifier、Sandbox、RBAC、审批身份和 Human Confirmation 独立掌握真实业务动作。</td>
  </tr>
</table>

> **认知自治，权限确定。** 让系统学习“下一步查什么”，而不是学习“怎样获得更多权限”。

---

## 产品漫游

### 01 · 一个任务，一面完整证据墙

不是把附件当聊天上下文，而是把它们变成可定位、可核验、可补充、可排除的证据对象。Runtime 看到的是当前证据状态与缺口，而不是一段越来越长的历史 prompt。

<p align="center">
  <img src="docs/images/product-evidence-wall.svg" alt="EcomEvo 商品证据空间" width="94%" />
</p>

<p align="center"><sub><b>商品证据空间</b> · 主图、详情、资质、声明与企业证据进入同一验证链。</sub></p>

### 02 · 运行状态不是黑盒

用户不需要看到隐藏思维链，但必须知道：**现在还缺什么、为什么继续查、预算还剩多少、为什么停止、最终权限在哪里。**

<p align="center">
  <img src="docs/images/product-runtime-control.svg" alt="EcomEvo 运行质量与权限控制面" width="88%" />
</p>

<p align="center"><sub><b>运行质量与权限控制面</b> · Evidence completeness、stop reason、tool budget、routing state 与 authority boundary 都是结构化状态。</sub></p>

### 03 · 从商品治理到售后判责，使用同一套 Runtime

<p align="center">
  <img src="docs/images/product-scenes.svg" alt="EcomEvo 五类高价值业务场景" width="96%" />
</p>

| 场景 | Runtime 真正解决的问题 |
| --- | --- |
| **商品治理** | 主图、详情、声明、品牌与资质是否彼此一致；缺证时继续追证而不是猜测 |
| **商家审核** | 主体、授权链、经营范围、关联关系和风险记录能否形成闭环 |
| **售后判责** | 订单、物流、聊天、图片、录音和规则如何重建事实时间线 |
| **风险核查** | 弱线索、相关性、独立证据和反证如何分开，避免把可疑直接当事实 |
| **内容审核** | 图片、视频、音频、文案和结构化材料如何在同一验证过程里形成结论 |

### 04 · 窄屏不是删功能，而是重排任务控制面

<table>
  <tr>
    <td width="38%" align="center" valign="middle">
      <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务工作台" width="72%" />
    </td>
    <td width="62%" valign="middle">
      <h3>同一个任务语义，换一种空间组织</h3>
      <p>移动端仍然保留任务、资料、证据缺口、运行质量和待确认动作，只把控制面变成可开合抽屉。</p>
      <p>当前 Chromium E2E 会在 <code>390×844</code> 视口下验证抽屉、焦点、消息发送、Runtime Pulse 与 busy-state release。</p>
    </td>
  </tr>
</table>

---

## 为什么它不是普通 Tool Agent

| 常见 Agent 设计 | EcomEvo 的选择 |
| --- | --- |
| 模型直接决定下一工具 | **EvoGain-APR** 只在合法只读动作空间内学习路由 |
| 工具越多越容易无限调用 | Budget、并行上限、timeout、stagnation 与 contextual abstention 联合约束 |
| 用模型自评作为 reward | **Verifier Difference Credit** 用反事实边际证据贡献形成学习信号 |
| 失败后盲目重试 | Dynamic Task Graph + verification fingerprint 触发重规划、补证或停止 |
| 历史经验不断堆进 prompt | Bayesian Skill Library 用 posterior、shadow gate 与 retirement 管理长期经验 |
| 模型既判断又执行 | Verifier、Governance、RBAC 与 Human Approval 独立掌握真实业务权限 |
| 进程退出任务就丢 | Durable Job + immutable evidence snapshot + cross-process lease 恢复 |
| WebSocket 是唯一实时真相 | SQLite event log 保持跨 worker 顺序，queue 仅作为低延迟唤醒信号 |

---

## Runtime：从目标到业务动作

```text
Goal
 │
 ▼
Evidence / Belief State
 │
 ▼
Observe ──► Decide ──► Route ──► Read-only Act
  ▲                                  │
  │                                  ▼
  └──── Replan / Stop ◄── Verify ◄── Review
                             │
                             ▼
                  Counterfactual Credit
                             │
                             ▼
                     Posterior Update

════════════ Deterministic Authority Boundary ════════════

Verifier ──► BusinessAction Proposal ──► Approver ──► Executor
```

语言模型可以提出认知动作，但**不能决定动作是否合法**。真正可执行集合先经过 Registry、只读策略、预算和 Sandbox 的交集：

$$
\boxed{
\mathcal A_t^{\mathrm{safe}}
=
\hat{\mathcal A}_t
\cap \mathcal A^{\mathrm{registered}}
\cap \mathcal A^{\mathrm{read\text{-}only}}
\cap \mathcal A^{\mathrm{budget}}
\cap \mathcal A^{\mathrm{sandbox}}
}
$$

学习只发生在这个可行域内部。

---

# EvoGain-APR

## 在安全可行域内学习“下一步最值得做什么”

EcomEvo 的 routing 不是一组永久固定的手写权重。它把冷启动系数降级为 **prior**，随后用真实任务中的 verifier marginal contribution 持续更新 posterior。

整个学习目标可以概括为一个受约束的 sequential evidence acquisition 问题：

$$
\boxed{
\pi^{\star}
=
\arg\max_{\pi}
\;\mathbb E_{\pi}
\left[\sum_t \mathrm{Credit}_t\right]
\quad
\text{s.t.}\quad
 a_t\in\mathcal A_t^{\mathrm{safe}}
}
$$

安全定义可行域，学习只负责在可行域里提高证据效率。

### 1 · Hierarchical Bayesian Posterior

对每个候选只读工具构造上下文向量：evidence coverage、authority、skill support、novelty、counter-evidence value、specificity、tool reliability、cost pressure、redundancy、evidence gap 与 recovery context。

冷启动：

$$
w\sim\mathcal N\!\left(\mu_0,\Lambda_0^{-1}\right)
$$

每个并行 round 把真实 credit 批量写入 sufficient statistics：

$$
\begin{aligned}
A_t &= A_0+\delta(A_{t-1}-A_0)+\sum_{i=1}^{k}x_i x_i^{\top}\\[4pt]
b_t &= b_0+\delta(b_{t-1}-b_0)+\sum_{i=1}^{k}r_i x_i\\[4pt]
\mu_t &= A_t^{-1}b_t,\qquad
\sigma_t^2(x)=x^{\top}A_t^{-1}x
\end{aligned}
$$

因此 prior 只决定起跑方向；真实 outcome 足够多时，posterior 可以完全反转初始排序。

对业务域采用有界的 global → domain shrinkage：

$$
\tau_d=\frac{n_d}{n_d+\lambda},
\qquad
\tilde\mu_d=\tau_d\mu_d+(1-\tau_d)\mu_g
$$

新域可以借用全局经验，但不会一开始就拥有成熟域的完整激活权。

### 2 · Deterministic UCB

生产 routing 不依赖随机 Thompson sampling 来决定工具顺序：

$$
\boxed{
Q_t(x)=\mu_t^{\top}x+\beta_t\sqrt{x^{\top}A_t^{-1}x}
}
$$

第一项利用已经学到的 evidence value；第二项只给合法但高不确定候选有限探索空间。相同 state + posterior 会得到可复现排序。

### 3 · Contextual Abstention

系统不仅学习“哪个工具更好”，还学习“现在继续调用工具是否比停止更值得”。

$$
\boxed{
\mathrm{Adv}_t(a_i\mid s_t)
=
Q_t(x_i)-Q_t\!\left(x_{\varnothing}(s_t)\right)
}
$$

只有当：

$$
\mathrm{Adv}_t(a_i\mid s_t)>0
$$

候选才进入执行集合。停止边界和 posterior 使用同一标尺，不再依赖一个永久固定的 absolute utility cutoff。

### 4 · Verifier Difference Credit

模型不给自己打分。学习信号来自**拿掉某个工具结果之后，可验证状态到底下降了多少**。

先定义同时受 verifier quality 与 evidence completeness 限制的调和势能：

$$
\Phi(v)=
\begin{cases}
\dfrac{2q(v)c(v)}{q(v)+c(v)}, & q(v)+c(v)>0\\[6pt]
0, & \text{otherwise}
\end{cases}
$$

然后执行 deterministic leave-one-out：

$$
\boxed{
D_i
=
\Phi\!\left(V(R)\right)
-
\Phi\!\left(V(R\setminus\{r_i\})\right)
}
$$

并按工具成本归一化：

$$
\mathrm{Credit}_i=\frac{D_i}{1+\mathrm{Cost}_i}
$$

这让 routing posterior 学到的是**真实证据边际贡献**，而不是语言模型对自己行为的主观评价。

### 5 · Tool Reliability Posterior

“证据价值高”和“运行稳定”是两个变量。每个 domain × tool 单独维护可靠性 posterior：

$$
\boxed{
 p_{d,a}\sim\operatorname{Beta}(\alpha_{d,a},\beta_{d,a})
}
$$

成功/失败持续更新 reliability；它只是 routing feature，永远不会自动升级成业务证据。

> 完整定义、feature space、activation、non-stationary decay、复杂度与研究边界见 **[算法技术报告](docs/ALGORITHM.md)**。

---

## 三层学习，互不混淆

<table>
  <tr>
    <td width="33%" valign="top">
      <b>Routing Learning</b><br/><br/>
      学习当前状态下下一步查什么。<br/><br/>
      <code>context → posterior → UCB → tool set</code>
    </td>
    <td width="33%" valign="top">
      <b>Tool Reliability</b><br/><br/>
      学习数据源在不同业务域里是否稳定。<br/><br/>
      <code>success / failure → Beta posterior</code>
    </td>
    <td width="34%" valign="top">
      <b>Skill Evolution</b><br/><br/>
      学习哪些长期认知策略值得复用、晋升或退休。<br/><br/>
      <code>shadow → replay → QD archive → promote / retire</code>
    </td>
  </tr>
</table>

三层学习都不能修改 registered tool、required evidence、credential scope、hard budget、confirmation requirement 或审批身份。

---

## Durable Execution：任务不属于某个进程

```text
User Message
    │
    ▼
Atomic Message + Accepted Event + Durable Job
    │
    ▼
Immutable Input / Asset SHA Snapshot
    │
    ▼
Cross-process Job Lease
    │
    ▼
Autonomous Runtime
    │
    ▼
Atomic Assistant Message + Proposals + Terminal Event
```

worker 中断后，lease 到期可被其他 worker reclaim。运行中的任务不能悄悄加入新资料，因此一个 autonomous turn 始终面对封闭的 evidence snapshot。

WebSocket 的进程内 queue 只负责低延迟唤醒；SQLite `task_events` 才是跨 worker 的 durable ordering source。断线重连通过 `after_id` 增量续传，不需要重新回放整个任务历史。

---

## 真实业务动作永远在学习层之外

```text
Adaptive Cognition
        │
        ▼
Read-only Tools / Specialists
        │
        ▼
Verifier
════════════════════════════════════
Deterministic Authority Boundary
        │
        ▼
BusinessAction Proposal
        │
        ▼
Approver Identity + Human Confirmation
        │
        ▼
Business Executor
```

Agent 可以自主查证、并行工具、反证、重规划、停止和学习；但不能自行降低 required evidence、把模型回答当独立证据、修改 Sandbox / Verifier、注册生产 side-effect tool、批准退款/下架/冻结，或在 `uncertain` 状态下盲目重放副作用请求。

MCP / 企业工具的 timeout、连接中断、HTTP 5xx/408、损坏响应、内部错误和无法确认的执行结果会进入 `uncertain`，而不是被包装成“明确失败，因此可以安全重试”。

---

## Tenant · RBAC · Approval Audit

生产身份边界支持 trusted proxy / gateway 在服务端签入 tenant、user 与 role：

```text
viewer  <  operator  <  approver  <  admin
```

- tenant-scoped conversation / asset / action 不泄漏其他租户资源存在性；
- action decision 需要 `approver`；
- runtime / evolution 全局控制面需要 `admin`；
- 审批 CAS 自动记录 actor tenant / user / role / auth mode；
- 浏览器永远不持有服务端 HMAC secret。

具体企业 IdP / SSO 属于部署环境集成，不在仓库里伪造“已完成”。

---

## 已经被自动化验证的部分

当前 CI 把 correctness、pressure 和真实浏览器交互拆成三条独立 release gate。

### Regression Gate

完整 `pytest -q`、9-case Gold Set × fresh/persisted replay、malicious-controller authority、durable job/crash reclaim、WebSocket ordering、tenant/RBAC/approval audit、MCP uncertain/no-blind-replay，以及所有生产 JS / HTML / DOM / CSS 基础结构检查。

### Current-head Pressure Gate

最终 head 的 GitHub-hosted Ubuntu runner 会自动跑 `1 / 8 / 32 / 64 / 120 / 240` 并发任务。当前记录：

| 并发任务 | Throughput | p50 | p95 | p99 | Safety failures |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 24.633 tasks/s | 0.0405 s | 0.0405 s | 0.0405 s | 0 |
| 64 | 38.283 tasks/s | 1.2719 s | 1.3861 s | 1.3934 s | 0 |
| 120 | 38.947 tasks/s | 2.3324 s | 2.6088 s | 2.6275 s | 0 |
| 240 | **39.100 tasks/s** | **4.6430 s** | **5.1802 s** | **5.2152 s** | **0** |

这些数字是 hosted runner 上的**本地 Runtime / SQLite / policy 路径测量**，不包含真实外部认知引擎、企业 MCP 网络和复杂媒体解析，因此不是生产业务 QPS。

### Real Chromium E2E

Playwright 真正启动 Uvicorn + Chromium，覆盖：首屏、场景切换、durable message → assistant result、Runtime Pulse、命令面板、键盘焦点、390×844 移动端抽屉、双标签页同一任务实时同步、busy-state release，以及 page/console error。

完整证据见 **[验证报告](docs/VERIFICATION_REPORT.md)**。

---

## Quick Start

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

认知引擎是可替换组件，不拥有业务执行权：

```bash
OPEN_MODEL_BASE_URL=http://your-runtime/compatible-endpoint
OPEN_MODEL_API_KEY=your-key
OPEN_MODEL_MODEL=your-current-model
OPEN_MODEL_MULTIMODAL=0
```

底层模型升级不要求修改 EvoGain-APR、Verifier、Sandbox 或 BusinessAction 权限链。

---

## 文档地图

| 文档 | 面向 | 重点 |
| --- | --- | --- |
| **[产品手册](docs/PRODUCT_MANUAL.md)** | 运营 / 审核 / 客服 / 风控 | 发起任务、看证据、补证、确认动作 |
| **[算法技术报告](docs/ALGORITHM.md)** | Agent / ML / Runtime | EvoGain-APR、posterior、credit、复杂度 |
| **[技术手册](docs/TECHNICAL_MANUAL.md)** | 平台 / 后端 / 安全 | API、durable job、MCP、RBAC、恢复与部署 |
| **[性能手册](docs/PERFORMANCE.md)** | 性能 / 架构 | 压测口径、SQLite、routing-store 与 release gate |
| **[架构](docs/ARCHITECTURE.md)** | 系统设计 | 组件分层、数据流、学习平面与权限边界 |
| **[设计系统](docs/DESIGN.md)** | 前端 / 产品设计 | 中文排版、动效、响应式和任务工作台规则 |
| **[验证报告](docs/VERIFICATION_REPORT.md)** | Reviewer / 发布负责人 | CI、Gold Set、压力、Chromium 与真实边界 |

---

## 真实边界

仓库内可自动化的核心门禁已经闭环，但以下内容必须在真实部署环境继续验证：

- 企业 IdP / SSO / Gateway 的真实接入；
- 真实 provider / MCP 凭证、区域、rate limit、schema drift 与下游幂等；
- Safari / Edge 实机验证；
- 大型或畸形 PDF、Office、图片、视频与音频业务语料；
- 超过 SQLite WAL 单 writer 边界后的生产多节点数据库 / 队列拓扑；
- 由真实业务团队持续扩展并裁决的 Gold Set。

智能与产品能力的比较，也必须在**相同任务、相同模型、相同工具、相同 token / cost budget 和一致评估标准**下进行。

---

<div align="center">

## EcomEvo

### 目标交给 Runtime。证据交给 Verifier。权限留给业务。

**Adaptive cognition. Deterministic authority.**

<sub>Build agents that can learn what to do next — without learning how to bypass control.</sub>

</div>
