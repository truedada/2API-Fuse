# app/core/redis/cache.py
import json
import time
import asyncio
from typing import Optional, Dict, List, Any, Set
from loguru import logger
from app.core.redis.connection import get_redis_client 

# 注意：这里需要在函数内部导入 Model 或确保无循环引用
# 如果你的项目结构导致循环引用，请将 Model 导入移到方法内部
from app.models.apikey import ApiKey
from app.models.channel import Channel
from app.models.platform import Platform

PREFIX = "2api:"

class CacheKeys:
    """
    统一管理 Redis Key 的生成，避免硬编码字符串
    """
    @staticmethod
    def apikey(key: str) -> str:
        # 哈希结构 (Hash)，存储 API Key 的余额等信息
        return f"{PREFIX}ak:{key}"
        
    @staticmethod
    def available_pool(model: str) -> str:
        # 集合结构 (Set)，存储支持该模型且当前“未被封禁”的所有 Channel ID
        # 路由时从中随机取一个
        return f"{PREFIX}pool:{model}:avail"
        
    @staticmethod
    def channel_info(c_id: int) -> str:
        # 字符串结构 (String/JSON)，存储 Channel 的静态配置（URL, Key, RateLimits）
        return f"{PREFIX}ch:{c_id}"
        
    @staticmethod
    def platform_config(p_id: int) -> str:
        """存储平台公共配置 (String/JSON)"""
        return f"{PREFIX}platform:{p_id}"

    @staticmethod
    def channel_usage(c_id: int, bucket_name: str, period: int) -> str:
        """
        字符串结构 (String/Int)，存储某渠道在特定周期内的已用次数。
        【修改】第二个参数由 model 改为 bucket_name。
        如果配置了 group，这里传入 group 名；否则传入 model 名。
        """
        return f"{PREFIX}usage:ch:{c_id}:b:{bucket_name}:p:{period}"
    
    @staticmethod
    def channel_error_count(c_id: int) -> str:
        """存储渠道连续错误次数 (String/Int)"""
        return f"{PREFIX}error:ch:{c_id}"
        
    @staticmethod
    def sync_queue_channel() -> str:
        # 列表结构 (List)，作为消息队列
        return f"{PREFIX}queue:usage_sync:channel"

    @staticmethod
    def sync_queue_apikey() -> str:
        # 列表结构 (List)，ApiKey 的扣费队列
        return f"{PREFIX}queue:usage_sync:apikey"

    # --- 模型列表缓存 Keys (优化版) ---
    @staticmethod
    def sys_all_models() -> str:
        # 集合结构 (Set)，存储所有可用模型 (用于去重和快速增量添加)
        return f"{PREFIX}sys:models_set"

    @staticmethod
    def sys_models_json() -> str:
        # 字符串结构 (JSON String)，预先构建好的 API 响应体
        # API 直接读取此 Key 返回，性能极高
        return f"{PREFIX}sys:models_response"

class CacheService:
    
    @staticmethod
    async def async_get_client():
        return get_redis_client()

    # -------------------------------------------------------------------------
    # Platform 配置逻辑
    # -------------------------------------------------------------------------

    @staticmethod
    async def sync_platform(platform_id: int):
        """
        将平台配置同步到 Redis，实现热更新。
        并联动刷新该平台下所有 Channel 的缓存，确保数据一致性。
        """
        platform = await Platform.get_or_none(id=platform_id)
        client = await get_redis_client()
        key = CacheKeys.platform_config(platform_id)

        # 1. 删除系统级模型列表缓存（触发重构）
        await client.delete(CacheKeys.sys_models_json())

        # 2. 如果平台不存在（被删除），清除平台缓存
        if not platform:
            await client.delete(key)
            # 注意：如果数据库设置了级联删除，Channel 可能也已经没了。
            # 这里可以尝试清理残留 Channel 缓存，但通常由 remove_channel 处理。
            return

        # 3. 更新平台自身的缓存
        cache_data = {
            "id": platform.id,
            "adapter_type": platform.adapter_type,
            "base_url": platform.base_url,
            "proxy_url": platform.proxy_url,
            "model_map": platform.model_map,
            "default_models": platform.default_models,
            "extra_config": platform.extra_config
        }
        
        # 存入 JSON，设置较长过期时间 (如 7 天)
        await client.set(key, json.dumps(cache_data), ex=86400 * 7)
        logger.info(f"同步了平台 {platform_id} 到 Redis")

        # ----------------------------------------------------------
        # 【修复核心】联动更新：刷新所有属于该平台的 Channel 缓存
        # ----------------------------------------------------------
        # 因为 Channel 缓存中冗余存储了 Platform 的 base_url 等信息，
        # Platform 变了，Channel 的缓存也必须跟着变。
        channels = await Channel.filter(platform_id=platform_id).all()
        
        if channels:
            logger.info(f"平台 {platform_id} 配置变更，正在联动刷新 {len(channels)} 个 Channel 的缓存...")
            # 使用 asyncio.gather 并发执行，避免循环 await 导致阻塞太久
            sync_tasks = [CacheService.sync_channel(c.id) for c in channels]
            await asyncio.gather(*sync_tasks)
            logger.info(f"平台 {platform_id} 下的所有 Channel 缓存刷新完毕")

    @staticmethod
    async def get_platform_config(platform_id: int) -> Dict[str, Any]:
        """
        Adapter 调用的高性能读取接口。
        """
        client = await get_redis_client()
        key = CacheKeys.platform_config(platform_id)
        data = await client.get(key)
        
        if data:
            return json.loads(data)
        
        # 缓存未命中，回源同步
        await CacheService.sync_platform(platform_id)
        
        # 再次读取
        data = await client.get(key)
        return json.loads(data) if data else {}

    # -------------------------------------------------------------------------
    # API Key 相关逻辑
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_apikey_quota(api_key: str) -> Optional[int]:
        client = await get_redis_client()
        val = await client.hget(CacheKeys.apikey(api_key), "balance")
        if val is None:
            return None 
        return int(val)

    @staticmethod
    async def sync_apikey(key_obj: "ApiKey"):
        ### 将数据库中的 API Key 信息同步到 Redis Hash 中
        client = await get_redis_client()
        mapping = {
            "balance": key_obj.balance,
            "key": key_obj.key
        }
        await client.hset(CacheKeys.apikey(key_obj.key), mapping=mapping)
        # 缓存 3 天，不活跃的 Key 自动从 Redis 消失
        await client.expire(CacheKeys.apikey(key_obj.key), 86400 * 3)

    @staticmethod
    async def remove_apikey(api_key: str):
        """
        【新增】强制移除 API Key 缓存
        场景：管理员在后台禁用 Key 或修改额度时调用，迫使下次请求回源数据库
        """
        client = await get_redis_client()
        key = CacheKeys.apikey(api_key)
        await client.delete(key)
        logger.debug(f"已清除 API Key 缓存: {api_key[:8]}...")

    @staticmethod
    async def atomic_deduct_quota(api_key: str, cost: int = 1) -> bool:
        ### Lua 脚本：原子性扣除 API Key 余额
        script = """
        local k = KEYS[1]
        local cost = tonumber(ARGV[1])
        local bal_str = redis.call('hget', k, 'balance')
        
        if not bal_str then return -2 end -- Key 不存在
        
        local balance = tonumber(bal_str)
        
        if balance == -1 then
            return 1  -- 无限额度
        end
        
        if balance < cost then
            return 0  -- 余额不足
        end
        
        redis.call('hincrby', k, 'balance', -cost)
        return 1      -- 扣费成功
        """
        client = await get_redis_client()
        res = await client.eval(script, 1, CacheKeys.apikey(api_key), cost)
        
        if res == 1:
            # 扣费成功，异步写入队列，稍后同步给数据库
            msg = f"{api_key}|{cost}|0|{int(time.time())}"
            await client.rpush(CacheKeys.sync_queue_apikey(), msg)
            return True
        elif res == -2:
            return False 
        else:
            return False 

    @staticmethod
    async def record_apikey_tokens(api_key: str, tokens: int):
        ### 记录 Token 消耗（用于统计，不参与扣费判断）
        if tokens <= 0: return
        client = await get_redis_client()
        # 格式: Key | 次数消耗(0) | Token消耗 | 时间戳
        msg = f"{api_key}|0|{tokens}|{int(time.time())}"
        await client.rpush(CacheKeys.sync_queue_apikey(), msg)

    # -------------------------------------------------------------------------
    # Channel 路由与限流逻辑
    # -------------------------------------------------------------------------

    @staticmethod
    async def get_best_channel(model_name: str) -> Optional[Dict[str, Any]]:
        client = await get_redis_client()
        avail_key = CacheKeys.available_pool(model_name)
        
        # 负载均衡：从 Redis Set 中随机取一个 ID
        c_id = await client.srandmember(avail_key)
        
        if not c_id:
            return None
            
        info_json = await client.get(CacheKeys.channel_info(c_id))
        
        # 如果池子里有 ID 但拿不到 Info (数据不一致)，则清理池子并重试
        if not info_json:
            await client.srem(avail_key, c_id)
            return await CacheService.get_best_channel(model_name)
            
        return json.loads(info_json)
    
    @staticmethod
    async def remove_from_pool(model_name: str, channel_id: int):
        """
        紧急从指定模型的可用池中移除 Channel
        """
        client = await get_redis_client()
        key = CacheKeys.available_pool(model_name)
        await client.srem(key, channel_id)

    @staticmethod
    async def record_channel_usage(channel_id: int, model_name: str, cost: int = 1):
        ### 核心限流逻辑 (支持分组限流) ###
        # 【修改】增加 cost 参数，支持自定义后端消耗权重
        client = await get_redis_client()
        info_json = await client.get(CacheKeys.channel_info(channel_id))
        if not info_json: 
            return
        info = json.loads(info_json)
        
        limits_map = info.get('rate_limits', {})
        # 获取当前模型的所有规则
        rules = limits_map.get(model_name) or limits_map.get('default')
            
        if not rules: 
            return 

        pool_key = CacheKeys.available_pool(model_name)
        usage_keys = []
        limits = []
        
        for rule in rules:
            p = rule.get('period')
            c = rule.get('count')
            # 【核心修改】检查是否存在 'group' 字段
            # 如果存在 group，则使用 group 作为 bucket_name，否则使用 model_name
            bucket_name = rule.get('group') or model_name

            if p and c:
                usage_keys.append(CacheKeys.channel_usage(channel_id, bucket_name, p))
                limits.append(c)

        if not usage_keys:
            return

        # Lua 脚本：原子性多周期检查
        # 【修改】使用 incrby 增加 cost
        script = """
        local pool_key = KEYS[1]
        local channel_id = ARGV[1]
        local rule_count = tonumber(ARGV[2])
        local cost = tonumber(ARGV[3]) -- 获取 cost
        local is_banned = 0
        
        for i = 1, rule_count do
            local u_key = KEYS[i+1]
            local limit = tonumber(ARGV[i+3]) -- limit 索引偏移
            
            -- 【核心】使用 incrby 增加自定义的 cost
            local curr = redis.call('incrby', u_key, cost)
            
            if limit > 0 and curr >= limit then
                is_banned = 1
            end
        end
        
        if is_banned == 1 then
            redis.call('srem', pool_key, channel_id)
        end
        return is_banned
        """
        
        script_keys = [pool_key] + usage_keys
        # 参数顺序: channel_id, rule_count, cost, limit1, limit2...
        script_args = [channel_id, len(limits), cost] + limits
        
        try:
            is_banned = await client.eval(script, len(script_keys), *script_keys, *script_args)
            if is_banned:
                logger.warning(f"渠道 {channel_id} 的 {model_name} (Cost: {cost}) 达到了使用限制")
            
            # 记录用于 Scheduler 异步持久化的消息
            # 注意：这里我们只记录 model_name，Scheduler 需要自行聚合处理数据库的 usage_progress
            # 或者，如果 Scheduler 也升级了逻辑，可以传递 group 信息。
            # 为了兼容性，这里暂时只推一条记录，代表“发生了一次调用”
            # 如果需要记录精确的 cost，这里可以扩展消息格式，比如 "id|model|time|cost"
            # 这里保持原样，只做限流控制即可，数据库统计稍微有点偏差通常可以接受，或者后续你可扩展消息格式
            msg = f"{channel_id}|{model_name}|{int(time.time())}"
            await client.rpush(CacheKeys.sync_queue_channel(), msg)
            
        except Exception as e:
            logger.error(f"Lua 脚本错误: {e}")

    # -------------------------------------------------------------------------
    # 错误熔断机制
    # -------------------------------------------------------------------------
    
    @staticmethod
    async def incr_channel_error(channel_id: int) -> int:
        client = await get_redis_client()
        key = CacheKeys.channel_error_count(channel_id)
        val = await client.incr(key)
        if val == 1:
            await client.expire(key, 3600) 
        return val

    @staticmethod
    async def reset_channel_error(channel_id: int):
        client = await get_redis_client()
        key = CacheKeys.channel_error_count(channel_id)
        await client.delete(key)

    # -------------------------------------------------------------------------
    # 管理接口 (Channel)
    # -------------------------------------------------------------------------

    @staticmethod
    async def sync_channel(channel_id: int):
        ### 将 Channel 从数据库全量同步到 Redis ###
        
        # 预加载 Platform
        channel = await Channel.filter(id=channel_id).prefetch_related("platform").first()
        if not channel:
            return

        client = await get_redis_client()
        pipeline = client.pipeline()

        # 【关键】渠道配置变动，删除预构建的模型 JSON
        await client.delete(CacheKeys.sys_models_json())
        
        p = channel.platform
        
        db_supported = channel.supported_models
        if not db_supported:
            db_supported = getattr(p, "default_models", [])
            
        supported_models_set = set(db_supported)
        p_map = getattr(p, "model_map", {}) or {}
        if p_map:
            supported_models_set.update(p_map.keys())
            
        supported_models = list(supported_models_set)
        
        channel_data = {
            "id": channel.id,
            "platform_id": p.id, 
            "adapter": p.adapter_type,
            "base_url": p.base_url,
            "proxy_url": p.proxy_url, 
            "credentials": channel.credentials,
            "model_map": p.model_map, 
            "rate_limits": channel.rate_limits,
            "weight": channel.weight,
            "models": supported_models
        }
        
        pipeline.set(CacheKeys.channel_info(channel.id), json.dumps(channel_data))
        
        if channel.is_active:
            usage_prog = channel.usage_progress or {}
            limits_map = channel.rate_limits or {}
            
            for model in supported_models:
                rules = limits_map.get(model) or limits_map.get('default', [])
                is_banned = False
                
                for rule in rules:
                    p_val = str(rule['period'])
                    limit = rule['count']
                    
                    # 【核心修改】同步逻辑也要支持 group
                    # 如果配置了 group，进度应该去 group 下面找
                    # 注意：Scheduler 写入 usage_progress 时也需要遵循此逻辑（Key 为 group 名或 model 名）
                    bucket_name = rule.get('group') or model
                    
                    # 从 usage_progress 获取已用量 (假设 DB 中也按照 bucket_name 存储了)
                    # 如果 DB 还是按 model 存，这里逻辑需要适配。
                    # 建议：DB 的 usage_progress 第一层 Key 统一改为 bucket_name
                    used = usage_prog.get(bucket_name, {}).get(p_val, {}).get("count", 0)
                    
                    if used >= limit:
                        is_banned = True
                    
                    # 恢复 Redis 计数器
                    pipeline.set(CacheKeys.channel_usage(channel.id, bucket_name, int(p_val)), used)

                if not is_banned:
                    pipeline.sadd(CacheKeys.available_pool(model), channel.id)
                else:
                    pipeline.srem(CacheKeys.available_pool(model), channel.id)
        else:
            for model in supported_models:
                pipeline.srem(CacheKeys.available_pool(model), channel.id)
        
        await pipeline.execute()
        
        # 【新增】触发增量添加模型到缓存，无需等待全量重构
        if channel.is_active and supported_models:
             await CacheService.add_models_to_cache(supported_models)

        logger.debug(f"向 Redis 同步渠道 {channel.id} (Models {supported_models})")

    @staticmethod
    async def remove_channel(channel_id: int, supported_models: List[str] = None):
        client = await get_redis_client()
        
        # 【关键】渠道移除，删除预构建 JSON，让其下次重建
        await client.delete(CacheKeys.sys_models_json())

        if not supported_models:
            info_json = await client.get(CacheKeys.channel_info(channel_id))
            if info_json:
                data = json.loads(info_json)
                supported_models = data.get("models") 
            
            if not supported_models:
                try:
                    ch = await Channel.filter(id=channel_id).prefetch_related("platform").first()
                    if ch:
                        supported_models = ch.supported_models
                        if not supported_models and ch.platform:
                            p = ch.platform
                            defaults = set(p.default_models or [])
                            if p.model_map:
                                defaults.update(p.model_map.keys())
                            supported_models = list(defaults)
                except Exception as e:
                    logger.error(f"Remove Channel 回源查询失败: {e}")

        pipeline = client.pipeline()
        pipeline.delete(CacheKeys.channel_info(channel_id))
        pipeline.delete(CacheKeys.channel_error_count(channel_id))
        
        if supported_models:
            for model in supported_models:
                pipeline.srem(CacheKeys.available_pool(model), channel_id)
        
        await pipeline.execute()

    # -------------------------------------------------------------------------
    # 【核心新增】模型列表高效缓存与重构逻辑
    # -------------------------------------------------------------------------

    @staticmethod
    async def add_models_to_cache(models: List[str]):
        """
        【增量更新】当有新渠道添加/更新时，快速将新模型加入缓存 Set
        同时删除 JSON 缓存，迫使下次读取重新排序生成
        """
        if not models:
            return
        
        client = await get_redis_client()
        # 1. 添加到 Set (自动去重)
        await client.sadd(CacheKeys.sys_all_models(), *models)
        
        # 2. 删除 JSON 缓存
        await client.delete(CacheKeys.sys_models_json())

    @staticmethod
    async def rebuild_system_models_cache():
        """
        【全量重构】后台 Scheduler 调用：从数据库全量拉取并重算。
        用于确保 Redis 中的模型列表与数据库最终一致（解决删除账号后的残留问题）。
        """
        start_time = time.time()
        client = await get_redis_client()
        
        all_models = set()

        # 1. 聚合 Platform (量小，直接全量)
        platforms = await Platform.all().values("default_models", "model_map")
        for p in platforms:
            if p.get("default_models"):
                all_models.update(p["default_models"])
            if p.get("model_map"):
                all_models.update(p["model_map"].keys())

        # 2. 聚合 Channel (分批处理，防止万级数据 OOM)
        limit = 2000
        offset = 0
        
        while True:
            # 仅取 supported_models 字段
            channels_chunk = await Channel.filter(is_active=True)\
                .limit(limit)\
                .offset(offset)\
                .values("supported_models")
            
            if not channels_chunk:
                break
                
            for c in channels_chunk:
                s_models = c.get("supported_models")
                # 排除空列表或None
                if s_models: 
                    all_models.update(s_models)
            
            offset += limit
            if len(channels_chunk) < limit:
                break

        # 3. 更新 Redis
        model_list = sorted(list(all_models))
        json_str = json.dumps(model_list)
        
        pipeline = client.pipeline()
        
        # 重置 Set
        pipeline.delete(CacheKeys.sys_all_models())
        if model_list:
            pipeline.sadd(CacheKeys.sys_all_models(), *model_list)
        
        # 写入 JSON 缓存 (过期时间可稍长，靠 Scheduler 周期性刷新)
        pipeline.set(CacheKeys.sys_models_json(), json_str, ex=3600)
        
        await pipeline.execute()
        logger.debug(f"全量重构模型列表完成，耗时 {time.time() - start_time:.2f}s，共 {len(model_list)} 个模型")
        return model_list

    @staticmethod
    async def get_all_system_models() -> List[str]:
        """
        获取所有模型给 API 使用，优先读 Redis String，O(1)
        """
        client = await get_redis_client()
        
        # 1. 优先读预生成的 JSON
        cached_json = await client.get(CacheKeys.sys_models_json())
        if cached_json:
            try:
                return json.loads(cached_json)
            except Exception:
                pass
        
        # 2. 如果 JSON 不在 (可能是过期了或被清除了)，读 Set
        members = await client.smembers(CacheKeys.sys_all_models())
        if members:
            # 排序
            m_list = sorted(list(members))
            # 异步回写 JSON 缓存，不阻塞当前请求
            json_str = json.dumps(m_list)
            asyncio.create_task(client.set(CacheKeys.sys_models_json(), json_str, ex=3600))
            return m_list
            
        # 3. 实在没有（冷启动），触发全量构建
        logger.info("模型缓存全量重构中...")
        return await CacheService.rebuild_system_models_cache()
    
    @staticmethod
    async def apply_upstream_sync(channel_id: int, remaining_map: Dict[str, Dict[str, int]]):
        """
        【核心同步逻辑 - 极简修复版】
        根据上游返回的剩余量，结合本地配置的 Limit，反推已用量并覆写 Redis。
        
        核心思想：
        本地配置是主宰。遍历本地 Channel 的所有配置，根据配置里的 Group 或 Model 名 (Bucket Name)，
        直接去上游数据里找。找到了就更新，没找到就拉倒。
        
        不需要任何自动适配或猜测，完全依赖本地配置。
        """
        if not remaining_map:
            return

        client = await get_redis_client()
        
        # 1. 获取渠道配置 (为了拿到 Limit)
        info_json = await client.get(CacheKeys.channel_info(channel_id))
        if not info_json:
            logger.warning(f"同步失败: 找不到 Channel {channel_id} 的缓存配置")
            return
            
        channel_info = json.loads(info_json)
        rate_limits = channel_info.get("rate_limits", {})
        
        pipeline = client.pipeline()
        updated_keys = []

        # 用于去重：因为 rate_limits 是按 Model 存的，多个 Model 可能属于同一个 Group。
        # 我们只需要为这个 Group 更新一次 Redis 即可。
        processed_buckets = set()

        # 2. 遍历本地所有的限流规则
        # rate_limits 结构: { "gemini-2.5-pro": [ { "period": 18000, "count": 3000, "group": "pool_2_5" } ] }
        for model_key, rules in rate_limits.items():
            if not rules:
                continue

            for rule in rules:
                period = rule.get("period")
                limit = rule.get("count")
                group = rule.get("group")
                
                # 【核心逻辑】确定 Bucket Name
                # 既然配置了 Group，那就用 Group 名；没配置就用 Model 名。
                # 这就是我们在 Redis 里存计数器的 Key。
                bucket_name = group if group else model_key
                
                # 唯一标识：避免同 Group 多次处理
                bucket_ident = f"{bucket_name}:{period}"
                if bucket_ident in processed_buckets:
                    continue

                # 3. 直接去上游返回的数据里找这个 Bucket Name
                # 无论上游是按 Group 返回的，还是按 Model 返回的，只要和本地配置的 Bucket Name 对得上就行。
                if bucket_name in remaining_map:
                    upstream_data = remaining_map[bucket_name]
                    period_str = str(period)
                    
                    if period_str in upstream_data:
                        remaining = upstream_data[period_str]
                        
                        # 【核心计算】 已用 = 总限额 - 剩余
                        used = limit - remaining
                        
                        # 边界处理：防止负数 (例如本地 Limit 改小了)
                        if used < 0: 
                            logger.warning(f"Channel {channel_id} [{bucket_name}] Used<0 (Limit {limit} - Rem {remaining}). 归零.")
                            used = 0
                        
                        # 边界处理：防止溢出
                        if used > limit: used = limit

                        # 4. 写入 Redis
                        key = CacheKeys.channel_usage(channel_id, bucket_name, period)
                        pipeline.set(key, used)
                        pipeline.expire(key, period) # 续期
                        
                        updated_keys.append(f"{bucket_name}/{period}={used}")
                        
                        # 标记已处理
                        processed_buckets.add(bucket_ident)

        if updated_keys:
            await pipeline.execute()
            logger.debug(f"Channel {channel_id} 同步使用限额完成: {updated_keys}")
        else:
            # 如果走到这里，说明上游返回的数据 Key，和本地配置算出来的 Bucket Name 没一个对得上的。
            # 比如本地配置 group="pool_A"，上游返回 key="pool_B"。
            pass
    
    @staticmethod
    async def get_current_channel_usage(channel_id: int) -> Dict[str, Dict[str, int]]:
        """
        【新增】辅助方法：从 Redis 读取该渠道当前的所有使用量。
        用于 Service 层将 Redis 数据持久化回数据库。
        """
        client = await get_redis_client()
        info_json = await client.get(CacheKeys.channel_info(channel_id))
        if not info_json:
            return {}

        channel_info = json.loads(info_json)
        rate_limits = channel_info.get("rate_limits", {})
        
        # 结果容器: { "bucket": { "period": count } }
        result = {}
        
        # 遍历配置的所有规则，去 Redis 查当前值
        for model_or_group, rules in rate_limits.items():
            # 这里的 model_or_group 只是 dict 的 key，具体 bucket 要看 rule 里的 group
            for rule in rules:
                period = rule.get("period")
                # 【核心逻辑】保持一致：优先取 group，无 group 取 model 名
                bucket_name = rule.get("group") or model_or_group
                
                key = CacheKeys.channel_usage(channel_id, bucket_name, period)
                val = await client.get(key)
                
                if val is not None:
                    if bucket_name not in result:
                        result[bucket_name] = {}
                    result[bucket_name][str(period)] = int(val)
                    
        return result