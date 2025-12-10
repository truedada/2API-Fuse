# app/adapters/geminicli/adapter.py

import json
import time
import httpx
# jwt 库移除，因为不再支持 Service Account
from httpx import Proxy
from typing import Dict, Any, AsyncGenerator, List, Optional
from loguru import logger
from app.core.config import settings
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
    
    # 强制指定 Base URL
    DEFAULT_BASE_URL = "https://cloudcode-pa.googleapis.com/v1internal"
    # 标准 OAuth2 Token 刷新地址
    GOOGLE_OAUTH2_TOKEN_URL = "https://oauth2.googleapis.com/token"
    
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
        # 初始化时，尝试从 credentials 中读取已有的 token 和过期时间
        self._access_token: Optional[str] = self.credentials.get("token") or self.credentials.get("access_token")
        
        # 处理 expiry，确保转为 float/int
        cred_expiry = self.credentials.get("expiry")
        self._token_expiry: float = float(cred_expiry) if cred_expiry else 0.0
        
        # 凭证解析
        self.cred_type = self._determine_credential_type()
         # 打印调试信息，确认代理配置是否生效
        if self.proxy_url:
            
            logger.debug(f"[GeminiCli] 初始化完成，已配置代理: {self.proxy_url}")
        else:
            logger.debug("[GeminiCli] 初始化完成，未配置代理。")
        
    def _determine_credential_type(self) -> str:
        """
        判断凭证类型 (严格匹配，不猜测)
        """
        creds = self.credentials
        if isinstance(creds, dict):
            # 移除 Service Account 判断
            # authorized_user 通常包含 refresh_token, client_id, client_secret
            if "refresh_token" in creds and "client_id" in creds:
                return "refresh_token"
            # 移除 API Key 判断
            # 移除 access_token 判断
        
        # 移除 字符串 API Key 判断
            
        return "unknown"

    async def _get_access_token(self) -> str:
        """
        获取有效的 Access Token，如果过期则刷新并持久化
        """
        # 移除 API Key 直接返回逻辑

        now = time.time()
        
        # 检查缓存: 如果有 token 且距离过期还有 5 分钟以上，直接使用
        if self._access_token and now < self._token_expiry - 300:
            return self._access_token

        # 刷新 Token
        new_token = None
        expires_in = 3599

        try:
            proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
            async with httpx.AsyncClient(proxy=proxy_object, timeout=30.0, verify=False) as client:
                # 移除 Service Account 分支
                # 移除 access_token 分支，只保留 refresh_token
                if self.cred_type == "refresh_token":
                    new_token, expires_in = await self._refresh_oauth_token(client)
                else:
                    raise InvalidCredentials(f"未知的凭证类型或不支持的模式: {self.cred_type}")
        except Exception as e:
            logger.error(f"GeminiAdapter: 获取 Token 失败: {e}")
            raise InvalidCredentials(f"身份验证失败: {str(e)}")

        if not new_token:
             raise InvalidCredentials("获取到的 Access Token 为空")

        # 更新内存缓存
        self._access_token = new_token
        self._token_expiry = now + expires_in
        
        # === [修复] 持久化 Token ===
        # 调用父类方法，触发 Service 层回调 -> 更新 DB 和 Redis
        if isinstance(self.credentials, dict):
            new_creds = self.credentials.copy()
            new_creds["token"] = new_token
            new_creds["expiry"] = self._token_expiry
            # 注意：refresh_token 本身可能不会变，保持原样即可
            await self.save_credentials(new_creds)
        
        return new_token

    # 移除 _refresh_service_account 方法

    async def _refresh_oauth_token(self, client: httpx.AsyncClient) -> tuple[str, int]:
        """OAuth2 Refresh Token 流程"""
        creds = self.credentials
        
        # GCLI 的 refresh_token 需要配合 client_id 和 client_secret 使用
        client_id = creds.get("client_id")
        client_secret = creds.get("client_secret")
        refresh_token = creds.get("refresh_token")

        if not refresh_token:
            raise InvalidCredentials("缺少 refresh_token，无法刷新")

        data = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token"
        }
        
        resp = await client.post(self.GOOGLE_OAUTH2_TOKEN_URL, data=data)
        
        if resp.status_code != 200:
            logger.error(f"Refresh Token 失败: {resp.text}")
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
        full_model = model
        if full_model.startswith("models/"):
            full_model = full_model.replace("models/", "")
            
        # [新增] 剥离自定义后缀 (-maxthinking, -nothinking)
        # 确保发送给 Google 内部接口的是干净的原始模型名
        full_model = full_model.replace("-maxthinking", "").replace("-nothinking", "")

        return {
            "model": full_model,
            "project": project_id,
            "request": standard_payload
        }

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """非流式对话"""
        model = request_data.get("model")
        token = await self._get_access_token()
        
        # 1. 使用 Converter 转换基础请求 (包含 Thinking Config 处理)
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装为 GCLI 格式 (这里会剥离后缀)
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        
        # 3. 准备 URL 和 Headers
        url = self._get_api_url("generateContent", stream=False)
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0", # 统一 User-Agent
            "Authorization": f"Bearer {token}"
        }
        
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
                
                # 6. 使用 Converter 转换响应
                return GeminiConverter.gemini_response_to_openai(actual_response, model)
                
            except httpx.RequestError as e:
                logger.error(f"Gemini 请求网络错误: {e}")
                raise ExternalServiceError(f"网络连接失败: {e}")

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话"""
        model = request_data.get("model")
        token = await self._get_access_token()
        
        # 1. 使用 Converter 转换基础请求 (包含 Thinking Config 处理)
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装为 GCLI 格式 (这里会剥离后缀)
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        #logger.debug(f"Gemini 流式请求头 {final_payload}")
        # 3. 准备 URL
        url = self._get_api_url("streamGenerateContent", stream=True) + "?alt=sse"
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "geminicli-oauth/1.0",
            "Authorization": f"Bearer {token}"
        }
        
        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])

        # 4. 发起请求
        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        async with httpx.AsyncClient(proxy=proxy_object, timeout=60.0, verify=False) as client:
            try:
                async with client.stream("POST", url, json=final_payload, headers=headers) as response:
                    
                    if response.status_code != 200:
                        err_text = await response.aread()
                        try:
                            err_json = json.loads(err_text.decode('utf-8', errors='ignore'))
                            err_msg = err_json.get('error', {}).get('message', err_text.decode('utf-8', errors='ignore'))
                        except:
                            err_msg = err_text.decode('utf-8', errors='ignore')

                        logger.error(f"Gemini 流式请求失败 [{response.status_code}]: {err_msg}")
                        if response.status_code in [401, 403]:
                            raise PermissionDenied(f"权限错误: {response.status_code} (请检查 Project ID 或 Scope)")
                        elif response.status_code == 429:
                            raise ServiceUnavailable(f"429 Rate Limit: {err_msg}")
                        else:
                            raise ExternalServiceError(f"上游服务错误: {response.status_code} - {err_msg}")

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
                            # 5.2 解包 response 字段 (Internal API 特性)
                            # 原始数据可能是: { "response": { "candidates": ... } }
                            # logger.debug(raw_chunk)
                            actual_chunk = raw_chunk.get("response", raw_chunk)
                            
                            # 5.3 重新构造成 SSE 行字符串，供 Converter 解析
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
        由于 internal 接口可能不支持列出模型，改为直接尝试刷新 Token。
        这能确保 Refresh Token 是实时有效的。
        """
        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        
        try:
            # 必须使用 refresh_token 模式，否则无法通过此测试
            if self.cred_type != "refresh_token":
                return False
                
            async with httpx.AsyncClient(proxy=proxy_object, timeout=10.0, verify=False) as client:
                # 强制发起刷新请求，不走缓存
                await self._refresh_oauth_token(client)
            
            return True
            
        except (InvalidCredentials, PermissionDenied):
            return False
        except Exception as e:
            logger.warning(f"凭证验证过程发生非鉴权类异常: {e}")
            return False

    async def fetch_models(self) -> List[str]:
        """
        获取可用模型列表
        [修改] 动态生成变体列表，方便前端自动发现
        """
        BASE_MODELS = [
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-3-pro-preview",
        ]
        
        final_list = []
        for base in BASE_MODELS:
            final_list.append(base) # 原始
            final_list.append(f"{base}-maxthinking") # 满血思考
            final_list.append(f"{base}-nothinking") # 禁用思考
            
        return final_list