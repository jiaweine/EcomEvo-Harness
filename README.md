<div align="center">

# EcomEvo 商业决策工作台

**对话式电商任务执行与决策工作台**

把商品、商家、订单、截图、视频、文档和表格放进同一个任务里。EcomEvo 持续整理事实、核对资料、形成结论，并把真正会改变业务状态的操作交给用户确认后执行。

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.11%2B-1f6feb?logo=python&logoColor=white" />
  <img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Backend-0f8a72?logo=fastapi&logoColor=white" />
  <img alt="Multimodal" src="https://img.shields.io/badge/Multimodal-Images%20%7C%20Video%20%7C%20Audio%20%7C%20Docs-253858" />
  <img alt="Tests" src="https://img.shields.io/badge/Regression-136%20tests-1f7a5a" />
</p>

</div>

## 产品预览

![EcomEvo 商业决策工作台](docs/images/product-workbench.svg)

<p align="center">
  <img src="docs/images/product-mobile.svg" alt="EcomEvo 移动端任务工作台" width="360" />
</p>

## 这不是一个只负责回答问题的聊天框

EcomEvo 把**对话当成任务入口**，而不是最终产品形态。

一个任务可以持续多轮：用户补充资料、追问、修改目标，工作台仍然保留同一任务中的业务上下文。系统会把“已确认事实”“关键依据”“风险点”“待确认操作”和“执行结果”分开呈现，避免把所有内容混在一段回答里。

高影响操作不会因为一句自然语言指令直接发生。退款、下架、商家审核、风险升级等动作会先形成明确的待确认项；确认后再进入业务执行，并把结果写回当前任务。

## 适用场景

| 场景 | 典型任务 |
| --- | --- |
| 商品治理 | 商品标题/主图/详情核对、功效声明、资质缺口、下架建议 |
| 商家审核 | 主体信息、经营资质、品牌授权、历史风险、准入结论 |
| 售后判责 | 订单、物流、沟通记录、用户举证、责任与退款金额 |
| 风险核查 | 交易、账户、商品、履约异常，区分强证据与普通线索 |
| 内容审核 | 图片、视频、文案和商品事实的一致性检查 |

## 产品能力

- **持续任务空间**：历史说明和已上传资料在同一任务内持续有效，不需要每轮重新描述背景。
- **多模态资料**：支持图片、视频、音频、PDF、Word、Excel、CSV/JSON、日志与文本。
- **多服务接入**：支持 OpenAI、DeepSeek、通义千问、豆包、Claude、Gemini，以及企业 OpenAI-Compatible Endpoint。
- **证据优先**：资料不足时明确要求补充，不会因为回答“看起来合理”就生成真实业务操作。
- **业务确认**：退款、下架、审核、风险升级等操作必须确认，并防止重复执行。
- **任务恢复**：工具异常、网络中断或服务重启后，可以从已保存状态继续处理。
- **完整留痕**：任务、资料、核对结果、操作确认和执行结果都可追踪。
- **企业工具接入**：可连接订单、商品、商家、风控等内部系统；只读查询和有副作用操作分开处理。

## 自研执行层

模型在 EcomEvo 中是**可替换的推理与多模态服务**，不是任务控制器。

任务状态维护、资料可信性检查、工具选择与组合、高影响操作确认、失败恢复和执行留痕由 EcomEvo 自己的运行层负责。更换模型厂商不会改变这套业务执行规则。

工程实现集中在：

- `ecomevo/runtime/`：任务执行、状态记录、工具连接、复核与恢复
- `ecomevo/product/`：多模态资料处理与产品编排
- `ecomevo/providers/`：多厂商服务适配
- `ecomevo/api/`：FastAPI、WebSocket、会话、附件与操作接口
- `frontend/`：客户工作台

## 快速启动

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn ecomevo.api.app:app --host 0.0.0.0 --port 8000
```

浏览器打开 `http://localhost:8000`。

没有配置外部服务时可以选择 **本地演示** 跑通业务流程。选择外部服务时，当前任务内容和所需资料会按你的配置发送到对应服务；页面会明确提示这一数据流向。

### Docker

```bash
docker build -t ecomevo .
docker run --rm -p 8000:8000 --env-file .env -v ecomevo-data:/app/outputs ecomevo
```

## 外部服务配置

复制 `.env.example` 后按需填写相应 Key。未配置的服务仍会显示，但不会被自动路由使用。

企业内部兼容接口可以通过 OpenAI-Compatible 配置接入。MCP 服务可用于连接订单、商品、商家、风控等内部工具；高影响动作仍会经过产品侧确认流程。

## 验证

```bash
pytest -q
python scripts/e2e_smoke.py
```

真实本地网络层：

```bash
uvicorn ecomevo.api.app:app --host 127.0.0.1 --port 8000
python scripts/live_smoke.py --base http://127.0.0.1:8000
```

当前仓库回归覆盖业务反例、并发确认、多模态证据、异常上传、长文档、任务恢复、MCP 协议、WebSocket 和响应式前端。完整范围见 [`docs/VERIFICATION_REPORT.md`](docs/VERIFICATION_REPORT.md)。

## 目录

```text
.
├── ecomevo/          # 后端与执行层
├── frontend/         # 客户工作台
├── docs/             # 架构、设计、部署与验证说明
├── scripts/          # E2E / live smoke
├── tests/            # 自动化回归
├── Dockerfile
├── pyproject.toml
└── requirements.txt
```

## 生产部署

当前工程按单企业工作区设计。正式公网环境建议放在企业 SSO / API Gateway / 反向代理之后，并为真实退款、下架、冻结等业务系统继续配置最小权限、幂等键和企业审计策略。

本地自动化测试用于验证工程行为，不替代 EComAgentBench、τ³-bench Retail、TUA-Bench 等外部基准的独立复现。
