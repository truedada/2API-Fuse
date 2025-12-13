# app/services/google_auth.py

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Optional
from urllib.parse import urlparse, parse_qs

from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.utils.google_oauth_api import GoogleOAuth2Helper
from app.core.config import settings
from app.schemas.google_auth import (
    GoogleAuthURLRequest, 
    GoogleAuthURLResponse,
    GoogleAuthCallbackRequest, 
    GoogleAuthCallbackResponse
)
from app.core.redis.cache import CacheService
from app.core.exceptions.definitions import NotFound, ResourceConflict, ExternalServiceError, InvalidInput

class GoogleAuthService:
    def __init__(self, channel_repo: ChannelRepository, platform_repo: PlatformRepository):
        self.channel_repo = channel_repo
        self.platform_repo = platform_repo

    def _get_google_config(self):
        """
        获取配置。
        对于 Gemini CLI 的 Client ID，强制默认回调为 localhost:8080
        """
        # 如果环境变量没有配置，这里提供默认的 Gemini CLI 配置作为兜底
        # 这是 Cloud Code (Gemini Code Assist) 的公开 Client ID
        client_id = settings.GEMINICLI_CLIENT_ID or "681255809395-oo8ft2oprdrnp9e3aqf6av3hmdib135j.apps.googleusercontent.com"
        client_secret = settings.GEMINICLI_CLIENT_SECRET or "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"
        # 必须是 localhost，且端口要与实际监听的一致，通常是 8080
        redirect_uri = settings.GOOGLE_REDIRECT_URI or "http://localhost:8080"

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri
        }

    async def create_auth_url(self, payload: GoogleAuthURLRequest) -> GoogleAuthURLResponse:
        conf = self._get_google_config()
        
        # 1. 确定 redirect_uri
        redirect_uri = conf["redirect_uri"]
            
        # 安全检查：对于此特定 Client ID，必须包含 localhost
        if "681255809395" in conf["client_id"] and "localhost" not in redirect_uri and "127.0.0.1" not in redirect_uri:
             # 如果不是 localhost，Google 会直接报错 400 Policy Error
             # 这里强制修正回 localhost:8080 以保证流程通过
             redirect_uri = "http://localhost:8080"

        
        # 3. 权限 Scope
        default_scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "openid" # 显式添加 openid
        ]

        url = GoogleOAuth2Helper.generate_auth_url(
            client_id=conf["client_id"],
            redirect_uri=redirect_uri,
            scopes=default_scopes
        )

        return GoogleAuthURLResponse(url=url)

    async def handle_callback_and_save(self, payload: GoogleAuthCallbackRequest) -> GoogleAuthCallbackResponse:
        conf = self._get_google_config()

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
            
        if "681255809395" in conf["client_id"] and "localhost" not in redirect_uri:
             redirect_uri = "http://localhost:8080"

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

        # 5. 获取项目ID并激活服务 (增加手动指定逻辑)
        final_project_id = None

        # 分支 A: 用户手动提供了 project_id
        if payload.project_id:
            final_project_id = payload.project_id
            # 尝试为指定项目激活服务 (即使激活失败也继续保存，防止权限不足导致无法保存凭证)
            try:
                await GoogleOAuth2Helper.enable_required_services(access_token, final_project_id)
            except Exception:
                pass
        
        # 分支 B: 用户未提供，执行自动检测逻辑
        else:
            try:
                projects = await GoogleOAuth2Helper.get_user_projects(access_token)
                if projects:
                    # 策略: 优先找包含 "default" 的项目，否则取第一个
                    for p in projects:
                        pid = p.get("projectId", "")
                        pname = p.get("displayName", "")
                        if "default" in pid.lower() or "default" in pname.lower():
                            final_project_id = pid
                            break
                    
                    if not final_project_id and len(projects) > 0:
                        final_project_id = projects[0].get("projectId")
                    
                    # 如果找到了项目，尝试激活 Gemini 必要服务
                    if final_project_id:
                        await GoogleOAuth2Helper.enable_required_services(access_token, final_project_id)
            except Exception:
                # 获取项目或激活服务失败不强制阻断
                pass

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
            "expiry": expiry_timestamp, 
            "project_id": final_project_id # 存储最终确定的项目ID (可能为None)
        }

        # 7. 生成渠道名称
        final_name = payload.channel_name
        if not final_name:
            ts = int(datetime.now(timezone.utc).timestamp())
            if email:
                final_name = f"{email}"
            else:
                final_name = f"UnknownEmail-{ts}"

        # 8. 查重处理
        if await self.channel_repo.exists(platform_id=payload.platform_id, name=final_name):
             if not payload.channel_name:
                 final_name = f"{final_name}-{int(datetime.now(timezone.utc).timestamp())}"
             else:
                 raise ResourceConflict(detail=f"渠道名称 '{final_name}' 已存在")

        # 9. 入库
        new_channel_data = {
            "platform_id": payload.platform_id,
            "name": final_name,
            "credentials": credentials,
            "is_active": True,
            # 更新为参考代码中的较新模型, 且符合 GCLI 的调用习惯
            "supported_models": ["gemini-2.5-pro",
        "gemini-2.5-pro-maxthinking",
        "gemini-2.5-pro-nothinking",
        "gemini-2.5-flash",
        "gemini-2.5-flash-maxthinking",
        "gemini-2.5-flash-nothinking",
        "gemini-3-pro-preview",
        "gemini-3-pro-preview-maxthinking",
        "gemini-3-pro-preview-nothinking"] ,
            "rate_limits": {
  "gemini-2.5-flash": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-2.5-flash-maxthinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-2.5-flash-nothinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-2.5-pro": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-2.5-pro-maxthinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-2.5-pro-nothinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-3-pro-preview": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-3-pro-preview-maxthinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ],
  "gemini-3-pro-preview-nothinking": [
    {
      "period": 60,
      "count": 5,
      "group": "all_1m_limit"
    },
    {
      "period": 86400,
      "count": 400,
      "group": "pro_1d_limit"
    },
    {
      "period": 86400,
      "count": 1500,
      "group": "all_1d_limit"
    }
  ]
}
        }
        
        channel = await self.channel_repo.create(**new_channel_data)
        await CacheService.sync_channel(channel.id)
        return GoogleAuthCallbackResponse(
            channel_id=channel.id,
            channel_name=channel.name,
            email=email,
            credentials_snapshot=credentials
        )