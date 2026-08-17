# EcomEvo 工程验证与压力记录

## 当前结论

EcomEvo 的自主运行时升级已经完成并进入 feature branch。当前已执行的专项回归、并发压力和恶意控制器测试中，**没有观察到事件链破坏、缺证据副作用泄漏或模型越权执行。**

本报告刻意区分：

1. **当前自主运行时升级的专项验证**；
2. **升级前项目基线的历史 136 项回归**；
3. **仍需在目标生产环境完成的验证**。

因此不会把历史通过结果冒充成当前分支的全量重新验证。

---

# 自主运行时升级验证

## 编译与专项回归

升级过程中执行：

```bash
python -m compileall -q ecomevo
```

并运行自主控制、安全边界、技能持久化、Evolution Patch 去重、旧 SQLite migration 等专项回归。

结果：

- 专项兼容/回归：**12 / 12 通过**
- Python compileall：通过
- Dedicated autonomy test file 在完成验证后按发布包装要求从 feature branch 删除；压力脚本始终只存在于临时目录，没有进入仓库。

专项验证覆盖：

- Model Controller 可以自主选择合法只读工具；
- 非法 side-effect proposal 会被 deterministic policy 拒绝；
- 动态 cognitive delegation 不会获得 BusinessAction 权限；
- 缺证据任务不会生成可执行动作；
- 完整证据任务仍只生成 `requires_confirmation=True` 的高影响操作；
- Skill Bayesian posterior 会随真实失败下降；
- Skill 状态可跨 Runtime 重启持久化；
- Evolution Patch 按语义 fingerprint 去重；
- 旧 evolution table 可以无损 migration。

---

# 压力验证

所有压力脚本均在临时目录运行，**未写入项目代码**。

## Shared SQLite Runtime：240 concurrent runs

240 个 Runtime 任务并发共享同一个 SQLite event/evolution store。

| 指标 | 结果 |
| --- | ---: |
| Throughput | **37.2 runs/s** |
| p50 | **3.74 s** |
| p95 | **5.22 s** |
| p99 | **5.25 s** |
| Event chain failures | **0** |
| Side-effect leaks on incomplete cases | **0** |
| Valid-case failures | **0** |
| Duplicate semantic evolution patches | **0** |

验证重点不是单纯追求 QPS，而是确认高并发下：

- append-only event sequence 仍然连续；
- hash chain 仍然有效；
- 并发 Evolution 不会堆重复策略；
- incomplete case 不会因为竞争条件产生 BusinessAction；
- 有效案例仍然可以正常完成。

## Adversarial Model Controller：80 concurrent runs

构造恶意 Model Controller，每轮同时提出：

1. 非法 `refund.issue`；
2. 合法 `evidence.search`；
3. 合法 cognitive specialist delegation。

结果：

| 指标 | 结果 |
| --- | ---: |
| Throughput | **29.3 runs/s** |
| p50 | **1.51 s** |
| p95 | **2.20 s** |
| Event chain failures | **0** |
| Side-effect leaks | **0** |
| Unsafe proposals rejected | **80 / 80** |
| Runs with cognitive delegation | **80 / 80** |
| Model-controller runs | **80 / 80** |

这个测试验证了 EcomEvo 最核心的安全原则：

> **模型可以拥有自主认知，但模型 proposal 不等于执行权限。**

---

# 当前安全不变量

自主运行时升级后继续保留：

- 证据完整性是高影响动作硬条件；
- Model output 不是独立业务证据；
- Memory 不是独立业务证据；
- Skill guidance 不是独立业务证据；
- 用户自然语言描述不会自动升级成附件/企业系统强证据；
- Agent 只能从 Tool Registry 中选择真实存在的工具；
- Sandbox 会拒绝 unknown / side-effect tool 的自主执行；
- BusinessAction 仍需人工确认；
- Action decision 使用数据库 atomic compare-and-set；
- 下游结果无法确认时进入 `uncertain`，禁止自动盲重试；
- 文件归属、SHA-256 和视频关键帧完整性仍然校验；
- 多媒体低置信度读取 fail closed；
- Event Store 继续使用 hash chain；
- 并发 evolution candidate 使用语义 fingerprint 去重。

---

# 升级前历史基线

在自主运行时升级之前，项目曾完成一轮完整工程验证：

- 自动化回归：**136 项通过**；
- 售后 E2E：任务、上传、判责、待确认动作、执行、事件链通过；
- Uvicorn `/api/health`、首页、静态资源、`/docs` 实际 HTTP 200；
- WebSocket 实际连接与最终事件通过；
- 同任务并发连续 5 组均为一个 200 + 一个 409；
- 安全响应头验证；
- `compileall` / `node --check`；
- Chromium 静态真实渲染：1600×1000、1024×900、390×844；
- 桌面/平板/移动端布局、抽屉、键盘焦点与弹窗交互。

**这 136 项是升级前基线，不作为当前自主分支已经重新跑完 136 项的声明。**

---

# 历史修复项

此前工程验证已经修复/覆盖：

1. 本地演示意外命中外部服务；
2. SKU/库存数字误当价格证据；
3. 文字附件替代未读取图片/视频；
4. 风险问卷后置否定误判；
5. 长日志尾部证据被截断；
6. 流式检索过早停止；
7. 历史问题污染当前证据要求；
8. 多媒体缓存来源归因丢失；
9. 缺证据仍调用最终外部模型；
10. 未完成案例写入业务 memory；
11. MCP 危险动作错误协议回退；
12. MCP 内部配置向前端泄漏；
13. 慢 WebSocket 客户端丢终态；
14. 长会话浏览器状态无限增长；
15. 动作历史拖大响应；
16. 图片像素炸弹异常；
17. Provider 能力与附件类型不匹配；
18. UI hidden 状态覆盖；
19. 移动端审批入口丢失；
20. 任务切换/上传/WebSocket 竞态；
21. 重复点击业务操作；
22. 键盘与可访问性；
23. Grid 排版把输入区挤出视口；
24. 资料预览失败出现原生破图；
25. 待补资料误显示 100%；
26. 多行用户指令丢换行；
27. 服务弹窗关闭后焦点丢失。

---

# 尚需目标环境完成

以下内容必须在真实目标环境继续验证，当前项目不会伪报“已经线上通过”：

- OpenAI / DeepSeek / Qwen / Doubao / Claude / Gemini 的真实 API Key、区域网络、配额和数据合规；
- 企业真实 MCP 地址、鉴权、工具 schema、幂等和权限模型；
- 大规模分布式多 writer event/evolution store；
- 真正生产流量下的长期 skill evolution 行为；
- EComAgentBench、τ³-bench Retail、TUA-Bench 等外部 benchmark 的独立复现；
- 针对当前自主运行时分支重新执行完整历史回归矩阵。

---

# 已知扩展边界

当前默认 SQLite 的优势是：

- 简单；
- 可移植；
- transaction 清晰；
- 单节点一致性强。

但它的 single-writer 行为仍是当前单节点吞吐扩展上限。

240 并发压力测试说明**正确性在该测试范围内保持稳定**，不代表 SQLite 是最终生产级分布式存储方案。

面向大规模线上部署，建议把：

- Event Store；
- Skill Store；
- Task State；
- Evolution State

迁移到支持多 writer、HA、强一致事务或明确并发控制的基础设施。

---

# 生产建议

公网或企业生产环境建议继续配置：

- SSO / IAM；
- API Gateway；
- 反向代理；
- 最小权限；
- 网络隔离；
- 下游幂等键；
- 企业审计；
- 数据合规与留存策略。

EcomEvo 的目标是让 Agent 的认知能力持续增强，而不是让安全边界随着模型能力一起漂移。
