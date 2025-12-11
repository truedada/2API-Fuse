# app/models/usage_log.py
from tortoise import fields, models

class UsageLog(models.Model):
    """
    API 使用记录
    """
    id = fields.IntField(pk=True)
    
    # 追踪ID，建议在请求入口生成 UUID，方便排查问题
    trace_id = fields.CharField(max_length=64, index=True, null=True)

    # 关联的 API Key
    # on_delete=fields.SET_NULL: 当 ApiKey 被删除时，设为 NULL，保留历史账单
    api_key = fields.ForeignKeyField("models.ApiKey", related_name="usage_logs", on_delete=fields.SET_NULL, null=True)

    # 关联的渠道
    channel = fields.ForeignKeyField("models.Channel", related_name="usage_logs", on_delete=fields.SET_NULL, null=True)

    # 调用的模型名称 (用户请求的模型 vs 实际执行的模型，这里记录用户请求的即可，或者两者都记录)
    model_name = fields.CharField(max_length=100, index=True)

    # 消耗的Token
    prompt_tokens = fields.IntField(default=0)
    completion_tokens = fields.IntField(default=0)
    total_tokens = fields.IntField(default=0)

    # 请求耗时 (毫秒)
    duration_ms = fields.IntField(default=0)
    
    # 是否流式请求
    is_stream = fields.BooleanField(default=False)

    # 记录创建时间
    created_at = fields.DatetimeField(auto_now_add=True, index=True)

    class Meta:
        table = "usage_logs"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Log {self.id} | {self.model_name} | Tokens: {self.total_tokens}"