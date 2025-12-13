# app/services/antigravity_auth.py

import httpx
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional

from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.utils.google_oauth_api import GoogleOAuth2Helper
from app.core.config import settings
from app.schemas.antigravity_auth import (
    AntigravityAuthURLRequest,
    AntigravityAuthURLResponse,
    AntigravityCallbackRequest,
    AntigravityCallbackResponse
)
from app.core.redis.cache import CacheService
from app.core.exceptions.definitions import NotFound, ResourceConflict, ExternalServiceError, InvalidInput

class AntigravityAuthService:
    # --- Antigravity Constants ---
    CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    CLIENT_SECRET = "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"
    # 默认回调地址，通常本地代理工具会监听这个端口
    DEFAULT_REDIRECT_URI = "http://localhost:8080"
    
    # 特定的 User Agent
    USER_AGENT = "antigravity/1.11.9 windows/amd64"

    def __init__(self, channel_repo: ChannelRepository, platform_repo: PlatformRepository):
        self.channel_repo = channel_repo
        self.platform_repo = platform_repo

    async def create_auth_url(self, payload: AntigravityAuthURLRequest) -> AntigravityAuthURLResponse:
        redirect_uri = payload.redirect_uri or self.DEFAULT_REDIRECT_URI
        
        # Antigravity 专用 Scopes
        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
            "openid"
        ]

        url = GoogleOAuth2Helper.generate_auth_url(
            client_id=self.CLIENT_ID,
            redirect_uri=redirect_uri,
            scopes=scopes
        )
        return AntigravityAuthURLResponse(url=url)

    async def handle_callback_and_save(self, payload: AntigravityCallbackRequest) -> AntigravityCallbackResponse:
        # 1. 解析 URL 获取 Code
        try:
            parsed_url = urlparse(payload.callback_url)
            query_params = parse_qs(parsed_url.query)
            code = query_params.get("code", [None])[0]
            if not code:
                raise InvalidInput(detail="URL 中缺少 code 参数")
        except Exception as e:
            raise InvalidInput(detail=f"URL 解析失败: {str(e)}")

        # 2. 校验平台
        platform = await self.platform_repo.get_by_id(payload.platform_id)
        if not platform:
            raise NotFound(detail=f"平台 ID {payload.platform_id} 不存在")

        # 3. 确定 redirect_uri (必须与生成 URL 时一致，通常通过 URL 判断或使用默认)
        # 简单的逻辑：如果 payload.callback_url 是 localhost:xxxx，则截取 origin
        redirect_uri = self.DEFAULT_REDIRECT_URI
        if parsed_url.port:
             redirect_uri = f"{parsed_url.scheme}://{parsed_url.hostname}:{parsed_url.port}"
        # 特殊处理：如果 URL 包含 oauth-callback 路径，需要去掉还是保留取决于 Google 配置
        # 通常 Antigravity 是 http://localhost:<port> 直接回调，或者 /oauth-callback
        # 这里假设 Google 后台配置的是 http://localhost:<port>/oauth-callback
        if parsed_url.path == '/oauth-callback':
            redirect_uri += '/oauth-callback'

        # 4. 交换 Token
        try:
            token_data = await GoogleOAuth2Helper.exchange_code(
                client_id=self.CLIENT_ID,
                client_secret=self.CLIENT_SECRET,
                code=code,
                redirect_uri=redirect_uri
            )
        except Exception as e:
            raise ExternalServiceError(detail=f"Token 交换失败: {str(e)}")

        access_token = token_data.get("access_token")

        # 5. 获取 Email
        email = None
        try:
            user_info = await GoogleOAuth2Helper.get_user_info(access_token)
            email = user_info.get("email")
        except Exception:
            pass

        # 6. 获取 Project ID (Antigravity 核心逻辑)
        final_project_id = payload.project_id
        if not final_project_id:
            try:
                final_project_id = await self._fetch_antigravity_project_id(access_token)
            except Exception as e:
                # 即使获取失败，也不阻断保存（可能用户只想先存下来手动改）
                print(f"Antigravity Project ID fetch failed: {e}")

        # 7. 构造凭证
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        expires_in = token_data.get("expires_in", 3599)
        
        credentials = {
            "type": "authorized_user", # 保持通用结构
            "client_id": self.CLIENT_ID,
            "client_secret": self.CLIENT_SECRET,
            "refresh_token": token_data.get("refresh_token"),
            "token": access_token, # access_token
            "access_token": access_token, # 冗余存储，适配不同读取习惯
            "expiry": current_timestamp + expires_in,
            "project_id": final_project_id
        }

        # 8. 生成渠道名
        final_name = payload.channel_name
        if not final_name:
            ts = current_timestamp
            if email:
                final_name = f"{email}"
            else:
                final_name = f"UnknownEmail-{ts}"

        # 9. 查重
        if await self.channel_repo.exists(platform_id=payload.platform_id, name=final_name):
            if not payload.channel_name:
                final_name = f"{final_name}-{current_timestamp}"
            else:
                raise ResourceConflict(detail=f"渠道名称 '{final_name}' 已存在")

        # 10. 定义支持的模型 (包含别名)
        supported_models = [
            "gemini-2.5-pro", "gemini-2.5-pro-maxthinking", "gemini-2.5-pro-nothinking",
            "gemini-2.5-flash", "gemini-2.5-flash-maxthinking", "gemini-2.5-flash-nothinking",
            "gemini-3-pro-preview", "gemini-3-pro-preview-maxthinking", "gemini-3-pro-preview-nothinking",
            "claude-sonnet-4-5", "claude-sonnet-4-5-thinking"
        ]

        # 11. 定义限速 (参考 Gemini CLI)
        rate_limits = self._get_default_rate_limits()

        # 12. 入库
        new_channel_data = {
            "platform_id": payload.platform_id,
            "name": final_name,
            "credentials": credentials,
            "is_active": True,
            "supported_models": supported_models,
            "rate_limits": rate_limits
        }

        channel = await self.channel_repo.create(**new_channel_data)
        await CacheService.sync_channel(channel.id)

        return AntigravityCallbackResponse(
            channel_id=channel.id,
            channel_name=channel.name,
            email=email,
            credentials_snapshot=credentials
        )

    async def _fetch_antigravity_project_id(self, access_token: str) -> Optional[str]:
        """
        调用 Internal API 获取真实的 Project ID
        逻辑源自: v1internal:loadCodeAssist
        """
        url = "https://daily-cloudcode-pa.sandbox.googleapis.com/v1internal:loadCodeAssist"
        headers = {
            "Authorization": f"Bearer {access_token}",
            "User-Agent": self.USER_AGENT,
            "Content-Type": "application/json",
            "Host": "daily-cloudcode-pa.sandbox.googleapis.com"
        }
        data = {"metadata": {"ideType": "ANTIGRAVITY"}}

        async with httpx.AsyncClient(timeout=10.0, verify=False) as client:
            resp = await client.post(url, json=data, headers=headers)
            if resp.status_code == 200:
                json_data = resp.json()
                return json_data.get("cloudaicompanionProject")
            else:
                raise ExternalServiceError(f"LoadCodeAssist failed: {resp.status_code} {resp.text}")

    def _get_default_rate_limits(self):
        """生成默认限速配置"""
        common_limit = [
            {"period": 60, "count": 5, "group": "all_1m_limit"},
            {"period": 86400, "count": 1500, "group": "all_1d_limit"}
        ]
        # 假设所有模型共享相似的限速，如果 pro 模型更严格，可单独定义
        limits = {}
        
        models = [
            "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-pro-preview", 
            "claude-sonnet-4-5", "claude-sonnet-4-5-thinking"
        ]
        
        # 基础模型
        for m in models:
            limits[m] = common_limit
            # 变体
            limits[f"{m}-maxthinking"] = common_limit
            limits[f"{m}-nothinking"] = common_limit
            
        return limits