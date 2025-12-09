# app/models/platform.py
from tortoise import fields, models

class Platform(models.Model):
    """
    上游平台配置模板
    """
    id = fields.IntField(pk=True)
    name = fields.CharField(max_length=50, unique=True)
    # 适配器类型 (openai, azure, gemini, claude...)
    adapter_type = fields.CharField(max_length=50, default="openai")
    
    base_url = fields.CharField(max_length=255)
    proxy_url = fields.CharField(max_length=255, null=True)
    
    # 模型重命名映射
    model_map = fields.JSONField(default={})

    default_models = fields.JSONField(default=[])

    # 【新增】公共额外配置
    # 用于存储 headers, cookies, auth token 等需要热更新且复用的字段
    extra_config = fields.JSONField(default={})

    class Meta:
        table = "platforms"