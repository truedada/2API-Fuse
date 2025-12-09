# app/repositories/platform.py
from typing import Optional
from app.models.platform import Platform
from app.repositories.base import BaseRepository

class PlatformRepository(BaseRepository[Platform]):
    model = Platform

    async def get_by_name(self, name: str) -> Optional[Platform]:
        """根据平台名称查找"""
        return await self.get_by(name=name)