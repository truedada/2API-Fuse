# app/core/database.py

from typing import Any, Dict, List
from tortoise import Tortoise, connections
from loguru import logger
from app.core.config import settings

# 1. 定义模型列表
# 将所有需要迁移和初始化的模型在这里注册
MODELS: List[str] = [
    'aerich.models',        # 必须：Aerich 迁移工具的模型
    'app.models',    # 模型全部导出了
]

def get_tortoise_config() -> Dict[str, Any]:
    """
    构建 Tortoise ORM 配置字典。
    根据不同的 DB_TYPE 生成不同的连接配置。
    """
    
    # 默认连接配置
    connection_config: Any = None

    # --- 场景 A: 测试环境 (强制内存 SQLite) ---
    if settings.ENVIRONMENT == "test":
        connection_config = "sqlite://:memory:"
    
    # --- 场景 B: MySQL 环境 (生产/开发推荐) ---
    elif settings.DB_TYPE == "mysql":
        connection_config = {
            'engine': 'tortoise.backends.mysql',
            'credentials': {
                'host': settings.MYSQL_SERVER,
                'port': settings.MYSQL_PORT,
                'user': settings.MYSQL_USER,
                'password': settings.MYSQL_PASSWORD,
                'database': settings.MYSQL_DB,
                # 【关键配置】
                # MySQL 默认 wait_timeout 为 8 小时。
                # 如果连接池中的连接空闲时间超过该值，MySQL 会断开连接。
                # 设置 pool_recycle 小于 wait_timeout (如 60秒 或 3600秒) 
                # 可以强制回收旧连接，防止 "MySQL server has gone away" 错误。
                'pool_recycle': 60
            }
        }
    
    # --- 场景 C: SQLite 文件环境 (本地轻量开发) ---
    else:
        # 直接使用 config.py 中生成的 URL (例如: sqlite://db.sqlite3)
        connection_config = settings.DATABASE_URL

    # 构建完整的配置字典
    config = {
        "connections": {
            "default": connection_config
        },
        "apps": {
            "models": {
                "models": MODELS,
                "default_connection": "default",
            }
        },
        "use_tz": True,      # 强制使用 UTC 时区存储
        "timezone": "UTC",   
    }
    
    return config

# 2. 暴露给 Aerich 使用的配置变量
# 运行 `aerich init -t app.core.database.TORTOISE_ORM` 时会用到
TORTOISE_ORM = get_tortoise_config()

async def init_db() -> None:
    """
    应用启动时的数据库初始化逻辑
    """
    logger.info(f"正在初始化数据库 (Type: {settings.DB_TYPE}, Env: {settings.ENVIRONMENT})...")
    
    try:
        await Tortoise.init(config=TORTOISE_ORM)
        logger.info("数据库初始化完成。")
            
    except Exception as e:
        logger.critical(f"数据库初始化失败: {e}")
        # 数据库连不上是致命错误，直接抛出异常终止启动
        raise e

async def close_db() -> None:
    """
    关闭数据库连接
    """
    await connections.close_all()
    logger.info("数据库连接已关闭。")