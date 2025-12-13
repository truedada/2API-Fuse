# app/adapters/constants.py
from enum import Enum

class AdapterType(str, Enum):
    """
    适配器类型枚举
    Value 对应数据库中存储的字符串，也对应 Factory 中的判断依据
    """
    QWEN = "qwen"
    OPENAI = "openai"
    ZAI = "zai"
    GEMINICLI = "geminicli"
    ANTIGRAVITY = "antigravity"

    @classmethod
    def list_all(cls):
        """返回所有支持的类型列表"""
        return [member.value for member in cls]

    @classmethod
    def choices(cls):
        """返回 (Value, Label) 格式，可用于前端 Select"""
        return [
            {"value": cls.QWEN.value, "label": "Qwen (通义千问)"},
            {"value": cls.OPENAI.value, "label": "OpenAI (标准协议)"},
            {"value": cls.ZAI.value, "label": "Z.ai (智谱海外)"},
            {"value": cls.GEMINICLI.value, "label": "Gemini CLI"},
            {"value": cls.ANTIGRAVITY.value, "label": "Antigravity"},
        ]