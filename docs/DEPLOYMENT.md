# Deployment notes

- Python 3.11+；CI 对最低支持版本 3.11 和当前生产镜像版本 3.14 分别执行完整回归。
- 需要 ffmpeg/ffprobe 处理视频；Dockerfile 已安装 ffmpeg。
- `ECOMEVO_DATA` 应指向持久卷。
- wheel 已包含 Web Workbench 静态资源；CI 会在干净环境安装 wheel 后实际启动服务。
- Docker 镜像使用 UID/GID `10001:10001`，支持只读根文件系统；只需让 `ECOMEVO_DATA` 对该用户可写。
- `/healthz` 是不含 Provider、MCP、租户或任务统计的公开存活探针；详细 `/api/health` 仍经过身份边界。
- 默认同源；前后端分离时配置 `ECOMEVO_CORS_ORIGINS`。
- 外部模型和 MCP 均通过环境变量配置，不要把 Key 写进前端或仓库。
- 正式环境建议由企业反向代理/SSO 保护整个工作区服务。
- 多 worker 可用：EventStore 序号写入、动作确认和同任务处理租约均使用 SQLite 跨进程事务。
- SQLite 适合单工作区/中等并发；高吞吐多租户 SaaS 建议把产品状态、事件流和任务队列迁到集中式数据库/队列。
