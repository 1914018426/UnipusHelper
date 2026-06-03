# UnipusHelper Pro

U校园AI自动答题服务 —— 一键完成 U校园AI 课程作业任务。

[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](https://docs.docker.com/compose/)
[![FastAPI](https://img.shields.io/badge/FastAPI-009688)](https://fastapi.tiangolo.com/)
[![Celery](https://img.shields.io/badge/Celery-37814A)](https://docs.celeryq.dev/)

## 功能特性

- **自动答题**: 自动获取课程任务答案并提交，支持多种题型
- **智能跳过**: 自动检测已完成的任务，避免重复提交
- **单账号锁定**: 首次使用即绑定手机号，防止账号切换
- **单任务队列**: 每个用户同时只能执行一个任务，避免并发冲突
- **邮件通知**: 登录成功、任务完成、异常告警实时邮件推送
- **安全加固**:
  - JWT + Cookie/Header 双认证
  - Fernet 加密存储 U校园密码
  - IP 速率限制（登录/注册/任务创建分别限制）
  - 安全响应头（HSTS、X-Frame-Options、CSP 等）
  - HTTPS 支持（自签名证书）

## 技术架构

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│  User   │────▶│  Nginx  │────▶│ FastAPI │
│ Browser │◀────│ 443/80  │◀────│ Backend │
└─────────┘     └─────────┘     └────┬────┘
                                     │
                        ┌────────────┼────────────┐
                        ▼            ▼            ▼
                   ┌────────┐   ┌────────┐   ┌────────┐
                   │ Redis  │   │SQLite  │   │Celery  │
                   │ Broker │   │  DB    │   │Worker  │
                   └────────┘   └────────┘   └────────┘
```

- **Backend**: FastAPI + SQLAlchemy + SQLite
- **Task Queue**: Celery + Redis
- **Proxy**: Nginx (反向代理 + SSL + 速率限制)
- **Frontend**: 纯 HTML/JS 单页面，零构建依赖

## 快速开始

### 1. 克隆仓库

```bash
git clone https://github.com/1914018426/UnipusHelper.git
cd UnipusHelper
```

### 2. 生成密钥

首次部署必须生成安全密钥：

```bash
# JWT 签名密钥
openssl rand -hex 32

# Fernet 任务密码加密密钥
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 3. 配置环境变量

复制示例文件并编辑：

```bash
cp .env.example .env
```

编辑 `.env`：

```env
# 服务端口（宿主机 80/443 被占用时使用高阶端口）
HTTP_PORT=18083
HTTPS_PORT=18084
BACKEND_PORT=18003

# 安全密钥（必须替换！）
UNIPUS_SECRET=<openssl rand -hex 32 的输出>
AES_KEY_PREFIX=<任意8位以上字符串>
TASK_ENCRYPTION_KEY=<Fernet.generate_key() 的输出>

# CORS（生产环境限制为实际域名）
ALLOWED_ORIGINS=https://localhost,https://127.0.0.1

# SMTP 邮件通知（可选，使用 QQ 邮箱授权码）
SMTP_HOST=smtp.qq.com
SMTP_PORT=465
SMTP_USER=your@qq.com
SMTP_PASSWORD=your_qq_auth_code
SMTP_FROM=your@qq.com
ADMIN_EMAIL=admin@example.com
```

### 4. 生成 SSL 证书（自签名）

```bash
mkdir -p nginx/ssl
openssl req -x509 -nodes -days 365 -newkey rsa:2048 \
  -keyout nginx/ssl/key.pem \
  -out nginx/ssl/cert.pem \
  -subj "/C=CN/ST=State/L=City/O=Org/CN=localhost"
```

### 5. 启动服务

```bash
docker compose up -d
```

访问 `http://服务器IP:18083` 或 `https://服务器IP:18084`。

### 6. 查看日志

```bash
# Backend
docker compose logs -f backend

# Celery Worker
docker compose logs -f celery_worker

# Nginx
docker compose logs -f nginx
```

## 项目结构

```
.
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口 + 安全中间件
│   │   ├── config.py            # 配置管理
│   │   ├── models.py            # SQLAlchemy 模型 (User, Task)
│   │   ├── schemas.py           # Pydantic 校验模型
│   │   ├── api/
│   │   │   └── routes.py        # REST API 路由
│   │   ├── services/
│   │   │   ├── unipus.py        # U校园AI 核心逻辑
│   │   │   ├── crypto.py        # 密码加密/解密
│   │   │   ├── email.py         # 邮件通知模板
│   │   │   └── decrypt.py       # 答案解密/请求构造
│   │   └── tasks/
│   │       └── celery_app.py    # Celery 异步任务
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── index.html               # 纯前端单页面
├── nginx/
│   ├── nginx.conf               # 反向代理 + SSL + 限流
│   └── ssl/                     # SSL 证书目录
├── docker-compose.yml
├── .env.example
└── README.md
```

## API 文档

开发模式下访问 `/docs` 查看 Swagger UI。

### 核心接口

| Method | Path | 描述 |
|--------|------|------|
| POST | `/api/auth/register` | 用户注册 |
| POST | `/api/auth/login` | 用户登录（返回 JWT + Cookie） |
| POST | `/api/auth/logout` | 退出登录 |
| GET | `/api/auth/me` | 获取当前用户信息 |
| POST | `/api/tasks` | 创建刷题任务 |
| GET | `/api/tasks` | 查询任务列表 |
| GET | `/api/tasks/{id}/status` | 查询任务状态 |
| POST | `/api/tasks/{id}/run` | 重新执行任务 |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| GET | `/health` | 健康检查 |

## 安全说明

### 密码存储

- U校园密码使用 **Fernet (AES-128-CBC + HMAC)** 加密后存入 SQLite
- 加密密钥通过环境变量 `TASK_ENCRYPTION_KEY` 配置
- **切勿泄露 `.env` 文件**

### 访问控制

- 手机号首次使用即绑定到用户账号，不可更换
- 每个用户同时只能有一个运行中的任务
- IP 级别速率限制防止暴力破解

### HTTPS 建议

当前使用自签名证书，浏览器会提示"不安全"。生产环境建议：
- 配置域名 + Let's Encrypt 证书
- 或在内网使用 HTTP 访问（Nginx 已配置 HSTS 清除头避免缓存问题）

## 免责声明

本项目仅供学习研究使用。使用本工具可能违反 U校园AI 的服务条款，请自行承担风险。开发者不对因使用本工具导致的任何后果负责。

## License

MIT License
