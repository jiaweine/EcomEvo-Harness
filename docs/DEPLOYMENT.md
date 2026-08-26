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

## Tencent Cloud Studio

仓库已提供 `.vscode/preview.yml`。在 Cloud Studio 从 GitHub 导入本仓库后，点击启动即可安装 Python 包、运行完整 FastAPI 服务，并把 `8000` 作为主预览端口。

- 启动命令绑定 `0.0.0.0:8000`，前端与 API 同源；
- Cloud Studio 的发布操作会生成可分享的云沙箱地址；
- 正式业务环境仍应配置持久卷、企业身份、Provider/MCP 密钥与反向代理；
- 不要把任何密钥写入 `.vscode/preview.yml` 或仓库。
