# app/repositories/channel.py
from typing import List, Optional, Dict, Any
from app.models.channel import Channel
from app.repositories.base import BaseRepository

class ChannelRepository(BaseRepository[Channel]):
    model = Channel

    async def get_with_platform(self, channel_id: int) -> Optional[Channel]:
        """获取渠道详情，并预加载 Platform 信息 (用于同步到 Redis)"""
        # prefetch 用于减少 N+1 查询问题
        return await self.get_by_id(channel_id, prefetch=["platform"])

    async def get_active_channels_by_platform(self, platform_id: int) -> List[Channel]:
        """获取某平台下所有启用的账号"""
        return await self.filter(platform_id=platform_id, is_active=True)
    
    async def get_all_active_with_platform(self) -> List[Channel]:
        """获取全系统所有启用的账号（用于系统启动时全量预热 Redis）"""
        return await self.filter(is_active=True, prefetch=["platform"])

    async def disable_channel(self, channel_id: int, error_msg: str = None) -> bool:
        """
        快速禁用账号
        如果提供了 error_msg，会将其更新到 status_msg 字段中
        """
        update_data = {"is_active": False}
        
        # 如果提供了错误信息，保存到 status_msg
        if error_msg:
            # 数据库定义 max_length=255，这里做截断处理防止报错
            update_data["status_msg"] = str(error_msg)[:255]
        
        # 使用 bulk_update 避免先查后改 (高效更新)
        count = await self.bulk_update_by_filter(
            update_data=update_data, 
            id=channel_id
        )
        return count > 0

    async def update_credentials(self, channel_id: int, new_credentials: Dict[str, Any]) -> bool:
        """
        更新渠道凭证 (Adapter 回调使用)
        """
        count = await self.bulk_update_by_filter(
            update_data={"credentials": new_credentials},
            id=channel_id
        )
        return count > 0