import json
import time
import httpx
from datetime import datetime
from typing import Dict, Any, AsyncGenerator, List, Optional
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
    
    # 定义配额池映射 (参考 Auth Service 逻辑)
    # 格式: Group Name -> { Limit, Period, Reference Model ID }
    # 使用 Reference Model ID 在 API 返回的模型列表中查找对应的 Quota Info
    # 【关键】这里的 Key (如 pool_2_5) 必须与数据库中配置的 "group" 字段完全一致！
    QUOTA_POOLS = {
        "pool_2_5": {
            "limit": 3000, 
            "period": "18000", 
            "ref_model": "gemini-2.5-flash" 
        },
        "pool_3_0": {
            "limit": 400, 
            "period": "18000", 
            "ref_model": "gemini-3-pro-low"
        },
        "pool_computer_use": {
            "limit": 500, 
            "period": "18000", 
            "ref_model": "rev19-uic3-1p"
        },
        "pool_claude": {
            "limit": 250, 
            "period": "18000", 
            "ref_model": "claude-sonnet-4-5"
        },
        "pool_banana": {
            "limit": 20, 
            "period": "18000", 
            "ref_model": "gemini-3-pro-image"
        }
    }

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
        return [constants.BASE_URL_DAILY, constants.BASE_URL_PROD]

    def get_backend_usage_cost(self, model_name: str) -> int:
        """
        【自定义后端扣费逻辑】
        
        由于 Auth Service 中已经根据不同的模型组 (Group) 设定了独立的物理计数器限额
        (例如: pool_2_5=3000, pool_claude=250)，
        因此这里每次请求的消耗统一为 1 即可。
        
        1 Request = 1 Quota Count
        """
        return 1

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
                
                # [关键修复] 如果是流式请求但状态码错误，需要主动读取 body 才能知道错误原因
                if stream:
                    try:
                        err_bytes = await response.aread()
                        err_text = err_bytes.decode('utf-8', errors='ignore')
                    except Exception:
                        err_text = "[无法读取流式错误响应体]"
                else:
                    err_text = response.text

                await client.aclose()

                if response.status_code == 401:
                    raise InvalidCredentials(f"无效的 Token: {err_text}")
                elif response.status_code == 429:
                    raise ServiceUnavailable(f"触发限流 ({base_url}) {err_text}")
                else:
                    # 这里会把 err_text 抛出来，你就能看到 400 的具体原因了
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

    async def _fetch_raw_models(self) -> Dict[str, Any]:
        """
        内部方法：获取原始模型数据（包含 quotaInfo）
        用于 fetch_models 和 fetch_remaining_quota 复用
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
                        return data.get("models", {})
                    else:
                        logger.warning(f"获取模型列表失败: {resp.status_code}")

            except Exception as e:
                logger.error(f"获取模型列表异常 [{base_url}]: {e}")
                continue
        return {}

    async def fetch_models(self) -> List[str]:
        """
        获取模型列表。
        直接返回 API 提供的原始模型 ID，去除 IGNORED_MODELS。
        不再进行别名转换，转换逻辑交由数据库 model_map 配置。
        """
        models_data = await self._fetch_raw_models()
        
        if models_data:
            #logger.debug(f"额度: {models_data}")
            # 日志记录配额
            #self._log_quota_info(models_data)
            # 暂时注释，之后要用再说

            # 返回经过过滤的原始模型 ID
            raw_models = list(models_data.keys())
            return [m for m in raw_models if m not in constants.IGNORED_MODELS]
        
        return []

    async def fetch_remaining_quota(self) -> Dict[str, Dict[str, int]]:
        """
        获取上游 API 的【剩余额度】。
        
        修正逻辑：
        Google API 返回的是 "resetTime" (窗口结束/下次重置时间)。
        但系统内部通常将 Redis 中的 timestamp 视为 "last_reset" (窗口开始时间)。
        为了让系统计算出正确的下次重置时间 (Start + Period)，我们需要在这里将 Google 的时间倒推一个 Period。
        """
        models_data = await self._fetch_raw_models()
        if not models_data:
            return {}
        
        result = {}
        
        for pool_name, config in self.QUOTA_POOLS.items():
            ref_model = config["ref_model"]
            period_seconds = int(config["period"])  # 确保转为 int
            
            # 在 API 返回的模型列表中查找参考模型
            model_info = models_data.get(ref_model)
            if not model_info or "quotaInfo" not in model_info:
                continue
                
            quota_info = model_info["quotaInfo"]
            
            # 1. 计算剩余次数
            fraction = quota_info.get("remainingFraction", 1.0)
            remaining_count = int(config["limit"] * fraction)
            
            bucket_data = {
                config["period"]: remaining_count
            }
            
            # 2. 解析 Reset Time 并转换为 Window Start Time
            reset_time_str = quota_info.get("resetTime")
            if reset_time_str:
                try:
                    # 解析 Google 返回的 UTC 时间
                    if reset_time_str.endswith("Z"):
                        dt_next_reset = datetime.fromisoformat(reset_time_str.replace('Z', '+00:00'))
                    else:
                        dt_next_reset = datetime.fromisoformat(reset_time_str)
                    
                    if dt_next_reset.tzinfo is None:
                        dt_next_reset = dt_next_reset.replace(tzinfo=timezone.utc)
                    
                    # [关键修复]
                    # Google Reset Time 是 "未来结束时间"。
                    # 我们需要存入 Redis 的是 "当前窗口开始时间"。
                    # Start Time = Next Reset Time - Period
                    ts_next_reset = int(dt_next_reset.timestamp())
                    ts_window_start = ts_next_reset - period_seconds
                    
                    bucket_data["reset_ts"] = ts_window_start
                
                except Exception as e:
                    logger.warning(f"解析 resetTime 失败 ({reset_time_str}): {e}")
            
            result[pool_name] = bucket_data
            
        return result

    def _log_quota_info(self, models_data: Dict):
        """记录配额信息的辅助方法"""
        log_lines = ["\n[Antigravity Model Quotas]"]
        log_lines.append(f"{'Model ID':<35} | {'Remaining':<10} | {'Reset Time'}")
        log_lines.append("-" * 75)
        
        for model_id, info in models_data.items():
            # 过滤掉不需要关注的内部模型，除非它有显式的 quotaInfo
            if model_id in constants.IGNORED_MODELS and "quotaInfo" not in info:
                continue
                
            quota = info.get("quotaInfo", {})
            remaining = str(quota.get("remainingFraction", "N/A"))
            reset_time = quota.get("resetTime", "N/A")
            log_lines.append(f"{model_id:<35} | {remaining:<10} | {reset_time}")
        
        logger.info("\n".join(log_lines))