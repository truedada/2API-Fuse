# app/adapters/geminicli/adapter.py

import json
import time
import httpx
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
# 导入拆分后的模块
from . import constants
from .auth_manager import GeminiCliAuthManager

class GeminiCliAdapter(BaseAdapter):
    """
    Google Gemini 适配器 (GeminiCli / Cloud Code 内部接口专用)
    
    仅用于适配 GCLI 获取的凭证 (Scope: cloud-platform)。
    核心逻辑：
    1. 使用 OAuth2 refresh_token 换取 access_token
    2. 构造 Cloud Code 内部 API 请求 (v1internal)
    3. 处理特殊的 response 封包格式
    """

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        
        # === Base URL 优先级处理 ===
        # 1. 如果配置了 base_url，直接使用 (假设用户配置的是域名/代理根地址)
        # 2. 如果没配置，使用默认的 cloudcode-pa 域名
        custom_url = self.config.get("base_url")
        if custom_url:
            self.base_url = custom_url.rstrip("/")
            logger.debug(f"[GeminiCli] 使用自定义 Base URL: {self.base_url}")
        else:
            self.base_url = constants.DEFAULT_BASE_URL
            logger.debug(f"[GeminiCli] 使用默认 Base URL: {self.base_url}")
        
        # 初始化认证管理器 (接管 Token 逻辑)
        self.auth = GeminiCliAuthManager(
            credentials=self.credentials,
            proxy_url=self.proxy_url,
            save_callback=self.save_credentials
        )
        
        # 打印调试信息，确认代理配置是否生效
        if self.proxy_url:
            logger.debug(f"[GeminiCli] 初始化完成，已配置代理: {self.proxy_url}")
        else:
            logger.debug("[GeminiCli] 初始化完成，未配置代理。")

    def _get_api_url(self, action: str) -> str:
        """
        构造 GCLI 内部接口 URL
        格式: {base_url}{API_PATH_PREFIX}:{action}
        例如: https://cloudcode-pa.googleapis.com/v1internal:generateContent
        """
        return f"{self.base_url}{constants.API_PATH_PREFIX}:{action}"

    def _wrap_internal_payload(self, model: str, standard_payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        封装 Internal API 专用 Payload
        结构: { "model": "...", "project": "...", "request": {...} }
        """
        project_id = self.credentials.get("project_id")
        if not project_id:
            logger.error("[GeminiCli] 凭证中缺少 project_id")
            raise InvalidCredentials("GCLI 模式必须提供 project_id")

        # 修复: v1internal 接口通常要求使用原始 model 名称，不带 models/ 前缀。
        full_model = model
        if full_model.startswith("models/"):
            full_model = full_model.replace("models/", "")
            
        # [关键] 剥离自定义后缀 (-maxthinking, -nothinking)
        # 确保发送给 Google 内部接口的是干净的原始模型名，否则会报 404/400
        full_model = full_model.replace("-maxthinking", "").replace("-nothinking", "")

        return {
            "model": full_model,
            "project": project_id,
            "request": standard_payload
        }

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """非流式对话补全"""
        model = request_data.get("model")
        
        # 获取 Token (AuthManager 会自动处理刷新和过期)
        token = await self.auth.get_token()
        
        # 1. 使用 Converter 转换基础请求 (包含 Thinking Config 处理)
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装为 GCLI 格式 (这里会剥离后缀)
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        
        # 3. 准备 URL 和 Headers
        url = self._get_api_url("generateContent")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": constants.USER_AGENT, 
            "Authorization": f"Bearer {token}"
        }
        
        # 合并额外 Header 配置
        if self.extra_config and "headers" in self.extra_config:
            headers.update(self.extra_config["headers"])

        # 4. 发起请求
        proxy_object = Proxy(self.proxy_url) if self.proxy_url else None
        async with httpx.AsyncClient(proxy=proxy_object, timeout=300.0, verify=False) as client:
            try:
                # logger.debug(f"[GeminiCli] 发起非流式请求: {url}")
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
                logger.error(f"[GeminiCli] 请求网络错误: {e}")
                raise ExternalServiceError(f"网络连接失败: {e}")

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话补全"""
        model = request_data.get("model")
        # 获取 Token
        token = await self.auth.get_token()
        
        # 1. 转换请求
        gemini_payload = await GeminiConverter.openai_to_gemini_payload(request_data)
        
        # 2. 封装 Payload
        final_payload = self._wrap_internal_payload(model, gemini_payload)
        # logger.debug(f"[GeminiCli] 流式 Payload: {json.dumps(final_payload, ensure_ascii=False)}")

        # 3. 准备 URL (注意添加 ?alt=sse)
        url = self._get_api_url("streamGenerateContent") + "?alt=sse"
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": constants.USER_AGENT,
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

                        logger.error(f"[GeminiCli] 流式请求失败 [{response.status_code}]: {err_msg}")
                        
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
                            # 5.2 解包 response 字段
                            # 原始数据可能是: { "response": { "candidates": ... } }
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
                logger.error(f"[GeminiCli] 流式网络错误: {e}")
                raise ExternalServiceError(f"流式连接中断: {e}")

    def _handle_error(self, response: httpx.Response):
        """统一错误处理逻辑"""
        try:
            error_data = response.json()
            # GCLI 错误信息可能嵌套在 error 字段中
            error_msg = error_data.get("error", {}).get("message", response.text)
        except:
            error_msg = response.text

        logger.error(f"[GeminiCli] API Error [{response.status_code}]: {error_msg}")

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
        委托给 AuthManager 处理
        """
        return await self.auth.validate()

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