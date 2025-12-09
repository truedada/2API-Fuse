# app/models/apikey.py
from tortoise import fields, models
import uuid

def generate_sk():
    return f"sk-{uuid.uuid4().hex}"

class ApiKey(models.Model):
    """
    下游用户的调用凭证
    策略：余额扣减模式 (Balance)
    """
    id = fields.IntField(pk=True)
    
    # 核心凭证
    key = fields.CharField(max_length=64, unique=True, index=True, default=generate_sk)
    name = fields.CharField(max_length=100, null=True)
    # 备注/用户名
    
    # 计费字段 (核心改动: 余额模式)
    # -1 代表无限余额，>0 代表剩余可用次数
    balance = fields.IntField(default=0)
    
    # 统计字段 (仅记录，不参与鉴权逻辑)
    used_count = fields.IntField(default=0)
    # 累计调用次数
    total_tokens = fields.BigIntField(default=0)
    # 累计消耗Token数(Prompt+Completion)
    
    # 状态控制
    is_active = fields.BooleanField(default=True)
    # 是否启用
    created_at = fields.DatetimeField(auto_now_add=True)
    updated_at = fields.DatetimeField(auto_now=True)

    class Meta:
        table = "api_keys"

    @property
    def has_quota(self) -> bool:
        """是否有可用额度"""
        if self.balance == -1:
            return True
        return self.balance > 0

    def __str__(self):
        return f"{self.name} [{self.key[:8]}...] Bal:{self.balance}"