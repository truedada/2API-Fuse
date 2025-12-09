# app/core/config.py

from typing import Annotated, Any, Literal, List, Optional
from pydantic import (
    AnyUrl,
    BeforeValidator,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

# --- 辅助函数：解析 CORS 字符串 ---
def parse_cors(v: Any) -> List[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    raise ValueError(v)

class Settings(BaseSettings):
    # Pydantic V2 配置
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore", # 忽略环境变量中多余的字段，防止报错
        env_file_encoding='utf-8'
    )

    # --- 1. 项目基础配置 ---
    PROJECT_NAME: str = "2API Fuse"
    VERSION: str = "0.1.0"
    DESCRIPTION: str = "A high-performance gateway for 2API AI model aggregation."
    
    HOST: str = "0.0.0.0"
    PORT: int = 40223

    # 使用 Literal 强制类型检查，防止手滑写错环境名
    ENVIRONMENT: Literal["dev", "test", "prod"] = "dev"
    DEBUG: bool = False

    # 跨域配置 (支持环境变量传 "http://a.com,http://b.com")
    BACKEND_CORS_ORIGINS: Annotated[
        List[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    # --- 2. 安全配置 (Admin/JWT) ---
    # 生产环境务必在 .env 中设置强密码
    SECRET_KEY: str = "2f0e33bc7fe570550e207cdeca01a8851ad8ec9c88e480b1a171f29aae77f531"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 8  # 8 days

    # --- 3. 数据库配置 (支持 SQLite/MySQL 切换) ---
    DB_TYPE: Literal["sqlite", "mysql"] = "mysql"
    
    # SQLite 配置
    SQLITE_FILE: str = "db.sqlite3"
    
    # MySQL 配置
    MYSQL_SERVER: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DB: str = "2api_fuse"

    ADMIN_TOKEN: str = "123456"
    # 【关键优化】自动计算数据库连接字符串
    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        if self.DB_TYPE == "sqlite":
            return f"sqlite://{self.SQLITE_FILE}"
        
        # MySQL URL 构建
        user = self.MYSQL_USER
        pwd = self.MYSQL_PASSWORD
        host = self.MYSQL_SERVER
        port = self.MYSQL_PORT
        db = self.MYSQL_DB
        
        # Tortoise ORM / SQLAlchemy 通常格式: mysql://user:pass@host:port/db
        if pwd:
            return f"mysql://{user}:{pwd}@{host}:{port}/{db}"
        return f"mysql://{user}@{host}:{port}/{db}"

    # --- 4. Redis 配置 ---
    USE_REDIS: bool = True 
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: Optional[str] = None
    REDIS_DB: int = 0
    REDIS_MAX_CONNECTIONS: int = 20

    @computed_field
    @property
    def REDIS_URL(self) -> str:
        """生成标准的 redis:// 连接字符串"""
        if not self.USE_REDIS:
            return ""
        
        auth_part = f":{self.REDIS_PASSWORD}@" if self.REDIS_PASSWORD else ""
        return f"redis://{auth_part}{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"

# 实例化
settings = Settings()