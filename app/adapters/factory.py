# app/adapters/factory.py
from typing import Dict, Any, List
from app.adapters.constants import AdapterType
from app.adapters.base import BaseAdapter
from app.adapters.qwen.adapter import QwenAdapter
from app.adapters.openai.adapter import OpenAIAdapter
from app.adapters.zai.adapter import ZaiAdapter
from app.adapters.geminicli.adapter import GeminiCliAdapter
from app.adapters.antigravity.adapter import AntigravityAdapter

class AdapterFactory:
    @staticmethod
    def get_adapter(adapter_type: str, config: Dict[str, Any]) -> BaseAdapter:
        """
        根据类型字符串返回对应的适配器实例
        """
        # 尝试将字符串转换为枚举，如果不在枚举中，会自动抛出 ValueError
        try:
            # 兼容传入的是枚举成员还是字符串
            type_enum = AdapterType(adapter_type.lower())
        except ValueError:
             raise ValueError(f"未知的适配器类型: {adapter_type}")

        if type_enum == AdapterType.QWEN:
            return QwenAdapter(config)
        elif type_enum == AdapterType.OPENAI:
            return OpenAIAdapter(config)
        elif type_enum == AdapterType.ZAI:
            return ZaiAdapter(config)
        elif type_enum == AdapterType.GEMINICLI:
            return GeminiCliAdapter(config)
        elif type_enum == AdapterType.ANTIGRAVITY:
            return AntigravityAdapter(config)
        else:
            # 理论上上面 try...except 已经拦截，但这作为防御性编程
            raise ValueError(f"适配器 {adapter_type} 尚未在 Factory 中实现")

    @staticmethod
    def get_supported_adapters() -> List[Dict[str, str]]:
        """
        获取所有支持的适配器类型列表 (用于前端下拉选择)
        """
        return AdapterType.choices()