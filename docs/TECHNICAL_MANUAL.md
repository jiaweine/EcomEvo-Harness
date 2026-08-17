# EcomEvo 技术手册

> 面向部署、二次开发、平台工程、安全和 Agent Runtime 工程团队。

## 1. 系统目标

EcomEvo 的工程目标不是提供一个无限工具调用的聊天代理，而是构建一个满足以下属性的业务 Runtime：

- 多模态输入进入统一证据空间；
- 只读认知可以自主规划和重规划；
- 工具调用有成本和权限边界；
- 高影响业务动作与认知层分离；
- 任务过程可追溯、可回放、可恢复；
- 技能可以从真实结果中更新，但不能自我扩权。

## 2. 代码结构

```text
ecomevo/
├── api/                  API、WebSocket、附件、业务动作
├── product/              产品编排、多模态事实提取
├── providers/            可替换认知引擎与能力路由
└── runtime/
    ├── autonomy.py       EvoLoop、Dynamic Task Graph、stagnation
    ├── control_policy.py EvoGain、模型动作清洗、工具策略
    ├── delegation.py     specialist 委派
    ├── skills.py         Bayesian skill library、quality-diversity
    ├── evolver.py        failure/success trajectory evolution
    ├── verifier.py       证据与安全硬门槛
    ├── governance.py     业务动作权限边界
    ├── event_store.py    event sourcing、hash chain、patch dedupe
    ├── sandbox.py        side-effect policy
    └── tools.py          本地与 MCP 工具注册/执行
frontend/                 Agent-native 操作台
docs/                     产品、算法、架构、设计与验证文档
```

## 3. 请求生命周期

典型任务路径：

```text
Browser
  ↓
Conversation API
  ↓
ProductAnalyzer
  ├─ validate assets
  ├─ multimodal fact extraction
  └─ provider capability routing
  ↓
EcomEvoEngine
  ↓
AutonomousController
  ├─ Planner safety floor
  ├─ DecisionPolicy / EvoGain
  ├─ Tool execution
  ├─ Specialist delegation
  ├─ Verification
  └─ bounded recovery
  ↓
GovernanceBoundary
  ↓
BusinessAction proposal
  ↓
Human confirmation
  ↓
MCP / local business executor
```

## 4. 数据与状态

### Product store

产品层 SQLite 保存：conversations、messages、assets、task events、actions 和 turn leases。

附件在数据库中保存元数据和文件路径，公开 API 只返回浏览器安全字段。文件下载与预览前会验证路径和内容哈希。

### Runtime event store

Runtime 维护 append-only 事件链和 snapshot。每个事件包含 `prev_hash` 与当前 hash，可用于检测轨迹篡改或写入异常。

Evolution patch 另外使用语义 fingerprint 去重，避免并发失败轨迹产生等价重复 patch。

### Skill store

同一 Runtime 数据库内持久化 runtime skills、skill outcomes 和 per-domain evolution policy。

SQLite 使用 WAL 和 busy timeout。当前单节点扩展上限主要来自 single-writer 特性。

## 5. 多模态附件管线

### 支持类型

- raster image；
- video；
- audio；
- PDF；
- DOCX；
- XLSX / XLSM；
- text / log / JSON / CSV / YAML / XML。

### 上传安全

上传阶段包含：后缀和 MIME 归一化、可执行/脚本类型阻断、容量上限、图片尺寸与 decompression-bomb 防护、Office ZIP 结构和解压倍率检查、PDF 加密/损坏检查、音视频 `ffprobe` 内容检查和 SHA-256 内容指纹。

### 语义提取

图片、视频关键帧、音频和低文本密度扫描 PDF 可进入认知引擎的事实提取阶段。事实提取 prompt 只允许输出直接观察到的业务事实，不允许直接产生审核通过、退款、下架等处置。

低于语义置信阈值的 observation 不会进入持久化语义缓存。

## 6. Provider 路由

ProviderRegistry 负责配置检测、文本/图片/音频/文档能力匹配、自动路由、显式本地受控模式以及 task-local current provider，避免并发请求互相污染 provider state。

可通过 `OPEN_MODEL_*` 配置兼容接口的开源权重或自托管推理服务。模型名称由部署环境配置，不写死在 Runtime。

## 7. AutonomousController

### 硬上限

运行时的探索强度由环境变量在代码允许范围内调节：

```text
ECOMEVO_AUTONOMY_STEPS
ECOMEVO_AUTONOMY_CALLS_PER_STEP
ECOMEVO_AUTONOMY_DELEGATIONS_PER_STEP
```

代码对这些值再次 clamp，环境配置不能无限放大自主循环。

### 初始路径

1. Planner 根据业务域产生确定性安全/证据底线计划；
2. 加入注册的匹配 MCP read-only call；
3. 若存在 reasoner，模型提出额外候选；
4. DecisionPolicy 过滤非法候选；
5. EvoGain 重新排序和裁剪；
6. 并行执行合法工具。

### Recovery

若 Verifier 未通过，Runtime 会更新 missing evidence、计算剩余预算、召回 relevant skills、重新询问控制器或使用 deterministic fallback、执行补证、重新 review 和 verify，并在 fingerprint 连续无变化时触发 topology mutation 或 stagnation stop。

## 8. Tool policy

工具目录只暴露 registry 中存在的能力。

模型候选必须经过：unknown tool rejection、sandbox side-effect rejection、confirmation-required rejection、budget limit、duplicate-call handling、per-step call cap 和 server-owned arguments sanitization。

除定向 `evidence.search` 外，模型不能自由构造任意 MCP 参数；远程参数由服务端注册配置拥有。

## 9. Specialist delegation

CognitiveDelegator 支持确定性 RecursiveCoordinator 和可选模型 specialist 两层复核。

模型 specialist 只接收压缩后的已核对工具结果，不拥有原始业务权限。输出是 review opinion，而不是独立 evidence。

在停滞场景中，Runtime 可以增加“反证审查”等角色，但仍保持 read-only。

## 10. Verification

DecisionVerifier 是最终认知输出与业务 proposal 之间的硬边界。至少检查 required evidence、constraints、side-effect safety、missing evidence、recommendation 和 score。

模型生成的最终自然语言只用于表达已经通过 Runtime 的结果，不能把一个 evidence-incomplete case 改写成通过。

## 11. BusinessAction 与权限

GovernanceBoundary 负责把通过验证的业务结论转换为 `BusinessAction` proposal。

所有高影响动作保持：

```text
side_effect = true
requires_confirmation = true
status = proposed
```

确认接口使用 compare-and-set 状态迁移，避免并发重复确认。

下游网络异常分为明确失败 `failed` 和无法判断是否已执行的 `uncertain`。`uncertain` 禁止自动盲重试。

## 12. MCP 集成

推荐把 MCP 能力分成两类：

```text
Read-only MCP
  → autonomous exploration
  → evidence

Side-effect MCP
  → BusinessAction proposal
  → human confirmation
  → execution
```

生产 MCP 建议配置最小权限凭据、明确 domain、purpose、evidence tags、request timeout、写操作幂等键和独立业务审计日志。

## 13. 自进化

FailureDrivenEvolver 可以从 verification failure 和 successful recovery trajectory 生成候选只读 skill。

候选必须经过 replay / regression gate，再进入 shadow 或 active skill archive。模型可以帮助生成 skill guidance，但不能修改 Verifier、安全源码或业务权限定义。

## 14. API 运营面

主要产品接口包括：

- `/api/conversations`；
- `/api/assets`；
- `/api/providers`；
- `/api/runtime`；
- `/api/evolution`；
- `/api/runtime/sessions/{session_id}/events`；
- `/api/actions/{action_id}/decision`；
- `/ws/conversations/{cid}`。

产品 API 默认对 `/api/` 响应设置 `private, no-store`，并设置内容嗅探、frame、防 referrer、权限和 CSP 等安全响应头。

## 15. 失败恢复

### UI 连接恢复

WebSocket 断开后前端按指数退避重连，并在重连前刷新当前任务状态。

### Turn lease

每个 conversation 同一时刻只允许一个处理 lease。长任务定期续租。进程异常后，过期 lease 会被恢复逻辑关闭，并产生明确 `answer.error`，防止 UI 永久卡在处理中。

### Action recovery

长时间停留在 `approved` 且 worker 已中断的动作会被恢复为 `uncertain`，而不是回退为可重复点击的 `proposed`。

## 16. 前端设计系统

样式目标加载顺序：

```text
app.css
→ visual.css
→ product-polish.css
```

`product-polish.css` 负责 CJK 稳定字体栈、12/13/14/16/20/26/display 类型尺度、中文标题和正文 line-height、字重约束、状态型动效、reduced motion、keyboard focus 和中屏 task path 保留。

产品字体不依赖公网 font CDN，避免网络、隐私和中国区加载不确定性。

## 17. 部署

开发环境：

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

Docker：

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

## 18. 生产化缺口

当前设计仍有明确工程边界：

- SQLite single-writer 不适合无限水平扩展；
- 进程内后台任务不等价于跨节点 durable workflow；
- tenant / SSO / RBAC / approver identity 仍需生产集成；
- 需要业务 gold set 和 CI eval gate；
- 需要真实浏览器视觉回归；
- 多节点演进需要进一步设计 distributed lease / evolution consistency。

这些限制不应通过“更强模型”解决，因为它们属于系统工程和权限模型问题。

## 19. 验证建议

每次修改 Runtime、Verifier、provider routing 或 skill strategy 后，至少运行：

```bash
pytest -q
python scripts/e2e_smoke.py
```

生产候选还应增加真实浏览器 desktop/tablet/mobile 回归、gold-set decision eval、tool-routing eval、side-effect safety eval、interruption/resume eval、provider failure/timeout eval 和 MCP idempotency eval。

详细压力记录见 `docs/VERIFICATION_REPORT.md`。
