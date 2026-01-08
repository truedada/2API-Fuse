# 2API-Fuse

一个高性能的 AI 模型聚合网关，基于 FastAPI 构建，支持多种 AI 模型适配器的统一接入和管理。

## 核心特性

- **多模型支持**: 统一接入 OpenAI、Zai、Google Gemini 等多种 AI 模型
- **灵活部署**: 支持 SQLite/MySQL 数据库和 Redis/FakeRedis 缓存的灵活切换
- **认证授权**: 内置 Google OAuth 认证和 API Key 管理
- **高性能**: 基于 FastAPI 异步框架，支持高并发请求处理
- **易于扩展**: 模块化的适配器架构，方便添加新的 AI 模型支持
- **Docker 就绪**: 提供完整的 Docker 和 Docker Compose 部署方案

## 技术栈

- **Web 框架**: FastAPI
- **ORM**: Tortoise ORM
- **数据库**: MySQL / SQLite
- **缓存**: Redis / FakeRedis
- **日志**: Loguru
- **任务调度**: APScheduler
- **认证**: Basic Auth

## 项目结构

```
2API-Fuse/
├── app/
│   ├── adapters/          # AI 模型适配器
│   │   ├── openai/        # OpenAI 适配器
│   │   ├── qwen/          # Qwen 适配器
│   │   ├── zai/           # Zai 适配器
│   │   ├── geminicli/     # Google Gemini CLI 适配器
│   │   ├── antigravity/   # Antigravity 适配器
│   │   ├── base.py        # 适配器基类
│   │   └── factory.py     # 适配器工厂
│   ├── api/               # API 接口
│   │   └── v1/
│   │       └── endpoints/ # API 端点实现
│   │           ├── admin.py        # 管理后台
│   │           ├── chat.py         # 聊天接口
│   │           └── google_auth.py  # Google 认证
│   ├── core/              # 核心模块
│   │   ├── config.py      # 配置管理
│   │   ├── database.py    # 数据库连接
│   │   ├── exceptions/    # 异常处理
│   │   ├── logger.py      # 日志配置
│   │   ├── redis/         # Redis 连接
│   │   └── scheduler.py   # 任务调度
│   ├── models/            # 数据模型
│   ├── repositories/      # 数据访问层
│   ├── schemas/           # Pydantic 模型
│   ├── services/          # 业务逻辑层
│   └── main.py            # 应用入口
├── migrations/            # 数据库迁移文件
├── static/                # 静态资源
├── .env.example           # 环境变量示例
├── .env.docker            # Docker 环境变量模板
├── docker-compose.yml     # Docker Compose 配置
├── Dockerfile             # Docker 镜像构建文件
├── requirements.txt       # Python 依赖
└── run.py                 # 启动脚本
```

## 快速开始

### 方式一：本地开发环境

#### 前置要求

- Python 3.10+
- MySQL 5.7+ (可选，也可使用 SQLite，推荐使用 Mysql 8.0以上)
- Redis (可选，嫌麻烦也可使用 FakeRedis)

#### 安装步骤

1. 克隆项目

```bash
git clone <repository-url>
cd 2API-Fuse
```

2. 创建虚拟环境并安装依赖

```bash
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

3. 配置环境变量

```bash
cp .env.example .env
# 编辑 .env 文件，配置数据库和其他参数
```

4. 初始化数据库

```bash
# 执行数据库迁移
aerich upgrade
```

5. 启动应用

```bash
python run.py
```

应用将在 `http://localhost:40223` 启动。

### 方式二：Docker 部署

> **提示**: 详细的 Docker 部署指南请参考 [DOCKER_GUIDE.md](./DOCKER_GUIDE.md)

#### 快速启动（使用 SQLite）

```bash
# 复制环境变量文件
cp .env.docker .env

# 启动应用
docker-compose up -d

# 查看日志
docker-compose logs -f app
```

#### 生产环境部署（使用 MySQL + Redis）

```bash
# 1. 编辑 .env 文件，设置数据库和 Redis 配置
# 2. 启动完整服务栈
docker-compose --profile full up -d

# 3. 查看服务状态
docker-compose ps
```

## 配置说明

### 环境变量

主要配置项说明：

```bash
# 项目基础配置
PROJECT_NAME=2API Fuse
VERSION=0.1.0
HOST=0.0.0.0
PORT=40223
ENVIRONMENT=prod  # dev | test | prod

# 数据库配置
DB_TYPE=mysql     # sqlite | mysql

# MySQL 配置 (当 DB_TYPE=mysql 时)
MYSQL_SERVER=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password
MYSQL_DB=2api_fuse

# SQLite 配置 (当 DB_TYPE=sqlite 时)
SQLITE_FILE=data/db.sqlite3

# Redis 配置
USE_REDIS=True
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_PASSWORD=
REDIS_DB=0

# 安全配置
SECRET_KEY=your_secret_key        # 使用 openssl rand -hex 32 生成
ADMIN_TOKEN=your_admin_token      # 管理员访问令牌

# Google OAuth 配置
GEMINICLI_CLIENT_ID=your_client_id
GEMINICLI_CLIENT_SECRET=your_client_secret
GOOGLE_REDIRECT_URI=http://localhost:8081
```

完整的配置说明请查看 `.env.example` 文件。

## API 文档

启动应用后，访问以下地址查看自动生成的 API 文档：

- **Swagger UI**: http://localhost:40223/docs

### 主要 API 端点

#### 聊天接口

```http
POST /v1/chat/completions
Content-Type: application/json
Authorization: Bearer YOUR_API_KEY

{
  "model": "gpt-4",
  "messages": [
    {"role": "user", "content": "Hello"}
  ]
}
```

#### 管理接口


```http
GET /api/admin/platforms
Authorization: Bearer YOUR_ADMIN_TOKEN
```

## 支持的 AI 模型

目前支持以下 AI 模型适配器：

- **OpenAI**: OpenAI 兼容格式
- **Zai**: Zai AI 模型
- **GeminiCli**: Google Geminicli
- **Antigravity**: Google Antigravity

## 数据库管理

### 初始化迁移

此部分一般无需操心，初始化脚本会自动处理。

### 创建迁移

若数据库更新，需要手动执行以下命令

```bash
aerich migrate
```

### 应用迁移

此部分一般无需操心，迁移管理器会自动应用

```bash
aerich upgrade
```

### 回滚迁移

```bash
aerich downgrade
```

## 贡献指南

欢迎提交 Issue 和 Pull Request。自用项目，来者不拒。
## 许可证

MIT License

## 相关文档

- [Docker 部署指南](./DOCKER_GUIDE.md)
