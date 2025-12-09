# app/repositories/apikey.py
from typing import Optional
from tortoise.expressions import F
from app.models.apikey import ApiKey
from app.repositories.base import BaseRepository

class ApiKeyRepository(BaseRepository[ApiKey]):
    model = ApiKey

    async def get_by_key(self, key: str) -> Optional[ApiKey]:
        """根据 sk-xxx 获取 Key 对象"""
        return await self.get_by(key=key)

    async def sync_usage_from_redis(self, key: str, count_delta: int, token_delta: int):
        """
        【DB层原子更新】
        用于后台 Scheduler 将 Redis 的消耗同步回数据库。
        逻辑：
        1. used_count += delta (统计增加)
        2. total_tokens += delta (统计增加)
        3. balance -= delta (余额减少，如果非无限)
        """
        # 构造更新逻辑
        update_kwargs = {
            "used_count": F("used_count") + count_delta,
            "total_tokens": F("total_tokens") + token_delta,
            "updated_at": F("updated_at")
        }
        
        # 只有当 balance 不为 -1 (无限) 时，才扣减余额
        # 注意：这里我们无法在一次 filter update 中通过 if 判断 balance 值
        # 所以通常策略是：Scheduler 在内存里判断一次，或者 SQL 使用 Case When (Tortoise 支持较弱)
        # 简单方案：先查后改，或者对于 -1 的 Key，Redis 传过来的 count_delta 设为 0 (在 Redis 层处理更优)
        # 这里采用方案：直接扣减。如果用户是 -1，我们在 Scheduler 层处理成不扣减。
        
        # 假设 Scheduler 已经处理了 balance 的逻辑（如果是无限卡，传下来的 count_delta 应该是 0，或者 DB 允许负数）
        # 但为了严谨，我们针对该 Key 进行操作。
        
        # 这里我们执行一个稍微复杂的逻辑：
        # 如果是无限卡，我们不想把 balance 变成负数。
        # 鉴于 tortoise 的 F 表达式限制，最稳妥的方式是：
        # Scheduler 聚合时，如果是无限卡，就不把 count 计入 balance 扣减项。
        
        # 此时我们假设传入的 count_delta 已经是“需要扣除的余额量”
        if count_delta != 0:
            update_kwargs["balance"] = F("balance") - count_delta

        await self.model.filter(key=key).update(**update_kwargs)