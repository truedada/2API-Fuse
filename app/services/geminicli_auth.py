# app/services/geminicli_auth.py
from typing import Dict, Optional, Tuple, Any
from app.core.config import settings
from app.services.base_google_oauth import BaseGoogleOAuthService
from app.utils.google_oauth_api import GoogleOAuth2Helper
from app.core.exceptions.definitions import ExternalServiceError

class GeminiCliAuthService(BaseGoogleOAuthService):
    
    def get_oauth_config(self) -> Dict[str, Any]:
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

        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "openid" # 显式添加 openid
        ]

        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "scopes": scopes
        }

    async def resolve_project_id(self, access_token: str, provided_project_id: Optional[str]) -> Optional[str]:
        final_project_id = None

        # 分支 A: 用户手动提供了 project_id
        if provided_project_id:
            final_project_id = provided_project_id
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
            except Exception as e:
                # 获取项目失败，抛出异常
                raise ExternalServiceError(
                    detail=f"无法获取 Google Cloud 项目列表，请检查账号权限。错误: {str(e)}"
                )

        # 最终检查：如果 project_id 仍然为 None，说明没有可用项目
        if not final_project_id:
            raise ExternalServiceError(
                detail="无法获取 Google Cloud Project ID。该账号可能没有可用的项目，请先在 Google Cloud Console 创建项目或提供 project_id 参数"
            )

        return final_project_id

    def get_channel_config(self) -> Tuple[list, Dict]:
        # 更新为参考代码中的较新模型, 且符合 GCLI 的调用习惯
        supported_models = [
            "gemini-2.5-pro",
            "gemini-2.5-pro-maxthinking",
            "gemini-2.5-pro-nothinking",
            "gemini-2.5-flash",
            "gemini-2.5-flash-maxthinking",
            "gemini-2.5-flash-nothinking",
            "gemini-3-pro-preview",
            "gemini-3-pro-preview-maxthinking",
            "gemini-3-pro-preview-nothinking"
        ]
        
        rate_limits = {
          "gemini-2.5-flash": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-2.5-flash-maxthinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-2.5-flash-nothinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-2.5-pro": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-2.5-pro-maxthinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-2.5-pro-nothinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-3-pro-preview": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-3-pro-preview-maxthinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ],
          "gemini-3-pro-preview-nothinking": [
            { "period": 60, "count": 5, "group": "all_1m_limit" },
            { "period": 86400, "count": 400, "group": "pro_1d_limit" },
            { "period": 86400, "count": 1500, "group": "all_1d_limit" }
          ]
        }
        
        return supported_models, rate_limits