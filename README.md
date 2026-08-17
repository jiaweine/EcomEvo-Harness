<p align="center"><sub>ADAPTIVE AUTONOMOUS COMMERCE RUNTIME</sub></p>

<h1 align="center">EcomEvo</h1>

<p align="center"><strong>让复杂电商任务持续查证、自主推进、可恢复、会学习——但永远不越权。</strong></p>

<p align="center"><b>目标交给 Runtime · 证据交给 Verifier · 权限留给业务</b></p>

<p align="center">
  <img alt="CI" src="https://github.com/jiaweine/EcomEvo-Harness/actions/workflows/ci.yml/badge.svg?branch=agent%2Fautonomous-self-evolving-runtime" />
  <img alt="Adaptive Routing" src="https://img.shields.io/badge/Routing-Adaptive%20Posterior-C76535" />
  <img alt="Multimodal Evidence" src="https://img.shields.io/badge/Evidence-Multimodal-BF7B32" />
  <img alt="Deterministic Authority" src="https://img.shields.io/badge/Authority-Deterministic-3F765E" />
</p>

<p align="center">
  <b><a href="docs/PRODUCT_MANUAL.md">产品手册</a> · <a href="docs/ALGORITHM.md">算法报告</a> · <a href="docs/TECHNICAL_MANUAL.md">技术手册</a> · <a href="docs/PERFORMANCE.md">性能</a> · <a href="docs/VERIFICATION_REPORT.md">验证</a></b>
</p>

<p align="center"><em><b>Give the Agent a goal, not a script.</b></em></p>

<br />

<p align="center">
  <img src="docs/images/product-workbench.svg" alt="EcomEvo 商业决策工作台" width="100%" />
</p>

<p align="center">
  <sub><b>EcomEvo 商业决策工作台</b> · 目标、对话、多模态证据、运行状态、验证结果与待确认动作，在一个持续任务里完成闭环。</sub>
</p>

<br />

## 一句话理解 EcomEvo

普通 Agent 往往在回答问题。

EcomEvo 运行的是一条**可验证的业务决策链**：它围绕目标持续观察证据，判断下一步还需要查什么，选择合法的只读工具，检查反证，重新规划，直到证据闭合或明确停止；如果最终需要退款、下架、冻结、审核通过等真实业务动作，权限仍然留在确定性的审批链里。

这意味着系统可以不断提高“**下一步查什么**”的能力，却不能学习“**怎样获得更多权限**”。

> **认知自治，权限确定。**
>
> 学习发生在安全可行域内部；Verifier、Sandbox、RBAC 与 Human Approval 永远位于学习层之外。

<br />

<table>
  <tr>
    <td width="25%" valign="top">
      <b>Evidence-native</b><br/><br/>
      图片、视频、音频、PDF、Office、表格、日志和业务 API 结果进入同一证据状态，而不是只被塞进一段长 prompt。
    </td>
    <td width="25%" valign="top">
      <b>Adaptive cognition</b><br/><br/>
      Routing 从冷启动 prior 出发，用真实 verifier outcome 更新 posterior，逐渐学会不同任务里什么更值得查。
    </td>
    <td width="25%" valign="top">
      <b>Durable runtime</b><br/><br/>
      任务、输入快照、事件和恢复租约持久化；进程中断不等于任务丢失，多 worker 仍能按 durable event 顺序恢复。
    </td>
    <td width="25%" valign="top">
      <b>Deterministic authority</b><br/><br/>
      模型可以提出认知动作，但真实副作用必须经过 Registry、Verifier、Governance、RBAC 和人工确认。
    </td>
  </tr>
</table>

<br />

## 产品 · Product

### 一面工作台，看完整任务，而不是只看一段对话

EcomEvo 把业务任务组织成四个同时存在的对象：**目标、证据、运行状态、权限状态**。

用户不需要查看隐藏思维链，但应该随时知道：当前证据是否闭合、还缺什么、系统为什么继续查、预算还剩多少、为什么停止，以及最后一步究竟由谁批准。

<br />

<table>
  <tr>
    <td width="50%" align="center" valign="top">
      <img src="docs/images/product-evidence-wall.svg" alt="EcomEvo 多模态证据空间" width="100%" />
      <br/><br/>
      <sub><b>Evidence Space</b> · 资料不是附件列表，而是可定位、可核验、可补充、可排除的证据对象。</sub>
    </td>
    <td width="50%" align="center" valign="top">
      <img src="docs/images/product-runtime-control.svg" alt="EcomEvo 运行质量与权限控制面" width="100%" />
      <br/><br/>
      <sub><b>Runtime Control</b> · Evidence completeness、缺口、预算、stop reason、routing state 与 authority boundary 都是结构化状态。</sub>
    </td>
  </tr>
</table>

<br />

### 同一套 Runtime，跨越不同高价值业务场景

<p align="center">
  <img src="docs/images/product-scenes.svg" alt="EcomEvo 五类高价值业务场景" width="96%" />
</p>

<p align="center">
  <sub><b>Business Scenes</b> · 场景不同，但底层都是“目标 → 证据 → 补证 → 验证 → 权限”的同一条运行链。</sub>
</p>

| 场景 | EcomEvo 真正解决的问题 |
| --- | --- |
| **商品治理** | 主图、详情、声明、品牌和资质是否彼此一致；缺证时继续追证，而不是猜测 |
| **商家审核** | 主体、授权链、经营范围、关联关系与风险记录能否形成证据闭环 |
| **售后判责** | 订单、物流、聊天、图片、录音与规则怎样重建事实时间线 |
| **风险核查** | 弱线索、相关性、独立证据与反证怎样分开，避免把“可疑”直接升级成“事实” |
| **内容审核** | 图片、视频、音频、文案与结构化材料怎样进入同一验证过程 |

<br />

<table>
  <tr>
    <td width="64%" valign="middle">
      <h3>桌面端是工作台，移动端仍然是同一个任务</h3>
      <p>窄屏不是把关键控制能力删除，而是重新组织空间。资料、证据缺口、运行质量与待确认动作仍然存在，只把控制面收进可开合抽屉。</p>
      <p>任务语义不会因为视口变化而改变；同一 conversation 在多标签页里也通过 durable event log 保持一致。</p>
    </td>
    <td width="36%" align="center" valign="middle">
      <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务控制面" width="72%" />
      <br/><sub><b>Mobile Control</b> · 390×844 Chromium E2E 持续回归。</sub>
    </td>
  </tr>
</table>

<br />

### 一个任务如何向前推进

<table>
  <tr>
    <td width="25%" valign="top"><b>01 · 给目标</b><br/><br/>用户描述希望系统完成什么，不需要提前写死工具顺序。</td>
    <td width="25%" valign="top"><b>02 · 建证据状态</b><br/><br/>多模态材料、企业数据与工具结果进入统一 evidence / belief state。</td>
    <td width="25%" valign="top"><b>03 · 自主补证</b><br/><br/>Runtime 选择合法只读工具、检查反证、更新 posterior、重规划或停止。</td>
    <td width="25%" valign="top"><b>04 · 验证与权限</b><br/><br/>Verifier 决定证据是否闭合；高影响动作只形成 proposal，交给有权限的人确认。</td>
  </tr>
</table>

<br />

## Runtime · 从目标到业务动作

```mermaid
flowchart TD
    G[Goal] --> E[Evidence / Belief State]
    E --> D[Observe · Decide · Route]
    D --> T[Read-only Tools / Specialists]
    T --> R[Review]
    R --> V[Verifier]
    V -->|Evidence gap| D
    V -->|Verified| P[BusinessAction Proposal]
    V --> C[Counterfactual Credit]
    C --> U[Posterior Update]
    U --> D
    P --> A[Approver Identity + Human Confirmation]
    A --> X[Business Executor]
```

这里最重要的不是“模型更聪明”，而是**认知动作空间与业务权限空间从架构上分开**。

模型提出的候选动作首先必须落入安全可行域：

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

学习只能在 $\mathcal A_t^{\mathrm{safe}}$ 内优化。

<br />

## EvoGain-APR · Algorithm

### Adaptive Posterior Routing for verified evidence acquisition

EcomEvo 的 routing 不是永久固定的一组手写权重，也不是把所有工具选择全部交给语言模型。

它把冷启动规则降级成 **prior**，然后用真实任务里“这个工具到底让可验证状态提高了多少”作为学习信号，持续更新 contextual posterior。

目标可以写成一个受约束的 sequential evidence acquisition 问题：

$$
\boxed{
\pi^{\star}
=
\arg\max_{\pi}
\;\mathbb E_{\pi}
\left[\sum_t \mathrm{Credit}_t\right]
\qquad
\text{s.t.}\quad
 a_t\in\mathcal A_t^{\mathrm{safe}}
}
$$

**Safety 决定可行域；Learning 只决定可行域里下一步什么更值得做。**

<br />

### 1 · Hierarchical Bayesian Posterior

对每个合法候选工具构造上下文向量 $x$，包含 evidence coverage、authority、skill support、novelty、counter-evidence value、specificity、tool reliability、cost pressure、redundancy、evidence gap 与 recovery context。

冷启动：

$$
w\sim\mathcal N\!\left(\mu_0,\Lambda_0^{-1}\right)
$$

真实 outcome 以一个 parallel round 为单位更新 sufficient statistics：

$$
\begin{aligned}
A_t
&=A_0+\delta\left(A_{t-1}-A_0\right)
+\sum_{i=1}^{k}x_i x_i^{\top},\\[4pt]
b_t
&=b_0+\delta\left(b_{t-1}-b_0\right)
+\sum_{i=1}^{k}r_i x_i,\\[4pt]
\mu_t
&=A_t^{-1}b_t,\\[4pt]
\sigma_t^2(x)
&=x^{\top}A_t^{-1}x.
\end{aligned}
$$

因此 prior 只决定起跑方向；随着真实 outcome 积累，posterior 可以覆盖甚至反转冷启动排序。

不同业务域之间采用有界 global → domain transfer：

$$
\tau_d=\frac{n_d}{n_d+\lambda},
\qquad
\tilde\mu_d
=\tau_d\mu_d+(1-\tau_d)\mu_g.
$$

新域可以借经验，但不会在第一天就继承成熟域的全部决策权重。

<br />

### 2 · Deterministic UCB

生产 routing 需要探索，但也需要可复现。

EcomEvo 使用确定性的 UCB-style score：

$$
\boxed{
Q_t(x)
=\mu_t^{\top}x
+\beta_t\sqrt{x^{\top}A_t^{-1}x}
}
$$

其中：

- $\mu_t^{\top}x$ 是已经学到的期望证据价值；
- $\sqrt{x^{\top}A_t^{-1}x}$ 是 epistemic uncertainty；
- $\beta_t$ 控制在合法动作空间里的有限探索。

相同 state、posterior 与 exploration 参数会得到相同排序，便于回放、审计与 CI。

<br />

### 3 · Contextual Abstention

真正高效的 Agent 不只会选择工具，也会知道**什么时候不应该再调用工具**。

EcomEvo 把候选工具与同一状态下的 no-op baseline 比较：

$$
\boxed{
\mathrm{Adv}_t(a_i\mid s_t)
=
Q_t(x_i)
-
Q_t\!\left(x_{\varnothing}(s_t)\right)
}
$$

只有当：

$$
\mathrm{Adv}_t(a_i\mid s_t)>0
$$

候选才值得进入执行集合。

因此“继续查还是停止”与“哪个工具更好”使用同一套 posterior 标尺，而不是依赖一个永久固定的 absolute utility threshold。

<br />

### 4 · Verifier Difference Credit

语言模型不给自己打 reward。

系统真正关心的是：**拿掉某个工具结果之后，可验证状态到底下降了多少。**

先把 verifier quality $q(v)$ 与 evidence completeness $c(v)$ 合成一个调和势能：

$$
\Phi(v)=
\begin{cases}
\dfrac{2q(v)c(v)}{q(v)+c(v)}, & q(v)+c(v)>0,\\[8pt]
0, & \text{otherwise}.
\end{cases}
$$

对第 $i$ 个工具结果执行 deterministic leave-one-out：

$$
\boxed{
D_i
=
\Phi\!\left(V(R)\right)
-
\Phi\!\left(V(R\setminus\{r_i\})\right)
}
$$

再按执行成本归一化：

$$
\boxed{
\mathrm{Credit}_i
=
\frac{D_i}{1+\mathrm{Cost}_i}
}
$$

于是 posterior 学到的是**真实证据边际贡献**，不是模型对自身行为的主观评价。

<br />

### 5 · Tool Reliability Posterior

“这个数据源很有价值”和“这个数据源运行稳定”不是一回事。

因此每个 domain × tool 独立维护可靠性 posterior：

$$
\boxed{
 p_{d,a}
\sim
\operatorname{Beta}(\alpha_{d,a},\beta_{d,a})
}
$$

成功与失败更新 reliability；reliability 可以影响 routing，但**绝不会自动升级成业务证据**。

> 更完整的 feature space、activation、non-stationary decay、complexity、counterfactual credit 与研究边界见 **[算法技术报告](docs/ALGORITHM.md)**。

<br />

## 三层学习，三种职责

<table>
  <tr>
    <td width="33%" valign="top">
      <b>Routing Learning</b><br/><br/>
      当前状态下，下一步查什么。<br/><br/>
      <code>context → posterior → UCB → tool set</code>
    </td>
    <td width="33%" valign="top">
      <b>Tool Reliability</b><br/><br/>
      不同业务域里，数据源是否稳定。<br/><br/>
      <code>success / failure → Beta posterior</code>
    </td>
    <td width="34%" valign="top">
      <b>Skill Evolution</b><br/><br/>
      哪些长期认知策略值得复用、晋升或退休。<br/><br/>
      <code>shadow → replay → QD archive → promote / retire</code>
    </td>
  </tr>
</table>

三层学习都不能修改 registered tools、required evidence、credential scope、hard budget、confirmation requirement 或 approver identity。

<br />

## Durable by design

一次 autonomous turn 面对的是**不可变的输入与 evidence snapshot**。

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

- worker 中断后，lease 到期可由其他 worker reclaim；
- active turn 期间不能悄悄加入新资料；
- SQLite `task_events` 是跨 worker 的 durable ordering source；
- process-local queue 只负责低延迟 wake-up；
- WebSocket 通过 `after_id` 增量续传，而不是重放整段历史。

<br />

## Authority by design

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

Agent 可以自主查证、并行工具、检查反证、重规划、停止和学习；但不能自行降低 required evidence、把模型回答当独立证据、修改 Sandbox / Verifier、注册生产 side-effect tool，或批准退款、下架、冻结等高影响动作。

对无法确认副作用是否已经发生的 MCP / enterprise-tool 异常——例如 timeout、连接中断、HTTP 5xx/408、损坏响应、内部错误——系统进入 `uncertain`，而不是把它包装成“明确失败，因此可以安全重试”。

<br />

## Tenant · RBAC · Approval Audit

```text
viewer  <  operator  <  approver  <  admin
```

- tenant-scoped conversation / asset / action 不暴露其他租户资源存在性；
- action decision 要求 `approver`；
- runtime / evolution 全局控制面要求 `admin`；
- approval CAS 自动持久化 actor tenant / user / role / auth mode；
- hardened 模式通过 trusted proxy / gateway 在服务端签入身份；
- 浏览器永远不持有服务端 HMAC secret。

具体企业 IdP / SSO 属于真实部署环境集成，而不是仓库里伪造的“已完成”。

<br />

## 被自动化证明的部分

README 不应该承担完整测试报告，所以这里只保留发布门禁本身；详细数据统一放在 **[验证报告](docs/VERIFICATION_REPORT.md)** 与 **[性能手册](docs/PERFORMANCE.md)**。

<table>
  <tr>
    <td width="33%" valign="top">
      <b>Regression Gate</b><br/><br/>
      Full pytest、fresh/persisted Gold Set、malicious-controller authority、durable recovery、WebSocket ordering、tenant/RBAC、MCP uncertainty 与生产前端静态检查。
    </td>
    <td width="33%" valign="top">
      <b>Pressure Gate</b><br/><br/>
      Current-head <code>1 / 8 / 32 / 64 / 120 / 240</code> 并发，硬检查 session、event chain、budget、stop reason 与 side-effect safety invariant。
    </td>
    <td width="34%" valign="top">
      <b>Real Chromium E2E</b><br/><br/>
      Uvicorn + Playwright Chromium，覆盖发送/收取结果、Runtime Pulse、键盘、移动端抽屉、双标签页同步与 console/page errors。
    </td>
  </tr>
</table>

<br />

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

<br />

## Documentation

| 文档 | 你会在这里找到什么 |
| --- | --- |
| **[产品手册](docs/PRODUCT_MANUAL.md)** | 怎样发起任务、看证据、补证、理解运行状态与确认动作 |
| **[算法技术报告](docs/ALGORITHM.md)** | EvoGain-APR、posterior、UCB、difference credit、复杂度与研究边界 |
| **[技术手册](docs/TECHNICAL_MANUAL.md)** | API、durable job、MCP、RBAC、恢复、身份与部署 |
| **[性能手册](docs/PERFORMANCE.md)** | 压测口径、SQLite 边界、routing-store、current-head performance gate |
| **[架构](docs/ARCHITECTURE.md)** | 系统组件、数据流、学习平面与 deterministic authority boundary |
| **[设计系统](docs/DESIGN.md)** | 中文排版、动效、响应式、任务工作台与交互原则 |
| **[验证报告](docs/VERIFICATION_REPORT.md)** | CI、Gold Set、压力测试、Chromium E2E 与真实部署边界 |

<br />

## 真实边界

仓库内能自动化的核心门禁已经闭环，但下面这些事情必须在真实环境继续验证：

- 企业 IdP / SSO / Gateway 的实际接入；
- 真实 provider / MCP 凭证、区域、rate limit、schema drift 与下游幂等；
- Safari / Microsoft Edge 实机；
- 大型或畸形 PDF、Office、图片、视频与音频业务语料；
- 超过 SQLite WAL 单 writer 边界后的生产多节点数据库 / queue topology；
- 由真实业务团队持续扩展和裁决的 Gold Set。

<br />

<h2 align="center">EcomEvo</h2>

<p align="center"><b>目标交给 Runtime。证据交给 Verifier。权限留给业务。</b></p>

<p align="center"><b>Adaptive cognition. Deterministic authority.</b></p>

<p align="center"><sub>Build agents that can learn what to do next — without learning how to bypass control.</sub></p>
