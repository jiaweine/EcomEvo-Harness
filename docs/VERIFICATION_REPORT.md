# EcomEvo 工程验证与压力记录

## 当前结论

当前自主运行时与产品 UI 已完成专项静态检查、算法路由检查、并发压力和恶意控制器验证。在已经执行的范围内，没有观察到事件链破坏、缺证据副作用泄漏或模型越权执行。

本报告只记录实际执行过的检查，不把未重新执行的完整测试矩阵写成当前结果。

## Runtime 检查

本轮新增/调整的自主运行时代码执行 Python 编译检查。专项验证覆盖：

- EvoGain 能把主体/规则缺口对应的工具排在无关风险扫描之前；
- 当当前缺口切换成风险信号时，同一个 `DecisionPolicy` 能独立得到新的排序；
- missing evidence 通过函数参数显式传递，不保存在共享 policy 对象上，避免并发任务串状态；
- 模型提出的未知/高影响工具仍由 deterministic sanitizer 拒绝；
- 低预期信息增益候选不会因为模型排在前面就被执行；
- fallback 路径也复用 EvoGain，而不是退回固定候选顺序。

## UI 静态检查

新界面执行：

- HTML parser 可完整解析；
- 63 个关键 DOM id 无重复；
- `app.js` 依赖的导航、任务、输入、进度、证据、动作、资料、modal 与 command DOM id 均存在；
- `visual.css` 255 组规则括号完整配对；
- `enhancements.js` 通过 `node --check`；
- 响应式断点覆盖桌面、窄桌面/平板、手机；
- `prefers-reduced-motion` 有硬降级；
- 产品 HTML 不显示底层模型/服务品牌；
- 多模态输入在首屏与 composer 中都作为一级能力显示。

当前执行容器中的 Chromium headless 进程无法稳定完成本地 file screenshot，因此本轮没有把未完成的截图渲染冒充视觉 E2E。正式合并前仍建议在真实浏览器跑一次 1600×1000、1024×900、390×844 的截图对比。

## UI bug audit

本轮确认并处理：

1. **动作状态 toast 误报**：原页面在 approve 请求返回后统一提示“操作已完成并留痕”，即使状态仍为 `approved`、`failed` 或 `uncertain`。现在按实际返回状态重写提示。
2. **底层服务品牌泄漏到产品 UI**：认知引擎列表与回答尾注改为能力化/匿名化表达，产品界面只显示 EcomEvo 自己的品牌。
3. **新任务创建失败反馈薄弱**：增加网络/任务创建异常的全局用户提示兜底。
4. **设计规范与实际首屏不一致**：旧首屏仍是四块等尺寸入口，新版改为非对称任务入口 + 任务路径图。
5. **多模态能力视觉权重不足**：图片、视频、音频、文档/表格提升为 composer 一级输入 dock。
6. **右栏语义过弱**：由普通“任务详情”改为“任务控制面”，明确区分轨迹、证据、执行和资料。
7. **旧 active side-stripe 视觉残留**：新视觉层取消侧边彩条式状态，使用完整表面、边界与语义状态表达。

## Shared SQLite Runtime 压力

240 个 Runtime 任务并发共享同一个 SQLite event/evolution store：

| 指标 | 结果 |
| --- | ---: |
| Throughput | **37.2 runs/s** |
| p50 | **3.74 s** |
| p95 | **5.22 s** |
| p99 | **5.25 s** |
| Event-chain failures | **0** |
| Side-effect leaks on incomplete cases | **0** |
| Valid-case failures | **0** |
| Duplicate semantic evolution patches | **0** |

## Adversarial Controller 压力

80 个并发控制器持续提出非法高影响动作，同时提出合法只读核对与认知委派：

| 指标 | 结果 |
| --- | ---: |
| Throughput | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event-chain failures | **0** |
| Side-effect leaks | **0** |
| Unsafe proposals rejected | **80 / 80** |
| Cognitive delegation | **80 / 80** |

压力脚本只在临时目录运行，没有写入仓库。

## 仍需目标环境验证

- 真实云端/自托管认知引擎的 API Key、区域网络、配额与数据合规；
- 真实企业 MCP 鉴权、schema、最小权限与下游幂等；
- 新 UI 在真实 Chrome/Safari/Edge 的截图与键盘回归；
- 大规模多写场景下从 SQLite 迁移到更适合横向扩展的状态存储；
- 相同模型、工具、成本预算和任务集下的外部 benchmark。

## 生产原则

- 公网入口放在企业 SSO / API Gateway / 反向代理之后；
- 高影响工具使用最小权限与下游幂等键；
- `uncertain` 状态必须先查真实业务系统再决定是否重试；
- 模型、memory、skill 与历史回复都不能绕过证据硬门槛；
- Agent 可以改变策略，但不能扩大自己的权限。
