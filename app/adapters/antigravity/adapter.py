import json
import time
import httpx
from typing import Dict, Any, AsyncGenerator, List
from loguru import logger

from app.adapters.base import BaseAdapter
from app.utils.converters.antigravity import AntigravityConverter
from app.core.exceptions.definitions import ExternalServiceError, ServiceUnavailable, InvalidCredentials
from . import constants
from .auth_manager import AntigravityAuthManager

class AntigravityAdapter(BaseAdapter):
    """
    Antigravity 适配器 (Google Cloud Code Internal API Proxy)
    """
    
    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.auth = AntigravityAuthManager(
            credentials=self.credentials,
            proxy_url=self.proxy_url,
            save_callback=self.save_credentials
        )

    def _get_fallback_base_urls(self) -> List[str]:
        """获取 Base URL 列表，优先使用 Config 配置，否则使用默认回退列表"""
        custom = self.config.get("base_url")
        if custom:
            return [custom.rstrip("/")]
        #return [constants.BASE_URL_DAILY, constants.BASE_URL_PROD]
        return constants.BASE_URL_PROD
        # 保留一个得了，麻烦死了

    async def _execute_request(self, payload: Dict, stream: bool) -> httpx.Response:
        """
        执行带回退机制的 HTTP 请求。
        手动管理 Client 生命周期，防止流式响应在返回前连接被自动关闭。
        """
        base_urls = self._get_fallback_base_urls()
        token = await self.auth.get_token()
        
        proxy = httpx.Proxy(self.proxy_url) if self.proxy_url else None
        timeout = 60.0 if stream else 300.0

        for idx, base_url in enumerate(base_urls):
            path = constants.PATH_STREAM if stream else constants.PATH_GENERATE
            url = f"{base_url}{path}" + ("?alt=sse" if stream else "")
            
            headers = {
                "Content-Type": "application/json",
                "User-Agent": constants.USER_AGENT,
                "Authorization": f"Bearer {token}",
                "Accept": "text/event-stream" if stream else "application/json"
            }

            client = httpx.AsyncClient(proxy=proxy, timeout=timeout, verify=False)

            try:
                if stream:
                    req = client.build_request("POST", url, json=payload, headers=headers)
                    response = await client.send(req, stream=True)
                else:
                    response = await client.post(url, json=payload, headers=headers)

                if 200 <= response.status_code < 300:
                    if stream:
                        # 挂载 client 以便稍后关闭
                        response._owner_client = client
                        return response
                    else:
                        await response.aread()
                        await client.aclose()
                        return response

                # --- 错误处理 ---
                logger.warning(f"Antigravity 请求失败 [{base_url}] Status: {response.status_code}")
                await client.aclose()

                # 如果是限流或服务端错误，且还有备用 URL，则重试
                #if (response.status_code == 429 or response.status_code >= 500) and idx < len(base_urls) - 1:
                    #continue
                # 两个url配额通用，没啥用。

                err_text = "" if stream else response.text
                if response.status_code == 401:
                    raise InvalidCredentials(f"无效的 Token: {err_text}")
                elif response.status_code == 429:
                    raise ServiceUnavailable(f"触发限流 ({base_url})")
                else:
                    raise ExternalServiceError(f"API Error {response.status_code}: {err_text}")

            except httpx.RequestError as e:
                await client.aclose()
                logger.warning(f"Antigravity 网络错误 [{base_url}]: {e}")
                if idx < len(base_urls) - 1:
                    continue
                raise ExternalServiceError(f"网络连接失败: {e}")
            
            except Exception as e:
                await client.aclose()
                raise e

        raise ExternalServiceError("所有 Base URL 重试均失败")

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """非流式对话补全"""
        project_id = await self.auth.get_project_id()
        
        # 使用 Converter 生成完整 Payload
        # 注意：这里的 request_data['model'] 应该是经过平台映射后的真实模型ID（包含 -maxthinking 后缀用于触发功能）
        final_payload = await AntigravityConverter.openai_to_antigravity_payload(request_data, project_id)

        response = await self._execute_request(final_payload, stream=False)

        try:
            resp_json = response.json()
            # Antigravity 可能会包裹一层 "response"
            actual_resp = resp_json.get("response", resp_json)
            logger.debug(f"Antigravity 非流返回: {resp_json}")
            # 使用 Converter 转换响应
            return AntigravityConverter.gemini_response_to_openai(actual_resp, request_data.get("model"))
        except json.JSONDecodeError:
            raise ExternalServiceError("Antigravity 响应非 JSON")

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话补全"""
        project_id = await self.auth.get_project_id()
        
        # 使用 Converter 生成完整 Payload
        final_payload = await AntigravityConverter.openai_to_antigravity_payload(request_data, project_id)

        response = await self._execute_request(final_payload, stream=True)
        request_id = f"chatcmpl-{int(time.time())}"

        try:
            async for line in response.aiter_lines():
                if not line or not line.startswith("data:"):
                    continue
                
                # 直接传入原始 line 即可，Converter 会处理
                chunk = AntigravityConverter.parse_antigravity_stream_chunk(
                    line, 
                    request_data.get("model"), 
                    request_id
                )
                
                if chunk:
                    yield f"data: {json.dumps(chunk)}\n\n"

            yield "data: [DONE]\n\n"

        finally:
            await response.aclose()
            if hasattr(response, "_owner_client"):
                await response._owner_client.aclose()

    async def fetch_models(self) -> List[str]:
        """
        获取模型列表。
        直接返回 API 提供的原始模型 ID，去除 IGNORED_MODELS。
        不再进行别名转换，转换逻辑交由数据库 model_map 配置。
        """
        base_urls = self._get_fallback_base_urls()
        token = await self.auth.get_token()
        proxy = httpx.Proxy(self.proxy_url) if self.proxy_url else None
        
        for base_url in base_urls:
            url = f"{base_url}{constants.PATH_MODELS}"
            headers = {
                "Authorization": f"Bearer {token}",
                "User-Agent": constants.USER_AGENT,
                "Content-Type": "application/json"
            }
            try:
                async with httpx.AsyncClient(proxy=proxy, timeout=10.0, verify=False) as client:
                    resp = await client.post(url, json={}, headers=headers)
                    
                    if resp.status_code == 200:
                        data = resp.json()
                        models_data = data.get("models", {})
                        
                        # 日志记录配额
                        self._log_quota_info(models_data)

                        # 返回经过过滤的原始模型 ID
                        raw_models = list(models_data.keys())
                        return [m for m in raw_models if m not in constants.IGNORED_MODELS]
                    else:
                        logger.warning(f"获取模型列表失败: {resp.status_code}")

            except Exception as e:
                logger.error(f"获取模型列表异常 [{base_url}]: {e}")
                continue
                
        return []

    def _log_quota_info(self, models_data: Dict):
        """记录配额信息的辅助方法"""
        log_lines = ["\n[Antigravity Model Quotas]"]
        log_lines.append(f"{'Model ID':<30} | {'Remaining':<10} | {'Reset Time'}")
        log_lines.append("-" * 70)
        
        for model_id, info in models_data.items():
            quota = info.get("quotaInfo", {})
            remaining = str(quota.get("remainingFraction", "N/A"))
            reset_time = quota.get("resetTime", "N/A")
            log_lines.append(f"{model_id:<30} | {remaining:<10} | {reset_time}")
        
        logger.info("\n".join(log_lines))