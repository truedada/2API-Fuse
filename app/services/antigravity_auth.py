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
            # --- Gemini 2.5 Pool ---
            "gemini-2.5-flash", "gemini-2.5-flash-maxthinking",
            
            # --- Gemini 3.0 Pool ---
            "gemini-3-pro-preview", "gemini-3-pro-preview-maxthinking",
            
            # --- Computer Use Pool (Internal ID: rev19-uic3-1p) ---
            "gemini-2.5-computer-use-preview",
            
            # --- Claude / GPT Pool (High Cost) ---
            "claude-sonnet-4-5", "claude-sonnet-4-5-thinking",
            "gpt-oss-120b",
            # 香蕉
            "gemini-3-pro-image-preview"
        ]

        # 11. 定义限速 (Pool Based Quota)
        # 默认重置周期：5小时 (18000秒)
        reset_period = 18000 
        
        # 定义各池子的限额策略 (Count)
        # 这里的 group 决定了共享配额的范围
        
        # Pool A: Gemini 2.5 (Cost ~0.00033 -> 3000次)
        limit_pool_2_5 = [
            {"period": reset_period, "count": 3000, "group": "pool_2_5"}
        ]
        
        # Pool B: Gemini 3.0 (Cost ~0.0025 -> 400次)
        limit_pool_3_0 = [
            {"period": reset_period, "count": 400, "group": "pool_3_0"}
        ]
        
        # Pool C: Computer Use (Cost 0.002 -> 500次)
        limit_pool_computer = [
            {"period": reset_period, "count": 500, "group": "pool_computer_use"}
        ]
        
        # Pool D: Claude / Premium (Cost 0.004 -> 250次)
        limit_pool_claude = [
            {"period": reset_period, "count": 250, "group": "pool_claude"}
        ]
        # Pool E: Banana (20次)
        limit_pool_banana = [
            {"period": reset_period, "count": 20, "group": "pool_banana"}
        ]
        limits = {}
        
        for model in supported_models:
            model_lower = model.lower()
            
            # 1. 匹配 Gemini 3.0
            if "gemini-3" in model_lower and "image" not in model_lower:
                limits[model] = limit_pool_3_0
                
            # 2. 匹配 Computer Use (优先于 2.5 匹配)
            elif "computer-use" in model_lower or "uic3" in model_lower:
                limits[model] = limit_pool_computer
                
            # 3. 匹配 Gemini 2.5
            elif "gemini-2.5" in model_lower:
                limits[model] = limit_pool_2_5
                
            # 4. 匹配 Claude / GPT (High Cost Pool)
            elif "claude" in model_lower or "gpt" in model_lower:
                limits[model] = limit_pool_claude
            
            # 4. 匹配 Banana
            elif "image" in model_lower:
                limits[model] = limit_pool_banana

            # 5. 兜底
            else:
                limits[model] = limit_pool_claude
        
        return supported_models, limits