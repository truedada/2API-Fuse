# app/core/config.py

from typing import Annotated, Any, Literal, List, Optional
from pydantic import (
    AnyUrl,
    BeforeValidator,
    computed_field,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

def parse_cors(v: Any) -> list[str] | str:
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
    SQLITE_FILE: str = "data/db.sqlite3"
    
    # MySQL 配置
    MYSQL_SERVER: str = "localhost"
    MYSQL_PORT: int = 3306
    MYSQL_USER: str = "root"
    MYSQL_PASSWORD: str = "123456"
    MYSQL_DB: str = "2api_fuse"

    ADMIN_TOKEN: str

    PROXY_URL: str = ""

    GOOGLE_AUTH_URL: str = "https://accounts.google.com/o/oauth2/auth"
    GOOGLE_TOKEN_URL: str = "https://oauth2.googleapis.com/token"
    GOOGLE_USERINFO_URL: str = "https://www.googleapis.com/oauth2/v3/userinfo"

    GEMINICLI_CLIENT_ID: str = "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com" 
    GEMINICLI_CLIENT_SECRET: str = "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"

    ANTIGRAVITY_CLIENT_ID : str = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    ANTIGRAVITY_CLIENT_SECRET: str = "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"

    GOOGLE_REDIRECT_URI: str = "http://localhost:8081" # 默认回调地址

    GOOGLE_SCOPES : List = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    USE_CLOUDFLARE : bool = False
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