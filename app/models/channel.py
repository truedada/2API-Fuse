# app/models/channel.py
from tortoise import fields, models

class Channel(models.Model):
    """
    具体账号 (Credentials)
    """
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=100)
    # 账号备注，一般用邮箱即可
    
    # 关联到平台模板 (Platform)
    platform = fields.ForeignKeyField('models.Platform', related_name='channels')
    
    # 核心凭证 (Key, Token, Cookie等)
    credentials = fields.JSONField(default={})
    # 认证信息
    
    # 【关键】该特定账号支持的模型列表
    # 这里的模型名应该是“下游请求的模型名”
    supported_models = fields.JSONField(default=[])
    # 该账号支持的模型列表

    # ### 限流配置 ###
    # 格式示例:
    # {
    #   "gemini-pro": [ {"period": 86400, "count": 100} ] 
    # }
    rate_limits = fields.JSONField(default={})
    # 模型速率配置限制
    
    # 【修改】对应记录不同周期的使用量
    # ### 进度存储 ###
    # 这是 Scheduler 异步写入的地方，记录了当前的使用状态
    # 格式: 
    # {
    #   "gemini-pro": {
    #       "60": {"count": 15, "last_reset": 171...},     # 分钟桶进度
    #       "86400": {"count": 500, "last_reset": 171...},  # 天桶进度
    #       "604800": {"count": 2000, "last_reset": 171...} # 周桶进度
    #   }
    # }
    usage_progress = fields.JSONField(default={})
    # 周期使用进度
    
    # 【新增】关键优化：索引下次重置时间
    # 之前的轮询是遍历所有账号，性能太差。
    # 我们记录该账号所有规则中，"最早需要重置" 的那个时间点。
    # Worker 只需要查询 next_reset_time <= now 的记录即可。
    next_reset_time = fields.IntField(default=0, index=True)
    
    # 权重 (用于负载均衡)
    weight = fields.IntField(default=1)
    
    # 状态
    is_active = fields.BooleanField(default=True)
    error_count = fields.IntField(default=0)
    
     # 【新增】用于存储同步回来的信息
    # 余额 (可以是金额，也可以是剩余次数，由 adapter 定义单位)
    balance = fields.FloatField(default=0.0) 
    # 余额更新时间
    balance_updated_at = fields.DatetimeField(null=True)
    
    # 最近一次测试/验证的结果信息 (如: "Valid", "Expired", "Quota Exceeded")
    status_msg = fields.CharField(max_length=255, null=True)
    # 最近一次测试时间
    test_at = fields.DatetimeField(null=True)
    created_at = fields.DatetimeField(auto_now_add=True)

    class Meta:
        table = "channels"
        # 【新增】联合唯一索引：同一个平台下，name 必须唯一
        unique_together = (("platform", "name"),)