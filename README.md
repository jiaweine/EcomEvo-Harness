<div align=center>

# EcomEvo

### Evidence-Driven Commerce Decision Workbench

**不是把模型接进聊天框，而是把电商业务目标、资料、证据、判断和动作组织成一条可复核、可确认、可恢复的持续任务。**

`STATEFUL TASK` · `MULTIMODAL EVIDENCE` · `HUMAN GATED ACTION` · `AUDITABLE` · `RECOVERABLE`

商品、商家、订单、内容与多模态资料进入同一个任务空间；系统持续形成证据和判断，高影响业务动作始终经过明确确认并保留执行结果。

</div>

![EcomEvo 商业决策工作台](docs/images/product-workbench.svg)

---

## Product Thesis · 从问 AI 变成完成一次商业判断

**目标 → 资料 → 证据 → 判断 → 人工确认 → 业务执行 → 审计结果**

| CONTROL | TRUST | CONTEXT | EXECUTION |
|---|---|---|---|
| **权限可控**：退款、下架、审核、风险升级等高影响动作进入明确确认边界 | **结论可核验**：关键判断回到资料、结构化数据、工具结果与可验证状态 | **任务可持续**：消息、资料、证据、进度和动作属于同一个持续任务 | **执行可区分**：本地演示、真实执行、结果不确定和明确失败保持不同状态 |

EcomEvo 把模型能力与商业决策工作台能力分开。模型可以替换；**任务状态、证据范围、执行权限、审计结果和恢复路径**属于产品 Runtime。

它处理的不是一次回答，而是一次完整商业判断。

---

# System Architecture · Evidence Runtime × Controlled Action

EcomEvo 的主分支围绕持续任务组织产品状态。Web Workbench 负责交互，FastAPI 负责产品 API，Conversation Store 持久化任务轨迹，Provider Registry 提供模型能力，Product Analyzer 与 Runtime 负责证据组织和判断，MCP 负责可选的真实业务执行。

```mermaid
flowchart TB
    U[Operator Goal] --> W[Web Workbench]
    W --> API[FastAPI Product API]

    API --> CONV[Conversation Store]
    API --> PROV[Provider Registry]
    API --> ANALYZE[Product Analyzer]
    API --> MCP[MCP Registry]

    CONV --> SNAP[Task Context]
    SNAP --> ANALYZE
    PROV --> ANALYZE
    ANALYZE --> RT[EcomEvo Runtime]

    RT --> EVID[Evidence + Decision]
    EVID --> CONV
    EVID --> ACTION[Business Action Proposal]

    ACTION --> GATE[Human Confirmation]
    GATE --> SIM[Simulation]
    GATE --> REAL[MCP Execution]

    SIM --> AUDIT[Audit Trail]
    REAL --> AUDIT
    AUDIT --> CONV
    CONV --> W
```

### 01 · 产品边界

| Surface | Main 行为 | 边界 |
|---|---|---|
| Task state | 持久化 conversation、历史消息、资料、事件与动作 | 对话是交互方式，不是唯一状态 |
| Evidence | 图片、视频、音频、PDF、Office、表格、JSON、日志与文本进入同一任务 | 关键结论需要能回到可检查来源 |
| Asset lifecycle | 资料可参与后续分析、排除、重新启用 | 已进入历史证据链的资料不能直接物理删除 |
| Provider | 支持本地演示与可配置外部 Provider | 多模态能力取决于实际 Provider 能力 |
| Action | proposed、approved、simulated、executed、uncertain、failed 分开表达 | 本地演示不能冒充真实业务执行 |
| Authority | 高影响动作必须经过人工确认 | 模型能力不会自动扩大业务权限 |
| Recovery | 任务租约、WebSocket 恢复、失败状态和审计事件持续保留 | 资料范围变更不能与分析快照形成竞态 |
| Adaptive Runtime | 独立开发线持续验证 | Draft PR #3 不作为当前 main 已上线能力 |

### 02 · 单次任务

```mermaid
sequenceDiagram
    participant U as Operator
    participant W as Workbench
    participant A as FastAPI
    participant S as Conversation Store
    participant P as Product Analyzer
    participant R as Runtime
    participant M as MCP

    U->>W: 提交目标与资料
    W->>A: message + asset scope
    A->>S: claim task lease
    S-->>A: stable task snapshot
    A->>P: goal + evidence + history
    P->>R: analyze and verify
    R-->>P: evidence + decision + action proposals
    P-->>A: result
    A->>S: persist message + evidence + actions
    A-->>W: answer ready
    U->>W: confirm high impact action
    W->>A: approve
    alt MCP 已连接
        A->>M: execute business tool
        M-->>A: confirmed result
        A->>S: executed
    else 本地演示
        A->>S: simulated
    end
```

### 03 · 资料生命周期

```mermaid
flowchart LR
    UPLOAD[Uploaded] --> ACTIVE[Active Evidence]
    ACTIVE --> EXCLUDE[Excluded from Future Analysis]
    EXCLUDE --> ACTIVE

    ACTIVE --> REF{Historical Reference}
    EXCLUDE --> REF

    REF -->|No| DELETE[Permanent Delete]
    REF -->|Yes| KEEP[Keep Audit History]
```

资料排除与资料删除是两个不同动作。排除只改变未来分析范围；永久删除只有在资料从未进入历史消息、证据或业务动作时才允许。

---

## 工作台 · Real Product Surface

下面的产品图对应当前仓库中的实际工作台设计资产，围绕场景、证据、状态和执行边界组织，而不是围绕模型配置组织。

| **01 · 五类业务场景** | **02 · 多模态证据空间** |
|---|---|
| 商品治理、商家审核、售后判责、风险核查、内容审核从业务目标进入。<br><br>![EcomEvo 五类业务场景](docs/images/product-scenes.svg) | 图片、视频、音频、文档、表格与日志进入同一个持续任务。<br><br>![EcomEvo 多模态证据空间](docs/images/product-evidence-wall.svg) |

| **03 · 状态与权限控制** | **04 · 商业决策工作台** |
|---|---|
| 进度、关键依据、待确认动作与资料范围保持分层，执行结果不混淆。<br><br>![EcomEvo 任务状态与权限控制](docs/images/product-runtime-control.svg) | 目标、对话、资料、证据、判断和动作集中在同一个任务空间。<br><br>![EcomEvo 商业决策工作台](docs/images/product-workbench.svg) |

---

# Product Method · Evidence Before Action

### 01 · Stateful Task

一个任务同时拥有目标、消息、资料、证据、处理进度、结论、动作和事件。用户可以持续补资料、追问和修改要求，不需要反复重建背景。

### 02 · Multimodal Evidence

当前产品可以接收图片、视频、音频、PDF、Word、Excel、CSV、JSON、日志和文本。外部 Provider 决定视觉、音频与扫描件理解能力；本地演示能力用于文本与结构化流程体验。

### 03 · Evidence Scope

资料不是普通附件。每份资料都拥有明确生命周期：

- **Active**：参与后续分析
- **Excluded**：保留历史，但不再进入未来分析
- **Deleted**：仅未进入历史证据链时允许永久删除

这样可以同时满足任务清理、未来分析范围和历史审计三个目标。

### 04 · Decision State

系统输出不只包含结论，还应该持续表达：

- 已确认事实
- 关键依据
- 证据缺口
- 冲突信息
- 后续建议
- 待确认业务动作

判断需要足够依据，权限需要独立确认。

### 05 · Action Truthfulness

```mermaid
stateDiagram-v2
    [*] --> proposed
    proposed --> rejected
    proposed --> approved
    approved --> simulated: local demo
    approved --> executed: confirmed MCP
    approved --> uncertain: transport interrupted
    approved --> failed: downstream failure
```

`simulated` 只代表本地演示链路完成，不代表真实商品、商家、订单或风险状态已经改变。

`executed` 只用于已确认的真实下游执行结果。

---

## Business Scenarios · Five Commerce Surfaces

| 场景 | 输入 | 主要判断 |
|---|---|---|
| **商品治理** | 标题、主图、详情、声明、品牌、资质 | 一致性、缺证据、高风险项、补件与下架建议 |
| **商家审核** | 主体、营业执照、授权链、经营范围、历史风险 | 通过、补件、拒绝与风险升级建议 |
| **售后判责** | 订单、物流、聊天、图片、视频、平台规则 | 时间线还原、责任判断、退款与后续处理建议 |
| **风险核查** | 交易、账户、商品、履约与异常信号 | 强证据、弱线索、反证与人工复核优先级 |
| **内容审核** | 图片、视频、文案与商品事实 | 一致性、误导风险、违规点与证据缺口 |

---

# Capability Boundary · Main Branch

README 只描述当前主分支能够验证的能力，不把研究线能力提前写成已上线功能。

| Capability | Status | 说明 |
|---|---|---|
| 持续业务任务 | **READY** | 历史任务、任务切换、消息、分享与状态持久化 |
| 五类业务场景 | **READY** | 商品、商家、售后、风险、内容 |
| 文本与结构化资料本地演示 | **READY** | 无外部模型也可以体验核心任务流程 |
| 图片与音视频理解 | **CONFIG** | 需要支持对应模态的 Provider |
| 证据面板与资料生命周期 | **READY** | 资料范围、排除、恢复与审计安全删除 |
| 高影响业务动作 | **GUARDED** | 必须经过明确人工确认 |
| 真实业务执行 | **CONFIG** | 需要可用 MCP 绑定与业务系统 |
| Adaptive Autonomous Runtime | **EXPERIMENTAL** | 独立 Draft PR #3 持续验证 |

---

# Quick Start · Local Runtime

### 01 · macOS / Linux

```bash
git clone https://github.com/jiaweine/EcomEvo-Harness.git
cd EcomEvo-Harness

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

### 02 · Windows PowerShell

```powershell
git clone https://github.com/jiaweine/EcomEvo-Harness.git
cd EcomEvo-Harness

python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .

Copy-Item .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

### 03 · Docker

```bash
docker build -t ecomevo .
docker run --rm \
  -p 8000:8000 \
  --env-file .env \
  -v ecomevo-data:/app/outputs \
  ecomevo
```

### 04 · Provider Capability

复制 `.env.example` 后按实际环境配置 Provider。没有配置外部模型时，产品仍可使用本地演示能力完成文本与结构化资料流程。

| Input | Requirement |
|---|---|
| Text / CSV / JSON / Log | 本地演示或文本 Provider |
| Image | 支持视觉输入的 Provider |
| Audio | 支持音频输入的 Provider 或业务侧预处理 |
| Video | 支持视觉理解的 Provider，关键帧由本地媒体流程提取 |
| Scanned PDF | OCR 或视觉能力 |
| Office / PDF / Table | 文档解析能力与必要的外部 Provider |

---

# Verification · Release Gate

仓库包含 GitHub Actions CI。每次 Pull Request 会执行完整工程门禁，而不是只做静态展示检查。

| Gate | Command / Scope |
|---|---|
| Python environment | Python 3.11 + editable install |
| System media dependency | ffmpeg |
| Python regression | `pytest -q` |
| Frontend syntax | `node --check frontend/app.js` |
| Product layer syntax | `node --check frontend/intro.js` |
| Product smoke | `python scripts/e2e_smoke.py` |
| Packaging | setuptools 显式限制 Python package discovery |

资料生命周期、动作状态真实性、审计引用和任务租约并发边界都已经进入回归覆盖。

---

# Production Boundary · Before Real Commerce

开源主分支提供可验证的产品与代码路径，但真实商业系统部署仍然需要结合企业环境完成安全与治理门禁。

| Area | Production requirement |
|---|---|
| Identity | 企业 SSO、用户身份、角色与操作人追踪 |
| Authorization | 高影响动作 RBAC、审批策略与动作白名单 |
| Provider | 凭证管理、超时、限流、成本与能力检测 |
| MCP | 真实业务系统绑定、幂等、重试与不确定结果核对 |
| Data | 保留策略、脱敏、敏感资料治理与审计要求 |
| Deployment | 多实例一致性、数据库与队列策略、备份与恢复 |
| Browser | Safari、Edge、移动设备与企业浏览器验证 |
| Security | 异常文件、恶意输入、大文件与上传边界验证 |

EcomEvo 的产品原则保持简单：**认知过程可以自动推进，真实业务权限必须明确授予。**

---

# Documentation

| 文档 | 内容 |
|---|---|
| **[ARCHITECTURE](docs/ARCHITECTURE.md)** | 当前主分支架构与组件边界 |
| **[DESIGN](docs/DESIGN.md)** | UI、响应式、中文排版与产品交互原则 |
| **[DEPLOYMENT](docs/DEPLOYMENT.md)** | 部署说明与环境要求 |
| **[VERIFICATION REPORT](docs/VERIFICATION_REPORT.md)** | 已有验证记录 |
| **[Adaptive Runtime · PR #3](https://github.com/jiaweine/EcomEvo-Harness/pull/3)** | 下一阶段自主 Runtime 与安全执行研究线 |

---

# Roadmap

当前产品方向按主分支价值排序：

1. 用真实浏览器产品截图替换部分示意设计资产
2. 提供可重复运行的首任务样例数据包
3. 完善 Provider 模态能力检测与首次配置体验
4. 增强证据来源定位、引用、时间线与冲突展示
5. 增强身份、租户、RBAC 与审批审计
6. 增加服务端任务搜索、分页与完整浏览器 E2E
7. Adaptive Runtime 通过独立验证门禁后再评估分阶段合入

---

<div align=center>

**EcomEvo**

Evidence first · Stateful task · Controlled action

**把复杂商业任务组织成证据，把真实业务动作留在明确权限边界内。**

</div>
