# Docker 部署指南

本指南详细说明了如何使用 Docker 和 Docker Compose 部署 2API-Fuse 项目，涵盖不同的部署场景和常见问题解决方案。

## 📋 目录

- [快速开始](#快速开始)
- [部署场景](#部署场景)
- [配置说明](#配置说明)
- [常用命令](#常用命令)
- [数据管理](#数据管理)

---

## 🚀 快速开始

### 前置要求

- Docker >= 20.10
- Docker Compose >= 2.0
- 至少 2GB 可用内存
- 至少 5GB 可用磁盘空间

### 最简部署（仅应用，使用 SQLite）

```bash
# 1. 检查配置文件
cat .env.docker

# 2. 启动应用
docker-compose up -d

# 3. 查看日志
docker-compose logs -f app

# 4. 检查服务状态
docker-compose ps

# 5. 访问应用
# 浏览器打开: http://localhost:40223
```

---

## 📦 部署场景

### 场景 1: 单机开发环境（SQLite + FakeRedis）

**适用于**: 本地开发、快速测试、资源受限环境

```bash
# 配置文件: .env.docker 保持默认
# DB_TYPE=sqlite
# USE_REDIS=True (应用会自动使用 FakeRedis)

# 启动
docker-compose up -d

# 验证
curl http://localhost:40223/
```

**特点**:
- ✅ 启动最快，资源占用最小
- ✅ 无需外部数据库
- ✅ 数据持久化在 Docker 卷 `app-data` 中

---

### 场景 2: 生产环境（MySQL + Redis）

**适用于**: 生产环境、高并发场景、需要数据备份

#### 步骤 1: 修改配置文件

编辑 `.env.docker`:

```bash
# 数据库配置
DB_TYPE=mysql
MYSQL_SERVER=mysql          # 使用 Docker 服务名
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_strong_password_here  # Mysql 密码
MYSQL_DB=2api_db

# Redis 配置
USE_REDIS=True
REDIS_HOST=redis            # 使用 Docker 服务名
REDIS_PORT=6379
REDIS_PASSWORD=   # 没有密码就留空
REDIS_DB=0

# 安全配置
SECRET_KEY=your_secret_key_here           # ⚠️ 必须修改
ADMIN_TOKEN=your_admin_token_here         # ⚠️ 必须修改
```

#### 步骤 2: 启动完整服务栈

```bash
# 使用 full profile 启动所有服务
docker-compose --profile full up -d

# 或者分别指定 profile
docker-compose --profile mysql --profile redis up -d
```

#### 步骤 3: 验证服务

```bash
# 查看所有服务状态
docker-compose ps

# 应该看到:
# - 2api-app     (healthy)
# - 2api-mysql   (healthy)
# - 2api-redis   (healthy)

# 查看应用日志
docker-compose logs -f app

# 检查数据库连接
docker-compose exec app python -c "from app.core.database import init_db; import asyncio; asyncio.run(init_db())"
```

**特点**:
- ✅ 高性能、支持高并发
- ✅ 数据安全、支持备份恢复
- ✅ 支持水平扩展（多实例）

---

### 场景 3: 混合模式（MySQL + FakeRedis）

**适用于**: 需要持久化数据但不需要分布式缓存的场景

```bash
# .env.docker 配置
DB_TYPE=mysql
MYSQL_SERVER=mysql
USE_REDIS=True
# REDIS_HOST 可以不配置，应用会自动使用 FakeRedis

# 启动（仅启动 MySQL）
docker-compose --profile mysql up -d
```

---

### 场景 4: 使用外部数据库

**适用于**: 已有独立的 MySQL/Redis 服务

#### 步骤 1: 修改配置指向外部服务

编辑 `.env.docker`:

```bash
# 使用外部 MySQL
DB_TYPE=mysql
MYSQL_SERVER=192.168.1.100  # 外部 MySQL IP
MYSQL_PORT=3306
MYSQL_USER=apiuser
MYSQL_PASSWORD=external_password
MYSQL_DB=2api_db

# 使用外部 Redis
USE_REDIS=True
REDIS_HOST=192.168.1.101    # 外部 Redis IP
REDIS_PORT=6379
REDIS_PASSWORD=redis_password
```

#### 步骤 2: 启动应用（不启动数据库服务）

```bash
# 仅启动应用服务
docker-compose up -d app
```

#### 注意事项:

- 确保外部数据库网络可达
- 如果使用防火墙，需要开放相应端口
- 外部数据库需要手动创建数据库和用户
- 应用会自动执行数据库迁移

---

## ⚙️ 配置说明

### 环境变量文件说明

| 文件 | 用途 | 优先级 |
|------|------|--------|
| `.env.docker` | Docker 环境模板配置 | 最低 |
| `.env` | 实际使用的配置（复制自 .env.docker） | 高 |

### 关键配置项说明

#### 数据库配置

```bash
# 数据库类型选择
DB_TYPE=sqlite              # 或 mysql

# SQLite 配置（DB_TYPE=sqlite 时）
SQLITE_FILE=/app/data/db.sqlite3  # 容器内路径，已挂载到卷

# MySQL 配置（DB_TYPE=mysql 时）
MYSQL_SERVER=mysql          # Docker 内使用服务名，外部使用 IP/域名
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=password
MYSQL_DB=2api_db
```

#### Redis 配置

```bash
USE_REDIS=True              # 是否使用 Redis

# 如果 Redis 不可达，应用会自动降级到 FakeRedis
REDIS_HOST=redis            # Docker 内使用服务名，外部使用 IP/域名
REDIS_PORT=6379
REDIS_PASSWORD=             # 留空表示无密码
REDIS_DB=0
```

#### 端口配置

```bash
# 应用端口
PORT=40223                  # 可修改为其他端口

# MySQL 端口（仅在使用 Docker MySQL 时）
MYSQL_PORT=3306

# Redis 端口（仅在使用 Docker Redis 时）
REDIS_PORT=6379
```

#### 安全配置

```bash
# 环境模式
ENVIRONMENT=prod            # dev | test | prod

# 密钥（生产环境务必修改）
SECRET_KEY=your_secret_key  # 使用: openssl rand -hex 32 生成
ADMIN_TOKEN=your_token      # 管理员访问令牌
```

---

## 🛠️ 常用命令

### 服务管理

```bash
# 启动服务（前台运行，查看日志）
docker-compose up

# 启动服务（后台运行）
docker-compose up -d

# 启动特定 profile
docker-compose --profile mysql up -d
docker-compose --profile redis up -d
docker-compose --profile full up -d

# 停止服务
docker-compose stop

# 停止并删除容器（保留数据卷）
docker-compose down

# 停止并删除容器和数据卷（⚠️ 会丢失所有数据）
docker-compose down -v

# 重启服务
docker-compose restart

# 重启单个服务
docker-compose restart app
```

### 日志查看

```bash
# 查看所有服务日志
docker-compose logs

# 实时跟踪日志
docker-compose logs -f

# 查看特定服务日志
docker-compose logs app
docker-compose logs mysql
docker-compose logs redis

# 查看最近 100 行日志
docker-compose logs --tail=100 app
```

### 服务状态检查

```bash
# 查看服务状态
docker-compose ps

# 查看详细信息
docker-compose ps -a

# 检查健康状态
docker inspect 2api-app | grep -A 10 Health
docker inspect 2api-mysql | grep -A 10 Health
docker inspect 2api-redis | grep -A 10 Health
```

### 进入容器调试

```bash
# 进入应用容器
docker-compose exec app bash

# 进入 MySQL 容器
docker-compose exec mysql bash
# 或直接连接数据库
docker-compose exec mysql mysql -uroot -p

# 进入 Redis 容器
docker-compose exec redis sh
# 或直接连接 Redis
docker-compose exec redis redis-cli
```

### 镜像管理

```bash
# 重新构建镜像
docker-compose build

# 不使用缓存重新构建
docker-compose build --no-cache

# 拉取最新基础镜像
docker-compose pull

# 查看镜像
docker images | grep 2api
```

---

## 💾 数据管理

### 数据持久化说明

本项目使用 Docker 卷（Volumes）持久化数据，即使删除容器，数据也不会丢失。

| 卷名 | 容器内路径 | 内容 | 重要性 |
|------|-----------|------|--------|
| `app-data` | `/app/data` | SQLite 数据库文件 | ⚠️ 高 |
| `app-logs` | `/app/logs` | 应用日志 | 中 |
| `mysql-data` | `/var/lib/mysql` | MySQL 数据文件 | ⚠️ 高 |
| `redis-data` | `/data` | Redis 持久化数据 | 中 |

### 查看数据卷

```bash
# 列出所有卷
docker volume ls | grep 2api

# 查看卷详细信息
docker volume inspect 2api-fuse_app-data
docker volume inspect 2api-fuse_mysql-data
docker volume inspect 2api-fuse_redis-data

# 查看卷在宿主机的实际路径
docker volume inspect 2api-fuse_app-data --format '{{ .Mountpoint }}'
```

### 数据备份

#### 备份 SQLite 数据库

```bash
# 方法 1: 直接复制卷中的文件
docker cp 2api-app:/app/data/db.sqlite3 ./backup/db.sqlite3.$(date +%Y%m%d_%H%M%S)

# 方法 2: 使用临时容器备份整个 app-data 卷
docker run --rm \
  -v 2api-fuse_app-data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/app-data-$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

#### 备份 MySQL 数据库

```bash
# 导出 SQL 文件
docker-compose exec mysql mysqldump -uroot -proot 2api_db > backup/mysql_backup_$(date +%Y%m%d_%H%M%S).sql

# 备份整个 MySQL 数据卷
docker run --rm \
  -v 2api-fuse_mysql-data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/mysql-data-$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

#### 备份 Redis 数据

```bash
# 触发 Redis 保存
docker-compose exec redis redis-cli SAVE

# 备份 Redis 数据卷
docker run --rm \
  -v 2api-fuse_redis-data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar czf /backup/redis-data-$(date +%Y%m%d_%H%M%S).tar.gz -C /data .
```

### 数据恢复

#### 恢复 SQLite 数据库

```bash
# 停止应用
docker-compose stop app

# 恢复数据库文件
docker cp ./backup/db.sqlite3.20240101_120000 2api-app:/app/data/db.sqlite3

# 启动应用
docker-compose start app
```

#### 恢复 MySQL 数据库

```bash
# 方法 1: 导入 SQL 文件
docker-compose exec -T mysql mysql -uroot -proot 2api_db < backup/mysql_backup_20240101_120000.sql

# 方法 2: 恢复整个数据卷
docker-compose down
docker volume rm 2api-fuse_mysql-data
docker volume create 2api-fuse_mysql-data
docker run --rm \
  -v 2api-fuse_mysql-data:/data \
  -v $(pwd)/backup:/backup \
  alpine tar xzf /backup/mysql-data-20240101_120000.tar.gz -C /data
docker-compose --profile mysql up -d
```

### 数据迁移（SQLite → MySQL）

```bash
# 1. 导出 SQLite 数据（需要手动编写脚本或使用工具）
docker-compose exec app python scripts/export_sqlite.py > data_export.json

# 2. 修改配置为 MySQL
# 编辑 .env.docker: DB_TYPE=mysql

# 3. 启动 MySQL 服务
docker-compose --profile mysql up -d

# 4. 导入数据到 MySQL
docker-compose exec app python scripts/import_to_mysql.py < data_export.json

# 5. 验证数据
docker-compose exec app python scripts/verify_data.py
```