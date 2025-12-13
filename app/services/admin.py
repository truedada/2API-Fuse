import time
from datetime import datetime, timezone  # 【修改】引入 timezone
from typing import List, Tuple, Optional, Any, Dict
from app.repositories.channel import ChannelRepository
from app.repositories.platform import PlatformRepository
from app.repositories.apikey import ApiKeyRepository
from app.core.redis.cache import CacheService
from tortoise.expressions import Q
from app.repositories.usage_log import UsageLogRepository
from app.schemas.admin import (
    ChannelCreate, ChannelUpdate, ChannelTestResponse,
    PlatformCreate, PlatformUpdate,
    ApiKeyCreate, ApiKeyUpdate,
    IDRequest,
    UsageLogResponse
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
        apikey_repo: ApiKeyRepository,
        usage_log_repo: UsageLogRepository
    ):
        self.channel_repo = channel_repo
        self.platform_repo = platform_repo
        self.apikey_repo = apikey_repo
        self.usage_log_repo = usage_log_repo

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
    # --- 新增：获取支持的适配器类型 ---
    async def get_supported_adapter_types(self) -> List[Dict[str, str]]:
        """获取系统支持的所有适配器类型"""
        return AdapterFactory.get_supported_adapters()

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
            
        # 【新增】关键修复：强制移除缓存
        # 无论是禁用 (is_active=False) 还是修改余额，都删除缓存
        # 这样下次用户请求时会 Cache Miss -> 回源数据库 -> 触发 is_active 检查或同步新余额
        await CacheService.remove_apikey(updated_key.key)
        
        return updated_key

    async def delete_apikey(self, data: IDRequest):
        # 【新增】关键修复：先获取 Key 字符串用于清理缓存，再删除
        target = await self.apikey_repo.get_by_id(data.id)
        if not target:
            raise NotFound(detail="API Key 不存在")
            
        # 移除缓存
        await CacheService.remove_apikey(target.key)
        
        # 数据库物理删除
        await self.apikey_repo.delete(data.id)
        return True


    async def get_usage_logs(
        self, 
        limit: int, 
        offset: int, 
        keyword: Optional[str] = None,
        api_key_id: Optional[int] = None,
        channel_id: Optional[int] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None
    ) -> Tuple[List[UsageLogResponse], int]:
        """
        分页查询使用日志，支持多种筛选条件
        """
        # 1. 构建过滤条件
        filters = Q()

        if api_key_id:
            filters &= Q(api_key_id=api_key_id)
        
        if channel_id:
            filters &= Q(channel_id=channel_id)
            
        if start_time:
            filters &= Q(created_at__gte=start_time)
            
        if end_time:
            filters &= Q(created_at__lte=end_time)

        if keyword:
            # 模糊搜索：匹配模型名 或 trace_id
            filters &= (Q(model_name__icontains=keyword) | Q(trace_id__icontains=keyword))

        # 2. 查询总数
        total = await self.usage_log_repo.count(filters)

        # 3. 查询数据 (关联查询 api_key, channel, platform)
        # 注意：Tortoise ORM 使用双下划线进行跨表关联 prefetch
        # 我们需要在 Repository 中支持 filter 方法传入 prefetch 参数
        logs = await self.usage_log_repo.filter(
            filters,
            limit=limit,
            offset=offset,
            order_by=["-created_at"],
            prefetch=["api_key", "channel", "channel__platform"]
        )

        # 4. 转换为 Schema
        result = []
        for log in logs:
            # 安全获取关联字段 (因为 on_delete=SET_NULL，可能为空)
            ak_name = log.api_key.name if log.api_key else "未知/已删除"
            ak_key = log.api_key.key if log.api_key else None
            
            ch_name = log.channel.name if log.channel else "未知/已删除"
            
            # channel__platform 表示获取 channel 下的 platform 对象
            pf_name = "未知"
            if log.channel and log.channel.platform:
                pf_name = log.channel.platform.name

            item = UsageLogResponse(
                id=log.id,
                trace_id=log.trace_id,
                model_name=log.model_name,
                prompt_tokens=log.prompt_tokens,
                completion_tokens=log.completion_tokens,
                total_tokens=log.total_tokens,
                duration_ms=log.duration_ms,
                is_stream=log.is_stream,
                created_at=log.created_at,
                # 填充关联名称
                api_key_name=ak_name,
                api_key_str=ak_key,
                channel_name=ch_name,
                platform_name=pf_name
            )
            result.append(item)

        return result, total

    async def sync_channel_usage(self, channel_id: int) -> dict:
        """
        同步渠道的使用进度 (Rate Limit Usage)
        逻辑：Adapter(获取剩余) -> CacheService(反推已用并覆盖Redis) -> DB(持久化)
        """
        # 1. 获取完整数据
        channel = await self.channel_repo.get_with_platform(channel_id)
        if not channel:
            raise NotFound(detail="账号不存在")
        
        # 如果账号未启用，通常无法请求上游，或者没有同步的必要
        if not channel.is_active:
             raise InvalidInput(detail="账号未启用，无法同步进度")

        # 2. 构建适配器
        adapter = self._get_adapter(channel.platform, channel)

        synced_data = {}
        
        try:
            # --- 阶段 A: 上游同步 ---
            # 获取剩余量 { "bucket": { "period": remaining, "_reset_ts": ts } }
            remaining_map = await adapter.fetch_remaining_quota()
            
            if remaining_map:
                # 计算 Used = Limit - Remaining，并写入 Redis
                await CacheService.apply_upstream_sync(channel.id, remaining_map)
                synced_data = remaining_map
            
            # --- 阶段 B: 持久化到数据库 ---
            # 无论上游是否返回数据，我们将 Redis 中最新的计数（包含刚才同步的和自然累加的）拉回数据库
            current_redis_usage = await CacheService.get_current_channel_usage(channel.id)
            
            if current_redis_usage:
                # DB 结构: { "bucket": { "period": { "count": X, "last_reset": T } } }
                db_progress = channel.usage_progress or {}
                now_ts = int(time.time())
                has_change = False

                for bucket, periods in current_redis_usage.items():
                    if bucket not in db_progress:
                        db_progress[bucket] = {}
                    
                    # 检查 Adapter 返回的该 bucket 是否包含 reset time metadata
                    upstream_reset_ts = None
                    if remaining_map and bucket in remaining_map:
                        upstream_reset_ts = remaining_map[bucket].get("reset_ts")
                    
                    for period_str, count in periods.items():
                        # 初始化结构
                        if period_str not in db_progress[bucket]:
                            db_progress[bucket][period_str] = {"count": 0, "last_reset": now_ts}
                        
                        entry = db_progress[bucket][period_str]
                        old_count = entry.get("count", 0)
                        old_reset = entry.get("last_reset")
                        
                        # 仅当数值变化时更新
                        if count != old_count:
                            entry["count"] = count
                            has_change = True

                        # [新增] 如果上游有明确的 reset time，且与当前不同，则更新 DB
                        # 这样能让 Scheduler 调度更准确，或者 UI 显示重置时间更准
                        if upstream_reset_ts and upstream_reset_ts != old_reset:
                            entry["last_reset"] = upstream_reset_ts
                            has_change = True
                
                if has_change:
                    await self.channel_repo.update(channel.id, {"usage_progress": db_progress})

            return {
                "success": True, 
                "upstream_data": synced_data, 
                "msg": "配额使用进度同步完成" if synced_data else "上游未返回配额使用进度数据，已更新本地缓存状态"
            }

        except NotImplementedError:
             return {"success": False, "msg": "该渠道不支持进度同步"}
        except Exception as e:
            raise ExternalServiceError(detail=f"同步进度失败: {str(e)}")