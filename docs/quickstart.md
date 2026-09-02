# ProjectTown Docker 快速部署

本指南只介绍使用 Docker Compose 部署 ProjectTown 后端。Docker 镜像已包含全部运行依赖。

## 环境要求

安装 [Docker Desktop](https://docs.docker.com/desktop/)，或 Docker Engine + Docker Compose v2。

```bash
docker --version
docker compose version
```

两个命令都能正常输出版本后即可继续。

## 1. 获取项目

下载仓库 ZIP 并解压，或使用 Git 克隆：

```bash
git clone https://github.com/OWNER/REPOSITORY.git
cd REPOSITORY
```

后续命令都在包含 `docker-compose.yml` 的项目根目录执行。

## 2. 启动服务

项目使用安全的默认配置，不需要创建 `.env`：

```bash
docker compose up --build -d
```

首次启动需要构建镜像，所需时间取决于网络速度。查看状态：

```bash
docker compose ps
```

当 `projecttown` 显示为 `healthy` 后，部署完成。

## 3. 验证部署

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/api/v2/health
```

两个接口都应返回正常状态。API 文档地址：

<http://127.0.0.1:8000/docs>

## 4. 可选配置

只有需要调整默认参数时才创建 `.env`：

```bash
# Linux / macOS
cp .env.example .env

# Windows PowerShell
Copy-Item .env.example .env
```

修改 `.env` 后重新创建容器：

```bash
docker compose up -d --force-recreate
```

数据库和任务工作区路径已由 `docker-compose.yml` 配置为持久化卷，一般不需要修改。

### 本机 Docker Settings（仅 development/test）

默认 Compose 不提供 Quest 设置面板的保存接口。若仅在当前用户、本机 Docker
开发环境中需要通过该面板保存 OpenAI/Qwen 的 Base URL、API Key 与模型名，先创建
受保护的普通目录 `.secrets`（它只保存短期会话 token，绝不保存 Key），再使用管理
脚本启动：

```powershell
.venv\Scripts\python.exe scripts\manage_docker_local_settings.py start
```

override 固定使用 `172.30.250.0/29` bridge；预检发现 Docker 子网冲突会停止，绝不
自动扩大信任范围。provider 配置仅存在 Docker named volume
`projecttown_local_settings`；宿主 `.secrets` 只接收握手成功后的 token mirror。基础
Compose 不挂载该目录，且路由仍为 404。普通 `docker compose restart` 只适用于基础
Compose；Settings 模式不得使用它，以免 token mirror 与容器镜像失步。Settings 模式需要
重启或重新部署时，使用现有管理脚本的 `start` 命令：

```powershell
.venv\Scripts\python.exe scripts\manage_docker_local_settings.py start
```

关闭该模式并回滚到安全默认值：

```powershell
.venv\Scripts\python.exe scripts\manage_docker_local_settings.py rollback
```

这不是生产 secret manager。Docker 管理员仍可访问 named volume，且同一 Windows 用户
可读取 token mirror；请只在开发/测试使用，并在任何共享或生产部署中改用受审计的
secret manager。

## 5. 常用命令

```bash
# 查看实时日志
docker compose logs -f projecttown

# 重启服务（仅基础 Compose；Settings 模式请使用上方管理脚本的 start 命令）
docker compose restart projecttown

# 停止服务并保留数据
docker compose down

# 再次启动
docker compose up -d

# 拉取代码后更新部署
git pull
docker compose up --build -d
```

## 6. 数据与安全

ProjectTown 使用两个由 Docker 管理的持久化卷：

- `projecttown-data`：SQLite 数据库；
- `projecttown-sandbox`：任务生成的文件。

`docker compose down` 不会删除数据。不要使用 `docker compose down -v`，除非确定要永久清空数据库和全部任务文件。

服务默认只监听宿主机 `127.0.0.1:8000`。当前版本没有内置公网认证和 TLS，请勿直接暴露到公网；远程访问应通过带 HTTPS 和身份认证的反向代理。

## 7. 故障排查

查看最终配置、容器状态和最近日志：

```bash
docker compose config
docker compose ps
docker compose logs --tail 100 projecttown
```

如果端口 8000 被占用，将 `docker-compose.yml` 中的端口改为其他本机端口，例如：

```yaml
ports:
  - "127.0.0.1:8010:8000"
```

随后通过 `http://127.0.0.1:8010` 访问服务。
