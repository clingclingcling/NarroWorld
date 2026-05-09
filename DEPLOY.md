# NarraWorld 国内可访问部署指南

目标：三小时内让中国大陆用户可以访问完整功能。

推荐部署方式：**香港 / 新加坡 VPS + Docker Compose 单容器部署**。

不推荐把 Railway 作为国内正式演示入口。Railway 可以部署，但没有中国大陆区域，`.railway.app` 域名在大陆访问稳定性不可控。

## 生产架构

当前项目已打包成单个 Docker Web 服务：

- Flask/Gunicorn 提供 `/api/*`
- Flask 同域托管 Vite 构建后的前端
- 上传文件、世界数据、生成任务、报告、模拟数据持久化在 `/app/backend/uploads`
- 浏览器只访问一个域名或一个 IP，SSE / 上传 / API 不需要跨域代理

## 服务器建议

三小时上线优先选：

- 阿里云香港 ECS
- 腾讯云香港轻量应用服务器
- 新加坡 VPS

最低配置建议：

- 2 vCPU
- 4 GB RAM
- 40 GB SSD
- Ubuntu 22.04 / 24.04

如果要上传长文本、多人同时生成世界，建议 4 vCPU / 8 GB RAM。

## 服务器安全组

先开放：

```text
22    SSH
5002  NarraWorld HTTP
```

如果后续配 Caddy/Nginx + HTTPS，再开放：

```text
80
443
```

## 环境变量

在服务器项目根目录创建 `.env`，不要提交到 Git。

```bash
LLM_API_KEY=your_llm_key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL_NAME=your_model_name
ZEP_API_KEY=your_zep_key

FLASK_DEBUG=false
SECRET_KEY=replace_with_a_long_random_string
APP_ACCESS_TOKEN=
CORS_ORIGINS=*

WEB_CONCURRENCY=1
GUNICORN_THREADS=8
GUNICORN_TIMEOUT=300
UPLOAD_FOLDER=/app/backend/uploads
OASIS_SIMULATION_DATA_DIR=/app/backend/uploads/simulations
FRONTEND_DIST_DIR=/app/frontend/dist
```

## 首次部署

```bash
sudo apt update
sudo apt install -y git ca-certificates curl
```

安装 Docker：

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

重新登录 SSH 后：

```bash
git clone <your-repo-url> NarraWorld
cd NarraWorld
mkdir -p backend/uploads
nano .env
docker compose up -d --build
```

检查状态：

```bash
docker compose ps
docker compose logs -f --tail=100
```

访问：

```text
http://服务器公网IP:5002
http://服务器公网IP:5002/health
http://服务器公网IP:5002/api/story/list?limit=1
```

公开评审演示时保持 `APP_ACCESS_TOKEN=` 为空，评委打开链接即可使用。若后续需要临时保护公网 API，再把 `APP_ACCESS_TOKEN` 改成一串私密口令并重启容器。

## 更新部署

```bash
cd NarraWorld
git pull
docker compose up -d --build
docker image prune -f
```

## 可选：用 Caddy 配 HTTPS

如果有域名，并且域名已经解析到服务器公网 IP：

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install -y caddy
```

写入 Caddyfile：

```bash
sudo nano /etc/caddy/Caddyfile
```

内容：

```caddyfile
your-domain.com {
  reverse_proxy 127.0.0.1:5002
}
```

重载：

```bash
sudo systemctl reload caddy
```

访问：

```text
https://your-domain.com
```

## 本地生产检查

```bash
npm --prefix frontend run build
cd backend
FRONTEND_DIST_DIR=../frontend/dist FLASK_DEBUG=false uv run gunicorn --bind 127.0.0.1:5055 --workers 1 --threads 4 --timeout 120 'app:create_app()'
```

打开：

```text
http://127.0.0.1:5055
```

## 海外备选

项目仍保留：

- `railway.json`
- `render.yaml`

它们适合海外演示，不建议作为中国大陆用户的主入口。
