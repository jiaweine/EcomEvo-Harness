# Architecture

## Product layer

`frontend/`：三栏对话工作台。左侧业务入口与任务，中间连续对话和多模态资料，右侧处理进度、关键依据、资料和待确认操作。

`ecomevo/api/`：FastAPI、WebSocket、会话、附件、动作确认、并发任务租约和异常恢复。

`ecomevo/product/`：资料解析、多媒体事实提取缓存、历史任务上下文、面向客户的结果编排。

`ecomevo/providers/`：多厂商模型能力路由。最终业务状态由 Runtime 决定；证据不足时外部模型不能覆盖受控结论。

## Runtime layer

`ecomevo/runtime/event_store.py`：append-only 事件、hash chain、JSON checkpoint、fork/replay、改进项存储。

`planner.py`：场景目标解析、成本门禁、证据检索计划、失败改进项加载。

`tools.py`：本地只读工具、企业 MCP 只读工具、并行工具组合。

`recursive.py`：第一层并行专业复核 + 条件触发的第二层交叉复核。

`verifier.py`：业务证据硬门槛、当前问题特定证据条件、副作用安全检查。

`evolver.py`：失败生成小 patch，隔离回放与 regression gate 后合并。

`mcp.py`：企业工具发现/调用、现代协议与 legacy fallback、动作映射。

## Data flow

用户消息与任务资料 → 文件指纹核验 → 多媒体可追溯事实提取/缓存 → Runtime 规划 → 并行工具核对 → 递归交叉复核 → 证据硬验证 → 必要时恢复/重规划 → 形成受控结果 → 可选模型润色 → 生成待确认业务操作。

模型润色不拥有业务状态决定权；资料不足时直接使用服务器受控结论。
