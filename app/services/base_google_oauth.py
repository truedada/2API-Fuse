# app/services/base_google_oauth.py
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from typing import Dict, Any, Optional, Tuple

from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.utils.google_oauth_api import GoogleOAuth2Helper
from app.core.redis.cache import CacheService
from app.schemas.google_auth import (
    GoogleAuthURLRequest,
    GoogleAuthURLResponse,
    GoogleAuthCallbackRequest,
    GoogleAuthCallbackResponse
)
from app.core.exceptions.definitions import NotFound, ResourceConflict, ExternalServiceError, InvalidInput

class BaseGoogleOAuthService:
    """
    Google OAuth2 基础服务类
    封装了 GeminiCli 和 Antigravity 共有的逻辑
    """

    def __init__(self, channel_repo: ChannelRepository, platform_repo: PlatformRepository):
        self.channel_repo = channel_repo
        self.platform_repo = platform_repo

    # --- 必须由子类实现的方法 (Hooks) ---
    def get_oauth_config(self) -> Dict[str, str]:
        """返回 {'client_id': ..., 'client_secret': ..., 'redirect_uri': ..., 'scopes': [...]}"""
        raise NotImplementedError

    async def resolve_project_id(self, access_token: str, provided_project_id: Optional[str]) -> Optional[str]:
        """解析 Project ID 的具体策略 (API查询 or 自动检测)"""
        raise NotImplementedError

    def get_channel_config(self) -> Tuple[list, Dict]:
        """返回 (supported_models, rate_limits)"""
        raise NotImplementedError
    # ----------------------------------

    async def create_auth_url(self, payload: GoogleAuthURLRequest) -> GoogleAuthURLResponse:
        conf = self.get_oauth_config()
        
        # 1. 确定 redirect_uri (来自配置)
        redirect_uri = conf["redirect_uri"]
        
        # 安全检查：对于此特定 Client ID，必须包含 localhost
        # (保留原 GeminiCli 逻辑，Antigravity 通常也用 localhost)
        if "681255809395" in conf["client_id"] and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
             # 如果不是 localhost，Google 会直接报错 400 Policy Error
             # 这里强制修正回 localhost:8080 以保证流程通过
             redirect_uri = "http://localhost:8080"

        # 2. 生成 URL
        url = GoogleOAuth2Helper.generate_auth_url(
            client_id=conf["client_id"],
            redirect_uri=redirect_uri,
            scopes=conf["scopes"]
        )

        return GoogleAuthURLResponse(url=url)

    async def handle_callback_and_save(self, payload: GoogleAuthCallbackRequest) -> GoogleAuthCallbackResponse:
        conf = self.get_oauth_config()

        # --- [逻辑] 解析 URL 获取 Code ---
        try:
            parsed_url = urlparse(payload.callback_url)
            query_params = parse_qs(parsed_url.query)
            
            # 获取 code 参数
            code_list = query_params.get("code")
            if not code_list:
                raise InvalidInput(detail="提供的 URL 中未包含 'code' 参数")
            code = code_list[0]
            
            # (可选) 检查是否有 error 参数
            error_list = query_params.get("error")
            if error_list:
                raise ExternalServiceError(detail=f"Google 授权返回错误: {error_list[0]}")
                
        except Exception as e:
            if isinstance(e, (InvalidInput, ExternalServiceError)):
                raise e
            raise InvalidInput(detail=f"URL 解析失败: {str(e)}")
        # ----------------------------------------
        
        # 1. 校验平台
        platform = await self.platform_repo.get_by_id(payload.platform_id)
        if not platform:
            raise NotFound(detail=f"平台 ID {payload.platform_id} 不存在")

        # 2. 确定 redirect_uri 
        # Token 交换时的 redirect_uri 必须与生成 Auth URL 时完全一致
        redirect_uri = conf["redirect_uri"]
            
        # 特殊逻辑复用
        if "681255809395" in conf["client_id"] and "localhost" not in redirect_uri:
             redirect_uri = "http://localhost:8080"

        # Antigravity 可能的特殊回调处理 (保留原 Antigravity 逻辑)
        # 如果 parsed_url 中包含 oauth-callback，且配置允许，可能需要微调
        # 这里暂且以 get_oauth_config 返回的 redirect_uri 为准，因为它是后端生成 URL 时用的。

        # 3. 交换 Token
        try:
            token_data = await GoogleOAuth2Helper.exchange_code(
                client_id=conf["client_id"],
                client_secret=conf["client_secret"],
                code=code, # 使用从 URL 解析出的 code
                redirect_uri=redirect_uri
            )
        except Exception as e:
            raise ExternalServiceError(detail=f"Token 交换失败: {str(e)}。请检查是否在浏览器中完成了 localhost 回调，且端口与配置一致。")

        access_token = token_data.get("access_token")
        
        # 4. 获取用户信息
        email = None
        try:
            user_info = await GoogleOAuth2Helper.get_user_info(access_token)
            email = user_info.get("email")
        except Exception:
            pass

        # 5. 获取项目ID并激活服务 (调用子类实现的逻辑)
        final_project_id = await self.resolve_project_id(access_token, payload.project_id)

        # 6. 整理凭证数据
        # 计算过期时间戳 (当前UTC时间 + expires_in)
        expires_in = token_data.get("expires_in", 3599)
        current_timestamp = int(datetime.now(timezone.utc).timestamp())
        expiry_timestamp = current_timestamp + expires_in

        credentials = {
            "type": "authorized_user",
            "client_id": conf["client_id"],
            "client_secret": conf["client_secret"],
            "refresh_token": token_data.get("refresh_token"),
            "token": access_token,
            "access_token": access_token, # 冗余存储，适配不同读取习惯 (Antigravity原代码有)
            "expiry": expiry_timestamp, 
            "project_id": final_project_id # 存储最终确定的项目ID (可能为None)
        }

        # 7. 生成渠道名称
        final_name = payload.channel_name
        if not final_name:
            ts = current_timestamp
            if email:
                final_name = f"{email}"
            else:
                final_name = f"UnknownEmail-{ts}"

        # 8. 查重处理
        if await self.channel_repo.exists(platform_id=payload.platform_id, name=final_name):
             if not payload.channel_name:
                 final_name = f"{final_name}-{current_timestamp}"
             else:
                 raise ResourceConflict(detail=f"渠道名称 '{final_name}' 已存在")

        # 9. 获取模型和限速配置 (子类实现)
        supported_models, rate_limits = self.get_channel_config()

        # 10. 入库
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
        
        return GoogleAuthCallbackResponse(
            channel_id=channel.id,
            channel_name=channel.name,
            email=email,
            credentials_snapshot=credentials
        )