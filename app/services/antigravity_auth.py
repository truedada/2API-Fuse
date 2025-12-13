# app/services/antigravity_auth.py
from typing import Dict, Any, Optional, Tuple
from app.services.base_google_oauth import BaseGoogleOAuthService
from app.utils.google_oauth_api import GoogleOAuth2Helper
from app.core.config import settings

class AntigravityAuthService(BaseGoogleOAuthService):
    # --- Antigravity Constants ---
    client_id = settings.ANTIGRAVITY_CLIENT_ID or "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
    client_secret = settings.ANTIGRAVITY_CLIENT_SECRET or "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"
        # 必须是 localhost，且端口要与实际监听的一致，通常是 8080
    redirect_uri = settings.GOOGLE_REDIRECT_URI or "http://localhost:8080"
    
    def get_oauth_config(self) -> Dict[str, Any]:
        # Antigravity 专用 Scopes
        scopes = [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/userinfo.email",
            "https://www.googleapis.com/auth/userinfo.profile",
            "https://www.googleapis.com/auth/cclog",
            "https://www.googleapis.com/auth/experimentsandconfigs",
            "openid"
        ]
        
        return {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": self.redirect_uri,
            "scopes": scopes
        }

    async def resolve_project_id(self, access_token: str, provided_project_id: Optional[str]) -> Optional[str]:
        # 6. 获取 Project ID (Antigravity 核心逻辑)
        final_project_id = provided_project_id
        if not final_project_id:
            try:
                # 调用我们移入 GoogleOAuth2Helper 的新方法
                final_project_id = await GoogleOAuth2Helper.fetch_antigravity_project_id(access_token)
            except Exception as e:
                # 即使获取失败，也不阻断保存（可能用户只想先存下来手动改）
                print(f"Antigravity Project ID fetch failed: {e}")
        return final_project_id

    def get_channel_config(self) -> Tuple[list, Dict]:
        # 10. 定义支持的模型 (包含别名)
        supported_models = [
            "gemini-2.5-pro", "gemini-2.5-pro-maxthinking", "gemini-2.5-pro-nothinking",
            "gemini-2.5-flash", "gemini-2.5-flash-maxthinking", "gemini-2.5-flash-nothinking",
            "gemini-3-pro-preview", "gemini-3-pro-preview-maxthinking", "gemini-3-pro-preview-nothinking",
            "claude-sonnet-4-5", "claude-sonnet-4-5-thinking"
        ]

        # 11. 定义限速 (参考 Gemini CLI)
        common_limit = [
            {"period": 60, "count": 5, "group": "all_1m_limit"},
            {"period": 86400, "count": 1500, "group": "all_1d_limit"}
        ]
        # 假设所有模型共享相似的限速，如果 pro 模型更严格，可单独定义
        limits = {}
        
        base_models = [
            "gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-pro-preview", 
            "claude-sonnet-4-5", "claude-sonnet-4-5-thinking"
        ]
        
        # 基础模型
        for m in base_models:
            limits[m] = common_limit
            # 变体 (如果不在 base_models 里的，可以单独加，这里简单处理)
            if f"{m}-maxthinking" in supported_models:
                limits[f"{m}-maxthinking"] = common_limit
            if f"{m}-nothinking" in supported_models:
                limits[f"{m}-nothinking"] = common_limit
        
        return supported_models, limits