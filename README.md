# EcomEvo 商业决策工作台

面向商品治理、商家审核、售后判责、风险核查和内容审核的对话式多模态业务决策平台。

客户页面只展示业务语言：资料、处理进度、关键依据、风险点和待确认操作。内部运行时负责事件记录、自适应核对、并行工具调用、递归交叉复核、结果验证、失败恢复和受控改进。

## 产品能力

- 对话式任务：同一任务中的历史用户说明和已上传资料持续有效，后续不必反复上传。
- 多模态资料：图片、视频、音频、PDF、Word、Excel、CSV/JSON、日志与文本。
- 多模型：OpenAI、DeepSeek、通义千问、豆包、Claude、Gemini、企业 OpenAI-Compatible Endpoint。
- 多媒体证据：视觉/音频/扫描 PDF 先提取可追溯事实，再进入业务核对；低置信度读取不会解锁业务操作。
- 长文档检索：展示文本与服务端检索索引分离，日志/文档/表格可检索后段内容。
- 事件审计：append-only Event Sourcing、SHA-256 hash chain、checkpoint、fork/replay。
- 自适应执行：按场景、证据缺口和成本选择工具；同阶段只读工具并行执行。
- 递归复核：第一层规则/证据/风险/业务并行复核；不确定或存在风险时进入第二层交叉复核。
- 恢复：证据不足时恢复稳定状态并重新核对；进程中断后租约过期可解除前端永久“处理中”。
- 受控改进：失败生成小范围改进项，经隔离回放和回归门禁后才应用；重启后恢复，重复改进去重。
- MCP：支持企业只读业务数据工具和确认后的业务动作；支持现代 Streamable HTTP 与旧版初始化/Session 回退。
- 副作用保护：退款、下架、审核、风险升级等动作必须二次确认，并用数据库原子状态迁移防止重复执行。

## 启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

打开 `http://localhost:8000`。没有外部模型 Key 时选择“本地演示”，也可以跑完整业务流程。

Docker：

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

## 验证

```bash
pytest -q
python scripts/e2e_smoke.py
```

真实网络层：

```bash
uvicorn ecomevo.api.app:app --host 127.0.0.1 --port 8000
python scripts/live_smoke.py --base http://127.0.0.1:8000
```

## 生产部署注意

当前工程按“单企业工作区服务”设计。正式暴露到公网前，应放在企业 SSO/API Gateway/反向代理之后，不应把未鉴权服务直接暴露到互联网。`ECOMEVO_CORS_ORIGINS` 默认空，即浏览器只走同源；前后端分离时再显式配置。

上传资料的内部磁盘路径、完整检索索引、关键帧路径和多媒体语义缓存不会返回给浏览器。原始附件在每轮执行前重新核验 SHA-256 内容指纹。

外部 benchmark 指标不由本地 pytest 代替。EComAgentBench、τ³-bench Retail、TUA-Bench 需要准备对应数据集、固定相同 backbone、工具集合和 baseline 后单独复现。

详细验收见 `docs/VERIFICATION_REPORT.md`。
