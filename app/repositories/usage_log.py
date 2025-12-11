# app/repositories/usage_log.py
from app.repositories.base import BaseRepository
from app.models.usage_log import UsageLog

class UsageLogRepository(BaseRepository[UsageLog]):
    """
    UsageLog 专用数据访问层
    """
    model = UsageLog

    async def get_logs_by_key_id(self, api_key_id: int, limit: int = 100, offset: int = 0):
        """获取指定 Key 的最近调用记录"""
        return await self.model.filter(api_key_id=api_key_id)\
            .order_by("-created_at")\
            .limit(limit)\
            .offset(offset)