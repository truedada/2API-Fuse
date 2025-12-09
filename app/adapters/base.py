# app/adapters/base.py
import abc
from typing import Dict, Any, AsyncGenerator, List, Optional, Callable, Awaitable
from datetime import datetime

class BaseAdapter(abc.ABC):
    """
    所有平台适配器的基类。
    """
    def __init__(self, config: Dict[str, Any]):
        """
        config: 包含 base_url, credentials, proxy, extra_config 等
        """
        self.config = config
        self.credentials = config.get("credentials", {})
        # 合并 extra_config 中的 headers 到 adapter 内部处理逻辑中
        self.extra_config = config.get("extra_config", {}) 
        self.base_url = config.get("base_url")
        self.proxy_url = config.get("proxy_url")
        
        # 回调函数：用于将更新后的凭证保存回数据库/缓存
        # 签名: async def callback(new_credentials: Dict[str, Any]) -> None
        self._credential_update_callback: Optional[Callable[[Dict[str, Any]], Awaitable[None]]] = None

    def set_credential_update_callback(self, callback: Callable[[Dict[str, Any]], Awaitable[None]]):
        """
        设置凭证更新回调。Service 层在初始化 Adapter 后调用此方法注入逻辑。
        """
        self._credential_update_callback = callback

    async def save_credentials(self, new_credentials: Dict[str, Any]):
        """
        适配器内部调用此方法来持久化新的凭证。
        """
        self.credentials = new_credentials
        # 同时更新 config 中的引用，防止部分逻辑读取 config
        self.config["credentials"] = new_credentials
        
        if self._credential_update_callback:
            await self._credential_update_callback(new_credentials)

    @abc.abstractmethod
    async def chat_completion(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """普通对话 (非流式)"""
        pass

    @abc.abstractmethod
    async def chat_completion_stream(self, request_data: Dict[str, Any]) -> AsyncGenerator[str, None]:
        """流式对话"""
        pass

    # --- 新增的管理功能接口 ---

    async def validate_credential(self) -> bool:
        """
        验证凭证是否有效。
        默认实现：尝试发起一个简单的请求 (如列出模型)。
        """
        try:
            # 默认调用获取模型接口来测试连通性
            await self.fetch_models()
            return True
        except Exception:
            return False

    async def fetch_models(self) -> List[str]:
        """
        获取该渠道支持的模型列表。
        """
        raise NotImplementedError("该适配器不支持自动获取模型列表")

    async def fetch_balance(self) -> Dict[str, Any]:
        """
        获取余额/限额信息。
        返回格式约定:
        {
            "balance": 10.5,      # 剩余数值 (必须)
            "currency": "USD",    # 单位 (CNY, USD, COUNT...)
            "total_usage": 100.0, # 已用 (可选)
            "raw": {...}          # 原始响应 (可选)
        }
        """
        raise NotImplementedError("该适配器不支持查询余额")

    async def refresh_session(self) -> Dict[str, Any]:
        """
        刷新会话/Token (针对 Web 逆向或需要定期换 Token 的渠道)。
        返回新的 credentials 字典，Service 层负责更新入库。
        注意：如果是在 chat_completion 过程中自动刷新的，请使用 await self.save_credentials(...)
        """
        # 默认不需要刷新
        return {}