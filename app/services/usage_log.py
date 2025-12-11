# app/services/usage_log.py
from loguru import logger
from typing import Optional
import time

from app.repositories.usage_log import UsageLogRepository
from app.repositories.apikey import ApiKeyRepository
from app.models.usage_log import UsageLog

class UsageLogService:
    
    @staticmethod
    async def log_transaction(
        api_key_str: str,
        channel_id: int,
        model_name: str,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        duration_ms: int,
        is_stream: bool,
        trace_id: Optional[str] = None
    ):
        """
        异步记录 API 使用日志
        注意：此方法应当在后台任务中运行，避免阻塞主请求
        """
        try:
            # 1. 解析 API Key ID
            # 因为 ChatService 只传递了 key 字符串，我们需要反查 ID 用于外键关联
            # 也可以考虑在 ChatService 中传递 key 对象，但为了解耦，这里查一次
            key_repo = ApiKeyRepository()
            api_key_obj = await key_repo.get_by_key(api_key_str)
            
            api_key_id = api_key_obj.id if api_key_obj else None

            # 2. 写入日志
            repo = UsageLogRepository()
            await repo.create(
                api_key_id=api_key_id,
                channel_id=channel_id,
                model_name=model_name,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                duration_ms=duration_ms,
                is_stream=is_stream,
                trace_id=trace_id
            )
            logger.debug(f"创建使用记录成功 Trace ID {trace_id or 'N/A'}")
            
        except Exception as e:
            # 日志记录失败不应影响主业务，记录错误日志即可
            logger.error(f"创建使用记录失败: {e}")