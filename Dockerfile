# ========================================
# 多阶段构建 - 基础镜像
# ========================================
FROM python:3.11-slim as base

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    default-libmysqlclient-dev \
    pkg-config \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ========================================
# 依赖安装阶段
# ========================================
FROM base as dependencies

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN pip install --user --no-warn-script-location -r requirements.txt

# ========================================
# 最终运行镜像
# ========================================
FROM base as runtime

# 从依赖阶段复制安装的包
COPY --from=dependencies /root/.local /root/.local

# 确保脚本在 PATH 中
ENV PATH=/root/.local/bin:$PATH

# 复制应用代码
COPY . .

# 创建必要的目录
RUN mkdir -p /app/data /app/logs /app/static && \
    chmod -R 755 /app/data /app/logs /app/static

# 暴露端口 (从配置文件默认是 40223)
EXPOSE 40223

# 健康检查
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:40223/ || exit 1

# 启动命令
CMD ["python", "run.py"]
