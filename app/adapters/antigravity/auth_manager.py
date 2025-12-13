# app/adapters/antigravity/auth_manager.py

import time
import httpx
from typing import Dict, Optional, Callable, Awaitable
from loguru import logger
from app.core.exceptions.definitions import InvalidCredentials, ExternalServiceError
from . import constants

class AntigravityAuthManager:
    def __init__(
        self, 
        credentials: Dict, 
        proxy_url: Optional[str], 
        save_callback: Callable[[Dict], Awaitable[None]]
    ):
        self.credentials = credentials
        self.proxy_url = proxy_url
        self.save_callback = save_callback
        
        # 内存缓存
        self._access_token: Optional[str] = credentials.get("access_token")
        self._token_expiry: float = float(credentials.get("expiry", 0) or 0)
        self._project_id: Optional[str] = credentials.get("project_id") or credentials.get("projectId")

    async def get_token(self) -> str:
        """获取有效 Token，必要时刷新"""
        now = time.time()
        if self._access_token and now < self._token_expiry - 300:
            return self._access_token
        return await self._refresh_oauth_token()

    async def get_project_id(self) -> str:
        """获取 Project ID，必要时远程获取"""
        if self._project_id:
            return self._project_id
        
        token = await self.get_token()
        return await self._fetch_remote_project_id(token)

    async def _refresh_oauth_token(self) -> str:
        refresh_token = self.credentials.get("refresh_token")
        if not refresh_token:
            raise InvalidCredentials("Antigravity 模式缺少 refresh_token")

        data = {
            "client_id": constants.CLIENT_ID,
            "client_secret": constants.CLIENT_SECRET,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token
        }

        headers = {"User-Agent": constants.USER_AGENT}
        proxy = httpx.Proxy(self.proxy_url) if self.proxy_url else None

        async with httpx.AsyncClient(proxy=proxy, timeout=30.0, verify=False) as client:
            try:
                resp = await client.post(constants.GOOGLE_OAUTH2_TOKEN_URL, data=data, headers=headers)
                if resp.status_code != 200:
                    raise InvalidCredentials(f"Token 刷新失败: {resp.text}")

                token_data = resp.json()
                self._update_credentials(token_data)
                
                await self.save_callback(self.credentials)
                return self._access_token

            except httpx.RequestError as e:
                raise ExternalServiceError(f"Token 刷新网络错误: {e}")

    async def _fetch_remote_project_id(self, token: str) -> str:
        url = f"{constants.BASE_URL_DAILY}{constants.PATH_LOAD_CODE_ASSIST}"
        headers = {
            "Authorization": f"Bearer {token}",
            "User-Agent": constants.USER_AGENT,
            "Content-Type": "application/json",
            "Host": "daily-cloudcode-pa.sandbox.googleapis.com"
        }
        
        logger.info("Antigravity: Fetching real Project ID...")
        proxy = httpx.Proxy(self.proxy_url) if self.proxy_url else None

        async with httpx.AsyncClient(proxy=proxy, timeout=15.0, verify=False) as client:
            resp = await client.post(url, json={"metadata": {"ideType": "ANTIGRAVITY"}}, headers=headers)
            if resp.status_code != 200:
                raise ExternalServiceError(f"获取 Project ID 失败: {resp.text}")

            project_id = resp.json().get("cloudaicompanionProject")
            if not project_id:
                raise ExternalServiceError("账号无资格：Project ID 为空")

            self._project_id = project_id
            self.credentials["project_id"] = project_id
            await self.save_callback(self.credentials)
            
            return project_id

    def _update_credentials(self, token_data: Dict):
        self._access_token = token_data["access_token"]
        self._token_expiry = time.time() + token_data.get("expires_in", 3600)
        self.credentials["access_token"] = self._access_token
        self.credentials["expiry"] = self._token_expiry
        if "refresh_token" in token_data:
            self.credentials["refresh_token"] = token_data["refresh_token"]