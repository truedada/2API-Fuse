from typing import Dict, Any
from app.adapters.base import BaseAdapter
from app.adapters.qwen.adapter import QwenAdapter
from app.adapters.openai.adapter import OpenAIAdapter
from app.adapters.zai.adapter import ZaiAdapter
from app.adapters.geminicli.adapter import GeminiCliAdapter
from app.adapters.antigravity.adapter import AntigravityAdapter
class AdapterFactory:
    @staticmethod
    def get_adapter(adapter_type: str, config: Dict[str, Any]) -> BaseAdapter:
        if adapter_type == "qwen":
            return QwenAdapter(config)
        elif adapter_type == "openai":
            return OpenAIAdapter(config)
        elif adapter_type == "zai":
            return ZaiAdapter(config)
        elif adapter_type == "geminicli":
            return GeminiCliAdapter(config)
        elif adapter_type == "antigravity":
            return AntigravityAdapter(config)
        else:
            # 这里抛出自定义异常，Handler 会处理成 400 或 500
            raise ValueError(f"未知的适配器: {adapter_type}")