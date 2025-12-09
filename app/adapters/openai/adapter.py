# app/adapters/openai/adapter.py
import httpx
import json
from typing import Dict, Any, AsyncGenerator, List, Optional
from loguru import logger
from app.core.exceptions.definitions import ExternalServiceError
from app.adapters.base import BaseAdapter 

class OpenAIAdapter(BaseAdapter):
    """
    OpenAI 协议标准适配器
    支持：DeepSeek-R1, OpenAI, NewAPI, OneAPI 等标准格式
    """
    
    def _build_client(self) -> httpx.AsyncClient:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.credentials.get('api_key')}"
        }  

        return httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=120.0 
        )

    # --- 核心对话接口 ---

    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        非流式请求 (Non-Streaming) - 增强版
        增加响应清洗，确保 reasoning_content 字段正确，修复客户端显示错位问题
        """
        async with self._build_client() as client:
            try:
                # 1. 强制关闭流式
                payload = request_data.copy()
                logger.debug(f"非流式请求参数: {payload}")
                payload['stream'] = False
                
                # 2. 发起请求
                response = await client.post("/v1/chat/completions", json=payload)
                
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"上游非流式错误 {response.status_code}: {error_text}")
                    raise ExternalServiceError(detail=f"上游返回状态码 {response.status_code}")
                
                # 3. 解析响应
                data = response.json()
                
                # 4. 【关键修复】标准化处理
                if "choices" in data:
                    for choice in data["choices"]:
                        message = choice.get("message", {})
                        
                        # 兼容性修复：如果存在 thinking_content，强制移动到 reasoning_content
                        if "thinking_content" in message:
                            message["reasoning_content"] = message.pop("thinking_content")
                            
                        # 确保 content 不为 None
                        if message.get("content") is None:
                             message["content"] = ""

                        # 更新回 choice
                        choice["message"] = message

                return data

            except httpx.RequestError as e:
                logger.error(f"非流式网络请求失败: {e}")
                raise ExternalServiceError(detail=f"网络请求错误: {str(e)}")
            except json.JSONDecodeError:
                logger.error("上游返回了无效的 JSON")
                raise ExternalServiceError(detail="上游返回数据格式异常")

    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """
        真·流式请求 (True Streaming) + 思考内容规范化
        """
        client = self._build_client()
        try:
            payload = request_data.copy()
            logger.debug(f"流式请求参数: {payload}")
            payload['stream'] = True

            async with client.stream("POST", "/v1/chat/completions", json=payload) as response:
                if response.status_code != 200:
                    error_text = await response.aread()
                    logger.error(f"流式请求上游错误 {response.status_code}: {error_text}")
                    raise ExternalServiceError(detail=f"上游返回状态码 {response.status_code}")

                async for line in response.aiter_lines():
                    if not line:
                        continue
                        
                    if line.startswith("data: "):
                        data = line[6:] 
                        
                        if data.strip() == "[DONE]":
                            yield "[DONE]\n\n"
                            break
                        
                        try:
                            chunk_json = json.loads(data)
                            
                            # 【核心修复】检查并透传 reasoning_content
                            if "choices" in chunk_json and len(chunk_json["choices"]) > 0:
                                delta = chunk_json["choices"][0].get("delta", {})
                                
                                if delta.get("reasoning_content"):
                                    # logger.debug(f"返回了思考块: {delta['reasoning_content'][:10]}...")
                                    pass

                            # 原样转发处理后的 JSON
                            yield f"data: {json.dumps(chunk_json, ensure_ascii=False)}\n\n"
                            
                        except json.JSONDecodeError:
                            logger.warning(f"无效的 JSON 数据块: {data}")
                            continue
                    else:
                        pass
                        
        except httpx.RequestError as e:
            logger.error(f"流式网络中断: {e}")
            raise ExternalServiceError(detail=f"流式网络错误: {str(e)}")
        finally:
            await client.aclose()

    # --- 新增：管理功能实现 ---

    async def fetch_models(self) -> List[str]:
        """
        实现获取模型列表，用于验证凭证
        """
        async with self._build_client() as client:
            try:
                response = await client.get("/v1/models")
                if response.status_code != 200:
                    raise ExternalServiceError(detail=f"获取模型列表失败: {response.status_code}")
                
                data = response.json()
                # 标准 OpenAI 格式: {"data": [{"id": "model-name", ...}]}
                if "data" in data and isinstance(data["data"], list):
                    return [item["id"] for item in data["data"] if "id" in item]
                return []
            except Exception as e:
                logger.warning(f"拉取模型列表失败: {e}")
                raise e

    async def fetch_balance(self) -> Dict[str, Any]:
        """
        尝试适配余额查询。
        原生 OpenAI 并没有简单的标准 API 来查余额。
        但 OneAPI/NewAPI 等中转程序通常提供兼容接口。
        这里尝试请求常见的订阅接口。
        """
        async with self._build_client() as client:
            try:
                # 尝试 OneAPI/NewAPI 常见路径
                # 路径 1: /dashboard/billing/subscription (OneAPI 常用)
                # 路径 2: /v1/dashboard/billing/subscription
                # 路径 3: /v1/dashboard/billing/usage
                
                # 这里的 base_url 通常包含 /v1，我们需要小心处理路径拼接
                # 假设 self.base_url 已经是以 /v1 结尾或者没有
                
                # 策略：尝试请求 /dashboard/billing/subscription
                # 很多中转站把这个做在根路径下或者 /v1 下
                
                # 尝试方案 A: 假设是 OneAPI 标准扩展接口
                response = await client.get("/dashboard/billing/subscription")
                
                if response.status_code == 404:
                    # 尝试方案 B: 加上 v1 前缀
                      response = await client.get("/v1/dashboard/billing/subscription")

                if response.status_code == 200:
                    data = response.json()
                    # OneAPI 返回格式通常包含 hard_limit_usd (总额) 和 has_remaining (余额? 不一定)
                    # 或者 hard_limit (总额), system_hard_limit_usd
                    # 以及 remaining (剩余)
                    
                    # 尽力解析
                    total = data.get("hard_limit_usd", 0.0)
                    # 如果有 remaining 字段
                    balance = data.get("remaining_amount_usd") # NewAPI 有时有这个
                    
                    if balance is None:
                         # 如果没有直接余额，可能需要通过 usage 算，这里简单处理
                         # 很多中转直接把余额放在 hard_limit_usd 里如果它是按量付费的话
                         # 这里为了兼容性，我们优先找 "balance" 或 "remaining"
                         balance = data.get("balance") or data.get("remaining") or total

                    return {
                        "balance": float(balance) if balance else 0.0,
                        "currency": "USD", # 假设是 USD
                        "raw": data
                    }
                
                # 如果都不是，抛出未实现，由 Service 捕获
                raise NotImplementedError("该渠道未通过标准接口检测到余额信息")

            except NotImplementedError:
                raise
            except Exception as e:
                logger.warning(f"余额查询失败: {e}")
                raise NotImplementedError(f"余额查询失败: {e}")