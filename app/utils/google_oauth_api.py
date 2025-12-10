from gc import enable
from loguru import logger
import httpx
from urllib.parse import urlencode
from typing import Dict, Any, List, Optional
from app.core.config import settings
from httpx import Proxy

class GoogleOAuth2Helper:
    """
    Google OAuth2 协议处理工具 (增强版)
    集成项目检测与服务激活逻辑
    """
    
    # 模拟 Gemini CLI 的 User-Agent，防止被风控
    USER_AGENT = "geminicli-oauth/1.0"

    @staticmethod
    def _get_client() -> httpx.AsyncClient:
        """获取配置了代理的 AsyncClient"""
        # 修复原代码中 ( "" or None ) 的逻辑瑕疵
        proxy = Proxy(settings.PROXY_URL) if settings.PROXY_URL else None
        return httpx.AsyncClient(
            proxy=proxy, 
            timeout=30.0,
            headers={"User-Agent": GoogleOAuth2Helper.USER_AGENT}
        )

    @staticmethod
    def generate_auth_url(client_id: str, redirect_uri: str, scopes: list[str]) -> str:
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(scopes),
            "access_type": "offline",  # 关键：获取 refresh_token
            "prompt": "consent",       # 关键：强制显示同意页
            "include_granted_scopes": "true"
        }
        auth_url = settings.GOOGLE_AUTH_URL
        return f"{auth_url}?{urlencode(params)}"

    @staticmethod
    async def exchange_code(
        client_id: str, 
        client_secret: str, 
        code: str, 
        redirect_uri: str
    ) -> Dict[str, Any]:
        """用 Code 换取 Token"""
        payload = {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": redirect_uri,
        }
        
        token_url = settings.GOOGLE_TOKEN_URL
        
        async with GoogleOAuth2Helper._get_client() as client:
            resp = await client.post(token_url, data=payload)
            #logger.debug(f"交换 Token: {resp.json()}")
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_user_info(access_token: str) -> Dict[str, Any]:
        """获取用户基本信息(邮箱)"""
        userinfo_url = settings.GOOGLE_USERINFO_URL
        
        async with GoogleOAuth2Helper._get_client() as client:
            resp = await client.get(
                userinfo_url, 
                headers={"Authorization": f"Bearer {access_token}"}
            )
            #logger.debug(f"基本信息: {resp.json()}")
            resp.raise_for_status()
            return resp.json()

    @staticmethod
    async def get_user_projects(access_token: str) -> List[Dict[str, Any]]:
        """
        获取用户活跃的 Google Cloud 项目列表
        参考: get_user_projects
        """
        url = "https://cloudresourcemanager.googleapis.com/v1/projects"
        
        async with GoogleOAuth2Helper._get_client() as client:
            resp = await client.get(
                url, 
                headers={"Authorization": f"Bearer {access_token}"}
            )
            if resp.status_code != 200:
                # 如果没有权限或出错，返回空列表，不抛出异常阻断流程
                return []
            
            data = resp.json()
            projects = data.get("projects", [])
            active_projects = [p for p in projects if p.get("lifecycleState") == "ACTIVE"]
            # 过滤出状态为 ACTIVE 的项目
            #logger.debug(f"Google Cloud 项目列表: {active_projects}")
            return active_projects

    @staticmethod
    async def enable_required_services(access_token: str, project_id: str) -> None:
        """
        为指定项目激活 Gemini 必需的 API 服务
        参考: enable_required_apis
        """
        services = [
            "geminicloudassist.googleapis.com", # Gemini Cloud Assist API
            "cloudaicompanion.googleapis.com"   # Gemini for Google Cloud API
        ]
        
        base_url = "https://serviceusage.googleapis.com/v1/projects"
        
        async with GoogleOAuth2Helper._get_client() as client:
            for service in services:
                # 1. 检查服务状态
                check_url = f"{base_url}/{project_id}/services/{service}"
                try:
                    check_resp = await client.get(
                        check_url,
                        headers={"Authorization": f"Bearer {access_token}"}
                    )
                    #logger.debug(f"Google Cloud 服务状态: {check_resp.json()}")
                    if check_resp.status_code == 200:
                        state = check_resp.json().get("state")
                        if state == "ENABLED":
                            continue # 已启用，跳过
                except Exception:
                    pass # 检查失败尝试直接启用

                # 2. 启用服务
                enable_url = f"{base_url}/{project_id}/services/{service}:enable"
                try:
                    enable_result = await client.post(
                        enable_url,
                        headers={"Authorization": f"Bearer {access_token}"},
                        json={} # Post body can be empty
                    )
                    #logger.debug(f"Google Cloud 启用结果: {enable_result.json()}")
                except Exception:
                    # 忽略启用失败，不阻断主流程
                    pass