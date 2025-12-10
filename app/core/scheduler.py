# app/core/scheduler.py
import asyncio
import time
from typing import Optional, Dict, Any, List
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor
from tortoise.expressions import F
from loguru import logger

from app.core.redis.cache import CacheService, CacheKeys
from app.core.redis.connection import get_redis_client
from app.models.channel import Channel
from app.models.apikey import ApiKey
from app.repositories.apikey import ApiKeyRepository

class SchedulerService:
    """
    2API 系统核心调度器
    负责：
    1. 消费 Redis 队列，将 Channel 使用量同步到 MySQL
    2. 消费 Redis 队列，将 ApiKey 使用量同步到 MySQL
    3. 扫描数据库，重置已过期的 Channel 配额
    4. 定时重构系统模型列表缓存 (Cache Rebuild)
    """
    _instance = None
    
    def __init__(self):
        if SchedulerService._instance is not None:
            raise Exception("调度器必须是单例的")
        
        self.scheduler: Optional[AsyncIOScheduler] = None
        self._running = False
        self.apikey_repo = ApiKeyRepository()
        SchedulerService._instance = self

    @classmethod
    def get_instance(cls):
        if cls._instance is None:
            cls()
        return cls._instance

    def _create_scheduler(self) -> AsyncIOScheduler:
        jobstores = {'default': MemoryJobStore()}
        executors = {'default': AsyncIOExecutor()}
        job_defaults = {
            'coalesce': True,        # 积压合并：如果任务卡住了，多次错过只执行一次
            'max_instances': 1,      # 并发控制：同一任务同一时间只允许一个实例运行
            'misfire_grace_time': 60 # 容错窗口
        }
        return AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults=job_defaults,
            timezone='UTC'
        )

    async def start(self):
        if self._running: return
        
        logger.info("开始启动调度器...")
        self.scheduler = self._create_scheduler()
        
        # 任务1: Channel Usage Sync (高频)
        # 将 Redis 里的渠道调用次数写入数据库
        self.scheduler.add_job(
            func=self._sync_channel_usage_to_db,
            trigger=IntervalTrigger(seconds=10),
            id="job_sync_ch_usage",
            name="Sync Channel Usage to DB",
            replace_existing=True
        )
        
        # 任务2: ApiKey Usage Sync (中频)
        # 将 Redis 里的 ApiKey 扣费记录写入数据库
        self.scheduler.add_job(
            func=self._sync_apikey_usage_to_db,
            trigger=IntervalTrigger(seconds=60),
            id="job_sync_ak_usage",
            name="Sync ApiKey Usage to DB",
            replace_existing=True
        )
        
        # 任务3: Quota Reset (中频)
        # 扫描数据库，重置那些“天/小时”周期结束的渠道，让它们复活
        self.scheduler.add_job(
            func=self._reset_expired_quotas,
            trigger=IntervalTrigger(seconds=60),
            id="job_reset_quotas",
            name="Reset Channel Quotas",
            replace_existing=True
        )

        # 【新增】任务4: System Models Cache Rebuild (低频/兜底)
        # 定时(每10分钟)全量从数据库计算最新的模型列表并更新到 Redis
        # 用于处理数据一致性（如删除了某个独家模型账号后，清理缓存）
        self.scheduler.add_job(
            func=CacheService.rebuild_system_models_cache,
            trigger=IntervalTrigger(minutes=10),
            id="job_rebuild_models",
            name="Rebuild System Models Cache",
            replace_existing=True
        )
        
        self.scheduler.start()
        self._running = True
        logger.success("调度器启动成功。")

    async def stop(self):
        if self.scheduler and self._running:
            self.scheduler.shutdown(wait=True)
            self._running = False
            logger.info("调度器停止成功。")

    # -------------------------------------------------------------------------
    # 任务逻辑实现
    # -------------------------------------------------------------------------

    async def _update_single_apikey(self, key: str, data: Dict[str, int]):
        """辅助函数：处理单个 Key 的 DB 更新"""
        try:
            # 先查对象以确认是否为无限卡 (balance = -1)
            # 优化：虽然这里有 SELECT，但为了并发安全和逻辑检查是必要的
            api_key_obj = await self.apikey_repo.get_by_key(key)
            if not api_key_obj: return
            
            # 构建更新字段 (使用 F 表达式实现数据库原子更新)
            update_data = {
                "used_count": F("used_count") + data['count'],
                "total_tokens": F("total_tokens") + data['tokens'],
                "updated_at": F("updated_at") # 强制更新时间戳
            }
            
            # 只有非无限卡，才扣余额
            if api_key_obj.balance != -1:
                update_data["balance"] = F("balance") - data['count']
                
            await ApiKey.filter(id=api_key_obj.id).update(**update_data)
        except Exception as e:
            logger.error(f"同步单个 API Key {key} 失败: {e}")

    async def _sync_apikey_usage_to_db(self):
        """
        同步 API Key 的使用情况 (Balance扣减, UsedCount增加, Token增加)
        """
        BATCH_SIZE = 200
        client = await get_redis_client()
        # Pop from apikey queue
        messages = await client.lpop(CacheKeys.sync_queue_apikey(), count=BATCH_SIZE)
        if not messages: return

        # ### 内存聚合 ###
        # 将多条消息合并，例如: key A +1, key A +1 => key A +2
        # { key_str: {"count": 0, "tokens": 0} }
        updates = {}
        
        for msg in messages:
            # msg: "sk-xxx|count|tokens|ts"
            try:
                parts = msg.split("|")
                key = parts[0]
                count = int(parts[1])
                tokens = int(parts[2])
                
                if key not in updates:
                    updates[key] = {"count": 0, "tokens": 0}
                
                updates[key]["count"] += count
                updates[key]["tokens"] += tokens
            except Exception as e:
                logger.error(f"解析 apikey 信息出错: {msg} {e}")

        # 并发写入 DB，避免串行阻塞
        tasks = [
            self._update_single_apikey(key, data) 
            for key, data in updates.items()
        ]
        
        if tasks:
            # 使用 gather 并发执行，大幅减少 Event Loop 占用时间
            await asyncio.gather(*tasks)
            logger.debug(f"同步了 {len(updates)} api keys 共 {len(messages)} 使用记录到数据库")

    async def _update_single_channel(self, c_id: int, model_increments: Dict[str, int]):
        """
        辅助函数：处理单个 Channel 的 DB 更新
        【修改】支持 Group 限流，写入 usage_progress 时使用 Bucket Name (Group or Model)
        """
        try:
            channel = await Channel.get_or_none(id=c_id)
            if not channel: return
            
            current_progress = channel.usage_progress or {}
            limits_map = channel.rate_limits or {}
            updated_flag = False
            now = int(time.time())
            
            # 1. 更新计数逻辑
            for model, inc_val in model_increments.items():
                rules = limits_map.get(model) or limits_map.get('default', [])
                if not rules: continue
                
                for rule in rules:
                    p = str(rule['period']) 
                    # 【核心修改】确定 Bucket Key (优先使用 Group)
                    bucket_name = rule.get('group') or model

                    if bucket_name not in current_progress:
                        current_progress[bucket_name] = {}
                    
                    if p not in current_progress[bucket_name]:
                        # 初始化：如果第一次用到，last_reset 设为当前时间
                        current_progress[bucket_name][p] = {
                            "count": 0, 
                            "last_reset": now 
                        }
                    
                    current_progress[bucket_name][p]["count"] += inc_val
                    updated_flag = True
            
            if updated_flag:
                # 2. 计算全局最小的 next_reset_time
                # 只有当 count > 0 的桶才需要参与下一次重置的计算
                min_next_reset = float('inf')
                
                for model_key, rules in limits_map.items():
                    # 注意：这里需要根据规则找到对应的 bucket
                    for rule in rules:
                        period = int(rule['period'])
                        bucket_name = rule.get('group') or model_key
                        
                        bucket = current_progress.get(bucket_name, {}).get(str(period))
                        
                        if bucket and bucket.get("count", 0) > 0:
                            # 只有在这个周期内用过的，才需要计算过期时间
                            last_reset = bucket.get('last_reset', now)
                            next_due = last_reset + period
                            if next_due < min_next_reset:
                                min_next_reset = next_due

                # 3. 准备更新字段
                update_fields = ["usage_progress"]
                channel.usage_progress = current_progress
                
                # 如果计算出了有效的下次重置时间，则更新索引列
                if min_next_reset != float('inf'):
                    # 正常情况，指向未来
                    if channel.next_reset_time != int(min_next_reset):
                        channel.next_reset_time = int(min_next_reset)
                        update_fields.append("next_reset_time")
                else:
                    # 如果所有桶都是空的（虽然这里是update一定是加了数，但逻辑上闭环），
                    # 或者刚重置完还没用（不应该进入这里），设为0避免空跑
                    if channel.next_reset_time != 0:
                        channel.next_reset_time = 0
                        update_fields.append("next_reset_time")

                await channel.save(update_fields=update_fields)
                
        except Exception as e:
            logger.error(f"同步单个 Channel {c_id} 失败: {e}")

    async def _sync_channel_usage_to_db(self):
        """
        消费者任务：批量获取 Redis Channel 队列消息，聚合后更新 DB
        """
        BATCH_SIZE = 200 # 稍微调大一点
        
        try:
            client = await get_redis_client()
            messages = await client.lpop(CacheKeys.sync_queue_channel(), count=BATCH_SIZE)
            
            if not messages:
                return 

            # ### 聚合数据 ###
            # { channel_id: { model_name: count } }
            updates: Dict[int, Dict[str, int]] = {}
            
            for msg in messages:
                # msg format: "12|gpt-4|1717..."
                try:
                    parts = msg.split("|")
                    c_id = int(parts[0])
                    model = parts[1]
                    
                    if c_id not in updates:
                        updates[c_id] = {}
                    updates[c_id][model] = updates[c_id].get(model, 0) + 1
                except Exception as e:
                    logger.error(f"非法的队列信息: {msg}, 错误: {e}")

            # 并发写入 DB
            tasks = [
                self._update_single_channel(c_id, model_increments)
                for c_id, model_increments in updates.items()
            ]
            
            if tasks:
                await asyncio.gather(*tasks)
                logger.info(f"同步了 {len(messages)} 渠道使用记录到数据库")

        except Exception as e:
            logger.error(f"执行 _sync_channel_usage_to_db 时出错: {e}")

    async def _reset_expired_quotas(self):
        """
        重置任务：查询 next_reset_time <= now 的渠道
        【优化策略】：如果渠道在过期周期内没有使用（count=0），则不执行重置动作，
        并将其 next_reset_time 设为 0，停止调度，直到下一次有真实调用触发更新。
        【修改】支持 Group 限流，清理 Redis Key 时使用 Bucket Name
        """
        try:
            now = int(time.time())
            
            # 查找需要处理的渠道
            channels = await Channel.filter(
                is_active=True, 
                next_reset_time__lte=now, 
                next_reset_time__gt=0 
            ).all()
            
            if not channels:
                return

            client = await get_redis_client()
            pipeline = client.pipeline()
            has_redis_ops = False
            reset_count = 0
            
            for ch in channels:
                progress = ch.usage_progress or {}
                limits_map = ch.rate_limits or {}
                
                min_next_reset = float('inf')
                db_dirty = False
                channel_was_reset = False # 标记该渠道本次是否发生了实际的额度恢复
                
                for model in ch.supported_models:
                    rules = limits_map.get(model) or limits_map.get('default', [])
                    if not rules: continue
                    
                    for rule in rules:
                        # 【核心修改】确定 Bucket Key
                        bucket_name = rule.get('group') or model
                        
                        period = str(rule['period'])
                        
                        # 获取对应的进度 bucket (注意：这里是从 progress[bucket_name] 获取)
                        bucket_prog = progress.get(bucket_name, {})
                        bucket = bucket_prog.get(period, {"count": 0, "last_reset": 0})
                        
                        last_reset = bucket.get('last_reset', 0)
                        count = bucket.get('count', 0)
                        
                        # 检查是否到期
                        if now - last_reset >= int(period):
                            # 只有当 count > 0 时，才执行“重置”动作
                            if count > 0:
                                bucket['count'] = 0
                                bucket['last_reset'] = now
                                db_dirty = True
                                channel_was_reset = True
                                
                                # 【核心修改】清理 Redis 计数器，使用 bucket_name
                                usage_key = CacheKeys.channel_usage(ch.id, bucket_name, int(period))
                                pipeline.delete(usage_key)
                                
                                # 复活策略
                                pipeline.sadd(CacheKeys.available_pool(model), ch.id)
                                has_redis_ops = True
                                
                                # 重置后，新的下次到期时间是 now + period
                                next_due = now + int(period)
                            else:
                                # Count 为 0，不更新 last_reset。
                                next_due = float('inf') 
                        else:
                            # 未到期
                            if count > 0:
                                next_due = last_reset + int(period)
                            else:
                                next_due = float('inf')
                        
                        # 维护全局最小重置时间
                        if next_due < min_next_reset:
                            min_next_reset = next_due
                            
                        # 写回内存结构
                        bucket_prog[period] = bucket
                        progress[bucket_name] = bucket_prog

                # 保存逻辑
                updates = {}
                if db_dirty:
                    updates['usage_progress'] = progress
                
                # 计算新的 DB 索引字段 next_reset_time
                new_next = min_next_reset if min_next_reset != float('inf') else 0
                
                if new_next != ch.next_reset_time:
                    updates['next_reset_time'] = new_next
                    db_dirty = True 
                
                if db_dirty:
                    await Channel.filter(id=ch.id).update(**updates)
                    if channel_was_reset:
                        reset_count += 1
            
            if has_redis_ops:
                await pipeline.execute()
                
            if reset_count > 0:
                logger.info(f"重置了 {reset_count} 个渠道的额度 (跳过空闲渠道 {len(channels) - reset_count} 个)")

        except Exception as e:
            logger.error(f"执行 _reset_expired_quotas 时出错: {e}")