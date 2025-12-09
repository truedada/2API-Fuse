import httpx
from loguru import logger
from typing import Dict, Any, AsyncGenerator
from app.adapters.base import BaseAdapter
# 引入自定义异常，方便在适配器层抛出统一错误
from app.core.exceptions.definitions import ExternalServiceError # 假设你在 definition 里加了这个，或者用 ServiceUnavailable

class QwenAdapter(BaseAdapter):
    def _get_headers(self) -> Dict[str, str]:
        api_key = self.credentials.get("api_key", "")
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/chat/completions"
        
        async with httpx.AsyncClient(proxy=self.proxy, timeout=60.0) as client:
            try:
                resp = await client.post(
                    url, 
                    json=request_data, 
                    headers=self._get_headers()
                )
                if resp.status_code != 200:
                    # 可以在这里解析上游的错误信息并抛出
                    raise Exception(f"Upstream Error: {resp.text}")
                return resp.json()
            except Exception as e:
                logger.error(f"Qwen API Error: {e}")
                # 抛出系统识别的异常，会被 Handler 捕获
                raise e 

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        request_data["stream"] = True
        url = f"{self.base_url}/chat/completions"
        
        async with httpx.AsyncClient(proxy=self.proxy, timeout=60.0) as client:
            async with client.stream("POST", url, json=request_data, headers=self._get_headers()) as resp:
                if resp.status_code != 200:
                    yield f"data: {{\"error\": \"Upstream {resp.status_code}\"}}\n\n"
                    return

                async for line in resp.aiter_lines():
                    if line:
                        yield f"{line}\n"