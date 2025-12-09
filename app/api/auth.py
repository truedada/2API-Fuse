# app/api/auth.py
from fastapi import Depends, Security, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from typing import Optional
from loguru import logger

from app.core.config import settings
from app.core.exceptions.definitions import (
    AuthenticationRequired, 
    PermissionDenied, 
    InvalidCredentials
)
from app.core.redis.cache import CacheService
from app.repositories.apikey import ApiKeyRepository
from app.models.apikey import ApiKey

# 定义认证方式
security_bearer = HTTPBearer(auto_error=False)
# 允许 Api Key 放在 Header: Authorization: Bearer sk-xxx
# 同时也兼容 Header: x-api-key: sk-xxx (某些客户端习惯)
api_key_header = APIKeyHeader(name="Authorization", auto_error=False)
api_key_header_alt = APIKeyHeader(name="x-api-key", auto_error=False)

# --- 1. 管理员鉴权 ---

async def verify_admin(
    creds: Optional[HTTPAuthorizationCredentials] = Security(security_bearer)
) -> str:
    """
    验证管理员 Token
    """
    if not creds:
        raise AuthenticationRequired(detail="需要管理员权限")
    
    token = creds.credentials
    # 简单比对配置中的 ADMIN_TOKEN
    if token != settings.ADMIN_TOKEN:
        raise PermissionDenied(detail="管理员密钥无效")
    
    return token

# --- 2. 用户 API Key 鉴权 ---

async def get_apikey_repo() -> ApiKeyRepository:
    return ApiKeyRepository()

async def verify_api_key(
    request: Request,
    auth_header: Optional[str] = Security(api_key_header),
    x_api_key: Optional[str] = Security(api_key_header_alt),
    repo: ApiKeyRepository = Depends(get_apikey_repo)
) -> str:
    """
    验证用户 API Key 是否有效。
    逻辑：
    1. 提取 Key (优先 Authorization: Bearer, 其次 x-api-key)
    2. 查 Redis (是否存在且 balance > 0)
    3. Redis 未命中 -> 查 DB 并回填 Redis -> 再次检查
    
    返回: 清洗后的 sk-xxx 字符串
    """
    
    # 1. 提取 Key
    api_key_str = auth_header or x_api_key
    
    if not api_key_str:
        # 兼容处理：有时 APIKeyHeader 提取的 auth_header 可能就是 Key 本身而不是 Bearer string
        # 如果 Security 提取失败，尝试手动从 headers 拿
        auth = request.headers.get("Authorization")
        if auth:
            api_key_str = auth
        else:
            raise AuthenticationRequired(detail="缺少 API Key")

    # 处理 Bearer 前缀
    if api_key_str.startswith("Bearer "):
        sk_key = api_key_str.replace("Bearer ", "").strip()
    else:
        sk_key = api_key_str.strip()

    if not sk_key:
        raise AuthenticationRequired(detail="API Key 格式错误")

    # 2. 快速检查 Redis
    # 返回: None(Miss), -1(Unlimited), >=0(Remaining Balance)
    quota = await CacheService.get_apikey_quota(sk_key)
    
    # 情况 A: Redis 中有记录
    if quota is not None:
        # 如果不是无限额度(-1) 且 余额 <= 0，则拒绝
        if quota != -1 and quota <= 0:
            raise PermissionDenied(detail="余额已耗尽")
        # 额度充足 或 无限，通过
        return sk_key

    # 情况 B: Redis 未命中 (Cache Miss)，回捞数据库 (Lazy Load)
    logger.debug(f"API Key {sk_key[:8]}... 的缓存丢失，正在从数据库加载")
    
    key_obj = await repo.get_by_key(sk_key)
    
    if not key_obj:
        # DB 也没找到 -> 无效 Key
        raise InvalidCredentials(detail="无效的 API Key")
    
    if not key_obj.is_active:
        raise PermissionDenied(detail="API Key 已被禁用")

    # 3. 检查 DB 余额 (Balance Mode)
    # balance = -1 代表无限
    if key_obj.balance != -1:
        if key_obj.balance <= 0:
            # 余额耗尽
            # 【关键】即使耗尽，也要同步到 Redis (记录为 0)，防止下次请求再次穿透到 DB
            await CacheService.sync_apikey(key_obj)
            raise PermissionDenied(detail="余额已耗尽")

    # 4. 回填 Redis (让下次请求变快)
    # 此时 key 有效且有余额 (或无限)
    await CacheService.sync_apikey(key_obj)
    
    return sk_key