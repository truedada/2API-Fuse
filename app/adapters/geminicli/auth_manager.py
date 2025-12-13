# app/adapters/geminicli/auth_manager.py

import time
import httpx
from typing import Dict, Optional, Callable, Awaitable
from loguru import logger
from httpx import Proxy

from app.core.exceptions.definitions import InvalidCredentials, ExternalServiceError
from . import constants

class GeminiCliAuthManager:
    """
    负责 GeminiCli 的 OAuth2 认证管理
    包括: Token 获取、刷新、过期检查、持久化回调
    """
    def __init__(
        self, 
        credentials: Dict, 
        proxy_url: Optional[str], 
        save_callback: Callable[[Dict], Awaitable[None]]
    ):
        self.credentials = credentials
        self.proxy_url = proxy_url
        self.save_callback = save_callback
        
        # 内存缓存: 初始化时尝试从传入的 credentials 读取
        self._access_token: Optional[str] = credentials.get("token") or credentials.get("access_token")
        
        # 处理 expiry，确保转为 float 类型进行比较
        cred_expiry = credentials.get("expiry")
        self._token_expiry: float = float(cred_expiry) if cred_expiry else 0.0

        # 简单的凭证类型完整性检查
        if not self._is_valid_refresh_token_cred():
            logger.warning("[GeminiCliAuth] ⚠️ 提供的凭证似乎不包含 refresh_token 或 client_id，可能无法自动刷新")

    def _is_valid_refresh_token_cred(self) -> bool:
        """检查凭证是否包含刷新所需的必要字段"""
        return "refresh_token" in self.credentials and "client_id" in self.credentials

    async def get_token(self) -> str:
        """
        获取有效的 Access Token。
        如果当前 Token 有效且未过期（预留 5 分钟缓冲），直接返回；
        否则执行刷新流程。
        """
        now = time.time()
        
        # 检查缓存: 如果有 token 且距离过期还有 5 分钟以上，直接使用
        if self._access_token and now < self._token_expiry - 300:
            return self._access_token
        
        logger.info("[GeminiCliAuth] Token 已过期或不存在，正在刷新...")
        return await self._refresh_oauth_token()

    async def validate(self) -> bool:
        """
        验证凭证有效性。
        强制执行一次刷新操作，如果成功则视为凭证有效。
        """
        # 必须是 refresh_token 模式，否则无法通过此测试
        if not self._is_valid_refresh_token_cred():
            logger.debug("[GeminiCliAuth] 凭证格式不符合 refresh_token 模式，验证失败")
            return False

        try:
            # 强制发起刷新请求，不走缓存
            await self._refresh_oauth_token()
            logger.info("[GeminiCliAuth] 凭证验证成功")
            return True
        except Exception as e:
            logger.warning(f"[GeminiCliAuth] 凭证验证失败: {e}")
            return False

    async def _refresh_oauth_token(self) -> str:
        """
        执行 OAuth2 Refresh Token 流程
        """
        client_id = self.credentials.get("client_id")
        client_secret = self.credentials.get("client_secret")
        refresh_token = self.credentials.get("refresh_token")

        if not refresh_token:
            raise InvalidCredentials("缺少 refresh_token，无法刷新")

        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }

        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        
        async with httpx.AsyncClient(proxy=proxy_object, timeout=30.0, verify=False) as client:
            try:
                logger.debug(f"[GeminiCliAuth]正在请求 Google OAuth 端点: {constants.GOOGLE_OAUTH2_TOKEN_URL}")
                resp = await client.post(constants.GOOGLE_OAUTH2_TOKEN_URL, data=data)
                
                if resp.status_code != 200:
                    logger.error(f"[GeminiCliAuth] Refresh Token 失败: {resp.text}")
                    raise InvalidCredentials(f"Refresh Token 授权失败 [{resp.status_code}]: {resp.text}")
                
                token_data = resp.json()
                
                # 更新内存状态
                self._access_token = token_data["access_token"]
                expires_in = token_data.get("expires_in", 3600)
                self._token_expiry = time.time() + expires_in
                
                # === 持久化 Token ===
                # 复制并更新 credentials 字典
                new_creds = self.credentials.copy()
                new_creds["token"] = self._access_token
                new_creds["expiry"] = self._token_expiry
                # 注意：refresh_token 本身可能不会变，保持原样即可
                
                # 调用父类/Service层传入的回调，触发 DB 和 Redis 更新
                if self.save_callback:
                    await self.save_callback(new_creds)
                
                logger.debug("[GeminiCliAuth] Token 刷新成功并已持久化")
                return self._access_token

            except httpx.RequestError as e:
                logger.error(f"[GeminiCliAuth] 网络错误: {e}")
                raise ExternalServiceError(f"身份验证网络失败: {e}")