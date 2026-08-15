# Deployment notes

- Python 3.11+
- 需要 ffmpeg/ffprobe 处理视频；Dockerfile 已安装 ffmpeg。
- `ECOMEVO_DATA` 应指向持久卷。
- 默认同源；前后端分离时配置 `ECOMEVO_CORS_ORIGINS`。
- 外部模型和 MCP 均通过环境变量配置，不要把 Key 写进前端或仓库。
- 正式环境建议由企业反向代理/SSO 保护整个工作区服务。
- 多 worker 可用：EventStore 序号写入、动作确认和同任务处理租约均使用 SQLite 跨进程事务。
- SQLite 适合单工作区/中等并发；高吞吐多租户 SaaS 建议把产品状态、事件流和任务队列迁到集中式数据库/队列。
