# app/core/redis/connection.py
import redis.asyncio as redis
from typing import Optional
from loguru import logger
from app.core.config import settings

# 尝试导入 fakeredis，用于模拟 Redis 环境
try:
    from fakeredis import FakeAsyncRedis
except ImportError:
    FakeAsyncRedis = None

# --- 连接管理 ---

# 这里的类型标注主要还是 redis.Redis，因为 FakeRedis 也就是模拟了它的接口
CacheClient = redis.Redis
_client: Optional[CacheClient] = None

async def init_redis() -> CacheClient:
    global _client
    if _client is not None: 
        return _client
    
    # 场景 1: 显式配置不使用 Redis (使用 fakeredis 模拟)
    if not settings.USE_REDIS:
        if FakeAsyncRedis is None:
            logger.error("检测到 USE_REDIS=False，但未安装 fakeredis。请执行: pip install fakeredis")
            raise ImportError("依赖缺失: fakeredis")
            
        logger.warning("ENV USE_REDIS=False，使用 fakeredis 模拟内存缓存。")
        # FakeAsyncRedis 接受与 redis.Redis 类似的参数，数据存储在内存字典中
        _client = FakeAsyncRedis(
            decode_responses=True, 
            encoding='utf-8'
        )
        return _client

    # 场景 2: 尝试连接真实 Redis
    try:
        _client = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            password=settings.REDIS_PASSWORD,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            decode_responses=True,
            encoding='utf-8',
            socket_timeout=5.0
        )
        await _client.ping()
        logger.info(f"Redis连接成功: {settings.REDIS_HOST}")
        return _client
    except Exception as e:
        # 场景 3: 真实 Redis 连接失败，自动降级到 fakeredis
        logger.error(f"Redis连接失败: {e}")
        
        if FakeAsyncRedis:
            logger.warning("已自动降级为 fakeredis 内存模式，数据将暂时存储在内存中")
            _client = FakeAsyncRedis(
                decode_responses=True, 
                encoding='utf-8'
            )
            return _client
        else:
            # 如果没装 fakeredis 且连接失败，则只能抛出异常
            logger.critical("Redis 连接失败且未安装 fakeredis，无法进行降级处理")
            raise e

async def close_redis():
    global _client
    if _client:
        await _client.close()
        _client = None

# 提供给业务层调用的获取客户端函数
async def get_redis_client() -> CacheClient:
    if _client is None:
        return await init_redis()
    return _client