# app/adapters/geminicli/adapter.py

import json
import time
import httpx
import jwt  # 需要 pyjwt[crypto]
from httpx import Proxy
from typing import Dict, Any, AsyncGenerator, List, Optional
from loguru import logger

from app.adapters.base import BaseAdapter
from app.utils.converters.gemini import GeminiConverter
from app.core.exceptions.definitions import (
    ExternalServiceError,
    PermissionDenied,
    ServiceUnavailable,
    InvalidCredentials
)

class GeminiCliAdapter(BaseAdapter):
    """
    Google Gemini 适配器 (GeminiCli / Cloud Code 内部接口专用)
    
    仅用于适配 GCLI 获取的凭证 (Scope: cloud-platform)。
    强制使用 cloudcode-pa.googleapis.com/v1internal 端点。
    """

    # Google OAuth2 Token 端点
    GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
    
    # 强制指定 Base URL
    DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
    
    # GCLI 凭证所需的 Scopes
    SCOPES = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ]

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # 强制覆盖 base_url，防止配置错误导致 403
        self.base_url = self.DEFAULT_BASE_URL
        
        # 缓存 Access Token 及其过期时间
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0
        
        # 凭证解析
        self.cred_type = self._determine_credential_type()
        
    def _determine_credential_type(self) -> str:
        """
        判断凭证类型 (严格匹配，不猜测)
        """
        creds = self.credentials
        if isinstance(creds, dict):
            if "private_key" in creds and "client_email" in creds:
                return "service_account"
            elif "refresh_token" in creds and "client_id" in creds:
                return "refresh_token"
            elif "api_key" in creds or "key" in creds:
                return "api_key"
            # 适配 GCLI 临时凭证 (直接传入 token)
            elif "token" in creds or "access_token" in creds:
                return "access_token"
        
        # 仅当明确传入字符串时视为 API Key
        if isinstance(creds, str):
            self.credentials = {"api_key": creds}
            return "api_key"
            
        return "unknown"

    async def _get_access_token(self) -> str:
        """
        获取有效的 Access Token
        """
        if self.cred_type == "api_key":
            return self.credentials.get("api_key") or self.credentials.get("key")

        # 检查缓存
        now = time.time()
        if self._access_token and now < self._token_expiry - 300:
            return self._access_token

        # 刷新 Token
        new_token = None
        expires_in = 3599

        try:
            proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
            async with httpx.AsyncClient(proxy=proxy_object, timeout=30.0, verify=False) as client:
                if self.cred_type == "service_account":
                    new_token, expires_in = await self._refresh_service_account(client)
                elif self.cred_type == "refresh_token":
                    new_token, expires_in = await self._refresh_oauth_token(client)
                elif self.cred_type == "access_token":
                    # 直接使用传入的 access_token (无法刷新，依赖外部管理)
                    new_token = self.credentials.get("token") or self.credentials.get("access_token")
                    expires_in = self.credentials.get("expires_in", 3600)
                else:
                    raise InvalidCredentials(f"未知的凭证类型: {self.cred_type}")
        except Exception as e:
            logger.error(f"GeminiAdapter: 获取 Token 失败: {e}")
            raise InvalidCredentials(f"身份验证失败: {str(e)}")

        if not new_token:
             raise InvalidCredentials("获取到的 Access Token 为空")

        # 更新缓存
        self._access_token = new_token
        self._token_expiry = now + expires_in
        
        return new_token

    async def _refresh_service_account(self, client: httpx.AsyncClient) -> tuple[str, int]:
        """Service Account JWT 签名流程"""
        creds = self.credentials
        now = int(time.time())
        
        payload = {
            "iss": creds["client_email"],
            "sub": creds["client_email"],
            "aud": self.GOOGLE_TOKEN_URL,
            "iat": now,
            "exp": now + 3600,
            "scope": " ".join(self.SCOPES)
        }
        
        try:
            encoded_jwt = jwt.encode(payload, creds["private_key"], algorithm="RS256")
        except Exception as e:
            raise InvalidCredentials(f"JWT 签名失败: {e}")

        data = {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": encoded_jwt
        }
        
        resp = await client.post(self.GOOGLE_TOKEN_URL, data=data)
        
        if resp.status_code != 200:
            raise InvalidCredentials(f"Service Account 授权失败 [{resp.status_code}]: {resp.text}")
            
        token_data = resp.json()
        return token_data["access_token"], token_data.get("expires_in", 3600)

    async def _refresh_oauth_token(self, client: httpx.AsyncClient) -> tuple[str, int]:
        """OAuth2 Refresh Token 流程"""
        creds = self.credentials
        data = {
            "client_id": creds.get("client_id"),
            "client_secret": creds.get("client_secret"),
            "refresh_token": creds.get("refresh_token"),
            "grant_type": "refresh_token"
        }
        
        resp = await client.post(self.GOOGLE_TOKEN_URL, data=data)
        
        if resp.status_code != 200:
            raise InvalidCredentials(f"Refresh Token 授权失败 [{resp.status_code}]: {resp.text}")
            
        token_data = resp.json()
        return token_data["access_token"], token_data.get("expires_in", 3600)

    def _get_api_url(self, action: str, stream: bool = False) -> str:
        """
        构造 GCLI 内部接口 URL
        格式: {base_url}:{action} (不包含 model name)
        """
        base = self.base_url.rstrip("/")
        # streamGenerateContent 或 generateContent
        return f"{base}:{action}"

    def _wrap_internal_payload(self, model: str, standard_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        封装 Internal API 专用 Payload
        结构: { "model": "...", "project": "...", "request": {...} }
        """
        project_id = self.credentials.get("project_id")
        if not project_id:
            raise InvalidCredentials("GCLI 模式必须提供 project_id")

        # 修复: v1internal 接口通常要求使用原始 model 名称，不带 models/ 前缀。
        # 如果传入 gemini-2.5-flash，直接使用 gemini-2.5-flash。
        # 如果传入 models/gemini-2.5-flash，尝试去除前缀（或者保持原样，视具体情况而定，但通常 GCLI 是裸名称）。
        # 这里为了稳妥，如果已经是 models/ 开头，则不做处理（兼容性），否则不加前缀。
        # 但根据报错日志，强制加前缀会导致 404，所以这里我们优先使用不带前缀的名称。
        # 许多 2api 实现中，model 字段直接透传。
        
        full_model = model
        # 如果上游传过来已经带了 models/，针对 GCLI 内部接口，可能需要 strip 掉，或者保留。
        # 参考其他实现，通常直接透传 payload.get("model")。
        # 为了修复 404，我们这里不强制添加 f"models/{model}"。

        return {
            "model": full_model,
            "project": project_id,
            "request": standard_payload
        }

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """非流式对话"""
        model = request_data.get("model")
        token = await self._get_access_token()
        
        # 1. 使用 Converter 转换基础请求
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装为 GCLI 格式
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        
        # 3. 准备 URL 和 Headers
        url = self._get_api_url("generateContent", stream=False)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0" # 统一 User-Agent
        }
        
        if self.cred_type == "api_key":
            headers["x-goog-api-key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        
        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])

        # 4. 发起请求
        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        async with httpx.AsyncClient(proxy=proxy_object, timeout=300.0, verify=False) as client:
            try:
                response = await client.post(url, json=final_payload, headers=headers)
                
                if response.status_code != 200:
                    self._handle_error(response)
                
                resp_json = response.json()
                
                # 5. 解包 response 字段 (Internal API 特性)
                # 格式通常为: { "response": { "candidates": [...] } }
                actual_response = resp_json.get("response", resp_json)
                #logger.info(f"非流返回: {resp_json} ")
                
                # 6. 使用 Converter 转换响应
                return GeminiConverter.gemini_response_to_openai(actual_response, model)
                
            except httpx.RequestError as e:
                logger.error(f"Gemini 请求网络错误: {e}")
                raise ExternalServiceError(f"网络连接失败: {e}")

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话"""
        model = request_data.get("model")
        token = await self._get_access_token()
        
        # 1. 使用 Converter 转换基础请求
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装为 GCLI 格式
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        
        # 3. 准备 URL
        url = self._get_api_url("streamGenerateContent", stream=True) + "?alt=sse"
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0"
        }
        
        if self.cred_type == "api_key":
            headers["x-goog-api-key"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        
        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])

        # 4. 发起请求
        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        async with httpx.AsyncClient(proxy=proxy_object, timeout=60.0, verify=False) as client:
            try:
                async with client.stream("POST", url, json=final_payload, headers=headers) as response:
                    
                    if response.status_code != 200:
                        err_text = await response.aread()
                        err_json = json.loads(err_text.decode('utf-8', errors='ignore'))
                        logger.error(f"Gemini 流式请求失败 [{response.status_code}]: {err_json.get('error', {}).get('message', err_text.decode('utf-8', errors='ignore'))}")
                        if response.status_code in [401, 403]:
                            raise PermissionDenied(f"权限错误: {response.status_code} (请检查 Project ID 或 Scope)")
                        elif response.status_code == 429:
                            error_msg = err_json.get('error', {}).get('message', "请求过多 (Rate Limit)")
                            raise ServiceUnavailable(f"429 Rate Limit: {error_msg}")
                        else:
                            raise ExternalServiceError(f"上游服务错误: {response.status_code}")

                    request_id = f"chatcmpl-{int(time.time())}"
                    
                    # 5. 解析 SSE
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        
                        chunk_str = line[6:].strip()
                        if chunk_str == "[DONE]":
                            break
                            
                        try:
                            # 5.1 解析原始 JSON
                            raw_chunk = json.loads(chunk_str)
                            #logger.info(f"流式Chunk: {raw_chunk}")
                            # 5.2 解包 response 字段 (Internal API 特性)
                            # 原始数据可能是: { "response": { "candidates": ... } }
                            actual_chunk = raw_chunk.get("response", raw_chunk)
                            
                            # 5.3 重新构造成 SSE 行字符串，供 Converter 解析
                            # Converter parse_gemini_stream_chunk 接收的是 bytes 类型的整行数据
                            reconstructed_line = f"data: {json.dumps(actual_chunk)}"
                            
                            # 5.4 调用 Converter
                            chunk = GeminiConverter.parse_gemini_stream_chunk(
                                reconstructed_line.encode('utf-8'), 
                                model, 
                                request_id
                            )
                            
                            if chunk:
                                yield f"data: {json.dumps(chunk)}\n\n"
                                
                        except json.JSONDecodeError:
                            continue
                    
                    yield "data: [DONE]\n\n"
                    
            except httpx.RequestError as e:
                logger.error(f"Gemini 流式网络错误: {e}")
                raise ExternalServiceError(f"流式连接中断: {e}")

    def _handle_error(self, response: httpx.Response):
        """统一错误处理"""
        try:
            error_data = response.json()
            # GCLI 错误信息可能嵌套在 error 字段中
            error_msg = error_data.get("error", {}).get("message", response.text)
        except:
            error_msg = response.text

        logger.error(f"Gemini API Error [{response.status_code}]: {error_msg}")

        if response.status_code == 400:
            raise ExternalServiceError(f"请求参数错误: {error_msg}")
        elif response.status_code == 401:
            raise InvalidCredentials(f"无效的 Token: {error_msg}")
        elif response.status_code == 403:
            raise PermissionDenied(f"权限不足: {error_msg} (确认 Project ID 绑定正确)")
        elif response.status_code == 404:
            raise ExternalServiceError(f"接口不存在或模型未找到: {error_msg} (请检查模型名称)")
        elif response.status_code == 429:
            raise ServiceUnavailable(f"触发限流: {error_msg}")
        elif response.status_code >= 500:
            raise ExternalServiceError(f"Google 服务器内部错误: {error_msg}")
        else:
            raise ExternalServiceError(f"未知错误 [{response.status_code}]: {error_msg}")

    async def validate_credential(self) -> bool:
        """
        验证凭证有效性
        由于 internal 接口可能不支持列出模型，通过发送一个最小 token 请求来验证。
        """
        try:
            await self._refresh_service_account(httpx.AsyncClient())
            return True
        except (InvalidCredentials, PermissionDenied):
            return False
        except Exception as e:
            logger.warning(f"凭证验证过程发生非鉴权类异常: {e}")
            # 如果是网络错误等，暂认为凭证本身可能是好的，但在 connectivity 上有问题
            # 这里保守返回 False
            return False

    async def fetch_models(self) -> List[str]:
        """
        获取可用模型列表
        Internal API 不提供标准的模型列表接口，尝试访问但预期会失败。
        """
        BASE_MODELS = [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-3-pro-preview",
        ]
        # Internal Endpoint 通常不暴露 models list，这里保留接口定义
        # 如果需要支持，可能需要手动维护一个支持列表，或者尝试调用 list_models API (如果存在)
        return BASE_MODELS