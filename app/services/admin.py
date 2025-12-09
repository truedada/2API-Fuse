# app/services/admin.py
import time
from datetime import datetime, timezone  # 【修改】引入 timezone
from typing import List, Tuple, Optional, Any
from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.repositories.apikey import ApiKeyRepository
from app.core.redis.cache import CacheService
from app.schemas.admin import (
    ChannelCreate, ChannelUpdate, ChannelTestResponse,
    PlatformCreate, PlatformUpdate,
    ApiKeyCreate, ApiKeyUpdate,
    IDRequest
)
from app.core.exceptions.definitions import NotFound, ResourceConflict, InvalidInput, ExternalServiceError
from app.adapters.base import BaseAdapter
from app.adapters.factory import AdapterFactory

# 如果你有其他适配器，请在这里导入
# from app.adapters.claude.adapter import ClaudeAdapter

class AdminService:
    """
    管理员业务逻辑：处理 CRUD 及缓存同步，以及渠道测试与维护
    """
    def __init__(
        self, 
        channel_repo: ChannelRepository, 
        platform_repo: PlatformRepository,
        apikey_repo: ApiKeyRepository
    ):
        self.channel_repo = channel_repo
        self.platform_repo = platform_repo
        self.apikey_repo = apikey_repo

    def _get_adapter(self, platform_data: Any, channel_data: Any) -> BaseAdapter:
        """
        内部辅助函数：根据平台配置和渠道凭证构建适配器实例
        """
        # 构造配置字典
        config = {
            "base_url": platform_data.base_url,
            "type": platform_data.adapter_type,
            "proxy": platform_data.proxy_url,
            "credentials": channel_data.credentials,
            "extra_config": platform_data.extra_config or {}
        }

        # 根据 adapter_type 返回对应的适配器实例
        adapter_type = platform_data.adapter_type.lower()
        
        adapter = AdapterFactory.get_adapter(adapter_type, config)
        return adapter

    # ==========================
    # Platform (平台) 管理
    # ==========================
    
    async def get_platforms(self) -> List[dict]:
        return await self.platform_repo.all(order_by=["id"])

    async def create_platform(self, data: PlatformCreate):
        if await self.platform_repo.exists(name=data.name):
            raise ResourceConflict(detail=f"平台 '{data.name}' 已存在")
        platform = await self.platform_repo.create(**data.model_dump())
        
        # 创建时立即同步缓存
        await CacheService.sync_platform(platform.id)
        
        return platform

    async def update_platform(self, data: PlatformUpdate):
        update_dict = data.model_dump(exclude_unset=True, exclude={"id"})
        updated = await self.platform_repo.update(data.id, update_dict)
        
        if not updated:
            raise NotFound(detail="平台不存在")
        
        # 更新后立即同步 Redis，实现热更新
        await CacheService.sync_platform(data.id)
        
        return updated

    async def delete_platform(self, data: IDRequest):
        """删除平台，如果有下属账号则禁止删除"""
        channels = await self.channel_repo.filter(platform_id=data.id)
        if channels:
            raise InvalidInput(detail="该平台下仍有渠道账号，无法删除。请先删除或转移账号。")
        
        count = await self.platform_repo.delete(data.id)
        if not count:
            raise NotFound(detail="平台不存在")
            
        # 清理 Redis 缓存
        await CacheService.sync_platform(data.id)
            
        return True

    # ==========================
    # Channel (渠道) 管理
    # ==========================

    async def get_channels(self, limit: int, offset: int, platform_id: Optional[int] = None) -> Tuple[List[dict], int]:
        kwargs = {}
        if platform_id:
            kwargs["platform_id"] = platform_id
            
        items = await self.channel_repo.filter(
            limit=limit, 
            offset=offset, 
            order_by=["-id"],
            prefetch=["platform"],
            **kwargs
        )
        total = await self.channel_repo.count(**kwargs)
        
        result = []
        for item in items:
            item.platform_name = item.platform.name if item.platform else "Unknown"
            result.append(item)
            
        return result, total

    async def create_channel(self, data: ChannelCreate):
        """
        创建渠道，增加重名检测
        """
        if not await self.platform_repo.get_by_id(data.platform_id):
            raise NotFound(detail=f"平台 ID {data.platform_id} 不存在")
        
        # 【修改】查重逻辑：同一平台下 name 不允许重复
        if await self.channel_repo.exists(platform_id=data.platform_id, name=data.name):
            raise ResourceConflict(detail=f"该平台下已存在名为 '{data.name}' 的渠道")
        
        channel = await self.channel_repo.create(**data.model_dump())
        full_channel = await self.channel_repo.get_with_platform(channel.id)
        if full_channel:
            await CacheService.sync_channel(full_channel.id)
        return full_channel

    async def upsert_channel(self, data: ChannelCreate) -> Any:
        """
        【新增】如果存在则更新，不存在则创建
        基于 platform_id 和 name 判断唯一性
        """
        if not await self.platform_repo.get_by_id(data.platform_id):
            raise NotFound(detail=f"平台 ID {data.platform_id} 不存在")

        # 查找是否存在
        existing_channel = await self.channel_repo.get_by(platform_id=data.platform_id, name=data.name)
        
        if existing_channel:
            # 存在 -> 执行更新逻辑
            # 将 ChannelCreate 模型转换为字典
            update_data = data.model_dump(exclude_unset=True)
            
            # 复用 update_channel 的逻辑，但需要构造 ChannelUpdate 对象
            # 注意：ChannelUpdate 需要 ID
            update_payload = ChannelUpdate(id=existing_channel.id, **update_data)
            return await self.update_channel(update_payload)
        else:
            # 不存在 -> 执行创建逻辑
            return await self.create_channel(data)

    async def update_channel(self, data: ChannelUpdate):
        old_channel = await self.channel_repo.get_by_id(data.id)
        if not old_channel:
            raise NotFound(detail="账号不存在")
            
        old_models = old_channel.supported_models
        update_dict = data.model_dump(exclude_unset=True, exclude={"id"})
        updated_channel = await self.channel_repo.update(data.id, update_dict)
        
        if updated_channel:
            # 如果停用或修改了模型，清理旧缓存
            if (data.is_active is False) or (data.supported_models is not None):
                 await CacheService.remove_channel(data.id, old_models)
            
            # 如果是激活状态，同步新配置
            if updated_channel.is_active:
                full_channel = await self.channel_repo.get_with_platform(data.id)
                await CacheService.sync_channel(full_channel.id)
        
        return updated_channel

    async def delete_channel(self, data: IDRequest):
        channel = await self.channel_repo.get_by_id(data.id)
        if not channel:
            raise NotFound(detail="账号不存在")
            
        await CacheService.remove_channel(channel.id, channel.supported_models)
        await self.channel_repo.delete(data.id)
        return True

    # --- 新增：渠道测试与维护方法 ---

    async def test_channel(self, channel_id: int) -> ChannelTestResponse:
        """
        测试渠道连通性，并更新数据库状态
        """
        # 1. 获取完整数据
        channel = await self.channel_repo.get_with_platform(channel_id)
        if not channel:
            raise NotFound(detail="账号不存在")
        
        # 2. 构建适配器
        adapter = self._get_adapter(channel.platform, channel)
        
        # 3. 执行验证
        start_time = time.time()
        is_valid = await adapter.validate_credential()
        elapsed = round(time.time() - start_time, 3)
        
        # 4. 更新数据库状态
        status_msg = "正常" if is_valid else "异常：连通性测验失败"
        
        # 【修复】使用 datetime.now(timezone.utc)
        update_data = {
            "test_at": datetime.now(timezone.utc), 
            "status_msg": status_msg
        }
        
        if not is_valid:
            update_data["error_count"] = channel.error_count + 1
        else:
            update_data["error_count"] = 0
            
        await self.channel_repo.update(channel.id, update_data)
        
        return ChannelTestResponse(
            id=channel.id,
            is_valid=is_valid,
            msg=status_msg,
            elapsed=elapsed
        )

    async def sync_channel_balance(self, channel_id: int) -> dict:
        """
        同步渠道余额
        """
        channel = await self.channel_repo.get_with_platform(channel_id)
        if not channel:
            raise NotFound(detail="账号不存在")
            
        adapter = self._get_adapter(channel.platform, channel)
        
        try:
            balance_info = await adapter.fetch_balance()
            
            # 【修复】使用 datetime.now(timezone.utc)
            update_data = {
                "balance": balance_info.get("balance", 0.0),
                "balance_updated_at": datetime.now(timezone.utc) 
            }
            await self.channel_repo.update(channel.id, update_data)
            return balance_info
            
        except NotImplementedError:
            raise InvalidInput(detail="该渠道不支持自动查询余额")
        except Exception as e:
            raise ExternalServiceError(detail=f"查询余额失败: {str(e)}")

    async def refresh_channel_session(self, channel_id: int) -> dict:
        """
        刷新渠道 Session/Token
        """
        channel = await self.channel_repo.get_with_platform(channel_id)
        if not channel:
            raise NotFound(detail="账号不存在")

        adapter = self._get_adapter(channel.platform, channel)
        
        try:
            new_creds = await adapter.refresh_session()
            if new_creds:
                current_creds = channel.credentials or {}
                current_creds.update(new_creds)
                
                await self.channel_repo.update(channel.id, {"credentials": current_creds})
                await CacheService.sync_channel(channel.id)
                return {"success": True, "msg": "Session 刷新成功"}
            else:
                return {"success": True, "msg": "Session 无需操作"}
                
        except NotImplementedError:
            raise InvalidInput(detail="该渠道不支持 Session 刷新")
        except Exception as e:
            raise ExternalServiceError(detail=f"刷新 Session 失败: {str(e)}")

    # ==========================
    # ApiKey 管理
    # ==========================
    
    async def get_apikeys(self, limit: int, offset: int) -> Tuple[List[dict], int]:
        items = await self.apikey_repo.filter(limit=limit, offset=offset, order_by=["-created_at"])
        total = await self.apikey_repo.count()
        return items, total

    async def create_apikey(self, data: ApiKeyCreate):
        return await self.apikey_repo.create(**data.model_dump())

    async def update_apikey(self, data: ApiKeyUpdate):
        update_dict = data.model_dump(exclude_unset=True, exclude={"id"})
        updated_key = await self.apikey_repo.update(data.id, update_dict)
        if not updated_key:
            raise NotFound(detail="API Key 不存在")
        return updated_key

    async def delete_apikey(self, data: IDRequest):
        count = await self.apikey_repo.delete(data.id)
        if not count:
            raise NotFound(detail="API Key 不存在")
        return True